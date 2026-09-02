"""Reusable structural checks for every connector implementation."""

from collections.abc import Iterable

from deadbolt.contracts.models import Entitlement
from deadbolt.contracts.provider import EntitlementProvider


def assert_provider_contract(provider: EntitlementProvider) -> None:
    """Assert the connector exposes the complete, runtime-checkable contract."""
    assert isinstance(provider, EntitlementProvider)
    assert isinstance(provider.system, str)
    assert provider.system
    assert callable(provider.snapshot)
    assert callable(provider.revoke)
    assert callable(provider.restore)
    entitlements = provider.snapshot()
    assert isinstance(entitlements, Iterable)
    assert all(isinstance(entitlement, Entitlement) for entitlement in entitlements)
