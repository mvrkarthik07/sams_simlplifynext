"""GitHub Tier-A connector for organization membership and repository access."""

from __future__ import annotations

import os
import re
import secrets
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx

from deadbolt.contracts.models import ActionResult, CredentialType, Entitlement, Scope
from deadbolt.errors import ProviderError


class _HttpClient(Protocol):
    def get(self, url: str, **kwargs: object) -> httpx.Response: ...

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response: ...


_Sleep = Callable[[float], None]
_Jitter = Callable[[], float]
_NEXT_LINK = re.compile(r"<([^>]+)>;\s*rel=\"([^\"]+)\"")
_HTTP_ERROR = 400
_HTTP_NOT_FOUND = 404


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _aware(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    return None


def _scope(permission: str) -> Scope:
    normalized = permission.lower()
    if normalized == "admin":
        return Scope.ADMIN
    if normalized in {"push", "maintain", "triage", "write"}:
        return Scope.WRITE
    return Scope.READ


def _sort_key(item: Entitlement) -> tuple[bytes, bytes, bytes]:
    return (
        item.identity_id.encode("utf-8"),
        item.resource.encode("utf-8"),
        item.scope.value.encode("utf-8"),
    )


def _reset_text(response: httpx.Response) -> str:
    raw = _text(response.headers.get("X-RateLimit-Reset"), "unknown")
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return raw


def _next_link(response: httpx.Response) -> str | None:
    header = _text(response.headers.get("Link"))
    for match in _NEXT_LINK.finditer(header):
        url = cast(str, match.group(1))
        relation = cast(str, match.group(2))
        if relation == "next":
            return url
    return None


class GitHubProvider:
    """GitHub connector with bounded rate-limit retries and complete pagination."""

    system = "github"

    def __init__(  # noqa: PLR0913 — retry and transport knobs are explicit for safe testing.
        self,
        org: str | None = None,
        token: str | None = None,
        *,
        repos: Sequence[str] = (),
        client: object | None = None,
        http_client: object | None = None,
        base_url: str = "https://api.github.com",
        max_retries: int = 3,
        backoff_base: float = 0.25,
        sleeper: _Sleep | None = None,
        jitter: _Jitter | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if client is not None and http_client is not None:
            raise ValueError("provide only one HTTP client")
        self.org = org or os.environ.get("GITHUB_ORG", "")
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.repos = tuple(repos)
        self._client = cast(
            _HttpClient,
            client
            if client is not None
            else http_client
            if http_client is not None
            else httpx.Client(),
        )
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sleep = sleeper or time.sleep
        self._jitter = jitter or secrets.SystemRandom().random
        self._rate_reset: str | None = None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
        }

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        if self._rate_reset is not None:
            self._sleep(self.backoff_base * (1.0 + max(0.0, self._jitter())))
            self._rate_reset = None
        request_url = url if url.startswith("http") else f"{self.base_url}{url}"
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(
                    method,
                    request_url,
                    headers=self._headers,
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    raise ProviderError(f"GitHub request failed: {type(exc).__name__}") from exc
                delay = self.backoff_base * (2**attempt) * (1.0 + max(0.0, self._jitter()))
                self._sleep(delay)
                continue
            remaining = response.headers.get("X-RateLimit-Remaining")
            rate_limited = response.status_code in {403, 429} and remaining == "0"
            if rate_limited:
                reset = _reset_text(response)
                self._rate_reset = reset
                if attempt >= self.max_retries:
                    raise ProviderError(f"GitHub rate limit exhausted; reset at {reset}")
                delay = self.backoff_base * (2**attempt) * (1.0 + max(0.0, self._jitter()))
                self._sleep(delay)
                continue
            if response.status_code >= _HTTP_ERROR:
                raise ProviderError(
                    f"GitHub {method} {request_url} failed with HTTP {response.status_code}"
                )
            if remaining == "0":
                self._rate_reset = _reset_text(response)
            return response
        raise ProviderError("GitHub request retry bound exhausted")

    def _paged(self, path: str) -> tuple[Mapping[str, object], ...]:
        values: list[Mapping[str, object]] = []
        next_url: str | None = path
        seen: set[str] = set()
        while next_url is not None:
            if next_url in seen:
                raise ProviderError("GitHub pagination returned a repeated next link")
            seen.add(next_url)
            response = self._request("GET", next_url)
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderError("GitHub returned invalid JSON") from exc
            if not isinstance(payload, list):
                raise ProviderError("GitHub list endpoint returned a non-list payload")
            values.extend(item for item in payload if isinstance(item, Mapping))
            next_url = _next_link(response)
        return tuple(values)

    def _user(self) -> Mapping[str, object]:
        response = self._request("GET", "/user")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("GitHub /user returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise ProviderError("GitHub /user returned a non-object payload")
        result = dict(payload)
        header_scopes = _text(response.headers.get("X-OAuth-Scopes"))
        if header_scopes and "scopes" not in result:
            result["scopes"] = header_scopes
        return result

    def snapshot(self) -> Iterable[Entitlement]:
        if not self.org:
            raise ProviderError("GitHub organization is required")
        members = self._paged(f"/orgs/{self.org}/members")
        member_by_login = {
            _text(member.get("login")): member for member in members if _text(member.get("login"))
        }
        user = self._user()
        login = _text(user.get("login"))
        identity = login or (next(iter(member_by_login), "github-token"))
        token_scopes = user.get("scopes", user.get("token_scopes", ()))
        if isinstance(token_scopes, str):
            scopes: tuple[str, ...] = tuple(
                item.strip() for item in token_scopes.split(",") if item.strip()
            )
        elif isinstance(token_scopes, Sequence) and not isinstance(token_scopes, (str, bytes)):
            scopes = tuple(item for item in token_scopes if isinstance(item, str))
        else:
            scopes = ()
        token_permissions = user.get("permissions", {})
        if isinstance(token_permissions, Mapping):
            permission_names = tuple(
                item
                for item, enabled in token_permissions.items()
                if isinstance(item, str) and enabled
            )
        else:
            permission_names = ()
        token_scope = (
            Scope.ADMIN
            if any(item in {"admin", "admin:org"} for item in scopes)
            else (Scope.WRITE if scopes or permission_names else Scope.READ)
        )
        last_used = _aware(
            user.get("last_used_at", user.get("token_last_used_at", user.get("lastUsedAt")))
        )
        entitlements: list[Entitlement] = [
            Entitlement(
                identity,
                self.system,
                f"github:pat:{identity}",
                token_scope,
                _aware(user.get("created_at")),
                last_used,
                CredentialType.PAT,
                False,
                {
                    "kind": "pat",
                    "login": identity,
                    "scopes": scopes,
                    "permissions": permission_names,
                    "user": dict(user),
                },
            )
        ]
        for repo in self.repos:
            owner, separator, name = repo.partition("/")
            if not separator or not owner or not name:
                raise ProviderError(f"GitHub repository must be owner/name: {repo!r}")
            collaborators = self._paged(f"/repos/{owner}/{name}/collaborators")
            for collaborator in collaborators:
                collaborator_login = _text(collaborator.get("login"))
                permission = _text(collaborator.get("permission"), "pull")
                if not collaborator_login:
                    continue
                member = member_by_login.get(collaborator_login, {})
                entitlements.append(
                    Entitlement(
                        collaborator_login,
                        self.system,
                        f"{owner}/{name}",
                        _scope(permission),
                        _aware(member.get("created_at")),
                        last_used if collaborator_login == identity else None,
                        CredentialType.PAT
                        if collaborator_login == identity
                        else CredentialType.FEDERATED,
                        True,
                        {
                            "kind": "collaborator",
                            "login": collaborator_login,
                            "owner": owner,
                            "repo": name,
                            "permission": permission,
                            "collaborator": dict(collaborator),
                            "token_scopes": scopes,
                            "token_last_used_at": last_used,
                        },
                    )
                )
        return tuple(sorted(entitlements, key=_sort_key))

    @staticmethod
    def _pre_image(e: Entitlement) -> dict[str, object]:
        raw = dict(e.raw)
        if _text(raw.get("kind"), "collaborator") != "collaborator":
            raise ProviderError("GitHub entitlement is not a repository collaborator")
        owner = _text(raw.get("owner"))
        repo = _text(raw.get("repo"))
        login = _text(raw.get("login"), e.identity_id)
        permission = _text(raw.get("permission"))
        if not owner or not repo or not login or not permission:
            raise ProviderError(
                "GitHub collaborator pre-image lacks owner, repo, login, or permission"
            )
        return {
            "provider": "github",
            "kind": "collaborator",
            "system": e.system,
            "identity_id": e.identity_id,
            "resource": e.resource,
            "scope": e.scope.value,
            "owner": owner,
            "repo": repo,
            "login": login,
            "permission": permission,
        }

    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        if e.system != self.system:
            raise ProviderError(f"entitlement belongs to {e.system}, not {self.system}")
        if not e.revocable:
            return ActionResult(
                False,
                self.system,
                e.resource,
                e.scope.value,
                dry_run,
                None,
                "GitHub entitlement is not revocable",
                0,
            )
        raw_target = e.raw.get("target_scope", e.raw.get("to_scope"))
        if isinstance(raw_target, str) and raw_target not in {"", "none"}:
            return self.downgrade(e, raw_target, dry_run)
        pre = self._pre_image(e)
        started = time.monotonic()
        if dry_run:
            return ActionResult(
                True,
                self.system,
                e.resource,
                e.scope.value,
                True,
                pre,
                "dry-run: GitHub collaborator would be revoked",
                _ms(started),
            )
        url = f"/repos/{pre['owner']}/{pre['repo']}/collaborators/{pre['login']}"
        response = self._request("DELETE", url)
        if response.status_code == _HTTP_NOT_FOUND:
            return ActionResult(
                True,
                self.system,
                e.resource,
                e.scope.value,
                False,
                pre,
                "GitHub collaborator already revoked",
                _ms(started),
            )
        if response.status_code not in {200, 204}:
            raise ProviderError(f"GitHub revoke returned HTTP {response.status_code}")
        return ActionResult(
            True,
            self.system,
            e.resource,
            e.scope.value,
            False,
            pre,
            "GitHub collaborator revoked",
            _ms(started),
        )

    def downgrade(
        self, e: Entitlement, to_scope: Scope | str = Scope.READ, dry_run: bool = False
    ) -> ActionResult:
        if e.system != self.system:
            raise ProviderError(f"entitlement belongs to {e.system}, not {self.system}")
        target = Scope(to_scope)
        if target is Scope.ADMIN or target.value == e.scope.value:
            raise ProviderError("GitHub downgrade must reduce access")
        pre = self._pre_image(e)
        started = time.monotonic()
        if dry_run:
            return ActionResult(
                True,
                self.system,
                e.resource,
                e.scope.value,
                True,
                pre,
                f"dry-run: GitHub collaborator would become {target.value}",
                _ms(started),
            )
        url = f"/repos/{pre['owner']}/{pre['repo']}/collaborators/{pre['login']}"
        response = self._request("PUT", url, json={"permission": _github_permission(target)})
        if response.status_code not in {200, 201, 204}:
            raise ProviderError(f"GitHub downgrade returned HTTP {response.status_code}")
        return ActionResult(
            True,
            self.system,
            e.resource,
            e.scope.value,
            False,
            pre,
            "GitHub collaborator downgraded",
            _ms(started),
        )

    def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
        if (
            _text(pre_image.get("provider"), "github") != "github"
            or _text(pre_image.get("kind")) != "collaborator"
        ):
            raise ProviderError("invalid GitHub pre-image")
        owner = _text(pre_image.get("owner"))
        repo = _text(pre_image.get("repo"))
        login = _text(pre_image.get("login"))
        permission = _text(pre_image.get("permission"))
        if not owner or not repo or not login or not permission:
            raise ProviderError("GitHub pre-image is incomplete")
        started = time.monotonic()
        response = self._request(
            "PUT",
            f"/repos/{owner}/{repo}/collaborators/{login}",
            json={"permission": permission},
        )
        if response.status_code not in {200, 201, 204}:
            raise ProviderError(f"GitHub restore returned HTTP {response.status_code}")
        verified = self._request("GET", f"/repos/{owner}/{repo}/collaborators/{login}")
        try:
            payload = verified.json()
        except ValueError as exc:
            raise ProviderError("GitHub restore verification returned invalid JSON") from exc
        if not isinstance(payload, Mapping) or _text(payload.get("permission")) != permission:
            raise ProviderError("GitHub restore verification failed")
        return ActionResult(
            True,
            self.system,
            _text(pre_image.get("resource"), f"{owner}/{repo}"),
            _text(pre_image.get("scope"), "read"),
            False,
            dict(pre_image),
            "GitHub collaborator restored and verified",
            _ms(started),
        )


def _github_permission(scope: Scope) -> str:
    return {Scope.READ: "pull", Scope.WRITE: "push", Scope.ADMIN: "admin"}[scope]


def _ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


GithubProvider = GitHubProvider

__all__ = ["GitHubProvider", "GithubProvider"]
