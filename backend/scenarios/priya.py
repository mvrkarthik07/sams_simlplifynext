"""The Priya mover/leaver demonstration scenario.

The checked-in scenario is deliberately fixture-backed so ``make demo-run`` is safe to
repeat and never needs credentials.  The manifest is also the contract for the recall
calculation: a finding counts as planted only when its complete entitlement key occurs in
``manifest.json``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from deadbolt.contracts.models import Entitlement
from deadbolt.contracts.provider import EntitlementProvider
from deadbolt.engine.drift import Finding, detect
from deadbolt.engine.scoring import RoleTemplate
from deadbolt.plan.builder import Plan, build
from deadbolt.providers.fixtures._base import FixtureProvider

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "scenario"
MANIFEST_PATH = SCENARIO_DIR / "manifest.json"
TEMPLATE_VERSION = "priya-v1"
DEFAULT_EVALUATED_AT = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
MOVER_ID = "priya@example.com"
LEAVER_ID = "leaver@example.com"
PLANTED_FINDING_COUNT = 20
TIER_A_SYSTEMS = ("aws-iam", "github", "slack", "notion")
TIER_B_SYSTEMS = ("salesforce", "workday")
SCENARIO_SYSTEMS = TIER_A_SYSTEMS + TIER_B_SYSTEMS


@dataclass(frozen=True, slots=True)
class ScenarioFinding:
    """The stable four-part key used by the planted-finding manifest."""

    identity_id: str
    system: str
    resource: str
    scope: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.identity_id, self.system, self.resource, self.scope

    def as_dict(self) -> dict[str, str]:
        return {
            "identity_id": self.identity_id,
            "system": self.system,
            "resource": self.resource,
            "scope": self.scope,
        }


@dataclass(frozen=True, slots=True)
class Scenario:
    """All pure inputs and provider handles for one deterministic demo run."""

    providers: tuple[EntitlementProvider, ...]
    identities: Mapping[str, Mapping[str, object]]
    templates: Mapping[str, RoleTemplate]
    reachability: Mapping[tuple[str, str], int]
    expected_findings: tuple[ScenarioFinding, ...]
    ratified_entitlements: tuple[ScenarioFinding, ...]
    mover_id: str = MOVER_ID
    leaver_id: str = LEAVER_ID
    hr_event_at: datetime = DEFAULT_EVALUATED_AT

    def entitlements(self) -> tuple[Entitlement, ...]:
        """Return a stable fan-out snapshot without coupling the engine to providers."""
        items: list[Entitlement] = []
        for provider in self.providers:
            items.extend(provider.snapshot())
        return tuple(
            sorted(
                items,
                key=lambda item: (
                    item.identity_id.encode("utf-8"),
                    item.system.encode("utf-8"),
                    item.resource.encode("utf-8"),
                    item.scope.value.encode("utf-8"),
                ),
            )
        )

    def findings(self, evaluated_at: datetime = DEFAULT_EVALUATED_AT) -> tuple[Finding, ...]:
        """Run the pure detector over this scenario."""
        return detect(
            self.entitlements(),
            self.identities,
            self.templates,
            self.reachability,
            evaluated_at,
        )

    def plan(self, evaluated_at: datetime = DEFAULT_EVALUATED_AT) -> Plan:
        """Build the immutable plan for this scenario."""
        return build(self.findings(evaluated_at), evaluated_at, TEMPLATE_VERSION)

    def provider_for(self, system: str) -> EntitlementProvider:
        """Resolve one action target provider by normalized system name."""
        for provider in self.providers:
            if provider.system == system:
                return provider
        raise KeyError(f"scenario has no provider for {system}")


def _manifest_items() -> tuple[ScenarioFinding, ...]:
    decoded: object = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(decoded, Mapping):
        raise ValueError("scenario manifest must be an object")
    raw_items = decoded.get("expected_findings")
    if not isinstance(raw_items, list):
        raise ValueError("scenario manifest expected_findings must be a list")
    result: list[ScenarioFinding] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise ValueError("scenario manifest finding must be an object")
        values = {name: raw.get(name) for name in ("identity_id", "system", "resource", "scope")}
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("scenario manifest finding has invalid fields")
        result.append(ScenarioFinding(**cast(dict[str, str], values)))
    if len(result) != PLANTED_FINDING_COUNT or len({item.key for item in result}) != len(result):
        raise ValueError("scenario manifest must contain exactly 20 unique findings")
    return tuple(result)


EXPECTED_FINDINGS = _manifest_items()
EXPECTED_FINDING_COUNT = len(EXPECTED_FINDINGS)
RATIFIED_TEMPLATE_ENTITLEMENTS = (ScenarioFinding(MOVER_ID, "github", "acme/platform", "read"),)


def _seed_path(system: str, fixture_dir: Path | None) -> Path:
    return (fixture_dir or SCENARIO_DIR) / f"{system}.json"


def build_scenario(*, fixture_dir: str | Path | None = None) -> Scenario:
    """Create fresh, idempotent fixture providers for all six demo systems.

    AWS IAM and GitHub are Tier A in production.  Their real providers are selected by the
    deployment registry and the explicit live rehearsal; the offline scenario uses the same
    provider protocol with local seeds so the deterministic demo has no network dependency.
    """
    seed_directory = Path(fixture_dir) if fixture_dir is not None else None
    providers = tuple(
        FixtureProvider(system, _seed_path(system, seed_directory)) for system in SCENARIO_SYSTEMS
    )
    identities: Mapping[str, Mapping[str, object]] = {
        MOVER_ID: {"identity_id": MOVER_ID, "role": "engineering-manager"},
        LEAVER_ID: {"identity_id": LEAVER_ID, "role": "former-employee"},
    }
    templates = {
        "engineering-manager": RoleTemplate("engineering-manager", ("read",)),
        "former-employee": RoleTemplate("former-employee", ()),
    }
    reachability = {(finding.system, finding.resource): 5 for finding in EXPECTED_FINDINGS}
    # The in-policy entitlement is deliberately reachable but recently used: it must remain
    # present and must never become an action.
    reachability[("github", "acme/platform")] = 1
    return Scenario(
        providers,
        identities,
        templates,
        reachability,
        EXPECTED_FINDINGS,
        RATIFIED_TEMPLATE_ENTITLEMENTS,
    )


__all__ = [
    "DEFAULT_EVALUATED_AT",
    "EXPECTED_FINDINGS",
    "EXPECTED_FINDING_COUNT",
    "LEAVER_ID",
    "MANIFEST_PATH",
    "MOVER_ID",
    "PLANTED_FINDING_COUNT",
    "RATIFIED_TEMPLATE_ENTITLEMENTS",
    "SCENARIO_DIR",
    "SCENARIO_SYSTEMS",
    "TEMPLATE_VERSION",
    "TIER_A_SYSTEMS",
    "TIER_B_SYSTEMS",
    "Scenario",
    "ScenarioFinding",
    "build_scenario",
]
