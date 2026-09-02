"""M3 tests for canonical plans, non-widening actions, and pre-image keys."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from deadbolt.contracts.models import CredentialType, Entitlement, Scope, Tier
from deadbolt.engine.drift import Finding
from deadbolt.errors import PolicyViolationError
from deadbolt.plan.builder import Action, Plan, _stable_finding_id, build
from deadbolt.plan.canonical import canonical_dumps, iso_second, plan_hash
from deadbolt.plan.preimage import derive_preimage_key, preimage_key

pytestmark = pytest.mark.m3
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
GOLDEN_HASH = Path(__file__).parents[1] / "gates" / "golden" / "plan_hash_seed_a.txt"


def finding(  # noqa: PLR0913 — the helper mirrors the Finding construction inputs.
    identity_id: str,
    resource: str,
    scope: Scope,
    tier: Tier,
    *,
    score: int = 6_000,
    observe_only: bool = False,
) -> Finding:
    entitlement = Entitlement(
        identity_id=identity_id,
        system="github",
        resource=resource,
        scope=scope,
        granted_at=NOW - timedelta(days=10),
        last_used_at=NOW,
        credential_type=CredentialType.FEDERATED,
        revocable=not observe_only,
        raw={},
    )
    return Finding(entitlement, 0, 0, 0, 0, score, tier, {}, observe_only)


def test_canonical_encoding_is_strict_and_ascii() -> None:
    assert canonical_dumps({"z": [True, None], "é": "✓"}) == (
        b'{"z":[true,null],"\\u00e9":"\\u2713"}'
    )
    assert canonical_dumps({}) == b"{}"
    assert canonical_dumps([1, "two"]) == b'[1,"two"]'
    with pytest.raises(TypeError, match=r"\$\.outer\[0\]\.bad"):
        canonical_dumps({"outer": [{"bad": 1.25}]})
    with pytest.raises(TypeError, match=r"unsupported canonical value at \$\.tuple"):
        canonical_dumps({"tuple": (1, 2)})
    with pytest.raises(TypeError, match="float"):
        canonical_dumps({1.5: "bad"})


def test_iso_second_normalizes_aware_instants_and_rejects_naive() -> None:
    offset = timezone(timedelta(hours=8))
    assert iso_second(datetime(2026, 9, 2, 20, 0, 1, 999_999, tzinfo=offset)) == (
        "2026-09-02T12:00:01Z"
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        iso_second(NOW.replace(tzinfo=None))


def test_preimage_key_is_deterministic_and_utf8_aware() -> None:
    digest = hashlib.sha256("repo/é|admin".encode()).hexdigest()[:16]
    expected = f"preimages/abc/3-{digest}.json"
    assert preimage_key("abc", 3, "repo/é", Scope.ADMIN) == expected
    assert derive_preimage_key("abc", 3, "repo/é", "admin") == expected


def test_action_constructor_rejects_widening_and_invalid_transitions() -> None:
    valid = Action(0, "github", "repo", "admin", "revoke", "admin", "none", "f", 1, "T2")
    assert "pre_image_key" not in valid.as_dict()
    Action(1, "github", "repo", "write", "downgrade", "write", "read", "f", 1, "T1")
    with pytest.raises(PolicyViolationError, match="widening"):
        Action(0, "github", "repo", "read", "downgrade", "read", "admin", "f", 1, "T1")
    with pytest.raises(PolicyViolationError, match="verb"):
        Action(0, "github", "repo", "read", "grant", "read", "admin", "f", 1, "T1")
    with pytest.raises(PolicyViolationError, match="no access"):
        Action(0, "github", "repo", "write", "revoke", "write", "read", "f", 1, "T2")
    with pytest.raises(PolicyViolationError, match="reduce"):
        Action(0, "github", "repo", "read", "downgrade", "read", "read", "f", 1, "T1")
    with pytest.raises(PolicyViolationError, match="equal"):
        Action(0, "github", "repo", "read", "revoke", "write", "none", "f", 1, "T1")
    with pytest.raises(PolicyViolationError, match="seq"):
        Action(-1, "github", "repo", "read", "revoke", "read", "none", "f", 1, "T1")
    with pytest.raises(PolicyViolationError, match="seq"):
        Action(True, "github", "repo", "read", "revoke", "read", "none", "f", 1, "T1")
    with pytest.raises(PolicyViolationError, match="score"):
        Action(0, "github", "repo", "read", "revoke", "read", "none", "f", 10_001, "T1")
    with pytest.raises(PolicyViolationError, match="score"):
        Action(0, "github", "repo", "read", "revoke", "read", "none", "f", True, "T1")
    with pytest.raises(PolicyViolationError, match="unknown"):
        Action(0, "github", "repo", "unknown", "revoke", "unknown", "none", "f", 1, "T1")


def test_build_is_order_independent_and_attaches_preimage_slots() -> None:
    findings = (
        finding("b", "z-resource", Scope.ADMIN, Tier.T2, score=8_500),
        finding("a", "é-resource", Scope.WRITE, Tier.T1, score=3_000),
        finding("c", "read-resource", Scope.READ, Tier.T1, score=3_001),
        finding("observe", "ignored", Scope.ADMIN, Tier.T0, observe_only=True),
    )
    first = build(findings, NOW, "template-7")
    second = build(tuple(reversed(findings)), NOW, "template-7")
    assert isinstance(first, Plan)
    assert first.plan_hash == second.plan_hash == plan_hash(first.body)
    assert first.plan_hash == GOLDEN_HASH.read_text(encoding="utf-8").strip()
    assert first.hash == first.plan_hash
    assert first.plan_id != second.plan_id
    assert first.created_at == "2026-09-02T12:00:00Z"
    assert first.attempt == 0
    assert first.trace_id
    assert [action["seq"] for action in first.body["actions"]] == [0, 1, 2]
    assert [action["resource"] for action in first.body["actions"]] == [
        "read-resource",
        "z-resource",
        "é-resource",
    ]
    assert [action.verb for action in first.actions] == ["revoke", "revoke", "downgrade"]
    assert all(action.pre_image_key is not None for action in first.actions)
    assert all(
        action.pre_image_key.startswith(f"preimages/{first.plan_hash}/") for action in first.actions
    )
    assert first.body["schema_version"] == "1"
    assert first.body["weights_version"] == "v1"


def test_build_handles_empty_and_nonrevocable_findings() -> None:
    empty = build((), NOW, "v1")
    assert empty.body["actions"] == []
    assert empty.actions == ()
    nonrevocable = build((finding("x", "r", Scope.WRITE, Tier.T0, observe_only=True),), NOW, "v1")
    assert nonrevocable.body["actions"] == []


def test_plan_properties_and_finding_id_edge_cases() -> None:
    assert canonical_dumps({"not-an-identifier": 1}) == b'{"not-an-identifier":1}'
    custom = SimpleNamespace(finding_id="provided", entitlement=None)
    assert _stable_finding_id(custom) == "provided"
    invalid_attempt = Plan({}, {"attempt": "zero"}, ())
    with pytest.raises(TypeError, match="attempt"):
        _ = invalid_attempt.attempt
    invalid_text = Plan({}, {"plan_id": 1}, ())
    with pytest.raises(TypeError, match="plan_id"):
        _ = invalid_text.plan_id
