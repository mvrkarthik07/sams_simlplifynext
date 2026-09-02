"""Shared deterministic implementation for Tier-B fixture providers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

from deadbolt.contracts.models import ActionResult, CredentialType, Entitlement, Scope
from deadbolt.errors import ProviderError

_Record = dict[str, object]
_Key = tuple[str, str, str]


def _default_seed_path(system: str) -> Path:
    return Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "seed" / f"{system}.json"


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("datetime seed values must be strings or null")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _record_from_seed(system: str, source: Mapping[str, object]) -> tuple[Entitlement, _Record]:
    email_value = source.get("email")
    email = email_value.lower() if isinstance(email_value, str) else None
    identity_value = source.get("identity_id", email)
    if not isinstance(identity_value, str) or not identity_value:
        raise ValueError("seed record requires identity_id or email")
    resource = source.get("resource")
    if not isinstance(resource, str) or not resource:
        raise ValueError("seed record requires resource")
    scope = Scope(str(source.get("scope", "read")))
    credential_type = CredentialType(str(source.get("credential_type", "federated")))
    raw_source = _mapping(source.get("raw", {}), "raw")
    raw = dict(raw_source)
    if email is not None:
        raw.setdefault("email", email)
    entitlement = Entitlement(
        identity_id=identity_value,
        system=system,
        resource=resource,
        scope=scope,
        granted_at=_parse_datetime(source.get("granted_at")),
        last_used_at=_parse_datetime(source.get("last_used_at")),
        credential_type=credential_type,
        revocable=bool(source.get("revocable", True)),
        raw=raw,
    )
    record: _Record = {
        "identity_id": entitlement.identity_id,
        "email": email if email is not None else entitlement.identity_id.lower(),
        "system": entitlement.system,
        "resource": entitlement.resource,
        "scope": entitlement.scope.value,
        "granted_at": entitlement.granted_at,
        "last_used_at": entitlement.last_used_at,
        "credential_type": entitlement.credential_type.value,
        "revocable": entitlement.revocable,
        "raw": dict(entitlement.raw),
    }
    return entitlement, record


class FixtureProvider:
    """A deterministic, in-memory implementation of the provider protocol."""

    system: str

    def __init__(
        self,
        system: str,
        seed_path: str | Path | None = None,
        seed: Sequence[Mapping[str, object]] | None = None,
    ) -> None:
        self.system = system
        sources: Sequence[Mapping[str, object]]
        if seed is None:
            path = Path(seed_path) if seed_path is not None else _default_seed_path(system)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            loaded_items = loaded.get("entitlements") if isinstance(loaded, Mapping) else loaded
            if not isinstance(loaded_items, list):
                raise ValueError("fixture seed must be a JSON array or an entitlements object")
            sources = tuple(_mapping(item, "seed item") for item in loaded_items)
        else:
            sources = tuple(_mapping(item, "seed item") for item in seed)
        self._state: dict[_Key, _Record] = {}
        self._pre_images: dict[_Key, _Record] = {}
        for source in sources:
            entitlement, record = _record_from_seed(system, source)
            key = self._key(entitlement)
            if key in self._state:
                raise ValueError(f"duplicate fixture entitlement: {key}")
            self._state[key] = record
            self._pre_images[key] = self._copy_record(record)

    @staticmethod
    def _key(entitlement: Entitlement) -> _Key:
        return entitlement.identity_id, entitlement.resource, entitlement.scope.value

    @staticmethod
    def _copy_record(record: _Record) -> _Record:
        copied = dict(record)
        raw = record.get("raw")
        if isinstance(raw, Mapping):
            copied["raw"] = dict(raw)
        return copied

    @staticmethod
    def _sort_key(entitlement: Entitlement) -> tuple[bytes, bytes, bytes]:
        return (
            entitlement.identity_id.encode("utf-8"),
            entitlement.resource.encode("utf-8"),
            entitlement.scope.value.encode("utf-8"),
        )

    @staticmethod
    def _entitlement(record: Mapping[str, object]) -> Entitlement:
        raw_value = record.get("raw", {})
        raw = dict(_mapping(raw_value, "pre-image raw"))
        return Entitlement(
            identity_id=cast(str, record["identity_id"]),
            system=cast(str, record["system"]),
            resource=cast(str, record["resource"]),
            scope=Scope(cast(str, record["scope"])),
            granted_at=_parse_datetime(record.get("granted_at")),
            last_used_at=_parse_datetime(record.get("last_used_at")),
            credential_type=CredentialType(cast(str, record["credential_type"])),
            revocable=cast(bool, record["revocable"]),
            raw=raw,
        )

    def snapshot(self) -> Iterable[Entitlement]:
        entitlements = tuple(self._entitlement(record) for record in self._state.values())
        return tuple(sorted(entitlements, key=self._sort_key))

    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        if e.system != self.system:
            raise ProviderError(f"entitlement belongs to {e.system}, not {self.system}")
        key = self._key(e)
        pre_image = self._pre_images.get(key)
        if pre_image is None:
            raise ProviderError(f"unknown {self.system} entitlement: {key}")
        if not e.revocable:
            return ActionResult(
                False,
                self.system,
                e.resource,
                e.scope.value,
                dry_run,
                self._copy_record(pre_image),
                "entitlement is not revocable",
                0,
            )
        was_present = key in self._state
        if not dry_run:
            self._state.pop(key, None)
        message = "dry-run: entitlement would be revoked" if dry_run else "entitlement revoked"
        if not was_present and not dry_run:
            message = "entitlement already revoked"
        return ActionResult(
            True,
            self.system,
            e.resource,
            e.scope.value,
            dry_run,
            self._copy_record(pre_image),
            message,
            0,
        )

    def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
        restored = self._entitlement(pre_image)
        if restored.system != self.system:
            raise ProviderError(f"pre-image belongs to {restored.system}, not {self.system}")
        key = self._key(restored)
        record = self._copy_record(dict(pre_image))
        self._state[key] = record
        self._pre_images[key] = self._copy_record(record)
        return ActionResult(
            True,
            self.system,
            restored.resource,
            restored.scope.value,
            False,
            self._copy_record(record),
            "entitlement restored",
            0,
        )


__all__ = ["FixtureProvider"]
