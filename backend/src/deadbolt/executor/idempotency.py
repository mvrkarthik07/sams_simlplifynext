"""DynamoDB-backed exactly-once delivery locks for executor actions."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from botocore.exceptions import ClientError  # type: ignore[import-untyped]  # no stubs.

from deadbolt.contracts.models import ActionResult
from deadbolt.errors import IdempotencyConflict
from deadbolt.plan.canonical import canonical_dumps


class _DynamoClient(Protocol):
    def delete_item(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_item(self, **kwargs: object) -> Mapping[str, object]: ...

    def put_item(self, **kwargs: object) -> Mapping[str, object]: ...

    def query(self, **kwargs: object) -> Mapping[str, object]: ...

    def update_item(self, **kwargs: object) -> Mapping[str, object]: ...


def lock_key(plan_id: str, seq: int, resource: str, scope: str) -> str:
    """Return the stable SHA-256 lock key required by the executor contract."""
    material = f"{plan_id}|{seq}|{resource}|{scope}".encode()
    return hashlib.sha256(material).hexdigest()


idempotency_key = lock_key
make_lock_key = lock_key
compute_lock_key = lock_key


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise IdempotencyConflict(f"idempotency record field {field} is not text")
    return value


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IdempotencyConflict(f"idempotency record field {field} is not an integer")
    return value


def _result_json(result: ActionResult) -> str:
    values: dict[str, object] = {
        "ok": result.ok,
        "system": result.system,
        "resource": result.resource,
        "scope": result.scope,
        "dry_run": result.dry_run,
        "pre_image": dict(result.pre_image) if result.pre_image is not None else None,
        "message": result.message,
        "provider_latency_ms": result.provider_latency_ms,
    }
    return canonical_dumps(values).decode("utf-8")


def _result_from_json(value: str) -> ActionResult:
    decoded = json.loads(value)
    if not isinstance(decoded, Mapping):
        raise IdempotencyConflict("stored idempotency result is not an object")
    pre_image = decoded.get("pre_image")
    if pre_image is not None and not isinstance(pre_image, Mapping):
        raise IdempotencyConflict("stored idempotency pre-image is not an object")
    normalized_pre_image: Mapping[str, object] | None = None
    if isinstance(pre_image, Mapping):
        values = dict(pre_image)
        for name in ("granted_at", "last_used_at"):
            timestamp = values.get(name)
            if isinstance(timestamp, str):
                values[name] = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        normalized_pre_image = values
    return ActionResult(
        ok=bool(decoded.get("ok")),
        system=_text(decoded.get("system"), "system"),
        resource=_text(decoded.get("resource"), "resource"),
        scope=_text(decoded.get("scope"), "scope"),
        dry_run=bool(decoded.get("dry_run")),
        pre_image=normalized_pre_image,
        message=_text(decoded.get("message"), "message"),
        provider_latency_ms=_int(decoded.get("provider_latency_ms"), "provider_latency_ms"),
    )


@dataclass(frozen=True, slots=True)
class IdempotencyLease:
    """A delivery's ownership of an action lock, or the prior result."""

    key: str
    acquired: bool
    result: ActionResult | None = None


@dataclass(frozen=True, slots=True)
class StoredAction:
    """Executor metadata needed to locate a pre-image during rollback."""

    plan_id: str
    seq: int
    plan_hash: str
    trace_id: str
    system: str
    resource: str
    scope: str
    pre_image_key: str
    lock_key: str
    result: ActionResult | None


