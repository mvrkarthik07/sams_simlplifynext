"""Deterministic, integer-only risk scoring primitives."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from types import MappingProxyType
from typing import Final

from deadbolt.contracts.models import Entitlement, Scope
from deadbolt.errors import PolicyViolationError

_MAX_BASIS_POINTS: Final = 10_000
_SECONDS_PER_DAY: Final = 86_400
_WEIGHT_TOTAL: Final = 100
_SCOPE_SEVERITIES: Final[Mapping[str, int]] = MappingProxyType(
    {
        "read": 1_000,
        "write": 5_000,
        "admin": 9_000,
        "billing": 10_000,
        "secrets": 10_000,
        "iam": 10_000,
    }
)
DEFAULT_WEIGHTS: Final[Mapping[str, int]] = MappingProxyType({"s": 40, "d": 25, "m": 20, "b": 15})


@dataclass(frozen=True, slots=True)
class RoleTemplate:
    """A small structural representation useful when callers do not use mappings."""

    name: str = ""
    scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "scopes", tuple(self.scopes))


def _basis_points(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_BASIS_POINTS:
        raise PolicyViolationError(f"{name} must be an integer from 0 to 10000")
    return value


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def scope_severity(scope: Scope | str) -> int:
    """Return the static severity of a normalized access scope."""
    name = scope.value if isinstance(scope, Scope) else scope
    try:
        return _SCOPE_SEVERITIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown scope: {name!r}") from exc


def _whole_days_since(last_used_at: datetime, evaluated_at: datetime) -> int:
    _aware(last_used_at, "last_used_at")
    _aware(evaluated_at, "evaluated_at")
    delta = evaluated_at - last_used_at
    whole_seconds = delta // timedelta(seconds=1)
    return max(0, whole_seconds // _SECONDS_PER_DAY)


def dormancy(last_used_at: datetime | None, evaluated_at: datetime) -> int:
    """Return dormancy in basis points, treating an unobserved use as maximal."""
    _aware(evaluated_at, "evaluated_at")
    if last_used_at is None:
        return _MAX_BASIS_POINTS
    days_since = _whole_days_since(last_used_at, evaluated_at)
    return min(_MAX_BASIS_POINTS, (_MAX_BASIS_POINTS * days_since) // 90)


def _scope_values(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset((value,))
    if isinstance(value, Iterable):
        return frozenset(item for item in value if isinstance(item, str))
    return frozenset()


def _scope_names(template: object) -> frozenset[str]:
    if isinstance(template, RoleTemplate):
        return frozenset(template.scopes)
    if isinstance(template, Mapping):
        return _scope_values(template.get("scopes", template.get("allowed_scopes")))
    return _scope_values(getattr(template, "scopes", None))


def role_mismatch(entitlement: Entitlement, template: object) -> int:
    """Return maximal risk when the entitlement is outside its role template."""
    allowed = _scope_names(template)
    return 0 if entitlement.scope.value in allowed else _MAX_BASIS_POINTS


def blast_radius(n_reachable: int) -> int:
    """Map reachable identities to basis points using Decimal logarithms."""
    if isinstance(n_reachable, bool) or not isinstance(n_reachable, int):
        raise ValueError("n_reachable must be an integer")
    count = max(0, n_reachable)
    if count == 0:
        return 0
    with localcontext(prec=28, rounding=ROUND_HALF_EVEN):
        numerator = Decimal(1 + count).ln()
        denominator = Decimal(10).ln()
        scaled = (Decimal(_MAX_BASIS_POINTS) * numerator / denominator) / Decimal(4)
        result = int(scaled.to_integral_value(rounding=ROUND_FLOOR))
    return min(_MAX_BASIS_POINTS, max(0, result))


def _weights(weights: Mapping[str, int]) -> tuple[int, int, int, int]:
    normalized = {key.lower(): value for key, value in weights.items()}
    aliases = {
        "s": ("s", "scope", "scope_severity"),
        "d": ("d", "dormancy"),
        "m": ("m", "mismatch", "role_mismatch"),
        "b": ("b", "blast", "blast_radius"),
    }
    values: list[int] = []
    for name, choices in aliases.items():
        selected = next((normalized[key] for key in choices if key in normalized), None)
        if isinstance(selected, bool) or not isinstance(selected, int):
            raise PolicyViolationError(f"weight {name!r} must be an integer")
        values.append(selected)
    if sum(values) != _WEIGHT_TOTAL:
        raise PolicyViolationError("risk weights must sum to 100")
    return values[0], values[1], values[2], values[3]


def risk(
    s: int,
    d: int,
    m: int,
    b: int,
    weights: Mapping[str, int] = DEFAULT_WEIGHTS,
) -> int:
    """Combine four basis-point factors with a validated policy configuration."""
    factors = (
        _basis_points(s, "s"),
        _basis_points(d, "d"),
        _basis_points(m, "m"),
        _basis_points(b, "b"),
    )
    weight_values = _weights(weights)
    weighted = sum(factor * weight for factor, weight in zip(factors, weight_values, strict=True))
    return weighted // _WEIGHT_TOTAL


__all__ = [
    "DEFAULT_WEIGHTS",
    "RoleTemplate",
    "blast_radius",
    "dormancy",
    "risk",
    "role_mismatch",
    "scope_severity",
]
