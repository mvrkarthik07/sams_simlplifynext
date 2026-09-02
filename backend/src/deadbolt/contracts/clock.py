"""Clock abstractions for explicit, testable time handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


def _validate_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock time must be timezone-aware")
    return value


@runtime_checkable
class Clock(Protocol):
    """A source of timezone-aware UTC timestamps."""

    def now(self) -> datetime:
        """Return the current timezone-aware instant."""
        ...


@dataclass(frozen=True, slots=True)
class FixedClock:
    """A fixed clock for deterministic tests."""

    value: datetime

    def __post_init__(self) -> None:
        _validate_now(self.value)

    def now(self) -> datetime:
        return self.value


__all__ = ["Clock", "FixedClock"]
