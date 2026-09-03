"""GitHub Enterprise Cloud/Server connector.

The client deliberately models the Enterprise-only governance surfaces while keeping the
normalised output identical to the existing GitHub connector.  A missing endpoint is a
capability degradation, not a provider-wide failure.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx

from deadbolt.contracts.models import ActionResult, CredentialType, Entitlement, Scope
from deadbolt.errors import ProviderError


class _Client(Protocol):
    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response: ...


_NEXT = re.compile(r"<([^>]+)>;\s*rel=\"next\"")
_HTTP_BAD_REQUEST = 400
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_METHOD_NOT_ALLOWED = 405


def _text(value: object, default: str = "") -> str:
    return str(value) if isinstance(value, (int, str)) else default


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def _scope(value: object) -> Scope:
    text = _text(value).lower()
    if text in {"owner", "admin", "admin:org"}:
        return Scope.ADMIN
    if text in {"push", "maintain", "triage", "write"}:
        return Scope.WRITE
    return Scope.READ


def _items(payload: object, keys: tuple[str, ...]) -> tuple[Mapping[str, object], ...]:
    if isinstance(payload, list):
        return tuple(item for item in payload if isinstance(item, Mapping))
    if isinstance(payload, Mapping):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return tuple(item for item in value if isinstance(item, Mapping))
        return (payload,)
    raise ProviderError("GitHub Enterprise returned a non-object/list payload")


class GitHubEnterpriseProvider:
    """Capability-aware GitHub Enterprise provider for Cloud and Server."""

    system = "github"

    def __init__(  # noqa: PLR0913 — connection and capability knobs are explicit.
        self,
        org: str | None = None,
        token: str | None = None,
        *,
        api_base: str = "https://api.github.com",
        graphql_base: str = "https://api.github.com/graphql",
        enterprise_slug: str = "",
        repos: Sequence[str] = (),
        client: object | None = None,
        probe: bool = True,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.org = org or os.environ.get("GITHUB_ENTERPRISE_ORG", os.environ.get("GITHUB_ORG", ""))
        self.token = token or os.environ.get(
            "GITHUB_ENTERPRISE_TOKEN", os.environ.get("GITHUB_TOKEN", "")
        )
        self.api_base = api_base.rstrip("/")
        self.graphql_base = graphql_base.rstrip("/")
        self.enterprise_slug = enterprise_slug or os.environ.get("GITHUB_ENTERPRISE_SLUG", "")
        self.repos = tuple(repos)
        self._client = cast(_Client, client if client is not None else httpx.Client())
        self._sleep = sleeper or time.sleep
        self.capabilities: dict[str, bool] = {}
        if probe and self.token and self.org:
            self.capabilities = self.probe_capabilities()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
        }

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.api_base}{path}"
        response = self._client.request(method, url, headers=self._headers, **kwargs)
        remaining = response.headers.get("X-RateLimit-Remaining")
        if response.status_code == _HTTP_FORBIDDEN and remaining == "0":
            reset = response.headers.get("X-RateLimit-Reset", "unknown")
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.1
            self._sleep(delay)
            raise ProviderError(f"GitHub Enterprise rate limit exhausted; reset at {reset}")
        if response.status_code >= _HTTP_BAD_REQUEST:
            raise ProviderError(
                f"GitHub Enterprise {method} {url} failed with HTTP {response.status_code}"
            )
        return response

    def _optional(self, path: str) -> tuple[Mapping[str, object], ...]:
        response = self._client.request("GET", f"{self.api_base}{path}", headers=self._headers)
        if response.status_code in {
            _HTTP_FORBIDDEN,
            _HTTP_NOT_FOUND,
            _HTTP_METHOD_NOT_ALLOWED,
        }:
            return ()
        if response.status_code >= _HTTP_BAD_REQUEST:
            raise ProviderError(
                f"GitHub Enterprise capability probe failed with HTTP {response.status_code}"
            )
        return _items(
            response.json(), ("items", "data", "users", "tokens", "credential_authorizations")
        )

    def probe_capabilities(self) -> dict[str, bool]:
        """Probe each Enterprise surface independently and record served endpoints."""
        endpoints = {
            "personal_access_tokens": f"/orgs/{self.org}/personal-access-tokens",
            "credential_authorizations": f"/orgs/{self.org}/credential-authorizations",
            "installations": f"/orgs/{self.org}/installations",
            "organization_roles": f"/orgs/{self.org}/organization-roles",
            "audit_log": f"/orgs/{self.org}/audit-log",
        }
        result: dict[str, bool] = {}
        for name, path in endpoints.items():
            response = self._client.request("GET", f"{self.api_base}{path}", headers=self._headers)
            result[name] = response.status_code < _HTTP_BAD_REQUEST
        if self.enterprise_slug:
            response = self._client.request(
                "GET",
                f"{self.api_base}/scim/v2/enterprises/{self.enterprise_slug}/Users",
                headers=self._headers,
            )
            result["scim"] = response.status_code < _HTTP_BAD_REQUEST
        return result

    def _paged(
        self, path: str, keys: tuple[str, ...] = ("items", "users", "tokens")
    ) -> tuple[Mapping[str, object], ...]:
        result: list[Mapping[str, object]] = []
        next_path: str | None = path
        while next_path:
            response = self._request("GET", next_path)
            payload = response.json()
            result.extend(_items(payload, keys))
            match = _NEXT.search(response.headers.get("Link", ""))
            next_path = match.group(1) if match else None
        return tuple(result)

    def snapshot(self) -> tuple[Entitlement, ...]:
        if not self.org:
            raise ProviderError("GitHub Enterprise organization is required")
        findings: list[Entitlement] = []
        pats = self._paged(f"/orgs/{self.org}/personal-access-tokens", ("tokens", "items"))
        for pat in pats:
            owner_value = pat.get("owner")
            owner: Mapping[str, object] = owner_value if isinstance(owner_value, Mapping) else pat
            identity = _text(owner.get("email"), _text(owner.get("login"), _text(pat.get("login"))))
            pat_id = _text(pat.get("id"))
            if not identity or not pat_id:
                continue
            findings.append(
                Entitlement(
                    identity.lower(),
                    self.system,
                    f"github:pat:{pat_id}",
                    _scope(pat.get("permission", pat.get("scopes"))),
                    _time(pat.get("created_at")),
                    _time(pat.get("last_used_at")),
                    CredentialType.PAT,
                    True,
                    {
                        "kind": "pat",
                        "pat_id": pat_id,
                        "email": identity.lower(),
                        "payload": dict(pat),
                        "reversible": False,
                    },
                )
            )
        authorizations = self._paged(
            f"/orgs/{self.org}/credential-authorizations", ("credential_authorizations", "items")
        )
        for auth in authorizations:
            identity = _text(auth.get("email"), _text(auth.get("login"), _text(auth.get("nameId"))))
            auth_id = _text(auth.get("id"))
            if identity and auth_id:
                findings.append(
                    Entitlement(
                        identity.lower(),
                        self.system,
                        f"github:saml:{auth_id}",
                        Scope.WRITE,
                        _time(auth.get("created_at")),
                        _time(auth.get("last_used_at")),
                        CredentialType.PAT,
                        True,
                        {
                            "kind": "saml-credential",
                            "authorization_id": auth_id,
                            "email": identity.lower(),
                            "payload": dict(auth),
                            "reversible": False,
                        },
                    )
                )
        for repo in self.repos:
            repo_owner, separator, name = repo.partition("/")
            if not separator or not repo_owner or not name:
                raise ProviderError(f"GitHub Enterprise repository must be owner/name: {repo!r}")
            keys = self._paged(f"/repos/{repo_owner}/{name}/keys", ("keys", "items"))
            for key in keys:
                key_id = _text(key.get("id"))
                if key_id:
                    findings.append(
                        Entitlement(
                            _text(key.get("owner"), "deploy-key"),
                            self.system,
                            f"github:deploy-key:{repo_owner}/{name}:{key_id}",
                            Scope.READ if bool(key.get("read_only", True)) else Scope.ADMIN,
                            _time(key.get("created_at")),
                            _time(key.get("last_used_at")),
                            CredentialType.PAT,
                            True,
                            {
                                "kind": "deploy-key",
                                "owner": repo_owner,
                                "repo": name,
                                "key_id": key_id,
                                "payload": dict(key),
                                "reversible": True,
                            },
                        )
                    )
        return tuple(sorted(findings, key=lambda e: (e.identity_id, e.resource, e.scope.value)))

    @staticmethod
    def _result(
        e: Entitlement, dry_run: bool, pre: Mapping[str, object], message: str
    ) -> ActionResult:
        return ActionResult(True, "github", e.resource, e.scope.value, dry_run, pre, message, 0)

    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        raw = dict(e.raw)
        kind = _text(raw.get("kind"))
        pre = {
            "provider": "github-enterprise",
            "system": "github",
            **raw,
            "resource": e.resource,
            "scope": e.scope.value,
        }
        if dry_run:
            return self._result(e, True, pre, f"dry-run: GitHub Enterprise {kind} would be revoked")
        if kind == "pat":
            self._request("DELETE", f"/orgs/{self.org}/personal-access-tokens/{raw.get('pat_id')}")
        elif kind == "saml-credential":
            self._request(
                "DELETE",
                f"/orgs/{self.org}/credential-authorizations/{raw.get('authorization_id')}",
            )
        elif kind == "deploy-key":
            self._request(
                "DELETE", f"/repos/{raw.get('owner')}/{raw.get('repo')}/keys/{raw.get('key_id')}"
            )
        else:
            raise ProviderError("unsupported GitHub Enterprise entitlement kind")
        return self._result(e, False, pre, f"GitHub Enterprise {kind} revoked")

    def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
        kind = _text(pre_image.get("kind"))
        if kind in {"pat", "saml-credential"}:
            raise ProviderError(f"GitHub Enterprise {kind} revocation is irreversible")
        if kind != "deploy-key":
            raise ProviderError("invalid GitHub Enterprise pre-image")
        owner, repo = _text(pre_image.get("owner")), _text(pre_image.get("repo"))
        payload = pre_image.get("payload")
        if not owner or not repo or not isinstance(payload, Mapping):
            raise ProviderError("deploy-key pre-image is incomplete")
        self._request("POST", f"/repos/{owner}/{repo}/keys", json=dict(payload))
        return ActionResult(
            True,
            "github",
            _text(pre_image.get("resource")),
            _text(pre_image.get("scope")),
            False,
            dict(pre_image),
            "deploy key restored",
            0,
        )


GitHubEnterprise = GitHubEnterpriseProvider

__all__ = ["GitHubEnterprise", "GitHubEnterpriseProvider"]
