"""DynamoDB single-table graph repository."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, cast

import boto3  # type: ignore[import-untyped]  # boto3 does not publish strict typing metadata.
from boto3.dynamodb.types import (  # type: ignore[import-untyped]  # boto3 has no py.typed marker.
    TypeDeserializer,
    TypeSerializer,
)

from deadbolt.contracts.models import CredentialType, Entitlement, Scope
from deadbolt.errors import ProviderError

_AttributeValue = dict[str, object]
_Item = dict[str, _AttributeValue]


class _DynamoClient(Protocol):
    def batch_write_item(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_item(self, **kwargs: object) -> Mapping[str, object]: ...

    def put_item(self, **kwargs: object) -> Mapping[str, object]: ...

    def query(self, **kwargs: object) -> Mapping[str, object]: ...


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("DynamoDB value is not text")
    return value


def _attribute(value: object) -> _AttributeValue:
    return cast(_AttributeValue, value)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("DynamoDB response is not a mapping")
    return value


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    instant = value.astimezone(UTC).replace(microsecond=0)
    return instant.isoformat().replace("+00:00", "Z")


def _stored_timestamp(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("stored timestamp is not a datetime string")


def _ddb_value(value: object) -> object:
    if isinstance(value, datetime):
        return _timestamp(value)
    if isinstance(value, Mapping):
        return {str(key): _ddb_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_ddb_value(item) for item in value]
    return value


class GraphStore:
    """Persist normalized graph objects in the PRD-defined single table."""

    def __init__(  # noqa: PLR0913 — retry knobs remain injectable for deterministic tests.
        self,
        table_name: str,
        dynamodb_client: object | None = None,
        *,
        client: object | None = None,
        region_name: str = "us-east-1",
        max_retries: int = 5,
        backoff_base: float = 0.01,
        backoff_cap: float = 0.25,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if dynamodb_client is not None and client is not None:
            raise ValueError("provide only one DynamoDB client")
        supplied = dynamodb_client if dynamodb_client is not None else client
        if supplied is None:
            supplied = boto3.client("dynamodb", region_name=region_name)
        self._client = cast(_DynamoClient, supplied)
        self.table_name = table_name
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()
        self._max_retries = max(0, max_retries)
        self._backoff_base = max(0.0, backoff_base)
        self._backoff_cap = max(0.0, backoff_cap)
        self._sleeper = sleeper

    @staticmethod
    def _key(pk: str, sk: str) -> _Item:
        return {"PK": {"S": pk}, "SK": {"S": sk}}

    def _serialize_item(self, values: Mapping[str, object]) -> _Item:
        return {
            str(key): cast(_AttributeValue, self._serializer.serialize(_ddb_value(value)))
            for key, value in values.items()
        }

    def _deserialize_item(self, values: object) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in _mapping(values).items():
            result[str(key)] = cast(object, self._deserializer.deserialize(_attribute(value)))
        return result

    def _put_item(self, values: Mapping[str, object]) -> None:
        self._client.put_item(TableName=self.table_name, Item=self._serialize_item(values))

    def _batch_write(self, items: list[Mapping[str, object]]) -> None:
        for offset in range(0, len(items), 25):
            pending: list[dict[str, object]] = [
                {"PutRequest": {"Item": self._serialize_item(item)}}
                for item in items[offset : offset + 25]
            ]
            for attempt in range(self._max_retries + 1):
                response = _mapping(
                    self._client.batch_write_item(RequestItems={self.table_name: pending})
                )
                unprocessed_value = response.get("UnprocessedItems", {})
                unprocessed = _mapping(unprocessed_value).get(self.table_name, [])
                if not unprocessed:
                    break
                if attempt == self._max_retries:
                    raise ProviderError("DynamoDB batch write exhausted retry budget")
                pending = cast(list[dict[str, object]], unprocessed)
                delay = min(self._backoff_cap, self._backoff_base * (2**attempt))
                self._sleeper(delay)

    @staticmethod
    def _entitlement_item(entitlement: Entitlement) -> dict[str, object]:
        identity_id = entitlement.identity_id
        resource = entitlement.resource
        system = entitlement.system
        scope = entitlement.scope.value
        values: dict[str, object] = {
            "PK": f"ID#{identity_id}",
            "SK": f"ENT#{system}#{resource}#{scope}",
            "GSI1PK": f"RES#{system}#{resource}",
            "GSI1SK": f"ID#{identity_id}",
            "identity_id": identity_id,
            "system": system,
            "resource": resource,
            "scope": scope,
            "granted_at": _timestamp(entitlement.granted_at),
            "last_used_at": _timestamp(entitlement.last_used_at),
            "credential_type": entitlement.credential_type.value,
            "revocable": entitlement.revocable,
            "raw": dict(entitlement.raw),
        }
        return {
            key: value
            for key, value in values.items()
            if value is not None or key in {"granted_at", "last_used_at"}
        }

    def put_entitlements(self, entitlements: Iterable[Entitlement]) -> None:
        """Write current entitlements with the PRD PK/SK and GSI1 keys."""
        ordered = sorted(
            entitlements,
            key=lambda e: tuple(
                value.encode("utf-8")
                for value in (e.identity_id, e.system, e.resource, e.scope.value)
            ),
        )
        self._batch_write([self._entitlement_item(entitlement) for entitlement in ordered])

    def _query_all(self, **kwargs: object) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        request = dict(kwargs)
        request["TableName"] = self.table_name
        while True:
            response = self._client.query(**request)
            response_map = _mapping(response)
            raw_items = response_map.get("Items", [])
            items.extend(self._deserialize_item(item) for item in cast(Iterable[object], raw_items))
            last_key = response_map.get("LastEvaluatedKey")
            if not last_key:
                return items
            request["ExclusiveStartKey"] = last_key

    @staticmethod
    def _to_entitlement(item: Mapping[str, object]) -> Entitlement:
        raw = item.get("raw", {})
        if not isinstance(raw, Mapping):
            raise ValueError("stored entitlement raw field is not an object")
        return Entitlement(
            identity_id=_text(item["identity_id"]),
            system=_text(item["system"]),
            resource=_text(item["resource"]),
            scope=Scope(_text(item["scope"])),
            granted_at=_stored_timestamp(item.get("granted_at")),
            last_used_at=_stored_timestamp(item.get("last_used_at")),
            credential_type=CredentialType(_text(item["credential_type"])),
            revocable=bool(item["revocable"]),
            raw=raw,
        )

    def get_identity_entitlements(self, identity_id: str) -> list[Entitlement]:
        items = self._query_all(
            KeyConditionExpression="#pk = :pk AND begins_with(#sk, :sk)",
            ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
            ExpressionAttributeValues={
                ":pk": {"S": f"ID#{identity_id}"},
                ":sk": {"S": "ENT#"},
            },
        )
        result = [self._to_entitlement(item) for item in items]
        return sorted(result, key=lambda e: (e.system, e.resource, e.scope.value))

    def who_can_reach(self, system: str, resource: str) -> list[str]:
        items = self._query_all(
            IndexName="GSI1",
            KeyConditionExpression="#pk = :pk AND begins_with(#sk, :sk)",
            ExpressionAttributeNames={"#pk": "GSI1PK", "#sk": "GSI1SK"},
            ExpressionAttributeValues={
                ":pk": {"S": f"RES#{system}#{resource}"},
                ":sk": {"S": "ID#"},
            },
            ProjectionExpression="identity_id",
        )
        return sorted(
            {_text(item["identity_id"]) for item in items},
            key=lambda value: value.encode("utf-8"),
        )

    def put_identity(
        self,
        identity_id: str,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        values = dict(attributes or {})
        values.update({"PK": f"ID#{identity_id}", "SK": "META", "identity_id": identity_id})
        self._put_item(values)

    def get_role_template(self, title_slug: str, version: str | int) -> dict[str, object] | None:
        response = self._client.get_item(
            TableName=self.table_name,
            Key=self._key(f"ROLE#{title_slug}", f"V#{version}"),
        )
        item = _mapping(response).get("Item")
        if not item:
            return None
        result = self._deserialize_item(item)
        result.pop("PK", None)
        result.pop("SK", None)
        return result

    def put_role_template(
        self,
        title_slug: str,
        version: str | int,
        template: Mapping[str, object],
    ) -> None:
        values = dict(template)
        values.update({"PK": f"ROLE#{title_slug}", "SK": f"V#{version}"})
        self._put_item(values)

    def put_finding(self, finding_id: str, finding: Mapping[str, object]) -> None:
        values = dict(finding)
        values.update({"PK": f"FIND#{finding_id}", "SK": "META", "finding_id": finding_id})
        self._put_item(values)

    def put_run(self, run_id: str, run: Mapping[str, object]) -> None:
        """Record snapshot freshness without adding a second persistence path."""
        values = dict(run)
        values.update({"PK": f"RUN#{run_id}", "SK": "META", "run_id": run_id})
        self._put_item(values)

    def put_plan(
        self,
        plan_id: str,
        actions: Iterable[Mapping[str, object]] | Mapping[str, object],
    ) -> None:
        if isinstance(actions, Mapping):
            raw_actions = actions.get("actions", ())
        else:
            raw_actions = actions
        if not isinstance(raw_actions, Iterable) or isinstance(raw_actions, (str, bytes)):
            raise ValueError("plan actions must be an iterable of objects")
        items: list[Mapping[str, object]] = []
        for index, action in enumerate(raw_actions):
            if not isinstance(action, Mapping):
                raise ValueError("each plan action must be an object")
            values = dict(action)
            sequence = values.get("seq", index)
            values.update({"PK": f"PLAN#{plan_id}", "SK": f"ACT#{sequence}", "plan_id": plan_id})
            items.append(values)
        self._batch_write(items)


__all__ = ["GraphStore"]
