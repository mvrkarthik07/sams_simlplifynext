"""Per-operator connector configuration and read-only connection checks.

Secrets are stored as SSM SecureString values keyed by a hash of the Cognito subject. The
browser receives metadata only; provider credentials never return in API responses.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, Protocol, cast

import boto3  # type: ignore[import-untyped]  # boto3 does not publish strict typing metadata.

from deadbolt.contracts.models import Entitlement
from deadbolt.contracts.provider import EntitlementProvider
from deadbolt.errors import ProviderError
from deadbolt.providers.github import GitHubProvider
from deadbolt.providers.salesforce import SalesforceProvider
from deadbolt.providers.workday import WorkdayProvider

PROVIDERS: Final[tuple[str, ...]] = ("github", "salesforce", "workday")
_PROVIDER_LABELS: Final[dict[str, str]] = {
    "github": "GitHub",
    "salesforce": "Salesforce",
    "workday": "Workday",
}
_CONFIG_KEYS: Final[dict[str, frozenset[str]]] = {
    "github": frozenset({"org", "token", "repos"}),
    "salesforce": frozenset({"instance_url", "client_id", "username", "private_key", "login_url"}),
    "workday": frozenset({"tenant", "base_url", "bearer_token"}),
}


class _SsmClient(Protocol):
    def get_parameter(self, **kwargs: object) -> Mapping[str, object]: ...

    def put_parameter(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_parameter(self, **kwargs: object) -> Mapping[str, object]: ...


class ConnectionStore(Protocol):
    def get(self, subject: str, provider: str) -> dict[str, object] | None: ...

    def put(self, subject: str, provider: str, value: Mapping[str, object]) -> None: ...

    def delete(self, subject: str, provider: str) -> None: ...


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _subject_key(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]


def _validate_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError("unsupported provider")


def _text(config: Mapping[str, object], key: str, *, required: bool = True) -> str:
    value = config.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if required:
        raise ValueError(f"{key} is required")
    return ""


def _config(provider: str, payload: Mapping[str, object]) -> dict[str, object]:
    _validate_provider(provider)
    unknown = set(payload) - set(_CONFIG_KEYS[provider])
    if unknown:
        raise ValueError("unsupported connection field")
    if provider == "github":
        repos = payload.get("repos", [])
        if not isinstance(repos, list) or not all(
            isinstance(item, str) and item.strip() for item in repos
        ):
            raise ValueError("repos must be a list of repository names")
        return {
            "org": _text(payload, "org"),
            "token": _text(payload, "token"),
            "repos": [item.strip() for item in repos],
        }
    if provider == "salesforce":
        return {
            "instance_url": _text(payload, "instance_url"),
            "client_id": _text(payload, "client_id"),
            "username": _text(payload, "username"),
            "private_key": _text(payload, "private_key"),
            "login_url": _text(payload, "login_url", required=False)
            or "https://login.salesforce.com",
        }
    return {
        "tenant": _text(payload, "tenant"),
        "base_url": _text(payload, "base_url"),
        "bearer_token": _text(payload, "bearer_token"),
    }


class MemoryConnectionStore:
    """Process-local store used only by the local, no-AWS rehearsal."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], dict[str, object]] = {}

    def get(self, subject: str, provider: str) -> dict[str, object] | None:
        value = self._values.get((subject, provider))
        return dict(value) if value is not None else None

    def put(self, subject: str, provider: str, value: Mapping[str, object]) -> None:
        self._values[(subject, provider)] = dict(value)

    def delete(self, subject: str, provider: str) -> None:
        self._values.pop((subject, provider), None)


class SsmConnectionStore:
    """SSM SecureString store for authenticated deployed operators."""

    def __init__(
        self,
        *,
        prefix: str = "/deadbolt/connections",
        client: object | None = None,
    ) -> None:
        self.prefix = prefix.rstrip("/")
        self._client = cast(_SsmClient, client) if client is not None else None

    @property
    def client(self) -> _SsmClient:
        if self._client is None:
            self._client = cast(
                _SsmClient,
                boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1")),
            )
        return self._client

    def _name(self, subject: str, provider: str) -> str:
        return f"{self.prefix}/{_subject_key(subject)}/{provider}"

    def get(self, subject: str, provider: str) -> dict[str, object] | None:
        try:
            response = self.client.get_parameter(
                Name=self._name(subject, provider),
                WithDecryption=True,
            )
        except Exception as exc:
            if exc.__class__.__name__ == "ParameterNotFound":
                return None
            raise ProviderError("connection storage is unavailable") from exc
        parameter = response.get("Parameter")
        if not isinstance(parameter, Mapping) or not isinstance(parameter.get("Value"), str):
            raise ProviderError("connection storage returned an invalid value")
        value = json.loads(parameter["Value"])
        if not isinstance(value, dict):
            raise ProviderError("connection storage returned an invalid value")
        return cast(dict[str, object], value)

    def put(self, subject: str, provider: str, value: Mapping[str, object]) -> None:
        try:
            self.client.put_parameter(
                Name=self._name(subject, provider),
                Value=json.dumps(dict(value), separators=(",", ":")),
                Type="SecureString",
                Overwrite=True,
            )
        except Exception as exc:
            raise ProviderError("connection storage is unavailable") from exc

    def delete(self, subject: str, provider: str) -> None:
        try:
            self.client.delete_parameter(Name=self._name(subject, provider))
        except Exception as exc:
            if exc.__class__.__name__ != "ParameterNotFound":
                raise ProviderError("connection storage is unavailable") from exc


