"""M5 offline connector and CLI tests."""

from __future__ import annotations

from datetime import UTC, datetime

import boto3
import httpx
import pytest
import respx
from moto import mock_aws

from deadbolt.cli import main
from deadbolt.contracts.models import Scope
from deadbolt.providers.aws_iam import AWSIAMProvider
from deadbolt.providers.github import GitHubProvider
from tests.support.provider_compliance import assert_provider_contract

pytestmark = pytest.mark.m5
EXPECTED_MEMBER_REQUESTS = 4


@pytest.mark.m5
@mock_aws
def test_iam_managed_policy_revoke_restore_and_contract() -> None:
    iam = boto3.client("iam", region_name="us-east-1")
    iam.create_user(UserName="alice")
    policy = iam.create_policy(
        PolicyName="ReadOnlyAccess",
        PolicyDocument=(
            '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
            '"Action":"s3:GetObject","Resource":"*"}]}'
        ),
    )
    iam.attach_user_policy(UserName="alice", PolicyArn=policy["Policy"]["Arn"])
    provider = AWSIAMProvider(iam, sleeper=lambda _: None)
    assert_provider_contract(provider)
    entitlement = next(item for item in provider.snapshot() if item.scope is Scope.READ)
    dry = provider.revoke(entitlement, True)
    assert dry.ok and dry.dry_run
    assert iam.list_attached_user_policies(UserName="alice")["AttachedPolicies"]
    applied = provider.revoke(entitlement, False)
    assert applied.ok and not iam.list_attached_user_policies(UserName="alice")["AttachedPolicies"]
    repeated = provider.revoke(entitlement, False)
    assert repeated.ok
    restored = provider.restore(applied.pre_image or {})
    assert restored.ok
    assert iam.list_attached_user_policies(UserName="alice")["AttachedPolicies"]


@pytest.mark.m5
@mock_aws
def test_iam_inline_policy_is_url_decoded_and_round_trips() -> None:
    iam = boto3.client("iam", region_name="us-east-1")
    iam.create_user(UserName="alice")
    document = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Action": "s3:GetObject", "Resource": "*"}],
    }
    iam.put_user_policy(
        UserName="alice",
        PolicyName="read-s3",
        PolicyDocument=(
            '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
            '"Action":"s3:GetObject","Resource":"*"}]}'
        ),
    )
    provider = AWSIAMProvider(iam, sleeper=lambda _: None)
    entitlement = next(iter(provider.snapshot()))
    assert entitlement.raw["policy_document"] == document
    result = provider.revoke(entitlement, False)
    assert result.ok
    assert provider.restore(result.pre_image or {}).ok
    assert iam.list_user_policies(UserName="alice")["PolicyNames"] == ["read-s3"]


@pytest.mark.m5
@respx.mock
def test_github_pagination_revoke_restore_and_rate_headers() -> None:
    base = "https://api.github.test"
    members_route = respx.get(f"{base}/orgs/acme/members").mock(
        side_effect=[
            httpx.Response(
                200,
                json=[{"login": "alice"}],
                headers={"Link": f'<{base}/orgs/acme/members?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[]),
            httpx.Response(
                200,
                json=[{"login": "alice"}],
                headers={"Link": f'<{base}/orgs/acme/members?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[]),
        ]
    )
    respx.get(f"{base}/user").mock(
        return_value=httpx.Response(
            200,
            json={
                "login": "alice",
                "scopes": ["repo"],
                "last_used_at": "2026-01-01T00:00:00Z",
            },
        )
    )
    respx.get(f"{base}/repos/acme/demo/collaborators").mock(
        return_value=httpx.Response(200, json=[{"login": "alice", "permission": "admin"}])
    )
    respx.delete(f"{base}/repos/acme/demo/collaborators/alice").mock(
        return_value=httpx.Response(204)
    )
    respx.put(f"{base}/repos/acme/demo/collaborators/alice").mock(return_value=httpx.Response(201))
    respx.get(f"{base}/repos/acme/demo/collaborators/alice").mock(
        return_value=httpx.Response(200, json={"permission": "admin"})
    )
    provider = GitHubProvider("acme", "token", repos=("acme/demo",), base_url=base)
    assert_provider_contract(provider)
    collaborator = next(
        item for item in provider.snapshot() if item.raw.get("kind") == "collaborator"
    )
    assert collaborator.last_used_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert provider.revoke(collaborator, True).ok
    result = provider.revoke(collaborator, False)
    assert result.ok
    assert provider.restore(result.pre_image or {}).ok
    assert members_route.call_count == EXPECTED_MEMBER_REQUESTS


@pytest.mark.m5
def test_cli_dry_run_commands_are_terminal_safe(capsys: pytest.CaptureFixture[str]) -> None:
    for command in ("snapshot", "detect", "plan", "execute", "rollback"):
        assert main([command, "--dry-run"]) == 0
    assert '"dry_run":true' in capsys.readouterr().out
