"""Small terminal path for snapshot, detection, planning, execution, and rollback rehearsal."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from deadbolt.contracts.models import ActionResult, Entitlement
from deadbolt.contracts.provider import EntitlementProvider
from deadbolt.engine.drift import Finding, detect
from deadbolt.plan.builder import Plan, build
from deadbolt.plan.canonical import canonical_dumps
from deadbolt.providers.fixtures.salesforce import SalesforceFixtureProvider
from deadbolt.providers.fixtures.workday import WorkdayFixtureProvider
from deadbolt.providers.github import GitHubProvider
from deadbolt.providers.registry import build_providers


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m deadbolt.cli")
    parser.add_argument("--dry-run", action="store_true", help="never perform provider writes")
    parser.add_argument(
        "--system",
        default="fixtures",
        choices=("fixtures", "aws-iam", "github"),
        help="provider set used by the rehearsal (default: fixtures)",
    )
    parser.add_argument("--github-org", default=None)
    parser.add_argument("--github-repo", action="append", default=[])
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "detect", "plan", "execute", "rollback"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
        sub.add_argument(
            "--system",
            choices=("fixtures", "aws-iam", "github"),
            default=argparse.SUPPRESS,
        )
        sub.add_argument("--github-org", default=argparse.SUPPRESS)
        sub.add_argument("--github-repo", action="append", default=argparse.SUPPRESS)
    return parser


def _providers(args: argparse.Namespace) -> tuple[EntitlementProvider, ...]:
    if args.system == "fixtures":
        seed_dir = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "seed"
        return (
            SalesforceFixtureProvider(seed_dir / "salesforce.json"),
            WorkdayFixtureProvider(seed_dir / "workday.json"),
        )
    config = {args.system: "real"}
    factories = None
    if args.system == "github":
        factories = {
            "github": lambda: GitHubProvider(
                args.github_org,
                repos=tuple(args.github_repo),
            )
        }
    return build_providers(config, factories=factories)


def _entitlements(providers: Iterable[EntitlementProvider]) -> tuple[Entitlement, ...]:
    result: list[Entitlement] = []
    for provider in providers:
        snapshot = provider.snapshot()
        result.extend(item for item in snapshot if isinstance(item, Entitlement))
    return tuple(result)


def _entitlement_wire(entitlement: Entitlement) -> dict[str, object]:
    return {
        "identity_id": entitlement.identity_id,
        "system": entitlement.system,
        "resource": entitlement.resource,
        "scope": entitlement.scope,
        "granted_at": entitlement.granted_at,
        "last_used_at": entitlement.last_used_at,
        "credential_type": entitlement.credential_type,
        "revocable": entitlement.revocable,
        "raw": entitlement.raw,
    }


def _finding_wire(finding: Finding) -> dict[str, object]:
    entitlement = finding.entitlement
    finding_id = "|".join(
        (
            entitlement.identity_id,
            entitlement.system,
            entitlement.resource,
            entitlement.scope.value,
        )
    )
    return {
        "finding_id": finding_id,
        "identity_id": entitlement.identity_id,
        "system": entitlement.system,
        "resource": entitlement.resource,
        "scope": entitlement.scope,
        "score": finding.score,
        "tier": finding.tier,
        "evidence": finding.evidence,
        "observe_only": finding.observe_only,
    }


def _plan_wire(plan: Plan) -> dict[str, object]:
    return {
        "body": plan.body,
        "envelope": plan.envelope,
        "plan_hash": plan.plan_hash,
        "actions": [action.as_dict() for action in plan.actions],
    }


def _result_wire(result: ActionResult) -> dict[str, object]:
    return {
        "ok": result.ok,
        "system": result.system,
        "resource": result.resource,
        "scope": result.scope,
        "dry_run": result.dry_run,
        "pre_image": result.pre_image,
        "message": result.message,
        "provider_latency_ms": result.provider_latency_ms,
    }


def _emit(value: object) -> None:
    print(canonical_dumps(value).decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    providers = _providers(args)
    entitlements = _entitlements(providers)
    if args.command == "snapshot":
        _emit(
            {
                "dry_run": args.dry_run,
                "entitlements": [_entitlement_wire(item) for item in entitlements],
            }
        )
        return 0

    evaluated_at = datetime.now(UTC)
    findings = detect(entitlements, {}, {}, {}, evaluated_at)
    if args.command == "detect":
        _emit({"dry_run": args.dry_run, "findings": [_finding_wire(item) for item in findings]})
        return 0
    plan = build(findings, evaluated_at, "cli-v1")
    if args.command == "plan":
        _emit({"dry_run": args.dry_run, **_plan_wire(plan)})
        return 0
    if args.command == "execute":
        results: list[object] = []
        for action in plan.actions:
            for provider in providers:
                if getattr(provider, "system", None) != action.system:
                    continue
                target = next(
                    (
                        item
                        for item in entitlements
                        if item.system == action.system
                        and item.resource == action.resource
                        and item.scope.value == action.scope
                    ),
                    None,
                )
                if target is not None:
                    results.append(provider.revoke(target, dry_run=args.dry_run))
        _emit(
            {
                "dry_run": args.dry_run,
                "plan_hash": plan.plan_hash,
                "results": [
                    _result_wire(result) for result in results if isinstance(result, ActionResult)
                ],
            }
        )
        return 0
    # A standalone rollback has no durable plan store.  It remains useful in rehearsal mode as
    # an explicit, side-effect-free acknowledgement of that boundary.
    _emit({"dry_run": args.dry_run, "ok": True, "message": "rollback requires a persisted plan id"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
