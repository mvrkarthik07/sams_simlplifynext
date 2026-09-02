"""Immutable data objects shared by every Deadbolt layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType


class CredentialType(StrEnum):
    """The kind of credential represented by an entitlement."""

    FEDERATED = "federated"
    PAT = "pat"
    OAUTH = "oauth"
    API_KEY = "api-key"


class Scope(StrEnum):
    """Normalized provider-independent access scopes."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    BILLING = "billing"
    SECRETS = "secrets"
    IAM = "iam"


class Tier(StrEnum):
    """Policy action tiers, ordered by increasing risk."""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


def _require_aware(value: datetime | None, field_name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} must be timezone-aware")


def _immutable_mapping(value: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class Entitlement:
    """A canonical, provider-neutral access entitlement."""

    identity_id: str
    system: str
    resource: str
    scope: Scope
    granted_at: datetime | None
    last_used_at: datetime | None
    credential_type: CredentialType
    revocable: bool
    raw: Mapping[str, object]

    def __post_init__(self) -> None:
        _require_aware(self.granted_at, "granted_at")
        _require_aware(self.last_used_at, "last_used_at")
        object.__setattr__(self, "raw", _immutable_mapping(self.raw))


@dataclass(frozen=True, slots=True)
class ActionResult:
    """The provider result for a dry-run, revoke, or restore operation."""

    ok: bool
    system: str
    resource: str
    scope: str
    dry_run: bool
    pre_image: Mapping[str, object] | None
    message: str
    provider_latency_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "pre_image", _immutable_mapping(self.pre_image))


__all__ = ["ActionResult", "CredentialType", "Entitlement", "Scope", "Tier"]
