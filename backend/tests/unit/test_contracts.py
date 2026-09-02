"""M0 tests for the immutable contract surface."""

from dataclasses import FrozenInstanceError, is_dataclass
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from deadbolt.contracts.clock import FixedClock
from deadbolt.contracts.models import ActionResult, CredentialType, Entitlement, Scope
from deadbolt.contracts.provider import EntitlementProvider
from deadbolt.errors import (
    DeadboltError,
    IdempotencyConflict,
    IrreversibleActionError,
    PolicyViolationError,
    ProviderError,
)
from tests.support.provider_compliance import assert_provider_contract


@pytest.mark.m0
def test_public_dataclasses_are_frozen_and_slotted() -> None:
    for cls in (Entitlement, ActionResult, FixedClock):
        assert is_dataclass(cls)
        assert cls.__slots__
        params = cls.__dataclass_params__
        assert params.frozen is True
    entitlement = Entitlement(
        "identity-1",
        "github",
        "acme/repo",
        Scope.READ,
        None,
        None,
        CredentialType.FEDERATED,
        True,
        {},
    )
    with pytest.raises(FrozenInstanceError):
        entitlement.system = "other"


@pytest.mark.m0
def test_entitlement_rejects_naive_datetime_fields() -> None:
    naive = datetime(2026, 9, 2, tzinfo=UTC).replace(tzinfo=None)
    values = dict(
        identity_id="identity-1",
        system="github",
        resource="acme/repo",
        scope=Scope.READ,
        granted_at=None,
        last_used_at=None,
        credential_type=CredentialType.FEDERATED,
        revocable=True,
        raw={"source": "test"},
    )
    for field_name in ("granted_at", "last_used_at"):
        values[field_name] = naive
        with pytest.raises(ValueError, match="timezone-aware"):
            Entitlement(**values)
        values[field_name] = None


@pytest.mark.m0
def test_aware_datetimes_and_raw_mapping_are_accepted_immutably() -> None:
    instant = datetime(2026, 9, 2, tzinfo=UTC)
    source = {"provider_id": "abc"}
    entitlement = Entitlement(
        identity_id="identity-1",
        system="github",
        resource="acme/repo",
        scope=Scope.ADMIN,
        granted_at=instant,
        last_used_at=instant,
        credential_type=CredentialType.PAT,
        revocable=True,
        raw=source,
    )
    assert isinstance(entitlement.raw, MappingProxyType)
    source["provider_id"] = "changed"
    assert entitlement.raw["provider_id"] == "abc"


@pytest.mark.m0
def test_provider_protocol_is_runtime_checkable_and_restore_is_required() -> None:
    class CompliantProvider:
        system = "test"

        def snapshot(self):
            return ()

        def revoke(self, e, dry_run):
            return ActionResult(True, "test", e.resource, str(e.scope), dry_run, None, "ok", 0)

        def restore(self, pre_image):
            return ActionResult(True, "test", "resource", "read", False, pre_image, "ok", 0)

    class MissingRestore:
        system = "test"

        def snapshot(self):
            return ()

        def revoke(self, e, dry_run):
            return ActionResult(True, "test", e.resource, str(e.scope), dry_run, None, "ok", 0)

    provider = CompliantProvider()
    assert isinstance(provider, EntitlementProvider)
    assert not isinstance(MissingRestore(), EntitlementProvider)
    assert_provider_contract(provider)


@pytest.mark.m0
def test_fixed_clock_rejects_naive_time_and_returns_fixed_aware_time() -> None:
    naive = datetime(2026, 9, 2, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(naive)
    instant = datetime(2026, 9, 2, tzinfo=UTC)
    assert FixedClock(instant).now() == instant


@pytest.mark.m0
def test_typed_errors_share_deadbolt_base() -> None:
    for error_type in (
        ProviderError,
        IrreversibleActionError,
        PolicyViolationError,
        IdempotencyConflict,
    ):
        assert issubclass(error_type, DeadboltError)
