"""M5b connector expansion tests using real response shapes and replay transport."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pytest
import respx

from deadbolt.contracts.models import CredentialType, Entitlement, Scope, Tier
from deadbolt.engine.drift import Finding
from deadbolt.errors import ProviderError
from deadbolt.graph.identity import resolve_identity
from deadbolt.plan.builder import build
from deadbolt.providers.github_enterprise import GitHubEnterpriseProvider
from deadbolt.providers.salesforce import SalesforceProvider
from deadbolt.providers.workday import WorkdayProvider
from tests.support.provider_compliance import assert_provider_contract

pytestmark = pytest.mark.m5b


@respx.mock
def test_github_enterprise_models_irreversible_pat_and_server_deploy_key() -> None:
    base = "https://ghe.example/api/v3"
    respx.get(f"{base}/orgs/acme/personal-access-tokens").mock(
        return_value=httpx.Response(
            200,
            json={
                "tokens": [
                    {
                        "id": 7,
                        "owner": {"login": "priya", "email": "priya@example.com"},
                        "permissions": ["repo"],
                        "last_used_at": "2026-01-02T03:04:05Z",
                    }
                ]
            },
        )
    )
    respx.get(f"{base}/orgs/acme/credential-authorizations").mock(
        return_value=httpx.Response(
            200,
            json={
                "credential_authorizations": [
                    {"id": 8, "login": "priya", "credential_type": "ssh_key", "last_used_at": None}
                ]
            },
        )
    )
    respx.get(f"{base}/repos/acme/platform/keys").mock(
        return_value=httpx.Response(
            200,
            json={"keys": [{"id": 9, "key": "ssh-rsa AAA", "read_only": False, "title": "deploy"}]},
        )
    )
    respx.post(f"{base}/repos/acme/platform/keys").mock(
        return_value=httpx.Response(201, json={"id": 9})
    )
    respx.delete(f"{base}/orgs/acme/personal-access-tokens/7").mock(
        return_value=httpx.Response(204)
    )
    respx.delete(f"{base}/orgs/acme/credential-authorizations/8").mock(
        return_value=httpx.Response(204)
    )
    respx.delete(f"{base}/repos/acme/platform/keys/9").mock(return_value=httpx.Response(204))
    provider = GitHubEnterpriseProvider(
        "acme", "token", api_base=base, repos=("acme/platform",), probe=False
    )
    assert_provider_contract(provider)
    entitlements = tuple(provider.snapshot())
    pat = next(item for item in entitlements if item.raw.get("kind") == "pat")
    key = next(item for item in entitlements if item.raw.get("kind") == "deploy-key")
    assert pat.raw["reversible"] is False
    assert key.scope is Scope.ADMIN
    assert provider.revoke(pat, True).ok
    assert provider.revoke(pat, False).ok
    assert provider.revoke(key, False).ok
    assert provider.restore(
        {**dict(key.raw), "kind": "deploy-key", "resource": key.resource, "scope": key.scope.value}
    ).ok
    with pytest.raises(ProviderError, match="irreversible"):
        provider.restore(dict(pat.raw))


@respx.mock
def test_salesforce_jwt_soql_permission_assignment_and_restore() -> None:
    base = "https://acme.my.salesforce.com"
    query = respx.get(f"{base}/services/data/v61.0/query")

    def query_response(request: httpx.Request) -> httpx.Response:
        query_text = str(request.url.params.get("q", ""))
        if "PermissionSetAssignment" in query_text:
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "Id": "PSA-1",
                            "AssigneeId": "005",
                            "Assignee": {"Email": "Aisha@example.com"},
                            "PermissionSetId": "PS-1",
                            "PermissionSet": {
                                "Name": "Opportunity Admin",
                                "PermissionsModifyAllData": True,
                            },
                        }
                    ]
                },
            )
        if "ObjectPermissions" in query_text:
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "ParentId": "PS-1",
                            "SobjectType": "Opportunity",
                            "PermissionsModifyAllRecords": True,
                        }
                    ]
                },
            )
        if "FieldPermissions" in query_text:
            return httpx.Response(200, json={"records": []})
        return httpx.Response(
            200,
            json={"records": [{"UserId": "005", "LoginTime": "2026-01-01T00:00:00Z"}]},
        )

    query.side_effect = query_response
    respx.delete(f"{base}/services/data/v61.0/sobjects/PermissionSetAssignment/PSA-1").mock(
        return_value=httpx.Response(204)
    )
    respx.post(f"{base}/services/data/v61.0/sobjects/PermissionSetAssignment").mock(
        return_value=httpx.Response(201, json={"id": "PSA-2"})
    )
    provider = SalesforceProvider(
        instance_url=base,
        access_token=os.environ.get("DEADBOLT_TEST_TOKEN", "fixture-token"),
    )
    assert_provider_contract(provider)
    entitlements = tuple(provider.snapshot())
    assert any(
        item.resource == "salesforce:object:Opportunity" and item.scope is Scope.ADMIN
        for item in entitlements
    )
    assignment = next(
        item for item in entitlements if item.raw.get("kind") == "permission-set-assignment"
    )
    applied = provider.revoke(assignment, False)
    assert applied.ok and provider.restore(applied.pre_image or {}).ok


def test_workday_real_shape_event_replay_and_read_only_contract() -> None:
    workers = {
        "data": [
            {
                "id": "W-1",
                "primaryWorkEmail": "Priya@example.com",
                "businessTitle": "Engineering Manager",
                "workerStatus": "Active",
            }
        ]
    }
    groups = {
        "Security_Group_Assignment": [
            {"Worker_Reference_ID": "W-1", "Security_Group_Name": "Payroll Admin"}
        ]
    }
    provider = WorkdayProvider(
        tenant="acme",
        bearer_token=os.environ.get("DEADBOLT_TEST_TOKEN", "fixture-token"),
        workers_payload=workers,
        security_groups_payload=groups,
    )
    assert_provider_contract(provider)
    entitlement = next(iter(provider.snapshot()))
    assert entitlement.identity_id == "priya@example.com"
    assert entitlement.revocable is False
    event = {
        "workerId": "W-1",
        "effectiveDate": "2026-03-01",
        "priorTitle": "Backend Engineer",
        "newTitle": "Engineering Manager",
        "eventType": "job_change",
    }
    assert provider.worker_event(event) == provider.worker_event(
        {
            "worker_id": "W-1",
            "effective_date": "2026-03-01",
            "prior_title": "Backend Engineer",
            "new_title": "Engineering Manager",
            "event_type": "job_change",
        }
    )
    assert provider.revoke(entitlement, True).ok is False


def test_irreversible_entitlement_is_never_scheduled_at_t1() -> None:
    entitlement = next(
        iter(
            WorkdayProvider(
                workers_payload={"data": []},
                security_groups_payload={"Security_Group_Assignment": []},
            ).snapshot()
        ),
        None,
    )
    assert entitlement is None
    item = Entitlement(
        "person@example.com",
        "github",
        "github:pat:7",
        Scope.ADMIN,
        None,
        None,
        CredentialType.PAT,
        True,
        {"reversible": False},
    )
    finding = Finding(item, 9000, 9000, 9000, 9000, 3000, Tier.T1, {"days_unused": "never"})
    assert build((finding,), datetime(2026, 1, 1, tzinfo=UTC), "v1").actions == ()


def test_identity_resolution_uses_explicit_precedence_and_quarantines_ambiguity() -> None:
    workers = {
        "w-scim": {
            "scim_external_id": "scim-1",
            "saml_name_id": "name-1",
            "primary_work_email": "one@example.com",
        },
        "w-two": {"primary_work_email": "two@example.com"},
    }
    assert (
        resolve_identity(
            {"account_id": "a", "scim_external_id": "scim-1", "verified_email": "two@example.com"},
            workers,
        ).strength
        == "scim"
    )
    assert (
        resolve_identity({"account_id": "b", "saml_name_id": "name-1"}, workers).strength == "saml"
    )
    assert (
        resolve_identity(
            {"account_id": "c", "verified_email": "TWO@example.com"}, workers
        ).worker_id
        == "w-two"
    )
    assert (
        resolve_identity(
            {"account_id": "d", "alias": "old-name"}, workers, alias_map={"old-name": "w-two"}
        ).strength
        == "alias"
    )
    missing = resolve_identity({"account_id": "e", "display_name": "Two"}, workers)
    assert missing.quarantined and missing.worker_id is None
    ambiguous = resolve_identity(
        {"account_id": "f", "verified_email": "one@example.com", "saml_name_id": "name-1"},
        {**workers, "w-three": {"saml_name_id": "name-1"}},
    )
    assert ambiguous.quarantined and ambiguous.strength == "ambiguous"


@respx.mock
def test_github_enterprise_capability_probe_degrades_endpoint_by_endpoint() -> None:
    base = "https://ghe.example/api/v3"
    for path in (
        "/orgs/acme/personal-access-tokens",
        "/orgs/acme/credential-authorizations",
        "/orgs/acme/installations",
        "/orgs/acme/organization-roles",
        "/orgs/acme/audit-log",
    ):
        respx.get(f"{base}{path}").mock(return_value=httpx.Response(404))
    respx.get(f"{base}/scim/v2/enterprises/acme/Users").mock(
        return_value=httpx.Response(200, json={"Resources": []})
    )
    provider = GitHubEnterpriseProvider(
        "acme", "enterprise-token", api_base=base, enterprise_slug="acme"
    )
    assert provider.capabilities["scim"] is True
    assert provider.capabilities["audit_log"] is False
