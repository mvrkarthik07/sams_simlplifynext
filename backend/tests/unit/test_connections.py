"""Provider onboarding storage and read-only connection checks."""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from deadbolt import connections
from deadbolt.connections import ConnectionService, MemoryConnectionStore, SsmConnectionStore
from deadbolt.errors import ProviderError

_GITHUB_RECORD_COUNT = 3
_SALESFORCE_RECORD_COUNT = 4
_WORKDAY_RECORD_COUNT = 5


class _FakeProvider:
    def __init__(self, count: int = 2) -> None:
        self.count = count

    def snapshot(self) -> tuple[object, ...]:
        return tuple(
            SimpleNamespace(
                identity_id=f"user-{index}",
                resource="acme/resource",
                scope=SimpleNamespace(value="read"),
                credential_type=SimpleNamespace(value="oauth"),
                revocable=True,
            )
            for index in range(self.count)
        )


def test_service_validates_and_removes_each_provider_without_returning_secrets() -> None:
    service = ConnectionService(MemoryConnectionStore())
    with pytest.raises(ValueError, match="unsupported provider"):
        service.save("operator", "slack", {})
    with pytest.raises(ValueError, match="repos"):
        service.save("operator", "github", {"org": "acme", "token": "secret", "repos": []})
    with pytest.raises(ValueError, match="unsupported connection field"):
        service.save(
            "operator",
            "github",
            {"org": "acme", "token": "secret", "repos": ["acme/app"], "raw": "x"},
        )

    github = service.save(
        "operator",
        "github",
        {"org": "acme", "token": "secret", "repos": ["acme/app"]},
    )
    salesforce = service.save(
        "operator",
        "salesforce",
        {
            "instance_url": "https://acme.my.salesforce.com",
            "client_id": "id",
            "username": "user",
            "private_key": "key",
        },
    )
    workday = service.save(
        "operator",
        "workday",
        {"tenant": "acme_dpt1", "base_url": "https://workday.example", "bearer_token": "secret"},
    )
    assert github["status"] == salesforce["status"] == workday["status"] == "connected"
    assert (
        "token" not in github and "private_key" not in salesforce and "bearer_token" not in workday
    )
    assert workday["read_only"] is True
    assert service.remove("operator", "github")["status"] == "not-configured"
    with pytest.raises(ValueError, match="not configured"):
        service.test("operator", "github")


def test_service_runs_read_only_provider_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConnectionService(MemoryConnectionStore())
    service.save("operator", "github", {"org": "acme", "token": "secret", "repos": ["acme/app"]})
    monkeypatch.setattr(
        connections,
        "GitHubProvider",
        lambda *args, **kwargs: _FakeProvider(_GITHUB_RECORD_COUNT),
    )
    result = service.test("operator", "github")
    assert result["record_count"] == _GITHUB_RECORD_COUNT
    assert "secret" not in json.dumps(result)

    service.save(
        "operator",
        "salesforce",
        {"instance_url": "https://sf", "client_id": "id", "username": "user", "private_key": "key"},
    )
    monkeypatch.setattr(
        connections,
        "SalesforceProvider",
        lambda **kwargs: _FakeProvider(_SALESFORCE_RECORD_COUNT),
    )
    assert service.test("operator", "salesforce")["record_count"] == _SALESFORCE_RECORD_COUNT

    service.save(
        "operator",
        "workday",
        {"tenant": "acme", "base_url": "https://wd", "bearer_token": "secret"},
    )
    monkeypatch.setattr(
        connections,
        "WorkdayProvider",
        lambda **kwargs: _FakeProvider(_WORKDAY_RECORD_COUNT),
    )
    assert service.test("operator", "workday")["record_count"] == _WORKDAY_RECORD_COUNT


class ParameterNotFound(Exception):  # noqa: N818 — mirrors the boto3 exception type name.
    pass


class _FakeSsm:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_parameter(self, **kwargs: object) -> Mapping[str, object]:
        name = str(kwargs["Name"])
        if name not in self.values:
            raise ParameterNotFound()
        return {"Parameter": {"Value": self.values[name]}}

    def put_parameter(self, **kwargs: object) -> Mapping[str, object]:
        self.values[str(kwargs["Name"])] = str(kwargs["Value"])
        return {}

    def delete_parameter(self, **kwargs: object) -> Mapping[str, object]:
        self.values.pop(str(kwargs["Name"]), None)
        return {}


def test_ssm_store_round_trip_and_storage_errors() -> None:
    client = _FakeSsm()
    store = SsmConnectionStore(client=client)
    assert store.get("operator", "github") is None
    store.put("operator", "github", {"token": "secret"})
    assert store.get("operator", "github") == {"token": "secret"}
    store.delete("operator", "github")
    assert store.get("operator", "github") is None

    class _Broken:
        def get_parameter(self, **kwargs: object) -> Mapping[str, object]:
            raise RuntimeError("offline")

        def put_parameter(self, **kwargs: object) -> Mapping[str, object]:
            raise RuntimeError("offline")

        def delete_parameter(self, **kwargs: object) -> Mapping[str, object]:
            raise RuntimeError("offline")

    broken = SsmConnectionStore(client=_Broken())
    with pytest.raises(ProviderError, match="storage"):
        broken.get("operator", "github")
