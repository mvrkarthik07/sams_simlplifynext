"""Provider fan-out and point-in-time graph snapshots."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol, cast

import boto3  # type: ignore[import-untyped]  # boto3 does not publish strict typing metadata.

from deadbolt.contracts.models import Entitlement
from deadbolt.contracts.provider import EntitlementProvider
from deadbolt.graph.store import GraphStore
from deadbolt.plan.canonical import canonical_dumps


class _S3Client(Protocol):
    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    """The durable outcome of a multi-provider snapshot run."""

    run_id: str
    evaluated_at: datetime
    statuses: Mapping[str, str]
    snapshot_keys: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "statuses", MappingProxyType(dict(self.statuses)))
        object.__setattr__(self, "snapshot_keys", MappingProxyType(dict(self.snapshot_keys)))


def _iso_hour(evaluated_at: datetime) -> str:
    hour = evaluated_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return hour.isoformat().replace("+00:00", "Z")


def _canonical_identity(entitlement: Entitlement) -> str:
    email = entitlement.raw.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip().lower()
    return entitlement.identity_id.lower()


def _normalize(entitlement: Entitlement) -> Entitlement:
    identity_id = _canonical_identity(entitlement)
    if identity_id == entitlement.identity_id:
        return entitlement
    return Entitlement(
        identity_id=identity_id,
        system=entitlement.system,
        resource=entitlement.resource,
        scope=entitlement.scope,
        granted_at=entitlement.granted_at,
        last_used_at=entitlement.last_used_at,
        credential_type=entitlement.credential_type,
        revocable=entitlement.revocable,
        raw=entitlement.raw,
    )


def _wire(entitlement: Entitlement) -> dict[str, object]:
    return {
        "identity_id": entitlement.identity_id,
        "system": entitlement.system,
        "resource": entitlement.resource,
        "scope": entitlement.scope,
        "granted_at": entitlement.granted_at,
        "last_used_at": entitlement.last_used_at,
        "credential_type": entitlement.credential_type,
        "revocable": entitlement.revocable,
        "raw": entitlement.raw,
    }


def run_snapshot(  # noqa: PLR0913 — explicit inputs keep snapshot I/O injectable.
    providers: Iterable[EntitlementProvider],
    store: GraphStore,
    bucket: str,
    evaluated_at: datetime,
    *,
    s3_client: object | None = None,
    run_id: str | None = None,
) -> SnapshotResult:
    """Fan out providers, isolate connector failures, and persist each result."""
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    client = cast(_S3Client, s3_client if s3_client is not None else boto3.client("s3"))
    hour = _iso_hour(evaluated_at)
    effective_run_id = run_id or hour
    statuses: dict[str, str] = {}
    snapshot_keys: dict[str, str] = {}
    run_systems: dict[str, object] = {}

    for provider in sorted(providers, key=lambda p: p.system.encode("utf-8")):
        system = provider.system
        try:
            entitlements = tuple(
                sorted((_normalize(item) for item in provider.snapshot()), key=_sort_key)
            )
        except Exception as exc:
            statuses[system] = "stale"
            run_systems[system] = {"status": "stale", "error": type(exc).__name__}
            continue
        store.put_entitlements(entitlements)
        identities = {
            entitlement.identity_id: {"email": entitlement.identity_id}
            for entitlement in entitlements
        }
        for identity_id, attributes in sorted(
            identities.items(), key=lambda pair: pair[0].encode("utf-8")
        ):
            store.put_identity(identity_id, attributes)
        key = f"snapshots/{hour}/{system}.json"
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=canonical_dumps([_wire(entitlement) for entitlement in entitlements]),
            ContentType="application/json",
        )
        statuses[system] = "fresh"
        snapshot_keys[system] = key
        run_systems[system] = {"status": "fresh", "snapshot_key": key, "count": len(entitlements)}

    store.put_run(
        effective_run_id,
        {"evaluated_at": evaluated_at, "systems": run_systems},
    )
    return SnapshotResult(effective_run_id, evaluated_at, statuses, snapshot_keys)


def _sort_key(entitlement: Entitlement) -> tuple[bytes, bytes, bytes, bytes]:
    return (
        entitlement.identity_id.encode("utf-8"),
        entitlement.system.encode("utf-8"),
        entitlement.resource.encode("utf-8"),
        entitlement.scope.value.encode("utf-8"),
    )


snapshot_providers = run_snapshot
take_snapshot = run_snapshot

__all__ = ["SnapshotResult", "run_snapshot", "snapshot_providers", "take_snapshot"]
