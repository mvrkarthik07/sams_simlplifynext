"""Deterministic Salesforce fixture connector."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from deadbolt.contracts.models import Entitlement
from deadbolt.providers.fixtures._base import FixtureProvider


class SalesforceFixtureProvider(FixtureProvider):
    """Salesforce Tier-B provider backed by a JSON seed."""

    def __init__(
        self,
        seed_path: str | Path | None = None,
        seed: Sequence[Mapping[str, object]] | None = None,
    ) -> None:
        super().__init__("salesforce", seed_path, seed)


SalesforceProvider = SalesforceFixtureProvider

__all__ = ["Entitlement", "SalesforceFixtureProvider", "SalesforceProvider"]