def _summary(provider: str, value: Mapping[str, object] | None) -> dict[str, object]:
    configured = value is not None
    return {
        "provider": provider,
        "label": _PROVIDER_LABELS[provider],
        "status": "connected" if configured else "not-configured",
        "configured_at": value.get("configured_at") if value else None,
        "last_tested_at": value.get("last_tested_at") if value else None,
        "record_count": value.get("record_count") if value else None,
        "read_only": provider == "workday",
    }


class ConnectionService:
    """Store credentials and perform bounded, read-only provider checks."""

    def __init__(self, store: ConnectionStore) -> None:
        self.store = store

    def list(self, subject: str) -> list[dict[str, object]]:
        return [_summary(provider, self.store.get(subject, provider)) for provider in PROVIDERS]

    def save(self, subject: str, provider: str, payload: Mapping[str, object]) -> dict[str, object]:
        config = _config(provider, payload)
        existing = self.store.get(subject, provider) or {}
        value = {
            **config,
            "configured_at": existing.get("configured_at", _now()),
            "last_tested_at": existing.get("last_tested_at"),
            "record_count": existing.get("record_count"),
        }
        self.store.put(subject, provider, value)
        return _summary(provider, value)

    def remove(self, subject: str, provider: str) -> dict[str, object]:
        _validate_provider(provider)
        self.store.delete(subject, provider)
        return _summary(provider, None)

    def _provider(self, subject: str, provider: str) -> EntitlementProvider:
        _validate_provider(provider)
        value = self.store.get(subject, provider)
        if value is None:
            raise ValueError("provider is not configured")
        if provider == "github":
            return GitHubProvider(
                str(value["org"]),
                str(value["token"]),
                repos=tuple(cast(list[str], value["repos"])),
            )
        if provider == "salesforce":
            return SalesforceProvider(
                instance_url=str(value["instance_url"]),
                client_id=str(value["client_id"]),
                username=str(value["username"]),
                private_key=str(value["private_key"]),
                login_url=str(value["login_url"]),
            )
        return WorkdayProvider(
            tenant=str(value["tenant"]),
            base_url=str(value["base_url"]),
            bearer_token=str(value["bearer_token"]),
        )

    def snapshot(self, subject: str, provider: str) -> tuple[Entitlement, ...]:
        """Read one provider for an explicit dashboard scan; never mutates it."""
        value = self.store.get(subject, provider)
        client = self._provider(subject, provider)
        try:
            records = tuple(client.snapshot())
        except Exception as exc:
            raise ValueError("provider scan failed; verify the provider configuration") from exc
        tested = {**(value or {}), "last_tested_at": _now(), "record_count": len(records)}
        self.store.put(subject, provider, tested)
        return records

    def github_provider(self, subject: str) -> GitHubProvider:
        """Return the authenticated GitHub client for the MCP admin surface."""
        if self.store.get(subject, "github") is None:
            raise ValueError("GitHub provider is not configured")
        return cast(GitHubProvider, self._provider(subject, "github"))

    def test(self, subject: str, provider: str) -> dict[str, object]:
        _validate_provider(provider)
        value = self.store.get(subject, provider)
        records = self.snapshot(subject, provider)
        tested = {**(value or {}), "last_tested_at": _now(), "record_count": len(records)}
        self.store.put(subject, provider, tested)
        return {
            **_summary(provider, tested),
            "message": f"Read-only check succeeded; {len(records)} records found.",
            "records": [
                {
                    "identity_id": record.identity_id,
                    "resource": record.resource,
                    "scope": record.scope.value,
                    "credential_type": record.credential_type.value,
                    "revocable": record.revocable,
                }
                for record in records
            ],
        }


__all__ = ["PROVIDERS", "ConnectionService", "MemoryConnectionStore", "SsmConnectionStore"]
