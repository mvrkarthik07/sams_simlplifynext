"""Redacted, canonical provider snapshots for the submission demo."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from deadbolt.contracts.models import ActionResult, CredentialType, Entitlement, Scope
from deadbolt.plan.canonical import canonical_dumps, iso_second

_CAPTURE_VERSION: Final[str] = "github-capture-v1"
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SECRET_KEYS: Final[frozenset[str]] = frozenset(
    {"authorization", "headers", "token", "access_token", "avatar_url", "avatarurl", "user"}
)
_ALLOWED_RAW_KEYS: Final[frozenset[str]] = frozenset(
    {"kind", "login", "owner", "repo", "permission", "scopes", "permissions", "token_scopes"}
)


def _pseudonym(value: str) -> str:
    return f"email_{hashlib.sha256(value.lower().encode()).hexdigest()[:16]}"


def _safe_string(value: str) -> str:
    if _EMAIL_RE.fullmatch(value):
        return _pseudonym(value)
    return _EMAIL_RE.sub(lambda match: _pseudonym(match.group(0)), value)


def _safe_value(value: object) -> object:
    if isinstance(value, str):
        return _safe_string(value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _SECRET_KEYS:
                continue
            result[str(key)] = _safe_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _safe_raw(raw: Mapping[str, object]) -> dict[str, object]:
    return {
        key: _safe_value(raw[key])
        for key in sorted(_ALLOWED_RAW_KEYS)
        if key in raw and key.lower() not in _SECRET_KEYS
    }


def _record(entitlement: Entitlement, captured_at: datetime) -> dict[str, object]:
    return {
        "identity_id": _safe_string(entitlement.identity_id),
        "system": entitlement.system,
        "resource": entitlement.resource,
        "scope": entitlement.scope.value,
        "granted_at": iso_second(entitlement.granted_at) if entitlement.granted_at else None,
        "last_used_at": iso_second(entitlement.last_used_at) if entitlement.last_used_at else None,
        "credential_type": entitlement.credential_type.value,
        "revocable": entitlement.revocable,
        "raw": _safe_raw(entitlement.raw),
        "source": "captured",
        "captured_at": iso_second(captured_at),
    }


def capture_body(
    entitlements: Iterable[Entitlement],
    *,
    org: str,
    captured_at: datetime,
    provider_version: str = _CAPTURE_VERSION,
) -> tuple[dict[str, object], dict[str, object]]:
    """Return a redacted canonical body and its integrity manifest."""
    captured_at = captured_at.astimezone(UTC).replace(microsecond=0)
    records = sorted(
        (_record(item, captured_at) for item in entitlements),
        key=lambda item: (
            cast(str, item["identity_id"]).encode("utf-8"),
            cast(str, item["resource"]).encode("utf-8"),
            cast(str, item["scope"]).encode("utf-8"),
        ),
    )
    body: dict[str, object] = {
        "schema_version": _CAPTURE_VERSION,
        "captured_at": iso_second(captured_at),
        "org": org,
        "provider_version": provider_version,
        "entitlements": records,
    }
    digest = hashlib.sha256(canonical_dumps(body)).hexdigest()
    manifest = {
        "captured_at": iso_second(captured_at),
        "org": org,
        "provider_version": provider_version,
        "record_count": len(records),
        "sha256": digest,
    }
    return body, manifest


def write_capture(
    entitlements: Iterable[Entitlement],
    output_dir: str | Path,
    *,
    org: str,
    captured_at: datetime,
    provider_version: str = _CAPTURE_VERSION,
) -> dict[str, object]:
    """Write the body and manifest without printing provider data or credentials."""
    body, manifest = capture_body(
        entitlements,
        org=org,
        captured_at=captured_at,
        provider_version=provider_version,
    )
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "github.json").write_bytes(canonical_dumps(body))
    (destination / "manifest.json").write_bytes(canonical_dumps(manifest))
    return manifest


class CapturedProvider:
    """Read-only provider backed by one verified capture artifact."""

    system = "github"

    def __init__(self, path: str | Path) -> None:
        body_path = Path(path)
        body = json.loads(body_path.read_text(encoding="utf-8"))
        if not isinstance(body, Mapping):
            raise ValueError("capture body must be an object")
        records = body.get("entitlements")
        if not isinstance(records, list):
            raise ValueError("capture body entitlements must be a list")
        self._entitlements = tuple(self._parse(record) for record in records)

    @staticmethod
    def _parse(value: object) -> Entitlement:
        if not isinstance(value, Mapping):
            raise ValueError("capture entitlement must be an object")

        def text(name: str) -> str:
            item = value.get(name)
            if not isinstance(item, str) or not item:
                raise ValueError(f"capture entitlement requires {name}")
            return item

        def timestamp(name: str) -> datetime | None:
            item = value.get(name)
            if item is None:
                return None
            return datetime.fromisoformat(text(name).replace("Z", "+00:00"))

        raw = value.get("raw", {})
        if not isinstance(raw, Mapping):
            raise ValueError("capture entitlement raw must be an object")
        return Entitlement(
            identity_id=text("identity_id"),
            system=text("system"),
            resource=text("resource"),
            scope=Scope(text("scope")),
            granted_at=timestamp("granted_at"),
            last_used_at=timestamp("last_used_at"),
            credential_type=CredentialType(text("credential_type")),
            revocable=bool(value.get("revocable", False)),
            raw={**dict(raw), "source": "captured", "captured_at": text("captured_at")},
        )

    def snapshot(self) -> tuple[Entitlement, ...]:
        return tuple(
            sorted(
                self._entitlements,
                key=lambda item: (
                    item.identity_id.encode("utf-8"),
                    item.resource.encode("utf-8"),
                    item.scope.value.encode("utf-8"),
                ),
            )
        )

    def revoke(self, entitlement: Entitlement, dry_run: bool) -> ActionResult:
        del entitlement, dry_run
        raise RuntimeError("captured GitHub data is read-only")

    def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
        del pre_image
        raise RuntimeError("captured GitHub data is read-only")


__all__ = ["CapturedProvider", "capture_body", "write_capture"]
