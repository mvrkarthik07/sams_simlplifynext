"""Canonical, deliberately small JSON encoding for deterministic plans."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum


def _path_for_key(path: str, key: str) -> str:
    """Return a readable object path without adding a second escaping grammar."""
    if key.isidentifier():
        return f"{path}.{key}"
    return f"{path}[{key!r}]"


def _unsupported(value: object, path: str) -> TypeError:
    if isinstance(value, float):
        return TypeError(f"float values are not permitted at {path}")
    return TypeError(f"unsupported canonical value at {path}: {type(value).__name__}")


def _canonical_value(value: object, path: str) -> object:
    """Validate a JSON primitive tree and return a JSON-serializable copy."""
    if isinstance(value, datetime):
        return iso_second(value)
    if isinstance(value, Enum):
        return _canonical_value(value.value, path)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise _unsupported(value, path)
    if isinstance(value, list):
        return [_canonical_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                key_path = f"{path}[{key!r}]"
                raise _unsupported(key, key_path)
            normalized[key] = _canonical_value(item, _path_for_key(path, key))
        return normalized
    raise _unsupported(value, path)


def canonical_dumps(obj: object) -> bytes:
    """Encode a tree of JSON primitives with the project-wide canonical settings."""
    normalized = _canonical_value(obj, "$")
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def iso_second(dt: datetime) -> str:
    """Format an aware instant as UTC with second precision."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def plan_hash(body: object) -> str:
    """Return the SHA-256 digest of a plan body in canonical form."""
    return hashlib.sha256(canonical_dumps(body)).hexdigest()


__all__ = ["canonical_dumps", "iso_second", "plan_hash"]
