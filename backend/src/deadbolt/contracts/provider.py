"""The provider protocol used by real and fixture connectors."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol, runtime_checkable

from deadbolt.contracts.models import ActionResult, Entitlement


@runtime_checkable
class EntitlementProvider(Protocol):
    """Connector contract; restore is mandatory for safe onboarding."""

    system: str

    def snapshot(self) -> Iterable[Entitlement]:
        """Return the provider's current normalized entitlements."""
        ...

    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        """Revoke one entitlement, or describe the mutation when dry-running."""
        ...

    def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
        """Restore the exact provider state captured before revocation."""
        ...


__all__ = ["EntitlementProvider"]