class IdempotencyStore:
    """Conditionally claim action delivery and durably retain its result."""

    def __init__(  # noqa: PLR0913 — retry and clock dependencies stay injectable.
        self,
        table_name: str,
        dynamodb_client: object,
        *,
        ttl_seconds: int = 86_400,
        wait_seconds: float = 5.0,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.table_name = table_name
        self._client = cast(_DynamoClient, dynamodb_client)
        self._ttl_seconds = max(1, ttl_seconds)
        self._wait_seconds = max(0.0, wait_seconds)
        self._sleeper = sleeper
        self._clock = clock

    @staticmethod
    def _key(key: str) -> dict[str, dict[str, str]]:
        return {"PK": {"S": key}, "SK": {"S": "LOCK"}}

    def _get_lock(self, key: str) -> dict[str, object] | None:
        response = self._client.get_item(
            TableName=self.table_name,
            Key=self._key(key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not isinstance(item, Mapping):
            return None
        result: dict[str, object] = {}
        for name, value in item.items():
            if not isinstance(value, Mapping) or "S" not in value:
                continue
            result[str(name)] = value["S"]
        return result

    def acquire(  # noqa: PLR0913 — lock metadata is explicit for audit and rollback.
        self,
        plan_id: str,
        seq: int,
        resource: str,
        scope: str,
        *,
        system: str = "",
        plan_hash: str = "",
        trace_id: str = "",
        pre_image_key: str = "",
    ) -> IdempotencyLease:
        """Claim a lock, waiting for an in-flight owner to publish its result."""
        key = lock_key(plan_id, seq, resource, scope)
        expires_at = int(self._clock()) + self._ttl_seconds
        item = {
            "PK": {"S": key},
            "SK": {"S": "LOCK"},
            "status": {"S": "pending"},
            "plan_id": {"S": plan_id},
            "seq": {"N": str(seq)},
            "plan_hash": {"S": plan_hash},
            "trace_id": {"S": trace_id},
            "system": {"S": system},
            "resource": {"S": resource},
            "scope": {"S": scope},
            "pre_image_key": {"S": pre_image_key},
            "expires_at": {"N": str(expires_at)},
            "ttl": {"N": str(expires_at)},
        }
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(PK)",
            )
            self._client.put_item(
                TableName=self.table_name,
                Item={
                    "PK": {"S": f"PLAN#{plan_id}"},
                    "SK": {"S": f"ACT#{seq}"},
                    "lock_key": {"S": key},
                    "plan_id": {"S": plan_id},
                    "seq": {"N": str(seq)},
                    "plan_hash": {"S": plan_hash},
                    "trace_id": {"S": trace_id},
                    "system": {"S": system},
                    "resource": {"S": resource},
                    "scope": {"S": scope},
                    "pre_image_key": {"S": pre_image_key},
                    "expires_at": {"N": str(expires_at)},
                    "ttl": {"N": str(expires_at)},
                },
            )
            return IdempotencyLease(key, True)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

        deadline = self._clock() + self._wait_seconds
        while True:
            existing = self._get_lock(key)
            if existing is not None:
                status = existing.get("status")
                stored_result = existing.get("result_json")
                if status == "complete" and isinstance(stored_result, str):
                    return IdempotencyLease(key, False, _result_from_json(stored_result))
            if self._clock() >= deadline:
                raise IdempotencyConflict(f"idempotency lock is still pending: {key}")
            self._sleeper(min(0.01, max(0.0, deadline - self._clock())))

    def complete(self, lease: IdempotencyLease, result: ActionResult) -> None:
        """Publish the original provider result for duplicate deliveries."""
        if not lease.acquired:
            return
        self._client.update_item(
            TableName=self.table_name,
            Key=self._key(lease.key),
            UpdateExpression="SET #status = :status, result_json = :result",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": {"S": "complete"},
                ":result": {"S": _result_json(result)},
            },
        )

    def release(self, lease: IdempotencyLease) -> None:
        """Release a lock when execution stopped before a provider mutation."""
        if lease.acquired:
            self._client.delete_item(
                TableName=self.table_name,
                Key=self._key(lease.key),
                ConditionExpression="attribute_exists(PK)",
            )

    def _stored_action(self, item: Mapping[str, object]) -> StoredAction:
        result_json = item.get("result_json")
        result = _result_from_json(result_json) if isinstance(result_json, str) else None
        return StoredAction(
            plan_id=_text(item.get("plan_id"), "plan_id"),
            seq=_int(item.get("seq"), "seq"),
            plan_hash=_text(item.get("plan_hash"), "plan_hash"),
            trace_id=_text(item.get("trace_id"), "trace_id"),
            system=_text(item.get("system"), "system"),
            resource=_text(item.get("resource"), "resource"),
            scope=_text(item.get("scope"), "scope"),
            pre_image_key=_text(item.get("pre_image_key"), "pre_image_key"),
            lock_key=_text(item.get("lock_key"), "lock_key"),
            result=result,
        )

    def get_action(self, plan_id: str, seq: int) -> StoredAction | None:
        response = self._client.get_item(
            TableName=self.table_name,
            Key={"PK": {"S": f"PLAN#{plan_id}"}, "SK": {"S": f"ACT#{seq}"}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not isinstance(item, Mapping):
            return None
        decoded: dict[str, object] = {}
        for name, value in item.items():
            if not isinstance(value, Mapping):
                continue
            if "S" in value:
                decoded[str(name)] = value["S"]
            elif "N" in value:
                decoded[str(name)] = int(cast(str, value["N"]))
        return self._stored_action(decoded)

    def get_actions(self, plan_id: str) -> tuple[StoredAction, ...]:
        response = self._client.query(
            TableName=self.table_name,
            KeyConditionExpression="#pk = :pk AND begins_with(#sk, :sk)",
            ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
            ExpressionAttributeValues={
                ":pk": {"S": f"PLAN#{plan_id}"},
                ":sk": {"S": "ACT#"},
            },
            ConsistentRead=True,
        )
        raw_items = response.get("Items", [])
        if not isinstance(raw_items, list):
            raise IdempotencyConflict("stored plan action list is invalid")
        actions: list[StoredAction] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                raise IdempotencyConflict("stored plan action is invalid")
            decoded: dict[str, object] = {}
            for name, value in raw_item.items():
                if not isinstance(value, Mapping):
                    continue
                if "S" in value:
                    decoded[str(name)] = value["S"]
                elif "N" in value:
                    decoded[str(name)] = int(cast(str, value["N"]))
            actions.append(self._stored_action(decoded))
        return tuple(sorted(actions, key=lambda action: action.seq))


IdempotencyLock = IdempotencyStore


__all__ = [
    "IdempotencyLease",
    "IdempotencyLock",
    "IdempotencyStore",
    "StoredAction",
    "compute_lock_key",
    "idempotency_key",
    "lock_key",
    "make_lock_key",
]
