"""MCP tool and safety-contract tests."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import Tool

from deadbolt.api import DemoApi
from deadbolt.mcp_lambda import lambda_handler
from deadbolt.mcp_server import DeadboltMcp, build_server
from deadbolt.providers.github import GitHubProvider

pytestmark = pytest.mark.m9
_TOOL_RESULT_PARTS = 2
_HTTP_OK = 200


def _call(server: FastMCP[None], name: str, arguments: dict[str, object]) -> tuple[object, object]:
    result = asyncio.run(server.call_tool(name, arguments))
    assert isinstance(result, tuple)
    assert len(result) == _TOOL_RESULT_PARTS
    return result[0], result[1]


def test_server_advertises_read_and_action_tools_with_annotations() -> None:
    async def list_tools() -> list[Tool]:
        return list(await build_server().list_tools())

    tools = asyncio.run(list_tools())
    names = {tool.name for tool in tools}
    assert names == {
        "list_findings",
        "get_finding",
        "get_plan",
        "get_audit_log",
        "get_metrics",
        "github_inventory",
        "github_user_access",
        "github_onboard_user",
        "github_remove_repository_access",
        "github_remove_team_access",
        "github_remove_organization_member",
        "rerun_drift",
        "approve_finding",
        "reduce_access",
        "record_decision",
        "rollback_finding",
    }
    read_only = next(tool for tool in tools if tool.name == "list_findings")
    action = next(tool for tool in tools if tool.name == "approve_finding")
    assert read_only.annotations is not None
    assert action.annotations is not None
    assert read_only.annotations.readOnlyHint is True
    assert action.annotations.readOnlyHint is False
    assert action.annotations.openWorldHint is False


def test_read_tools_return_structured_content_without_raw_provider_payloads() -> None:
    server = build_server(DemoApi())
    _, structured = _call(server, "list_findings", {"limit": 1})
    assert isinstance(structured, dict)
    findings = structured["findings"]
    assert isinstance(findings, list)
    assert findings[0]["finding_id"] == "FIND-001"
    assert "raw" not in findings[0]["entitlement"]

    _, metrics = _call(server, "get_metrics", {})
    assert isinstance(metrics, dict)
    assert metrics["cost"] == 0.0


def test_demo_actions_require_confirmation_and_current_plan_hash() -> None:
    handlers = DeadboltMcp(DemoApi())
    plan_hash = handlers.get_plan("FIND-001")["hash"]
    server = build_server(handlers.service)

    with pytest.raises(ToolError, match="confirm=true"):
        _call(
            server,
            "approve_finding",
            {"finding_id": "FIND-001", "approver": "demo", "expected_plan_hash": plan_hash},
        )
    with pytest.raises(ToolError, match="stale"):
        _call(
            server,
            "approve_finding",
            {
                "finding_id": "FIND-001",
                "approver": "demo",
                "expected_plan_hash": "stale",
                "confirm": True,
            },
        )

    _, finding = _call(
        server,
        "approve_finding",
        {
            "finding_id": "FIND-001",
            "approver": "demo",
            "expected_plan_hash": plan_hash,
            "confirm": True,
        },
    )
    assert isinstance(finding, dict)
    assert finding["current_stage"] == "Verified"


@pytest.mark.m9
@respx.mock
def test_github_iam_tools_preview_writes_and_read_live_inventory() -> None:
    base = "https://api.github.test"
    respx.get(f"{base}/orgs/acme/members").mock(
        return_value=httpx.Response(200, json=[{"login": "alice"}])
    )
    respx.get(f"{base}/orgs/acme/repos").mock(
        return_value=httpx.Response(200, json=[{"name": "demo", "full_name": "acme/demo"}])
    )
    respx.get(f"{base}/orgs/acme/teams").mock(
        return_value=httpx.Response(200, json=[{"slug": "engineering", "name": "Engineering"}])
    )
    respx.get(f"{base}/orgs/acme/memberships/alice").mock(
        return_value=httpx.Response(200, json={"login": "alice", "state": "active"})
    )
    respx.get(f"{base}/user").mock(return_value=httpx.Response(200, json={"login": "alice"}))
    respx.get(f"{base}/repos/acme/demo/collaborators").mock(
        return_value=httpx.Response(200, json=[{"login": "alice", "permission": "pull"}])
    )
    respx.put(f"{base}/orgs/acme/memberships/new-dev").mock(
        return_value=httpx.Response(200, json={"login": "new-dev", "role": "member"})
    )
    respx.put(f"{base}/repos/acme/demo/collaborators/new-dev").mock(
        return_value=httpx.Response(201, json={"permission": "pull"})
    )
    respx.put(f"{base}/orgs/acme/teams/engineering/memberships/new-dev").mock(
        return_value=httpx.Response(200, json={"role": "member"})
    )
    respx.delete(f"{base}/repos/acme/demo/collaborators/alice").mock(
        return_value=httpx.Response(204)
    )
    provider = GitHubProvider("acme", "token", repos=("acme/demo",), base_url=base)
    handlers = DeadboltMcp(DemoApi(), provider)

    inventory = handlers.github_inventory()
    assert inventory["organization"] == "acme"
    assert inventory["repositories"] == [{"name": "demo", "full_name": "acme/demo"}]
    access = handlers.github_user_access("alice")
    assert access["access"][0]["resource"] == "acme/demo"

    preview = handlers.github_onboard_user(
        "new-dev", ["acme/demo"], "pull", "member", ["engineering"], "", False
    )
    assert preview["status"] == "preview"
    assert isinstance(preview["plan_hash"], str)
    applied = handlers.github_onboard_user(
        "new-dev",
        ["acme/demo"],
        "pull",
        "member",
        ["engineering"],
        str(preview["plan_hash"]),
        True,
    )
    assert applied["status"] == "applied"
    removal = handlers.github_remove_repository_access("alice", "acme/demo", "", False)
    assert removal["status"] == "preview"
    removed = handlers.github_remove_repository_access(
        "alice", "acme/demo", str(removal["plan_hash"]), True
    )
    assert removed["status"] == "applied"
    assert (
        handlers.github_remove_team_access("alice", "engineering", "", False)["status"] == "preview"
    )
    assert handlers.github_remove_organization_member("alice", "", False)["status"] == "preview"


def test_limit_tier_and_finding_validation() -> None:
    server = build_server(DemoApi())
    _, structured = _call(server, "list_findings", {"tier": "T2", "limit": 1})
    assert isinstance(structured, dict)
    assert structured["count"] == 1
    assert structured["findings"][0]["tier"] == "T2"

    with pytest.raises(ToolError, match="between 1 and 100"):
        _call(server, "get_audit_log", {"limit": 0})
    with pytest.raises(ToolError, match="finding not found"):
        _call(server, "get_finding", {"finding_id": "FIND-999"})


def test_non_mutating_decision_and_rollback_are_available_in_demo() -> None:
    handlers = DeadboltMcp(DemoApi())
    server = build_server(handlers.service)
    _, deferred = _call(
        server,
        "record_decision",
        {
            "finding_id": "FIND-001",
            "action": "Defer 30 days",
            "approver": "demo",
            "reason": "Review next month",
        },
    )
    assert isinstance(deferred, dict)
    assert deferred["current_stage"] == "Planned"

    plan_hash = handlers.get_plan("FIND-001")["hash"]
    _, rolled_back = _call(
        server,
        "rollback_finding",
        {"finding_id": "FIND-001", "expected_plan_hash": plan_hash, "confirm": True},
    )
    assert isinstance(rolled_back, dict)
    assert rolled_back["current_stage"] == "Rolled back"


def test_lambda_adapter_completes_mcp_initialize() -> None:
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/mcp",
        "rawQueryString": "",
        "headers": {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "host": "demo.lambda-url.us-east-1.on.aws",
        },
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/mcp",
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "test",
            }
        },
        "body": json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        ),
        "isBase64Encoded": False,
    }
    response = lambda_handler(event, object())
    assert isinstance(response, dict)
    assert response["statusCode"] == _HTTP_OK
    assert '"protocolVersion"' in str(response["body"])

    second_response = lambda_handler(event, object())
    assert isinstance(second_response, dict)
    assert second_response["statusCode"] == _HTTP_OK
