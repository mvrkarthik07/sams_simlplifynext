"""M4 executor ordering, exactly-once delivery, and rollback tests."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from deadbolt.audit.writer import AuditWriter
from deadbolt.contracts.models import ActionResult, Entitlement
from deadbolt.errors import IdempotencyConflict, IrreversibleActionError
from deadbolt.executor import execute as execute_action
from deadbolt.executor import idempotency as idempotency_module
from deadbolt.executor import rollback as rollback_action
from deadbolt.executor.idempotency import IdempotencyLease, IdempotencyStore, lock_key
from deadbolt.executor.rollback import RollbackExecutor
from deadbolt.executor.run import Executor, read_preimage, verify_restored
from deadbolt.plan.builder import Action, Plan
from deadbolt.plan.preimage import preimage_key
from deadbolt.providers.fixtures.salesforce import SalesforceFixtureProvider

pytestmark = pytest.mark.m4
SEED = Path(__file__).parents[1] / "fixtures" / "seed" / "salesforce.json"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
CONCURRENT_DELIVERIES = 50


def _table(ddb: object, name: str) -> None:
    ddb.create_table(
        TableName=name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _plan(entitlement: Entitlement, *, plan_id: str = "plan-m4") -> tuple[Plan, Action]:
    base_action = Action(
        0,
        entitlement.system,
        entitlement.resource,
        entitlement.scope.value,
        "revoke",
        entitlement.scope.value,
        "none",
        "finding-m4",
        9_000,
        "T3",
    )
    body = {
        "schema_version": "1",
        "evaluated_at": "2026-09-03T12:00:00Z",
        "template_version": "m4",
        "weights_version": "v1",
        "actions": [base_action.as_dict()],
    }
    plan = Plan(
        body,
        {
            "plan_id": plan_id,
            "created_at": "2026-09-03T12:00:00Z",
            "trace_id": "trace-m4",
            "attempt": 0,
        },
        (base_action,),
    )
    action = Action(
        base_action.seq,
        base_action.system,
        base_action.resource,
        base_action.scope,
        base_action.verb,
        base_action.from_scope,
        base_action.to_scope,
        base_action.finding_id,
        base_action.score,
        base_action.tier,
        preimage_key(plan.plan_hash, base_action.seq, base_action.resource, base_action.scope),
    )
    return Plan(body, plan.envelope, (action,)), action


def _resources() -> tuple[object, IdempotencyStore, object]:
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    _table(ddb, "locks")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="preimages")
    s3.put_bucket_versioning(Bucket="preimages", VersioningConfiguration={"Status": "Enabled"})
    return ddb, IdempotencyStore("locks", ddb, wait_seconds=3), s3


@pytest.mark.m4
@mock_aws
def test_lock_key_and_fifty_concurrent_deliveries_mutate_once() -> None:
    ddb, locks, s3 = _resources()
    provider = SalesforceFixtureProvider(SEED)
    before = tuple(provider.snapshot())
    plan, action = _plan(before[0], plan_id="concurrent-plan")
    executor = Executor(provider, locks, s3, "preimages")

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = tuple(
            pool.map(lambda _: executor.execute(plan, action), range(CONCURRENT_DELIVERIES))
        )

    assert lock_key(plan.plan_id, action.seq, action.resource, action.scope)
    assert sum(result.ok for result in results) == CONCURRENT_DELIVERIES
    assert provider.mutation_count == 1
    assert tuple(provider.snapshot()) == before[1:]
    listed = s3.list_objects_v2(Bucket="preimages", Prefix="audit/")
    assert listed.get("KeyCount", 0) == 1

    item = ddb.get_item(
        TableName="locks",
        Key={
            "PK": {"S": lock_key(plan.plan_id, 0, action.resource, action.scope)},
            "SK": {"S": "LOCK"},
        },
    )["Item"]
    assert item["status"] == {"S": "complete"}
    assert int(cast(str, item["expires_at"]["N"])) > int(NOW.timestamp())


@pytest.mark.rollback
@mock_aws
def test_execute_writes_versioned_preimage_and_rollback_restores_exact_state() -> None:
    _, locks, s3 = _resources()
    provider = SalesforceFixtureProvider(SEED)
    before = tuple(provider.snapshot())
    plan, action = _plan(before[0])
    result = execute_action(
        provider,
        plan,
        action,
        idempotency=locks,
        s3_client=s3,
        preimage_bucket="preimages",
        audit_bucket="preimages",
    )
    assert result.ok is True
    assert result.action_result is not None and result.action_result.pre_image is not None
    assert tuple(provider.snapshot()) == before[1:]
    stored = read_preimage(s3, "preimages", action.pre_image_key or "")
    assert stored["identity_id"] == before[0].identity_id
    versions = s3.list_object_versions(Bucket="preimages", Prefix=action.pre_image_key)
    assert versions.get("Versions")

    rolled = rollback_action(
        provider,
        plan.plan_id,
        action.seq,
        idempotency=locks,
        s3_client=s3,
        preimage_bucket="preimages",
        audit_bucket="preimages",
    )
    assert rolled.ok is True
    assert tuple(provider.snapshot()) == before
    assert rolled.result is not None and rolled.result.ok is True


@pytest.mark.rollback
@mock_aws
def test_every_fixture_entitlement_round_trips_through_execute_and_rollback() -> None:
    _, locks, s3 = _resources()
    provider = SalesforceFixtureProvider(SEED)
    original = tuple(provider.snapshot())
    for index, entitlement in enumerate(original):
        plan, action = _plan(entitlement, plan_id=f"property-{index}")
        result = Executor(provider, locks, s3, "preimages").execute(plan, action)
        assert result.ok is True
        rolled = RollbackExecutor(provider, locks, s3, "preimages").rollback(plan.plan_id)
        assert rolled.ok is True
        assert tuple(provider.snapshot()) == original


class _DryRunFailure(SalesforceFixtureProvider):
    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        if dry_run:
            return ActionResult(
                False,
                self.system,
                e.resource,
                e.scope.value,
                True,
                {"raw": "safe"},
                "dry run rejected",
                0,
            )
        raise AssertionError("apply must not run after dry-run failure")


class _NoOpApply(SalesforceFixtureProvider):
    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        if dry_run:
            return super().revoke(e, True)
        pre = next(item for item in self.snapshot() if item.resource == e.resource)
        return ActionResult(
            True,
            self.system,
            e.resource,
            e.scope.value,
            False,
            {"identity_id": pre.identity_id},
            "pretended",
            0,
        )


class _ApplyFailure(SalesforceFixtureProvider):
    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        if dry_run:
            return super().revoke(e, True)
        return ActionResult(
            False, self.system, e.resource, e.scope.value, False, None, "apply rejected", 0
        )


class _RaiseAfterApply(SalesforceFixtureProvider):
    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        result = super().revoke(e, dry_run)
        if not dry_run:
            raise RuntimeError("connection dropped after provider mutation")
        return result


class _FailingAudit(AuditWriter):
    def __init__(self) -> None:
        pass

    def write(self, record: Mapping[str, object]) -> str:
        raise RuntimeError("audit unavailable")


class _RestoreFailure(SalesforceFixtureProvider):
    def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
        return ActionResult(
            False,
            self.system,
            str(pre_image.get("resource", "unknown")),
            str(pre_image.get("scope", "read")),
            False,
            pre_image,
            "restore rejected",
            0,
        )


@pytest.mark.m4
@mock_aws
def test_dry_run_failure_and_missing_preimage_are_fail_closed() -> None:
    _, locks, s3 = _resources()
    provider = _DryRunFailure(SEED)
    before = tuple(provider.snapshot())
    plan, action = _plan(before[0], plan_id="dry-fail")
    result = Executor(provider, locks, s3, "preimages").execute(plan, action)
    assert result.ok is False
    assert provider.mutation_count == 0
    assert tuple(provider.snapshot()) == before

    empty = SalesforceFixtureProvider(seed=[])
    plan2, action2 = _plan(before[0], plan_id="missing-preimage")
    with pytest.raises(IrreversibleActionError):
        Executor(empty, locks, s3, "preimages").execute(plan2, action2)
    assert empty.mutation_count == 0


@pytest.mark.m4
@mock_aws
def test_missing_action_key_and_ambiguous_snapshot_fail_before_provider_write() -> None:
    _, locks, s3 = _resources()
    entitlement = next(iter(SalesforceFixtureProvider(SEED).snapshot()))
    plan, action = _plan(entitlement, plan_id="missing-key")
    missing_key_action = replace(action, pre_image_key=None)
    with pytest.raises(IrreversibleActionError):
        Executor(SalesforceFixtureProvider(SEED), locks, s3, "preimages").execute(
            plan, missing_key_action
        )

    duplicate_seed = [
        {"identity_id": "one", "resource": entitlement.resource, "scope": entitlement.scope.value},
        {"identity_id": "two", "resource": entitlement.resource, "scope": entitlement.scope.value},
    ]
    ambiguous_provider = SalesforceFixtureProvider(seed=duplicate_seed)
    ambiguous_plan, ambiguous_action = _plan(entitlement, plan_id="ambiguous")
    with pytest.raises(IrreversibleActionError):
        Executor(ambiguous_provider, locks, s3, "preimages").execute(
            ambiguous_plan, ambiguous_action
        )
    assert ambiguous_provider.mutation_count == 0


@pytest.mark.m4
def test_idempotency_serialization_and_failure_paths() -> None:
    with pytest.raises(IdempotencyConflict):
        idempotency_module._result_from_json("[]")
    with pytest.raises(IdempotencyConflict):
        idempotency_module._result_from_json('{"pre_image": []}')
    with pytest.raises(IdempotencyConflict):
        idempotency_module._text(1, "field")
    with pytest.raises(IdempotencyConflict):
        idempotency_module._int(True, "field")

    class NonConditional:
        def put_item(self, **kwargs: object) -> Mapping[str, object]:
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException"}},
                "PutItem",
            )

    with pytest.raises(ClientError):
        IdempotencyStore("locks", NonConditional()).acquire("p", 0, "r", "read")

    class Pending:
        def put_item(self, **kwargs: object) -> Mapping[str, object]:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}},
                "PutItem",
            )

        def get_item(self, **kwargs: object) -> Mapping[str, object]:
            return {"Item": {"status": {"S": "pending"}}}

    with pytest.raises(IdempotencyConflict):
        IdempotencyStore("locks", Pending(), wait_seconds=0).acquire("p", 0, "r", "read")

    class Empty:
        def get_item(self, **kwargs: object) -> Mapping[str, object]:
            return {}

    empty_store = IdempotencyStore("locks", Empty())
    assert empty_store._get_lock("none") is None
    empty_lease = IdempotencyLease("none", False)
    empty_store.complete(
        empty_lease,
        ActionResult(True, "system", "resource", "read", False, None, "ok", 0),
    )
    empty_store.release(empty_lease)
    assert verify_restored(SalesforceFixtureProvider(SEED), {}) is False


@pytest.mark.rollback
@mock_aws
def test_rollback_failure_and_missing_preimage_are_reported_without_silent_success() -> None:
    _, locks, s3 = _resources()
    entitlement = next(iter(SalesforceFixtureProvider(SEED).snapshot()))
    provider = _RestoreFailure(SEED)
    plan, action = _plan(entitlement, plan_id="restore-failure")
    forward = Executor(provider, locks, s3, "preimages").execute(plan, action)
    assert forward.ok is True
    failed = RollbackExecutor(provider, locks, s3, "preimages").rollback(plan.plan_id)
    assert failed.ok is False
    assert failed.results and failed.results[0].ok is False

    missing_plan, missing_action = _plan(entitlement, plan_id="deleted-preimage")
    normal = SalesforceFixtureProvider(SEED)
    Executor(normal, locks, s3, "preimages").execute(missing_plan, missing_action)
    s3.delete_object(Bucket="preimages", Key=missing_action.pre_image_key or "")
    missing = RollbackExecutor(normal, locks, s3, "preimages").rollback(missing_plan.plan_id)
    assert missing.ok is False

    key_plan, key_action = _plan(entitlement, plan_id="empty-key")
    Executor(SalesforceFixtureProvider(SEED), locks, s3, "preimages").execute(key_plan, key_action)
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    ddb.update_item(
        TableName="locks",
        Key={"PK": {"S": f"PLAN#{key_plan.plan_id}"}, "SK": {"S": "ACT#0"}},
        UpdateExpression="SET pre_image_key = :empty",
        ExpressionAttributeValues={":empty": {"S": ""}},
    )
    empty_key = RollbackExecutor(normal, locks, s3, "preimages").rollback(key_plan.plan_id)
    assert empty_key.ok is False


@pytest.mark.m4
@mock_aws
def test_apply_failure_and_failed_verification_have_completed_safe_outcomes() -> None:
    _, locks, s3 = _resources()
    base = SalesforceFixtureProvider(SEED)
    entitlement = next(iter(base.snapshot()))
    failed, action = _plan(entitlement, plan_id="apply-fail")
    apply_result = Executor(_ApplyFailure(SEED), locks, s3, "preimages").execute(failed, action)
    assert apply_result.ok is False
    assert apply_result.action_result is not None and apply_result.action_result.ok is False

    verify_provider = _NoOpApply(SEED)
    verify_plan, verify_action = _plan(entitlement, plan_id="verify-fail")
    verify_result = Executor(verify_provider, locks, s3, "preimages").execute(
        verify_plan, verify_action
    )
    assert verify_result.ok is False
    assert verify_result.rollback_result is not None and verify_result.rollback_result.ok is True
    assert tuple(verify_provider.snapshot()) == tuple(base.snapshot())


@pytest.mark.m4
@mock_aws
def test_provider_error_after_apply_and_audit_error_leave_safe_terminal_records() -> None:
    _, locks, s3 = _resources()
    entitlement = next(iter(SalesforceFixtureProvider(SEED).snapshot()))
    plan, action = _plan(entitlement, plan_id="provider-error")
    provider = _RaiseAfterApply(SEED)
    result = Executor(provider, locks, s3, "preimages").execute(plan, action)
    assert result.ok is True
    assert provider.mutation_count == 1
    assert not tuple(item for item in provider.snapshot() if item.resource == action.resource)

    audit_provider = SalesforceFixtureProvider(SEED)
    audit_plan, audit_action = _plan(entitlement, plan_id="audit-error")
    audit_result = Executor(
        audit_provider,
        locks,
        s3,
        "preimages",
        audit_writer=_FailingAudit(),
    ).execute(audit_plan, audit_action)
    assert audit_result.ok is False
    assert audit_provider.mutation_count == 1


@pytest.mark.rollback
@mock_aws
def test_rollback_all_invalid_sequence_and_no_actions() -> None:
    _, locks, s3 = _resources()
    provider = SalesforceFixtureProvider(SEED)
    before = tuple(provider.snapshot())
    plan, action = _plan(before[0], plan_id="rollback-all")
    Executor(provider, locks, s3, "preimages").execute(plan, action)
    all_result = RollbackExecutor(provider, locks, s3, "preimages").rollback(plan.plan_id)
    assert all_result.ok is True
    assert len(all_result.results) == 1
    with pytest.raises(ValueError):
        RollbackExecutor(provider, locks, s3, "preimages").rollback(plan.plan_id, -1)
    no_result = RollbackExecutor(provider, locks, s3, "preimages").rollback("unknown")
    assert no_result.ok is False
    with pytest.raises(IrreversibleActionError):
        RollbackExecutor(provider, locks, s3, "preimages").rollback("unknown", 0)


@pytest.mark.m4
def test_idempotency_store_conflict_and_audit_writer_shape() -> None:
    class Client:
        def put_item(self, **kwargs: object) -> Mapping[str, object]:
            raise IdempotencyConflict("already owned")

        def get_item(self, **kwargs: object) -> Mapping[str, object]:
            return {}

    store = IdempotencyStore("locks", Client(), wait_seconds=0)
    with pytest.raises(IdempotencyConflict):
        store.acquire("p", 0, "r", "read")

    class S3:
        def __init__(self) -> None:
            self.record: Mapping[str, object] | None = None

        def put_object(self, **kwargs: object) -> Mapping[str, object]:
            self.record = cast(Mapping[str, object], kwargs)
            return {}

    s3 = S3()
    key = AuditWriter("audit", s3).write(
        {"plan_hash": "hash", "seq": 0, "raw": "excluded by caller"}
    )
    assert key.startswith("audit/hash/0/")
    assert s3.record is not None and s3.record["ContentType"] == "application/json"


@pytest.mark.m4
def test_preimage_and_idempotency_defensive_paths() -> None:
    class Body:
        def __init__(self, value: bytes) -> None:
            self.value = value

        def read(self) -> bytes:
            return self.value

    class S3:
        def __init__(self, value: bytes) -> None:
            self.value = value

        def get_object(self, **kwargs: object) -> Mapping[str, object]:
            return {"Body": Body(self.value)}

    with pytest.raises(IrreversibleActionError):
        read_preimage(S3(b"[]"), "bucket", "bad")

    class NoBody:
        def get_object(self, **kwargs: object) -> Mapping[str, object]:
            return {"Body": None}

    with pytest.raises(IrreversibleActionError):
        read_preimage(NoBody(), "bucket", "bad")

    class Client:
        def put_item(self, **kwargs: object) -> Mapping[str, object]:
            return {}

        def get_item(self, **kwargs: object) -> Mapping[str, object]:
            return {"Item": {"PK": {"S": "key"}, "seq": {"N": "1"}}}

        def delete_item(self, **kwargs: object) -> Mapping[str, object]:
            return {}

        def update_item(self, **kwargs: object) -> Mapping[str, object]:
            return {}

        def query(self, **kwargs: object) -> Mapping[str, object]:
            return {"Items": []}

    store = IdempotencyStore("locks", Client(), wait_seconds=0)
    lease = store.acquire("plan", 0, "resource", "read")
    assert lease.acquired is True
    store.complete(lease, ActionResult(True, "system", "resource", "read", False, None, "ok", 0))
    duplicate = store.acquire("plan", 0, "resource", "read")
    assert duplicate.acquired is True
    store.release(duplicate)
