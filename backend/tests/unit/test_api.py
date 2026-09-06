"""Browser API contract tests for the fixture-backed rehearsal service."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from http.client import HTTPResponse
from http.server import ThreadingHTTPServer
from threading import Thread
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from deadbolt.api import DemoApi, _RequestHandler, lambda_handler
from deadbolt.connections import ConnectionService, MemoryConnectionStore
from deadbolt.contracts.models import CredentialType, Entitlement, Scope
from deadbolt.graph.capture import write_capture

pytestmark = pytest.mark.m9
HTTP_OK = 200
HTTP_NOT_FOUND = 404
EXPECTED_FINDING_COUNT = 21
MAX_RISK_SCORE = 100


def _open_local(target: str | Request) -> HTTPResponse:
    return cast(HTTPResponse, urlopen(target))  # noqa: S310 — test target is loopback.


def test_api_exposes_real_scenario_findings_and_plans() -> None:
    api = DemoApi()

    status, findings = api.handle("GET", "/api/findings")
    assert status == HTTP_OK
    assert isinstance(findings, list)
    assert len(findings) == EXPECTED_FINDING_COUNT
    assert findings[0]["finding_id"] == "FIND-001"
    score = findings[0]["score"]
    assert isinstance(score, dict)
    total = score["total"]
    assert isinstance(total, (int, float))
    assert total <= MAX_RISK_SCORE

    status, plan = api.handle("GET", "/api/plans/FIND-001")
    assert status == HTTP_OK
    assert isinstance(plan, dict)
    assert plan["finding_id"] == "FIND-001"
    actions = plan["actions"]
    assert isinstance(actions, list)
    assert len(actions) == 1


def test_api_can_serve_a_redacted_captured_github_dataset(tmp_path) -> None:
    entitlement = Entitlement(
        identity_id="octocat",
        system="github",
        resource="acme/platform",
        scope=Scope.READ,
        granted_at=None,
        last_used_at=None,
        credential_type=CredentialType.PAT,
        revocable=False,
        raw={"kind": "pat", "login": "octocat"},
    )
    write_capture(
        (entitlement,),
        tmp_path,
        org="acme",
        captured_at=datetime(2026, 9, 6, tzinfo=UTC),
    )

    findings = DemoApi(capture_dir=tmp_path).findings()

    captured = [item for item in findings if item["source"] == "captured"]
    assert captured
    assert captured[0]["entitlement"]["system"] == "github"

    assert len(findings) == 1
    assert {item["entitlement"]["system"] for item in findings} == {"github"}


def test_connection_configuration_returns_no_secret_material() -> None:
    api = DemoApi()

    status, values = api.handle("GET", "/api/connections")
    assert status == HTTP_OK
    assert isinstance(values, list)
    assert {item["provider"] for item in values} == {"github", "salesforce", "workday"}

    status, saved = api.handle(
        "POST",
        "/api/connections/github",
        {"org": "acme", "repos": ["acme/platform"], "token": "secret-value"},
    )
    assert status == HTTP_OK
    assert isinstance(saved, dict)
    assert saved["status"] == "connected"
    assert "token" not in saved

    status, values = api.handle("GET", "/api/connections")
    assert status == HTTP_OK
    github = next(item for item in values if item["provider"] == "github")
    assert github["status"] == "connected"


def test_authenticated_scan_promotes_a_read_only_provider_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entitlement = Entitlement(
        identity_id="octocat",
        system="github",
        resource="acme/platform",
        scope=Scope.WRITE,
        granted_at=None,
        last_used_at=None,
        credential_type=CredentialType.PAT,
        revocable=True,
        raw={"kind": "collaborator", "login": "octocat"},
    )

    class _Provider:
        def snapshot(self) -> tuple[Entitlement, ...]:
            return (entitlement,)

    monkeypatch.setattr("deadbolt.connections.GitHubProvider", lambda *args, **kwargs: _Provider())
    service = ConnectionService(MemoryConnectionStore())
    api = DemoApi(connection_service=service)
    api.handle(
        "POST",
        "/api/connections/github",
        {"org": "acme", "repos": ["acme/platform"], "token": "secret-value"},
        subject="operator",
    )

    status, result = api.handle(
        "POST",
        "/api/connections/github",
        {"action": "scan"},
        subject="operator",
    )
    assert status == HTTP_OK
    assert isinstance(result, dict)
    assert result["dashboard_updated"] is True
    assert result["record_count"] == 1
    findings = api.findings()
    assert len(findings) == 1
    assert findings[0]["entitlement"]["resource"] == "acme/platform"
    assert findings[0]["source"] == "captured"


def test_captured_read_access_reduction_records_a_verified_staged_revoke(tmp_path) -> None:
    entitlement = Entitlement(
        identity_id="octocat",
        system="github",
        resource="acme/platform",
        scope=Scope.READ,
        granted_at=None,
        last_used_at=None,
        credential_type=CredentialType.OAUTH,
        revocable=True,
        raw={"kind": "collaborator", "login": "octocat"},
    )
    write_capture(
        (entitlement,),
        tmp_path,
        org="acme",
        captured_at=datetime(2026, 9, 6, tzinfo=UTC),
    )

    api = DemoApi(capture_dir=tmp_path)
    status, finding = api.handle(
        "POST",
        "/api/findings/FIND-001/decision",
        {"action": "Reduce further", "approver": "Test Approver"},
    )

    assert status == HTTP_OK
    assert isinstance(finding, dict)
    assert finding["current_stage"] == "Verified"
    assert finding["stage_status"] == "passed"


def test_api_uses_bounded_negotiation_and_safe_fixture_actions() -> None:
    api = DemoApi()
    original_hash = api.plan("FIND-001")["hash"]

    status, finding = api.handle(
        "POST",
        "/api/findings/FIND-001/decision",
        {"action": "Reduce further", "approver": "Test Approver"},
    )
    assert status == HTTP_OK
    assert isinstance(finding, dict)
    reduced_plan = api.plan("FIND-001")
    assert reduced_plan["hash"] != original_hash
    actions = reduced_plan["actions"]
    assert isinstance(actions, list)
    assert isinstance(actions[0], dict)
    assert actions[0]["type"] in {"downgrade", "revoke"}

    status, approved = api.handle(
        "POST",
        "/api/findings/FIND-001/decision",
        {"action": "Approve", "approver": "Test Approver"},
    )
    assert status == HTTP_OK
    assert isinstance(approved, dict)
    assert approved["current_stage"] == "Verified"
    assert approved["stage_status"] == "passed"

    status, rolled_back = api.handle("POST", "/api/findings/FIND-001/rollback")
    assert status == HTTP_OK
    assert isinstance(rolled_back, dict)
    assert rolled_back["current_stage"] == "Rolled back"


def test_api_rejects_invalid_decisions_and_supports_health_check() -> None:
    api = DemoApi()

    assert api.handle("GET", "/health") == (200, {"ok": True, "mode": "fixture"})
    metrics = api.metrics()
    assert metrics["mean_time_to_revocation"] == "—"
    assert metrics["reversibility"] == 0.0
    assert metrics["counts"] == {
        "planted": 20,
        "detected": 20,
        "executed": 0,
        "revocations": 0,
        "rollback_success": 0,
    }
    with pytest.raises(ValueError, match="unsupported approval action"):
        api.handle(
            "POST",
            "/api/findings/FIND-001/decision",
            {"action": "Delete everything", "approver": "Test Approver"},
        )


def test_api_covers_remaining_decision_and_route_paths() -> None:
    api = DemoApi()

    assert api.handle("GET", "/api/audit")[0] == HTTP_OK
    assert api.handle("GET", "/api/metrics")[0] == HTTP_OK
    assert api.handle("POST", "/api/findings/FIND-001/rerun")[0] == HTTP_OK
    assert api.handle("GET", "/api/findings/FIND-001")[0] == HTTP_OK
    assert api.handle("GET", "/api/plans/FIND-001")[0] == HTTP_OK
    assert api.handle("GET", "/unknown")[0] == HTTP_NOT_FOUND

    status, kept = api.handle(
        "POST",
        "/api/findings/FIND-001/decision",
        {
            "action": "Keep, with reason",
            "approver": "Test Approver",
            "reason": "Incident response coverage",
        },
    )
    assert status == HTTP_OK
    assert isinstance(kept, dict)

    status, deferred = api.handle(
        "POST",
        "/api/findings/FIND-003/decision",
        {"action": "Defer 30 days", "approver": "Test Approver"},
    )
    assert status == HTTP_OK
    assert isinstance(deferred, dict)
    assert deferred["current_stage"] == "Planned"


def test_http_handler_and_lambda_adapter_use_the_same_contract() -> None:
    service = DemoApi()

    class Handler(_RequestHandler):
        pass

    Handler.service = service
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with _open_local(f"{base_url}/health") as response:
            health = json.loads(response.read())
            assert health["mode"] == "fixture"
        with _open_local(f"{base_url}/api/metrics") as response:
            assert response.status == HTTP_OK
        request = Request(  # noqa: S310 — request is sent only to the loopback test server.
            f"{base_url}/api/findings/FIND-001/decision",
            data=json.dumps({"action": "Defer 30 days", "approver": "HTTP"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _open_local(request) as response:
            result = json.loads(response.read())
            assert result["current_stage"] == "Planned"
        with pytest.raises(HTTPError) as missing:
            _open_local(f"{base_url}/api/findings/NOPE")
        assert missing.value.code == HTTP_NOT_FOUND
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    encoded = base64.b64encode(
        json.dumps({"action": "Defer 30 days", "approver": "Lambda"}).encode()
    ).decode()
    result = lambda_handler(
        {
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": "/api/findings/FIND-001/decision",
            "body": encoded,
            "isBase64Encoded": True,
        }
    )
    assert result["statusCode"] == HTTP_OK
    health = lambda_handler({"requestContext": {"http": {"method": "GET"}}, "rawPath": "/health"})
    assert health["statusCode"] == HTTP_OK
