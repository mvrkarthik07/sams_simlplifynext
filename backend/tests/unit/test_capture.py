"""M11 capture redaction and determinism tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from deadbolt.contracts.models import CredentialType, Entitlement, Scope
from deadbolt.graph.capture import capture_body, write_capture


def _entitlement() -> Entitlement:
    return Entitlement(
        identity_id="octocat",
        system="github",
        resource="acme/platform",
        scope=Scope.WRITE,
        granted_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_used_at=None,
        credential_type=CredentialType.FEDERATED,
        revocable=True,
        raw={
            "kind": "collaborator",
            "login": "octocat",
            "owner": "acme",
            "repo": "platform",
            "permission": "push",
            "email": "octocat@example.com",
            "avatar_url": "https://avatars.example.com/octocat",
            "authorization": "Bearer ghp_secret",
            "user": {"email": "octocat@example.com"},
        },
    )


@pytest.mark.m11
def test_capture_redacts_secrets_email_and_payload_bodies() -> None:
    body, manifest = capture_body(
        (_entitlement(),),
        org="acme",
        captured_at=datetime(2026, 9, 6, 0, 0, tzinfo=UTC),
    )
    encoded = json.dumps({"body": body, "manifest": manifest}, sort_keys=True)
    for forbidden in ("ghp_", "github_pat_", "Bearer ", "@", "Authorization"):
        assert forbidden not in encoded
    record = body["entitlements"][0]
    assert isinstance(record, dict)
    assert record["source"] == "captured"
    assert record["captured_at"] == "2026-09-06T00:00:00Z"
    assert manifest["record_count"] == 1


@pytest.mark.m11
def test_capture_is_canonical_and_loader_artifact_is_stable(tmp_path: Path) -> None:
    timestamp = datetime(2026, 9, 6, 0, 0, 0, 123456, tzinfo=UTC)
    first, first_manifest = capture_body((_entitlement(),), org="acme", captured_at=timestamp)
    second, second_manifest = capture_body((_entitlement(),), org="acme", captured_at=timestamp)
    assert first == second
    assert first_manifest == second_manifest

    manifest = write_capture((_entitlement(),), tmp_path, org="acme", captured_at=timestamp)
    assert json.loads((tmp_path / "manifest.json").read_text()) == manifest
    assert json.loads((tmp_path / "github.json").read_text()) == first
