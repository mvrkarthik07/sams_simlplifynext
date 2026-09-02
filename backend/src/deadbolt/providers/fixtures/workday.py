"""Deterministic Workday fixture connector."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from deadbolt.providers.fixtures._base import FixtureProvider


class WorkdayFixtureProvider(FixtureProvider):
    """Workday Tier-B provider backed by a JSON seed."""

    def __init__(
        self,
        seed_path: str | Path | None = None,
        seed: Sequence[Mapping[str, object]] | None = None,
    ) -> None:
        super().__init__("workday", seed_path, seed)


WorkdayProvider = WorkdayFixtureProvider

__all__ = ["WorkdayFixtureProvider", "WorkdayProvider"]
