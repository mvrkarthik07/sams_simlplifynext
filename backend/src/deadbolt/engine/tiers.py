"""Risk tier selection and hard-coded break-glass handling."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from deadbolt.contracts.models import Tier
from deadbolt.errors import PolicyViolationError

PROTECTED_GROUPS: Final[frozenset[str]] = frozenset(
    {"break-glass", "break_glass", "protected", "security", "security-admin", "security-admins"}
)
_MAX_SCORE: Final = 10_000
_T1_START: Final = 3_000
_T2_START: Final = 6_000
_T3_START: Final = 8_500


@dataclass(frozen=True, slots=True)
class Identity:
    """Identity attributes consumed by the pure tier policy."""

    identity_id: str = ""
    role: str | None = None
    oncall: bool = False
    groups: frozenset[str] = frozenset()
    incident_tags: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", frozenset(self.groups))
        object.__setattr__(self, "incident_tags", frozenset(self.incident_tags))


def _text_set(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset((value,))
    if isinstance(value, Iterable):
        return frozenset(item for item in value if isinstance(item, str))
    return frozenset()


def _value(identity: object, name: str, default: object) -> object:
    if isinstance(identity, Identity):
        return getattr(identity, name, default)
    if isinstance(identity, Mapping):
        return identity.get(name, default)
    return getattr(identity, name, default)


def _break_glass(identity: object) -> bool:
    if bool(_value(identity, "oncall", False)):
        return True
    if bool(_value(identity, "protected", False)):
        return True
    groups = _text_set(_value(identity, "groups", ()))
    groups |= _text_set(_value(identity, "protected_groups", ()))
    if {group.lower() for group in groups} & PROTECTED_GROUPS:
        return True
    if bool(_value(identity, "active_incident", False)):
        return True
    if bool(_value(identity, "incident_active", False)):
        return True
    return bool(_text_set(_value(identity, "incident_tags", ())))


def select_tier(score: int, identity: object) -> Tier:
    """Assign a tier, applying the immutable break-glass rule to T1."""
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= _MAX_SCORE:
        raise PolicyViolationError("score must be an integer from 0 to 10000")
    if score < _T1_START:
        tier = Tier.T0
    elif score < _T2_START:
        tier = Tier.T1
    elif score < _T3_START:
        tier = Tier.T2
    else:
        tier = Tier.T3
    if tier is Tier.T1 and _break_glass(identity):
        return Tier.T2
    return tier


__all__ = ["PROTECTED_GROUPS", "Identity", "select_tier"]
