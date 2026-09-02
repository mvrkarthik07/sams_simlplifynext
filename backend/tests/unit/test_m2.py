"""M2 tests for deterministic scoring, tiers, and drift detection."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from deadbolt.contracts.models import CredentialType, Entitlement, Scope, Tier
from deadbolt.engine.drift import Finding, detect
from deadbolt.engine.scoring import (
    RoleTemplate,
    blast_radius,
    dormancy,
    risk,
    role_mismatch,
    scope_severity,
)
from deadbolt.engine.tiers import Identity, select_tier
from deadbolt.errors import PolicyViolationError

pytestmark = pytest.mark.m2
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
MAX_BP = 10_000
HALF_MAX_BP = 5_000
HIGH_SCOPE_BP = 9_000
QUARTER_MAX_BP = 2_500
DEFAULT_SCOPE_WEIGHTED_BP = 4_000
ADMIN_DORMANT_SCORE = 8_475


def entitlement(  # noqa: PLR0913, PLR0917 — test helper exposes each contract field.
    identity_id: str = "alice",
    system: str = "github",
    resource: str = "acme/repo",
    scope: Scope = Scope.READ,
    last_used_at: datetime | None = NOW,
    revocable: bool = True,
) -> Entitlement:
    return Entitlement(
        identity_id,
        system,
        resource,
        scope,
        NOW - timedelta(days=120),
        last_used_at,
        CredentialType.FEDERATED,
        revocable,
        {},
    )


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (Scope.READ, 1_000),
        ("write", 5_000),
        ("admin", 9_000),
        ("billing", MAX_BP),
        ("secrets", MAX_BP),
        ("iam", MAX_BP),
    ],
)
def test_scope_severity_catalog(scope: Scope | str, expected: int) -> None:
    assert scope_severity(scope) == expected


def test_scope_severity_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        scope_severity("unknown")


def test_dormancy_uses_whole_seconds_and_clamps() -> None:
    assert dormancy(None, NOW) == MAX_BP
    assert dormancy(NOW - timedelta(days=45), NOW) == HALF_MAX_BP
    assert dormancy(NOW - timedelta(days=200), NOW) == MAX_BP
    assert dormancy(NOW + timedelta(seconds=1), NOW) == 0
    assert dormancy(NOW - timedelta(seconds=86_399), NOW) == 0
    assert dormancy(NOW - timedelta(seconds=90 * 86_400), NOW) == MAX_BP


def test_dormancy_rejects_naive_inputs() -> None:
    naive = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        dormancy(naive, NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        dormancy(NOW, naive)


@pytest.mark.parametrize(
    "template",
    [RoleTemplate("engineer", ("read",)), {"scopes": ["read"]}, {"allowed_scopes": "read"}],
)
def test_role_mismatch_is_binary(template: object) -> None:
    assert role_mismatch(entitlement(scope=Scope.READ), template) == 0
    assert role_mismatch(entitlement(scope=Scope.ADMIN), template) == MAX_BP
    assert role_mismatch(entitlement(scope=Scope.READ), None) == MAX_BP


def test_role_mismatch_accepts_structural_template() -> None:
    class Template:
        scopes = ("read",)

    assert role_mismatch(entitlement(), Template()) == 0
    assert role_mismatch(entitlement(), {"scopes": 4}) == MAX_BP


def test_blast_radius_uses_decimal_logarithm_and_clamps() -> None:
    assert blast_radius(0) == 0
    assert blast_radius(-5) == 0
    assert blast_radius(9) == QUARTER_MAX_BP
    assert blast_radius(9_999) == MAX_BP
    assert blast_radius(10**100) == MAX_BP
    with pytest.raises(ValueError, match="integer"):
        blast_radius(True)


def test_risk_validates_weights_and_factors() -> None:
    assert risk(MAX_BP, 0, 0, 0) == DEFAULT_SCOPE_WEIGHTED_BP
    assert (
        risk(MAX_BP, 0, 0, 0, {"scope": 100, "dormancy": 0, "mismatch": 0, "blast_radius": 0})
        == MAX_BP
    )
    with pytest.raises(PolicyViolationError, match="sum to 100"):
        risk(0, 0, 0, 0, {"s": 1, "d": 1, "m": 1, "b": 1})
    with pytest.raises(PolicyViolationError, match="weight"):
        risk(0, 0, 0, 0, {"s": 100, "d": 0, "m": 0})
    with pytest.raises(PolicyViolationError, match="integer"):
        risk(0, 0, 0, 0, {"s": True, "d": 0, "m": 0, "b": 100})
    with pytest.raises(PolicyViolationError, match="integer"):
        risk(-1, 0, 0, 0)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (2_999, Tier.T0),
        (3_000, Tier.T1),
        (5_999, Tier.T1),
        (6_000, Tier.T2),
        (8_499, Tier.T2),
        (8_500, Tier.T3),
    ],
)
def test_tier_boundaries(score: int, expected: Tier) -> None:
    assert select_tier(score, Identity()) is expected


@pytest.mark.parametrize(
    "identity",
    [
        Identity(oncall=True),
        Identity(groups=frozenset({"security-admins"})),
        Identity(incident_tags=frozenset({"incident-123"})),
        {"oncall": True},
        {"groups": {"protected"}},
        {"active_incident": True},
    ],
)
def test_break_glass_promotes_only_t1(identity: object) -> None:
    assert select_tier(3_000, identity) is Tier.T2
    assert select_tier(5_999, identity) is Tier.T2
    assert select_tier(2_999, identity) is Tier.T0
    assert select_tier(6_000, identity) is Tier.T2
    assert select_tier(8_500, identity) is Tier.T3
    assert select_tier(3_000, {"oncall": False}) is Tier.T1


def test_tier_rejects_out_of_range_score() -> None:
    with pytest.raises(PolicyViolationError):
        select_tier(-1, Identity())
    with pytest.raises(PolicyViolationError):
        select_tier(10_001, Identity())


def test_tier_accepts_all_structural_break_glass_signals() -> None:
    assert select_tier(3_000, {"groups": "security-admin"}) is Tier.T2
    assert select_tier(3_000, {"groups": 4}) is Tier.T1
    assert select_tier(3_000, {"incident_active": True}) is Tier.T2
    assert select_tier(3_000, {"incident_tags": "incident-1"}) is Tier.T2

    class ProtectedIdentity:
        protected = True

    assert select_tier(3_000, ProtectedIdentity()) is Tier.T2


def test_detect_explains_and_forces_nonrevocable_observe_only() -> None:
    items = (
        entitlement("alice", scope=Scope.ADMIN, last_used_at=None),
        entitlement("bob", resource="acme/other", scope=Scope.WRITE, revocable=False),
    )
    findings = detect(
        items,
        {"alice": {"role": "engineer", "oncall": True}, "bob": {"role": "engineer"}},
        {"engineer": {"scopes": ["read", "write"]}},
        {("github", "acme/repo"): 9, ("github", "acme/other"): ["alice", "bob"]},
        NOW,
    )
    assert all(isinstance(item, Finding) for item in findings)
    admin = next(item for item in findings if item.entitlement.scope is Scope.ADMIN)
    assert admin.s == HIGH_SCOPE_BP
    assert admin.d == MAX_BP
    assert admin.m == MAX_BP
    assert admin.b == QUARTER_MAX_BP
    assert admin.score == ADMIN_DORMANT_SCORE
    assert admin.tier is Tier.T2
    assert admin.evidence["role_mismatch"] is True
    observe = next(item for item in findings if not item.entitlement.revocable)
    assert observe.tier is Tier.T0
    assert observe.observe_only is True


def test_detect_is_order_independent_and_accepts_nested_reachability() -> None:
    items = (
        entitlement("zara", resource="z", scope=Scope.READ),
        entitlement("anna", resource="a", scope=Scope.WRITE),
        entitlement("mike", resource="m", scope=Scope.ADMIN),
    )
    identities = [
        Identity("zara", "staff"),
        Identity("anna", "staff"),
        Identity("mike", "staff"),
    ]
    kwargs = dict(
        identities=identities,
        templates={"staff": RoleTemplate("staff", ("read", "write"))},
        reachability={"github": {"z": 1, "a": 2, "m": 3}},
        evaluated_at=NOW,
    )
    first = detect(items, **kwargs)
    second = detect(tuple(reversed(items)), **kwargs)
    assert first == second
    assert [item.entitlement.resource for item in first] == ["m", "a", "z"]
    assert detect(items, **kwargs) == first


def test_detect_handles_mapping_sequence_and_scalar_boundaries() -> None:
    item = entitlement("custom")
    custom_identity = SimpleNamespace(
        identity_id="custom",
        role="custom-role",
        oncall=False,
        groups="staff",
        incident_tags=4,
    )
    assert (
        detect(
            [item],
            [custom_identity],
            {"custom-role": {"scopes": ["read"]}},
            None,
            NOW,
        )[0].m
        == 0
    )
    assert detect([item], {"other": {}}, {}, {}, NOW)[0].m == MAX_BP
    assert detect([item], {"oncall": False}, {"scopes": ["read"]}, {"github#acme/repo": True}, NOW)
    assert detect([item], {"custom": {}}, {"custom": {"scopes": ["read"]}}, {"acme/repo": "a"}, NOW)
    assert detect([item], (), RoleTemplate(scopes=("read",)), {"github": {"acme/repo": 2}}, NOW)


def test_risk_is_monotone_and_bounded() -> None:
    factors = [0, 1, 2_999, 6_000, 10_000]
    for position in range(4):
        previous = -1
        for value in factors:
            current_factors = [0, 0, 0, 0]
            current_factors[position] = value
            current = risk(*current_factors)
            assert current >= previous
            assert 0 <= current <= MAX_BP
            previous = current


@pytest.mark.determinism
def test_detect_is_byte_identical_for_100_runs() -> None:
    items = (
        entitlement("seed-a", resource="resource-a", scope=Scope.ADMIN),
        entitlement("seed-b", resource="resource-b", scope=Scope.WRITE, last_used_at=None),
    )
    expected = detect(
        items,
        {"seed-a": {"role": "role"}, "seed-b": {"role": "role"}},
        {"role": {"scopes": ["read"]}},
        {("github", "resource-a"): 4, ("github", "resource-b"): 5},
        NOW,
    )
    for _ in range(100):
        assert (
            detect(
                items,
                {"seed-a": {"role": "role"}, "seed-b": {"role": "role"}},
                {"role": {"scopes": ["read"]}},
                {("github", "resource-a"): 4, ("github", "resource-b"): 5},
                NOW,
            )
            == expected
        )


def test_engine_has_no_forbidden_imports() -> None:
    forbidden = {"providers", "graph", "boto3", "time", "random", "math"}
    engine_dir = Path(__file__).parents[2] / "src" / "deadbolt" / "engine"
    for source_path in engine_dir.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = {node.module.split(".")[0]}
            else:
                continue
            assert not imported & forbidden, (source_path, imported & forbidden)
