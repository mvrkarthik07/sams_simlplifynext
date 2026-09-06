"""M11 captured dataset provider checks."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from deadbolt.contracts.models import CredentialType, Entitlement, Scope
from deadbolt.graph.capture import write_capture
from deadbolt.providers.registry import build_providers


@pytest.mark.m11
def test_registry_serves_captured_github_dataset(tmp_path: Path) -> None:
    entitlement = Entitlement(
        identity_id="octocat",
        system="github",
        resource="acme/platform",
        scope=Scope.READ,
        granted_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_used_at=None,
        credential_type=CredentialType.FEDERATED,
        revocable=True,
        raw={"kind": "collaborator", "login": "octocat", "permission": "pull"},
    )
    write_capture(
        (entitlement,),
        tmp_path,
        org="acme",
        captured_at=datetime(2026, 9, 6, tzinfo=UTC),
    )
    providers = build_providers({"github": "captured"}, captured_dir=tmp_path)
    records = tuple(providers[0].snapshot())
    assert len(records) == 1
    assert records[0].raw["source"] == "captured"
    assert records[0].identity_id == "octocat"
