"""Pure drift detection over normalized entitlements and graph inputs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from deadbolt.contracts.models import Entitlement, Tier
from deadbolt.engine.scoring import (
    DEFAULT_WEIGHTS,
    RoleTemplate,
    _whole_days_since,
    blast_radius,
    dormancy,
    risk,
    role_mismatch,
    scope_severity,
)
from deadbolt.engine.tiers import Identity, select_tier


@dataclass(frozen=True, slots=True)
class Finding:
    """An immutable explainable drift finding."""

    entitlement: Entitlement
    s: int
    d: int
    m: int
    b: int
    score: int
    tier: Tier
    evidence: Mapping[str, object]
    observe_only: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


def _identity(raw: object, identity_id: str) -> Identity:
    if isinstance(raw, Identity):
        return raw
    if isinstance(raw, Mapping):
        role = raw.get("role", raw.get("role_title", raw.get("title")))
        return Identity(
            identity_id=str(raw.get("identity_id", identity_id)),
            role=role if isinstance(role, str) else None,
            oncall=bool(raw.get("oncall", False)),
            groups=frozenset(_strings(raw.get("groups", ()))),
            incident_tags=frozenset(_strings(raw.get("incident_tags", ()))),
        )
    return Identity(
        identity_id=str(getattr(raw, "identity_id", identity_id)),
        role=getattr(raw, "role", None) if isinstance(getattr(raw, "role", None), str) else None,
        oncall=bool(getattr(raw, "oncall", False)),
        groups=frozenset(_strings(getattr(raw, "groups", ()))),
        incident_tags=frozenset(_strings(getattr(raw, "incident_tags", ()))),
    )


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _identity_for(identities: object, identity_id: str) -> Identity:
    if isinstance(identities, Mapping):
        if identity_id in identities:
            return _identity(identities[identity_id], identity_id)
        if any(key in identities for key in ("identity_id", "oncall", "groups", "incident_tags")):
            return _identity(identities, identity_id)
        return Identity(identity_id=identity_id)
    if isinstance(identities, Iterable) and not isinstance(identities, (str, bytes, bytearray)):
        for raw in identities:
            candidate = _identity(raw, identity_id)
            if candidate.identity_id == identity_id:
                return candidate
    return Identity(identity_id=identity_id)


def _template_for(templates: object, identity: Identity, identity_id: str) -> object:
    if not isinstance(templates, Mapping):
        return templates
    if "scopes" in templates or "allowed_scopes" in templates:
        return templates
    if identity.role is not None and identity.role in templates:
        return templates[identity.role]
    if identity_id in templates:
        return templates[identity_id]
    return None


def _reachable_count(reachability: object, entitlement: Entitlement) -> int:
    if not isinstance(reachability, Mapping):
        return 0
    value: object = None
    pair = (entitlement.system, entitlement.resource)
    for key in (pair, f"{entitlement.system}#{entitlement.resource}", entitlement.resource):
        if key in reachability:
            value = reachability[key]
            break
    if value is None:
        nested = reachability.get(entitlement.system)
        if isinstance(nested, Mapping):
            value = nested.get(entitlement.resource)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(0, value)
    return len(_strings(value))


def _finding(  # noqa: PLR0913, PLR0917 — explicit pure scoring inputs are intentional.
    entitlement: Entitlement,
    identity: Identity,
    templates: object,
    reachability: object,
    evaluated_at: datetime,
    weights: Mapping[str, int],
) -> Finding:
    template = _template_for(templates, identity, entitlement.identity_id)
    s = scope_severity(entitlement.scope)
    d = dormancy(entitlement.last_used_at, evaluated_at)
    m = role_mismatch(entitlement, template)
    count = _reachable_count(reachability, entitlement)
    b = blast_radius(count)
    score = risk(s, d, m, b, weights)
    tier = Tier.T0 if not entitlement.revocable else select_tier(score, identity)
    unused: int | str = (
        "never"
        if entitlement.last_used_at is None
        else _whole_days_since(entitlement.last_used_at, evaluated_at)
    )
    evidence = {
        "scope_severity": s,
        "dormancy": d,
        "role_mismatch": m != 0,
        "role_mismatch_score": m,
        "blast_radius": b,
        "days_unused": unused,
        "blast_radius_count": count,
    }
    return Finding(entitlement, s, d, m, b, score, tier, evidence, not entitlement.revocable)


def detect(  # noqa: PLR0913 — the public API makes every pure input explicit.
    entitlements: Iterable[Entitlement],
    identities: object,
    templates: object,
    reachability: object,
    evaluated_at: datetime,
    *,
    weights: Mapping[str, int] = DEFAULT_WEIGHTS,
) -> tuple[Finding, ...]:
    """Score every entitlement and return a stable, explainable tuple."""
    findings = [
        _finding(
            entitlement,
            _identity_for(identities, entitlement.identity_id),
            templates,
            reachability,
            evaluated_at,
            weights,
        )
        for entitlement in entitlements
    ]
    return tuple(
        sorted(
            findings,
            key=lambda finding: (
                -finding.score,
                finding.entitlement.system.encode("utf-8"),
                finding.entitlement.resource.encode("utf-8"),
                finding.entitlement.scope.value.encode("utf-8"),
                finding.entitlement.identity_id.encode("utf-8"),
            ),
        )
    )


__all__ = ["Finding", "Identity", "RoleTemplate", "detect"]
