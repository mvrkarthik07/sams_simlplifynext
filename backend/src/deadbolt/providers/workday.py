"""Workday Tier-B connector using real REST/RaaS and SOAP-shaped payloads."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx

from deadbolt.contracts.models import ActionResult, CredentialType, Entitlement, Scope
from deadbolt.errors import ProviderError

_HTTP_BAD_REQUEST = 400


class _Client(Protocol):
    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response: ...


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProviderError("Workday returned a non-object payload")
    return value


class WorkdayProvider:
    """Read-only Workday worker and security-group connector."""

    system = "workday"

    def __init__(  # noqa: PLR0913 — tenant, auth, replay payloads, and transport are explicit.
        self,
        *,
        tenant: str | None = None,
        base_url: str | None = None,
        bearer_token: str | None = None,
        workers_payload: Mapping[str, object] | None = None,
        security_groups_payload: Mapping[str, object] | None = None,
        client: object | None = None,
    ) -> None:
        self.tenant = tenant or os.environ.get("WORKDAY_TENANT", "")
        self.base_url = (base_url or os.environ.get("WORKDAY_BASE_URL", "")).rstrip("/")
        self.bearer_token = bearer_token or os.environ.get("WORKDAY_TOKEN", "")
        self.workers_payload = workers_payload
        self.security_groups_payload = security_groups_payload
        self._client = cast(_Client, client if client is not None else httpx.Client())

    def _get(self, path: str) -> Mapping[str, object]:
        if not self.base_url or not self.bearer_token:
            raise ProviderError("Workday tenant, base_url, and bearer token are required")
        response = self._client.request(
            "GET",
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.bearer_token}", "Accept": "application/json"},
        )
        if response.status_code >= _HTTP_BAD_REQUEST:
            raise ProviderError(f"Workday GET {path} failed with HTTP {response.status_code}")
        return _mapping(response.json())

    def _workers(self) -> tuple[Mapping[str, object], ...]:
        payload = self.workers_payload or self._get(f"/ccx/api/v1/{self.tenant}/workers")
        values = payload.get("data", payload.get("workers", ()))
        if not isinstance(values, list):
            raise ProviderError("Workday workers response must contain data[]")
        return tuple(item for item in values if isinstance(item, Mapping))

    def _groups(self) -> tuple[Mapping[str, object], ...]:
        payload = self.security_groups_payload or self._get(
            f"/ccx/service/{self.tenant}/soap/v39.2"
        )
        values = payload.get("Security_Group_Assignment", payload.get("security_groups", ()))
        if not isinstance(values, list):
            raise ProviderError("Workday security-group response must contain assignments")
        return tuple(item for item in values if isinstance(item, Mapping))

    def snapshot(self) -> tuple[Entitlement, ...]:
        workers = self._workers()
        worker_by_id: dict[str, Mapping[str, object]] = {}
        result: list[Entitlement] = []
        for worker in workers:
            worker_id = _text(worker.get("id"), _text(worker.get("worker_id")))
            email = _text(
                worker.get("primaryWorkEmail"), _text(worker.get("primary_work_email"))
            ).lower()
            if worker_id and email:
                worker_by_id[worker_id] = worker
                # Identity is an HR source, not an access grant; the identity linkage is retained
                # in raw for the graph snapshot and the security-group rows below.
        for assignment in self._groups():
            worker_id = _text(
                assignment.get("worker_id"), _text(assignment.get("Worker_Reference_ID"))
            )
            worker = worker_by_id.get(worker_id, {})
            email = _text(
                worker.get("primaryWorkEmail"), _text(worker.get("primary_work_email"))
            ).lower()
            group = _text(
                assignment.get("security_group"), _text(assignment.get("Security_Group_Name"))
            )
            if not email or not group:
                continue
            result.append(
                Entitlement(
                    email,
                    self.system,
                    f"workday:security-group:{group}",
                    Scope.READ,
                    _time(worker.get("hireDate", worker.get("hire_date"))),
                    None,
                    CredentialType.FEDERATED,
                    False,
                    {
                        "kind": "security-group",
                        "worker_id": worker_id,
                        "email": email,
                        "group": group,
                        "worker": dict(worker),
                        "assignment": dict(assignment),
                        "reversible": False,
                    },
                )
            )
        return tuple(sorted(result, key=lambda e: (e.identity_id, e.resource, e.scope.value)))

    @staticmethod
    def normalize_event(payload: Mapping[str, object]) -> dict[str, object]:
        """Normalize a Workday worker-change event from EventBridge or fixture replay."""
        event_type = _text(payload.get("event_type"), _text(payload.get("eventType")))
        if event_type not in {"hire", "job_change", "termination"}:
            raise ProviderError(f"unsupported Workday event type: {event_type!r}")
        return {
            "worker_id": _text(payload.get("worker_id"), _text(payload.get("workerId"))),
            "effective_date": _text(
                payload.get("effective_date"), _text(payload.get("effectiveDate"))
            ),
            "prior_title": _text(payload.get("prior_title"), _text(payload.get("priorTitle"))),
            "new_title": _text(payload.get("new_title"), _text(payload.get("newTitle"))),
            "event_type": event_type,
        }

    def worker_event(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self.normalize_event(payload)

    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        return ActionResult(
            False,
            self.system,
            e.resource,
            e.scope.value,
            dry_run,
            dict(e.raw),
            "Workday security groups are read-only in Deadbolt",
            0,
        )

    def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
        raise ProviderError("Workday security-group entitlements are not restorable")


__all__ = ["WorkdayProvider"]
