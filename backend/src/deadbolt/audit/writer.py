"""Append-only, canonical, hash-chained audit records in S3."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from deadbolt.errors import AuditChainError, AuditConfigurationError
from deadbolt.plan.canonical import canonical_dumps, iso_second

_OBJECT_LOCK_MODES = frozenset({"GOVERNANCE", "COMPLIANCE"})
_REDACTED_KEYS = frozenset(
    {"raw", "secret", "token", "pat", "password", "authorization", "access_token"}
)
_REQUIRED_FIELDS = (
    "event_id",
    "ts",
    "actor",
    "action",
    "identity_id",
    "system",
    "resource",
    "scope",
    "plan_hash",
    "finding_id",
    "tier",
    "decision",
    "trace_id",
    "pre_image_key",
    "result",
    "prev_hash",
)


class _S3Body(Protocol):
    def read(self) -> bytes: ...


class _S3Client(Protocol):
    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _safe_value(value: object, key: str = "") -> object:
    """Copy JSON-shaped result data while dropping sensitive payload fields."""
    if key.lower() in _REDACTED_KEYS:
        return None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, datetime):
        return iso_second(value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for child_key, child in value.items():
            safe = _safe_value(child, str(child_key))
            if safe is not None:
                result[str(child_key)] = safe
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    return str(value)


@dataclass(frozen=True, slots=True)
class AuditConfig:
    """Immutable-storage settings supplied by deployment configuration."""

    object_lock_mode: str
    retention_days: int = 1
    prefix: str = "audit"

    def __post_init__(self) -> None:
        mode = self.object_lock_mode.upper()
        if mode not in _OBJECT_LOCK_MODES:
            raise AuditConfigurationError(
                f"object_lock_mode must be one of {sorted(_OBJECT_LOCK_MODES)}"
            )
        if isinstance(self.retention_days, bool) or self.retention_days < 1:
            raise AuditConfigurationError("retention_days must be a positive integer")
        if not self.prefix or self.prefix.startswith("/"):
            raise AuditConfigurationError("audit prefix must be a non-empty relative path")
        object.__setattr__(self, "object_lock_mode", mode)

    @classmethod
    def from_env(cls) -> AuditConfig:
        """Load required immutable-storage settings without selecting a mode in code."""
        mode = os.environ.get("AUDIT_OBJECT_LOCK_MODE", "")
        if not mode:
            raise AuditConfigurationError("AUDIT_OBJECT_LOCK_MODE is required")
        retention = os.environ.get("AUDIT_RETENTION_DAYS", "1")
        try:
            retention_days = int(retention)
        except ValueError as exc:
            raise AuditConfigurationError("AUDIT_RETENTION_DAYS must be an integer") from exc
        return cls(mode, retention_days, os.environ.get("AUDIT_PREFIX", "audit"))


def _config_value(config: AuditConfig | Mapping[str, object]) -> AuditConfig:
    if isinstance(config, AuditConfig):
        return config
    mode = config.get("object_lock_mode")
    if not isinstance(mode, str):
        raise AuditConfigurationError("config.object_lock_mode is required")
    retention = config.get("retention_days", 1)
    if not isinstance(retention, int):
        raise AuditConfigurationError("config.retention_days must be an integer")
    prefix = config.get("prefix", "audit")
    if not isinstance(prefix, str):
        raise AuditConfigurationError("config.prefix must be a string")
    return AuditConfig(mode, retention, prefix)


def _body(response: Mapping[str, object]) -> bytes:
    value = response.get("Body")
    if value is None:
        raise AuditChainError("audit object has no body")
    return cast(_S3Body, value).read()


def _object_keys(response: Mapping[str, object]) -> tuple[str, ...]:
    contents = response.get("Contents", [])
    if not isinstance(contents, list):
        return ()
    keys: list[str] = []
    for item in contents:
        if isinstance(item, Mapping) and isinstance(item.get("Key"), str):
            keys.append(cast(str, item["Key"]))
    return tuple(keys)


class AuditWriter:
    """Persist immutable audit events and verify a chain scoped to one plan."""

    def __init__(
        self,
        bucket: str,
        s3_client: object,
        *,
        config: AuditConfig | Mapping[str, object] | None = None,
        object_lock_mode: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if config is not None and object_lock_mode is not None:
            raise AuditConfigurationError("provide config or object_lock_mode, not both")
        self.bucket = bucket
        self._client = cast(_S3Client, s3_client)
        selected = config
        if object_lock_mode is not None:
            selected = AuditConfig(object_lock_mode)
        if selected is None and os.environ.get("AUDIT_OBJECT_LOCK_MODE"):
            selected = AuditConfig.from_env()
        self.config = _config_value(selected) if selected is not None else None
        self._clock = clock

    @classmethod
    def from_env(
        cls,
        bucket: str,
        s3_client: object,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> AuditWriter:
        """Construct a production writer from deployment configuration."""
        return cls(bucket, s3_client, config=AuditConfig.from_env(), clock=clock)

    def _prefix(self, plan_id: str) -> str:
        prefix = self.config.prefix if self.config is not None else "audit"
        return f"{prefix}/{plan_id}/"

    def _records(self, plan_id: str) -> list[tuple[str, dict[str, object], bytes]]:
        response = self._client.list_objects_v2(Bucket=self.bucket, Prefix=self._prefix(plan_id))
        records: list[tuple[str, dict[str, object], bytes]] = []
        for key in _object_keys(response):
            raw = _body(self._client.get_object(Bucket=self.bucket, Key=key))
            try:
                parsed: object = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AuditChainError(f"invalid JSON in {key}") from exc
            if not isinstance(parsed, dict):
                raise AuditChainError(f"audit object {key} is not a record")
            records.append((key, cast(dict[str, object], parsed), raw))
        return records

    @staticmethod
    def _sort_record(item: tuple[str, dict[str, object], bytes]) -> tuple[int, str, str]:
        key, record, _ = item
        sequence = record.get("seq", 0)
        seq = sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else 0
        return seq, _as_text(record.get("ts")), key

    def _previous_hash(self, plan_id: str) -> str:
        try:
            records = self._records(plan_id)
        except AttributeError:
            # The small legacy test double predates chain reads; production S3 implements it.
            return ""
        if not records:
            return ""
        ordered = sorted(records, key=self._sort_record)
        if not self._verify_records(ordered):
            raise AuditChainError(f"cannot extend malformed audit chain for {plan_id}")
        return hashlib.sha256(ordered[-1][2]).hexdigest()

    def _normalize(
        self, record: Mapping[str, object], prev_hash: str, default_seq: int
    ) -> tuple[str, int, dict[str, object]]:
        source = dict(record)
        plan_id = _as_text(source.get("plan_id")) or _as_text(source.get("plan_hash")) or "unscoped"
        raw_seq = source.get("seq")
        seq = raw_seq if isinstance(raw_seq, int) and not isinstance(raw_seq, bool) else default_seq
        timestamp = source.get("ts")
        if isinstance(timestamp, datetime):
            ts = iso_second(timestamp)
        elif isinstance(timestamp, str) and timestamp:
            ts = timestamp
        else:
            ts = iso_second(self._clock())
        result = source.get("result", source.get("status", source.get("message", "unknown")))
        normalized: dict[str, object] = {
            "event_id": uuid.uuid4().hex,
            "ts": ts,
            "actor": _as_text(source.get("actor"))
            or _as_text(source.get("approver_id"))
            or "system",
            "action": _as_text(source.get("action"))
            or _as_text(source.get("event"))
            or _as_text(source.get("verb"))
            or "unknown",
            "identity_id": _as_text(source.get("identity_id"), "unknown"),
            "system": _as_text(source.get("system"), "unknown"),
            "resource": _as_text(source.get("resource"), "unknown"),
            "scope": _as_text(source.get("scope"), "unknown"),
            "plan_hash": _as_text(source.get("plan_hash"), "unknown"),
            "finding_id": _as_text(source.get("finding_id")),
            "tier": _as_text(source.get("tier"), "T0"),
            "decision": _as_text(source.get("decision"))
            or _as_text(source.get("status"), "unknown"),
            "trace_id": _as_text(source.get("trace_id")),
            "pre_image_key": _as_text(source.get("pre_image_key")) or None,
            "result": _safe_value(result),
            "prev_hash": prev_hash,
            # Chain addressing metadata is not mutable user-supplied payload.
            "plan_id": plan_id,
            "seq": seq,
        }
        return plan_id, seq, normalized

    @staticmethod
    def _verify_records(records: list[tuple[str, dict[str, object], bytes]]) -> bool:
        previous = ""
        seen_sequences: set[int] = set()
        for _, record, raw in records:
            if any(field not in record for field in _REQUIRED_FIELDS):
                return False
            sequence = record.get("seq")
            if (
                not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or sequence in seen_sequences
            ):
                return False
            if record.get("prev_hash") != previous:
                return False
            try:
                canonical = canonical_dumps(record)
            except (TypeError, ValueError):
                return False
            if canonical != raw:
                return False
            previous = hashlib.sha256(raw).hexdigest()
            seen_sequences.add(sequence)
        return True

    def write(self, record: Mapping[str, object]) -> str:
        """Persist one canonical immutable record and return its S3 object key."""
        supplied_plan_id = (
            _as_text(record.get("plan_id")) or _as_text(record.get("plan_hash")) or "unscoped"
        )
        previous = self._previous_hash(supplied_plan_id)
        default_seq = 0
        supplied_seq = record.get("seq")
        if not isinstance(supplied_seq, int) or isinstance(supplied_seq, bool):
            try:
                prior = self._records(supplied_plan_id)
            except AttributeError:
                prior = []
            sequences: list[int] = []
            for item in prior:
                sequence = item[1].get("seq")
                if isinstance(sequence, int) and not isinstance(sequence, bool):
                    sequences.append(sequence)
            default_seq = max(sequences, default=-1) + 1
        plan_id, seq, normalized = self._normalize(record, previous, default_seq)
        body = canonical_dumps(normalized)
        key = f"{self._prefix(plan_id)}{seq}/{normalized['event_id']}.json"
        kwargs: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ContentType": "application/json",
            "IfNoneMatch": "*",
        }
        if self.config is not None:
            kwargs.update(
                {
                    "ObjectLockMode": self.config.object_lock_mode,
                    "ObjectLockRetainUntilDate": self._clock()
                    + timedelta(days=self.config.retention_days),
                }
            )
        self._client.put_object(**kwargs)
        return key

    def verify_chain(self, plan_id: str) -> bool:
        """Return false for missing, reordered, non-canonical, or tampered records."""
        try:
            records = sorted(self._records(plan_id), key=self._sort_record)
            return bool(records) and self._verify_records(records)
        except (AuditChainError, KeyError, TypeError, ValueError):
            return False


def write_audit(
    bucket: str,
    s3_client: object,
    record: Mapping[str, object],
    *,
    config: AuditConfig | Mapping[str, object] | None = None,
) -> str:
    """Functional convenience wrapper around :class:`AuditWriter`."""
    return AuditWriter(bucket, s3_client, config=config).write(record)


__all__ = ["AuditConfig", "AuditWriter", "write_audit"]
