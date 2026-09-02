"""M1 fixture and graph persistence tests."""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import boto3
import pytest
from moto import mock_aws

from deadbolt.contracts import protocols
from deadbolt.contracts.export import export_schemas, schema_for_dataclass
from deadbolt.contracts.export import main as export_main
from deadbolt.contracts.models import ActionResult, CredentialType, Entitlement, Scope
from deadbolt.errors import ProviderError
from deadbolt.graph.snapshot import run_snapshot
from deadbolt.graph.store import GraphStore
from deadbolt.plan.canonical import canonical_dumps
from deadbolt.providers.fixtures.salesforce import SalesforceFixtureProvider
from deadbolt.providers.fixtures.workday import WorkdayFixtureProvider
from deadbolt.providers.registry import build_providers
from tests.support.provider_compliance import assert_provider_contract

SEED_DIR = Path(__file__).parents[1] / "fixtures" / "seed"


@pytest.mark.m1
def test_fixture_providers_pass_contract_and_are_sorted() -> None:
    salesforce = SalesforceFixtureProvider(SEED_DIR / "salesforce.json")
    workday = WorkdayFixtureProvider(SEED_DIR / "workday.json")
    assert_provider_contract(salesforce)
    assert_provider_contract(workday)
    for provider in (salesforce, workday):
        first = tuple(provider.snapshot())
        assert first == tuple(provider.snapshot())
        assert list(first) == sorted(
            first,
            key=lambda item: (item.identity_id, item.resource, item.scope.value),
        )


@pytest.mark.m1
def test_fixture_revoke_is_dry_run_safe_idempotent_and_restorable() -> None:
    provider = SalesforceFixtureProvider(SEED_DIR / "salesforce.json")
    entitlement = next(iter(provider.snapshot()))
    before = tuple(provider.snapshot())
    dry_run = provider.revoke(entitlement, dry_run=True)
    assert dry_run.ok is True
    assert dry_run.dry_run is True
    assert dry_run.pre_image is not None
    assert tuple(provider.snapshot()) == before

    applied = provider.revoke(entitlement, dry_run=False)
    repeated = provider.revoke(entitlement, dry_run=False)
    assert applied.ok is True
    assert repeated.ok is True
    assert repeated.pre_image == applied.pre_image
    assert entitlement not in tuple(provider.snapshot())
    restored = provider.restore(applied.pre_image or {})
    assert restored.ok is True
    assert tuple(provider.snapshot()) == before


@pytest.mark.m1
def test_fixture_rejects_wrong_or_unknown_entitlements_and_nonrevocable_seed() -> None:
    provider = SalesforceFixtureProvider(
        seed=[
            {
                "email": "NoOne@Example.com",
                "resource": "salesforce:object:Contact",
                "scope": "read",
                "credential_type": "federated",
                "revocable": False,
            }
        ]
    )
    entitlement = next(iter(provider.snapshot()))
    result = provider.revoke(entitlement, dry_run=True)
    assert result.ok is False
    assert result.pre_image is not None
    with pytest.raises(ProviderError):
        provider.revoke(
            Entitlement(
                "unknown",
                "salesforce",
                "missing",
                Scope.READ,
                None,
                None,
                CredentialType.FEDERATED,
                True,
                {},
            ),
            dry_run=True,
        )
    with pytest.raises(ProviderError):
        provider.revoke(replace(entitlement, system="workday"), True)


@pytest.mark.m1
def test_registry_swap_is_config_only() -> None:
    fixture = build_providers({"workday": "fixture", "salesforce": "fixture"})
    assert [provider.system for provider in fixture] == ["salesforce", "workday"]
    injected = build_providers(
        {"salesforce": "real"},
        factories={"salesforce": lambda: SalesforceFixtureProvider(SEED_DIR / "salesforce.json")},
    )
    assert injected[0].system == "salesforce"
    engine_dir = Path(__file__).parents[2] / "src" / "deadbolt" / "engine"
    engine_text = "".join(path.read_text(encoding="utf-8") for path in engine_dir.glob("*.py"))
    assert "deadbolt.providers" not in engine_text


