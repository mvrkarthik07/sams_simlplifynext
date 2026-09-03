"""Build immutable, deterministic and non-widening action plans."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final
from uuid import uuid4

from deadbolt.contracts.models import Scope, Tier
from deadbolt.engine.drift import Finding
from deadbolt.engine.scoring import scope_severity
from deadbolt.errors import PolicyViolationError
from deadbolt.plan.canonical import canonical_dumps, iso_second, plan_hash
from deadbolt.plan.preimage import preimage_key

SCHEMA_VERSION: Final[str] = "1"
WEIGHTS_VERSION: Final[str] = "v1"
NO_ACCESS_SCOPE: Final[str] = "none"
_ACTION_VERBS: Final[frozenset[str]] = frozenset({"revoke", "downgrade"})
_MAX_SCORE: Final[int] = 10_000


def _severity(scope: str) -> int:
    if scope == NO_ACCESS_SCOPE:
        return 0
    try:
        return scope_severity(scope)
    except ValueError as exc:
        raise PolicyViolationError(f"unknown action scope: {scope!r}") from exc


@dataclass(frozen=True, slots=True)
class Action:
    """One reversible access reduction proposed by a plan."""

    seq: int
    system: str
    resource: str
    scope: str
    verb: str
    from_scope: str
    to_scope: str
    finding_id: str
    score: int
    tier: str
    pre_image_key: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 0:
            raise PolicyViolationError("action seq must be a non-negative integer")
        if self.verb not in _ACTION_VERBS:
            raise PolicyViolationError(f"unsupported action verb: {self.verb!r}")
        if self.scope != self.from_scope:
            raise PolicyViolationError("action scope must equal from_scope")
        from_severity = _severity(self.from_scope)
        to_severity = _severity(self.to_scope)
        if to_severity > from_severity:
            raise PolicyViolationError("access-widening action is forbidden")
        if self.verb == "revoke" and self.to_scope != NO_ACCESS_SCOPE:
            raise PolicyViolationError("revoke actions must transition to no access")
        if self.verb == "downgrade" and not to_severity < from_severity:
            raise PolicyViolationError("downgrade actions must reduce access")
        if (
            isinstance(self.score, bool)
            or not isinstance(self.score, int)
            or not 0 <= self.score <= _MAX_SCORE
        ):
            raise PolicyViolationError("action score must be an integer from 0 to 10000")

    def as_dict(self) -> dict[str, object]:
        """Return the hashed action representation (pre-image keys are executor metadata)."""
        return {
            "seq": self.seq,
            "system": self.system,
            "resource": self.resource,
            "scope": self.scope,
            "verb": self.verb,
            "from_scope": self.from_scope,
            "to_scope": self.to_scope,
            "finding_id": self.finding_id,
            "score": self.score,
            "tier": self.tier,
        }


@dataclass(frozen=True, slots=True)
class Plan:
    """A hashed body plus mutable delivery metadata and executor-filled actions."""

    body: dict[str, object]
    envelope: dict[str, object]
    actions: tuple[Action, ...]

    @property
    def plan_hash(self) -> str:
        return plan_hash(self.body)

    @property
    def hash(self) -> str:
        """frontend-friendly alias for the canonical plan hash."""
        return self.plan_hash

    @property
    def plan_id(self) -> str:
        return _envelope_text(self.envelope, "plan_id")

    @property
    def created_at(self) -> str:
        return _envelope_text(self.envelope, "created_at")

    @property
    def trace_id(self) -> str:
        return _envelope_text(self.envelope, "trace_id")

    @property
    def attempt(self) -> int:
        attempt = self.envelope["attempt"]
        if not isinstance(attempt, int):
            raise TypeError("plan envelope attempt must be an integer")
        return attempt


def _envelope_text(envelope: dict[str, object], name: str) -> str:
    value = envelope[name]
    if not isinstance(value, str):
        raise TypeError(f"plan envelope {name} must be a string")
    return value


def _stable_finding_id(finding: Finding) -> str:
    candidate = getattr(finding, "finding_id", None)
    if isinstance(candidate, str) and candidate:
        return candidate
    entitlement = finding.entitlement
    identity = "|".join(
        (entitlement.identity_id, entitlement.system, entitlement.resource, entitlement.scope.value)
    )
    return hashlib.sha256(canonical_dumps({"identity": identity})).hexdigest()


def _action_for(finding: Finding, finding_id: str) -> Action | None:
    if finding.observe_only or finding.tier is Tier.T0:
        return None
    # Irreversible credentials (for example GHE PAT/SAML authorization revocation) can
    # never enter the automatic T1 path.  The provider advertises this capability in raw;
    # the frozen Entitlement contract remains unchanged.
    if finding.tier is Tier.T1 and finding.entitlement.raw.get("reversible") is False:
        return None
    entitlement = finding.entitlement
    from_scope = entitlement.scope.value
    if finding.tier is Tier.T1 and from_scope != Scope.READ.value:
        verb = "downgrade"
        to_scope = Scope.READ.value
    else:
        verb = "revoke"
        to_scope = NO_ACCESS_SCOPE
    return Action(
        seq=0,
        system=entitlement.system,
        resource=entitlement.resource,
        scope=from_scope,
        verb=verb,
        from_scope=from_scope,
        to_scope=to_scope,
        finding_id=finding_id,
        score=finding.score,
        tier=finding.tier.value,
    )


def build(findings: Iterable[Finding], evaluated_at: datetime, template_version: str) -> Plan:
    """Build a plan whose body is independent of input order and delivery metadata."""
    evaluated_at_text = iso_second(evaluated_at)
    proposed = [
        action
        for finding in findings
        for action in (_action_for(finding, _stable_finding_id(finding)),)
        if action is not None
    ]
    proposed.sort(
        key=lambda action: (
            action.system.encode("utf-8"),
            action.resource.encode("utf-8"),
            action.scope.encode("utf-8"),
            action.finding_id.encode("utf-8"),
        )
    )
    base_actions = tuple(replace(action, seq=seq) for seq, action in enumerate(proposed))
    body: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": evaluated_at_text,
        "template_version": template_version,
        "weights_version": WEIGHTS_VERSION,
        "actions": [action.as_dict() for action in base_actions],
    }
    digest = plan_hash(body)
    actions = tuple(
        replace(
            action,
            pre_image_key=preimage_key(digest, action.seq, action.resource, action.scope),
        )
        for action in base_actions
    )
    plan_id = str(uuid4())
    envelope: dict[str, object] = {
        "plan_id": plan_id,
        "created_at": evaluated_at_text,
        "trace_id": str(uuid4()),
        "attempt": 0,
    }
    return Plan(body=body, envelope=envelope, actions=actions)


__all__ = [
    "NO_ACCESS_SCOPE",
    "SCHEMA_VERSION",
    "WEIGHTS_VERSION",
    "Action",
    "Plan",
    "build",
]
