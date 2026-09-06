"""Deadbolt MCP server with safe analysis and authenticated GitHub IAM operations.

The core Deadbolt service selects the redacted GitHub capture when ``DEADBOLT_CAPTURE_DIR`` is
configured and otherwise uses the offline fixture scenario. The authenticated Lambda adapter
injects a per-operator GitHub client from SSM for the explicit GitHub IAM tools.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from collections.abc import Mapping
from typing import Final, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from deadbolt.api import DemoApi
from deadbolt.plan.canonical import canonical_dumps
from deadbolt.providers.github import GitHubProvider

_DEFAULT_HOST: Final[str] = "127.0.0.1"
_DEFAULT_PORT: Final[int] = 8001
_MAX_PAGE_SIZE: Final[int] = 100
_TIERS: Final[frozenset[str]] = frozenset({"T0", "T1", "T2", "T3"})
_DECISION_ACTIONS = Literal["Defer 30 days", "Keep, with reason"]
_JsonObject = dict[str, object]

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_DEMO_ACTION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


def _safe_finding(value: _JsonObject) -> _JsonObject:
    """Return a finding without provider raw payloads."""
    safe = dict(value)
    entitlement = value.get("entitlement")
    if isinstance(entitlement, Mapping):
        safe_entitlement: _JsonObject = {str(key): item for key, item in entitlement.items()}
        safe_entitlement.pop("raw", None)
        safe["entitlement"] = safe_entitlement
    return safe


def _safe_findings(values: list[_JsonObject]) -> list[_JsonObject]:
    return [_safe_finding(value) for value in values]


def _page_size(value: int) -> int:
    if value < 1 or value > _MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {_MAX_PAGE_SIZE}")
    return value


class DeadboltMcp:
    """Typed tool handlers backed by one in-memory demo service."""

    def __init__(
        self,
        service: DemoApi | None = None,
        github_provider: GitHubProvider | None = None,
    ) -> None:
        self.service = service or DemoApi(capture_dir=os.environ.get("DEADBOLT_CAPTURE_DIR"))
        self.mode = "captured" if os.environ.get("DEADBOLT_CAPTURE_DIR") else "fixture"
        self.github = github_provider

    def _github_required(self) -> GitHubProvider:
        if self.github is None:
            raise ValueError("configure GitHub on the dashboard before using GitHub IAM tools")
        return self.github

    @staticmethod
    def _safe_record(value: Mapping[str, object]) -> _JsonObject:
        allowed = (
            "login",
            "name",
            "full_name",
            "email",
            "permission",
            "role",
            "state",
            "slug",
            "id",
        )
        return {key: value[key] for key in allowed if key in value}

    def github_inventory(self) -> _JsonObject:
        provider = self._github_required()
        members = provider.organization_members()
        repositories = provider.repositories()
        teams = provider.teams()
        return {
            "organization": provider.org,
            "members": [self._safe_record(item) for item in members],
            "repositories": [self._safe_record(item) for item in repositories],
            "teams": [self._safe_record(item) for item in teams],
        }

    def github_user_access(self, username: str) -> _JsonObject:
        provider = self._github_required()
        records = [
            {
                "identity_id": record.identity_id,
                "resource": record.resource,
                "scope": record.scope.value,
                "credential_type": record.credential_type.value,
                "revocable": record.revocable,
            }
            for record in provider.snapshot()
            if record.identity_id == username
        ]
        return {
            "organization": provider.org,
            "username": username,
            "membership": self._safe_record(provider.membership(username)),
            "access": records,
        }

    @staticmethod
    def _onboarding_plan(  # noqa: PLR0913, PLR0917 — plan inputs are explicit IAM fields.
        provider: GitHubProvider,
        username: str,
        repositories: list[str],
        permission: str,
        organization_role: str,
        teams: list[str],
    ) -> tuple[_JsonObject, str]:
        if not username or "/" in username or any(char.isspace() for char in username):
            raise ValueError("username must be a GitHub login")
        if permission not in {"pull", "triage", "push", "maintain", "admin"}:
            raise ValueError("unsupported repository permission")
        if organization_role not in {"member", "admin"}:
            raise ValueError("organization_role must be member or admin")
        if not repositories:
            raise ValueError("at least one repository is required")
        if any("/" not in repo or repo.count("/") != 1 for repo in repositories):
            raise ValueError("repositories must use owner/name format")
        body: _JsonObject = {
            "operation": "github_onboard_user",
            "organization": provider.org,
            "username": username,
            "organization_role": organization_role,
            "repositories": sorted(repositories),
            "permission": permission,
            "teams": sorted(teams),
        }
        return body, hashlib.sha256(canonical_dumps(body)).hexdigest()

    def github_onboard_user(  # noqa: PLR0913, PLR0917 — MCP exposes each approval input explicitly.
        self,
        username: str,
        repositories: list[str],
        permission: str,
        organization_role: str,
        teams: list[str],
        expected_plan_hash: str,
        confirm: bool,
    ) -> _JsonObject:
        provider = self._github_required()
        body, plan_hash = self._onboarding_plan(
            provider, username, repositories, permission, organization_role, teams
        )
        if not confirm:
            return {"status": "preview", "plan": body, "plan_hash": plan_hash}
        if expected_plan_hash != plan_hash:
            raise ValueError("plan hash is stale; preview the GitHub onboarding plan again")
        membership = provider.invite_member(username, role=organization_role)
        repository_results = [
            {
                "repository": repo,
                "permission": permission,
                "result": self._safe_record(
                    provider.set_repository_access(
                        repo.split("/", 1)[0], repo.split("/", 1)[1], username, permission
                    )
                ),
            }
            for repo in repositories
        ]
        team_results = [
            {
                "team": team,
                "result": self._safe_record(provider.set_team_membership(team, username)),
            }
            for team in teams
        ]
        return {
            "status": "applied",
            "organization": provider.org,
            "username": username,
            "plan_hash": plan_hash,
            "membership": self._safe_record(membership),
            "repositories": repository_results,
            "teams": team_results,
        }

    def github_remove_repository_access(
        self,
        username: str,
        repository: str,
        expected_plan_hash: str,
        confirm: bool,
    ) -> _JsonObject:
        provider = self._github_required()
        if repository.count("/") != 1:
            raise ValueError("repository must use owner/name format")
        body = {
            "operation": "github_remove_repository_access",
            "username": username,
            "repository": repository,
        }
        plan_hash = hashlib.sha256(canonical_dumps(body)).hexdigest()
        if not confirm:
            return {"status": "preview", "plan": body, "plan_hash": plan_hash}
        if expected_plan_hash != plan_hash:
            raise ValueError("plan hash is stale; preview the removal plan again")
        provider.remove_repository_access(
            repository.split("/", 1)[0], repository.split("/", 1)[1], username
        )
        return {"status": "applied", "plan_hash": plan_hash, **body}

    def github_remove_team_access(
        self,
        username: str,
        team_slug: str,
        expected_plan_hash: str,
        confirm: bool,
    ) -> _JsonObject:
        provider = self._github_required()
        body = {"operation": "github_remove_team_access", "username": username, "team": team_slug}
        plan_hash = hashlib.sha256(canonical_dumps(body)).hexdigest()
        if not confirm:
            return {"status": "preview", "plan": body, "plan_hash": plan_hash}
        if expected_plan_hash != plan_hash:
            raise ValueError("plan hash is stale; preview the team removal plan again")
        provider.remove_team_membership(team_slug, username)
        return {"status": "applied", "plan_hash": plan_hash, **body}

    def github_remove_organization_member(
        self,
        username: str,
        expected_plan_hash: str,
        confirm: bool,
    ) -> _JsonObject:
        provider = self._github_required()
        body = {"operation": "github_remove_organization_member", "username": username}
        plan_hash = hashlib.sha256(canonical_dumps(body)).hexdigest()
        if not confirm:
            return {"status": "preview", "plan": body, "plan_hash": plan_hash}
        if expected_plan_hash != plan_hash:
            raise ValueError("plan hash is stale; preview the membership removal plan again")
        provider.remove_member(username)
        return {"status": "applied", "plan_hash": plan_hash, **body}

    def list_findings(self, tier: str | None, limit: int) -> _JsonObject:
        page_size = _page_size(limit)
        if tier is not None and tier not in _TIERS:
            raise ValueError("tier must be one of T0, T1, T2, or T3")
        findings = self.service.findings()
        if tier is not None:
            findings = [finding for finding in findings if finding.get("tier") == tier]
        findings = findings[:page_size]
        return {"findings": _safe_findings(findings), "count": len(findings), "mode": self.mode}

    def get_finding(self, finding_id: str) -> _JsonObject:
        return _safe_finding(self.service.finding(finding_id))

    def get_plan(self, finding_id: str) -> _JsonObject:
        return self.service.plan(finding_id)

    def get_audit_log(self, limit: int) -> _JsonObject:
        entries = self.service.audit()[: _page_size(limit)]
        return {"entries": entries, "count": len(entries), "mode": self.mode}

    def get_metrics(self) -> _JsonObject:
        return self.service.metrics()

    def rerun_drift(self, finding_id: str) -> _JsonObject:
        plan_hash = self.service.rerun(finding_id)
        return {"finding_id": finding_id, "plan_hash": plan_hash, "mode": self.mode}

    def _check_plan_hash(self, finding_id: str, expected_plan_hash: str) -> None:
        current_plan_hash = self.service.plan(finding_id)["hash"]
        if current_plan_hash != expected_plan_hash:
            raise ValueError("plan hash is stale; fetch the plan again before acting")

    def approve_finding(
        self,
        finding_id: str,
        approver: str,
        expected_plan_hash: str,
        confirm: bool,
    ) -> _JsonObject:
        if not confirm:
            raise ValueError("approval requires confirm=true")
        self._check_plan_hash(finding_id, expected_plan_hash)
        return _safe_finding(self.service.decision(finding_id, "Approve", approver))

    def reduce_access(
        self,
        finding_id: str,
        approver: str,
        expected_plan_hash: str,
        confirm: bool,
        reason: str,
    ) -> _JsonObject:
        if not confirm:
            raise ValueError("access reduction requires confirm=true")
        self._check_plan_hash(finding_id, expected_plan_hash)
        return _safe_finding(self.service.decision(finding_id, "Reduce further", approver, reason))

    def record_non_mutating_decision(
        self,
        finding_id: str,
        action: _DECISION_ACTIONS,
        approver: str,
        reason: str,
    ) -> _JsonObject:
        return _safe_finding(self.service.decision(finding_id, action, approver, reason))

    def rollback_finding(
        self,
        finding_id: str,
        expected_plan_hash: str,
        confirm: bool,
    ) -> _JsonObject:
        if not confirm:
            raise ValueError("rollback requires confirm=true")
        self._check_plan_hash(finding_id, expected_plan_hash)
        return _safe_finding(self.service.rollback(finding_id))


def build_server(
    service: DemoApi | None = None,
    *,
    github_provider: GitHubProvider | None = None,
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    public_http: bool = False,
) -> FastMCP[None]:
    """Build the Deadbolt MCP server with a testable service boundary."""
    handlers = DeadboltMcp(service, github_provider)
    server = FastMCP(
        "deadbolt",
        instructions=(
            "Deadbolt inspects access drift and can perform authenticated GitHub IAM operations. "
            "Inspect inventory and preview an onboarding/removal plan before applying it. Every "
            "GitHub write requires explicit confirm=true and the current preview plan hash. "
            "Other Deadbolt demo actions remain fixture-backed and do not mutate providers."
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=not public_http,
        ),
    )

    @server.tool(
        name="list_findings",
        title="List access findings",
        description="List detected entitlement drift findings from the current Deadbolt demo scan.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def list_findings(tier: str | None = None, limit: int = 20) -> _JsonObject:
        return handlers.list_findings(tier, limit)

    @server.tool(
        name="get_finding",
        title="Get an access finding",
        description="Get one finding, including score evidence and its current workflow stage.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_finding(finding_id: str) -> _JsonObject:
        return handlers.get_finding(finding_id)

    @server.tool(
        name="get_plan",
        title="Get a remediation plan",
        description="Get the deterministic remediation plan and hash for one finding.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_plan(finding_id: str) -> _JsonObject:
        return handlers.get_plan(finding_id)

    @server.tool(
        name="get_audit_log",
        title="Get the audit log",
        description="Read the newest Deadbolt demo audit entries.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_audit_log(limit: int = 20) -> _JsonObject:
        return handlers.get_audit_log(limit)

    @server.tool(
        name="get_metrics",
        title="Get demo metrics",
        description="Read deterministic recall, false-revocation, rollback, and cost metrics.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_metrics() -> _JsonObject:
        return handlers.get_metrics()

    @server.tool(
        name="github_inventory",
        title="Inspect GitHub IAM inventory",
        description="List the connected GitHub organization members, repositories, and teams.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def github_inventory() -> _JsonObject:
        return handlers.github_inventory()

    @server.tool(
        name="github_user_access",
        title="Inspect GitHub user access",
        description=(
            "Inspect one GitHub user's organization membership and current access findings."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def github_user_access(username: str) -> _JsonObject:
        return handlers.github_user_access(username)

    @server.tool(
        name="github_onboard_user",
        title="Onboard a GitHub user",
        description=(
            "Preview or apply organization membership, repository roles, and team membership "
            "for one GitHub user. Applying requires confirm=true and the preview plan hash."
        ),
        annotations=_DEMO_ACTION,
        structured_output=True,
    )
    def github_onboard_user(  # noqa: PLR0913, PLR0917 — tool arguments are the approval contract.
        username: str,
        repositories: list[str],
        permission: str = "pull",
        organization_role: str = "member",
        teams: list[str] | None = None,
        expected_plan_hash: str = "",
        confirm: bool = False,
    ) -> _JsonObject:
        return handlers.github_onboard_user(
            username,
            repositories,
            permission,
            organization_role,
            teams or [],
            expected_plan_hash,
            confirm,
        )

    @server.tool(
        name="github_remove_repository_access",
        title="Remove GitHub repository access",
        description=(
            "Preview or remove one user's direct repository access. Applying requires "
            "confirm=true and the preview plan hash."
        ),
        annotations=_DEMO_ACTION,
        structured_output=True,
    )
    def github_remove_repository_access(
        username: str,
        repository: str,
        expected_plan_hash: str = "",
        confirm: bool = False,
    ) -> _JsonObject:
        return handlers.github_remove_repository_access(
            username, repository, expected_plan_hash, confirm
        )

    @server.tool(
        name="github_remove_team_access",
        title="Remove GitHub team access",
        description=(
            "Preview or remove a user from a GitHub team. Applying requires confirm=true "
            "and the preview plan hash."
        ),
        annotations=_DEMO_ACTION,
        structured_output=True,
    )
    def github_remove_team_access(
        username: str,
        team_slug: str,
        expected_plan_hash: str = "",
        confirm: bool = False,
    ) -> _JsonObject:
        return handlers.github_remove_team_access(username, team_slug, expected_plan_hash, confirm)

    @server.tool(
        name="github_remove_organization_member",
        title="Remove GitHub organization member",
        description=(
            "Preview or remove a user from the GitHub organization. Applying requires "
            "confirm=true and the preview plan hash."
        ),
        annotations=_DEMO_ACTION,
        structured_output=True,
    )
    def github_remove_organization_member(
        username: str,
        expected_plan_hash: str = "",
        confirm: bool = False,
    ) -> _JsonObject:
        return handlers.github_remove_organization_member(username, expected_plan_hash, confirm)

    @server.tool(
        name="rerun_drift",
        title="Re-run drift detection",
        description="Re-run the deterministic demo engine for a finding and return its plan hash.",
        annotations=_DEMO_ACTION,
        structured_output=True,
    )
    def rerun_drift(finding_id: str) -> _JsonObject:
        return handlers.rerun_drift(finding_id)

    @server.tool(
        name="approve_finding",
        title="Approve a demo plan",
        description=(
            "Mark a fixture-backed finding approved. This is a demo-only state change and does "
            "not mutate AWS or another provider. Require the user to explicitly confirm it."
        ),
        annotations=_DEMO_ACTION,
        structured_output=True,
    )
    def approve_finding(
        finding_id: str,
        approver: str,
        expected_plan_hash: str,
        confirm: bool = False,
    ) -> _JsonObject:
        return handlers.approve_finding(finding_id, approver, expected_plan_hash, confirm)

    @server.tool(
        name="reduce_access",
        title="Reduce demo access",
        description=(
            "Apply the broker's validated narrower-scope decision in the fixture-backed demo. "
            "This does not mutate AWS or another provider; require explicit confirmation."
        ),
        annotations=_DEMO_ACTION,
        structured_output=True,
    )
    def reduce_access(
        finding_id: str,
        approver: str,
        expected_plan_hash: str,
        confirm: bool = False,
        reason: str = "",
    ) -> _JsonObject:
        return handlers.reduce_access(finding_id, approver, expected_plan_hash, confirm, reason)

    @server.tool(
        name="record_decision",
        title="Record a non-mutating decision",
        description="Record a defer or keep-with-reason decision for a demo finding.",
        annotations=_DEMO_ACTION,
        structured_output=True,
    )
    def record_decision(
        finding_id: str,
        action: _DECISION_ACTIONS,
        approver: str,
        reason: str = "",
    ) -> _JsonObject:
        return handlers.record_non_mutating_decision(finding_id, action, approver, reason)

    @server.tool(
        name="rollback_finding",
        title="Rollback a demo finding",
        description=(
            "Roll back a fixture-backed finding after checking the current plan hash. This is a "
            "demo-only state change and does not mutate AWS or another provider."
        ),
        annotations=_DEMO_ACTION,
        structured_output=True,
    )
    def rollback_finding(
        finding_id: str,
        expected_plan_hash: str,
        confirm: bool = False,
    ) -> _JsonObject:
        return handlers.rollback_finding(finding_id, expected_plan_hash, confirm)

    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixture-backed Deadbolt MCP server")
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    args = parser.parse_args()
    build_server(host=args.host, port=args.port).run(transport="streamable-http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DeadboltMcp", "build_server", "main"]