def _table(client: object, name: str) -> None:
    ddb = client
    ddb.create_table(
        TableName=name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.mark.m1
@mock_aws
def test_graph_store_uses_prd_keys_and_supports_all_reads_and_writes() -> None:
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    _table(ddb, "deadbolt")
    store = GraphStore("deadbolt", ddb, sleeper=lambda _: None)
    entitlements = tuple(SalesforceFixtureProvider(SEED_DIR / "salesforce.json").snapshot())
    store.put_entitlements(entitlements)
    assert (
        store.get_identity_entitlements("sf-aisha")[0].resource == "salesforce:object:Opportunity"
    )
    assert store.who_can_reach("salesforce", "salesforce:object:Opportunity") == ["sf-aisha"]
    item = ddb.get_item(
        TableName="deadbolt",
        Key={
            "PK": {"S": "ID#sf-aisha"},
            "SK": {"S": "ENT#salesforce#salesforce:object:Opportunity#admin"},
        },
    )["Item"]
    assert item["GSI1PK"] == {"S": "RES#salesforce#salesforce:object:Opportunity"}
    assert item["GSI1SK"] == {"S": "ID#sf-aisha"}

    store.put_identity("sf-aisha", {"email": "aisha@example.com", "status": "active"})
    store.put_role_template(
        "engineering-manager", 4, {"title": "Engineering Manager", "scopes": ["read"]}
    )
    assert store.get_role_template("engineering-manager", 4) == {
        "title": "Engineering Manager",
        "scopes": ["read"],
    }
    store.put_finding("finding-1", {"score": 90, "tier": "T3"})
    store.put_plan("plan-1", [{"seq": 0, "resource": "x"}, {"seq": 1, "resource": "y"}])
    assert ddb.get_item(
        TableName="deadbolt",
        Key={"PK": {"S": "PLAN#plan-1"}, "SK": {"S": "ACT#0"}},
    )["Item"]["resource"] == {"S": "x"}


@pytest.mark.m1
def test_graph_store_retries_unprocessed_batch_with_capped_backoff() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def batch_write_item(self, **kwargs: object) -> Mapping[str, object]:
            self.calls += 1
            if self.calls == 1:
                return {"UnprocessedItems": kwargs["RequestItems"]}
            return {"UnprocessedItems": {}}

    delays: list[float] = []
    client = Client()
    store = GraphStore(
        "deadbolt", client, backoff_base=0.1, backoff_cap=0.15, sleeper=delays.append
    )
    store.put_entitlements(
        [
            Entitlement(
                "id",
                "salesforce",
                "resource",
                Scope.READ,
                None,
                None,
                CredentialType.FEDERATED,
                True,
                {},
            )
        ]
    )
    assert client.calls == len(delays) + 1  # initial BatchWriteItem plus one retry
    assert delays == [0.1]


@pytest.mark.m1
@mock_aws
def test_snapshot_normalizes_email_writes_s3_and_isolates_partial_failure() -> None:
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    _table(ddb, "deadbolt")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="snapshots")
    store = GraphStore("deadbolt", ddb)

    class Broken:
        system = "broken"

        def snapshot(self) -> Iterable[Entitlement]:
            raise RuntimeError("fixture unavailable")

        def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
            raise AssertionError("not used")

        def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
            raise AssertionError("not used")

    result = run_snapshot(
        [Broken(), SalesforceFixtureProvider(SEED_DIR / "salesforce.json")],
        store,
        "snapshots",
        datetime(2026, 9, 2, 14, 37, tzinfo=UTC),
        s3_client=s3,
        run_id="run-1",
    )
    assert result.statuses == {"broken": "stale", "salesforce": "fresh"}
    assert result.snapshot_keys["salesforce"] == "snapshots/2026-09-02T14:00:00Z/salesforce.json"
    body = s3.get_object(Bucket="snapshots", Key=result.snapshot_keys["salesforce"])["Body"].read()
    assert body.startswith(b"[")
    assert b'"identity_id":"aisha.rahman@example.com"' in body
    assert (
        store.get_identity_entitlements("aisha.rahman@example.com")[0].identity_id
        == "aisha.rahman@example.com"
    )


@pytest.mark.m1
def test_canonical_encoder_is_stable_and_utc_second_precision() -> None:
    assert canonical_dumps({"z": "é", "a": datetime(2026, 9, 2, 6, 0, 0, 999999, tzinfo=UTC)}) == (
        b'{"a":"2026-09-02T06:00:00Z","z":"\\u00e9"}'
    )


@pytest.mark.m1
def test_contract_schema_export_and_protocol_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert schema_for_dataclass(Entitlement)["title"] == "Entitlement"
    output_dir = tmp_path / "schemas"
    export_schemas(output_dir)
    assert {path.name for path in output_dir.iterdir()} == {
        "ActionResult.json",
        "Entitlement.json",
        "FixedClock.json",
    }
    monkeypatch.setattr("sys.argv", ["contract-export", "--out", str(tmp_path / "cli")])
    export_main()
    assert (tmp_path / "cli" / "Entitlement.json").exists()
    assert protocols.EntitlementProvider is not None
    assert protocols.Clock is not None
    assert ActionResult(True, "test", "resource", "read", True, None, "ok", 0).pre_image is None


@pytest.mark.m1
def test_fixture_seed_validation_and_restore_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be an object"):
        SalesforceFixtureProvider(seed=[cast(Mapping[str, object], "bad")])
    with pytest.raises(ValueError, match="identity_id or email"):
        SalesforceFixtureProvider(seed=[{"resource": "r"}])
    with pytest.raises(ValueError, match="requires resource"):
        SalesforceFixtureProvider(seed=[{"email": "person@example.com"}])
    with pytest.raises(ValueError, match="datetime seed"):
        SalesforceFixtureProvider(
            seed=[{"email": "person@example.com", "resource": "r", "granted_at": 1}]
        )
    with pytest.raises(ValueError, match="duplicate"):
        SalesforceFixtureProvider(
            seed=[
                {"email": "person@example.com", "resource": "r"},
                {"email": "person@example.com", "resource": "r"},
            ]
        )
    object_seed = tmp_path / "object.json"
    object_seed.write_text('{"entitlements": []}', encoding="utf-8")
    assert tuple(SalesforceFixtureProvider(object_seed).snapshot()) == ()
    bad_seed = tmp_path / "bad.json"
    bad_seed.write_text('{"entitlements": "bad"}', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON array"):
        SalesforceFixtureProvider(bad_seed)
    provider = SalesforceFixtureProvider(seed=[{"email": "person@example.com", "resource": "r"}])
    pre_image = next(iter(provider.snapshot()))
    with pytest.raises(ProviderError, match="belongs"):
        provider.restore(
            {
                "identity_id": pre_image.identity_id,
                "system": "workday",
                "resource": pre_image.resource,
                "scope": pre_image.scope.value,
                "credential_type": pre_image.credential_type.value,
                "revocable": True,
                "raw": {},
            }
        )
