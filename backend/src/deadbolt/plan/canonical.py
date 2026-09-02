"""Canonical JSON encoding shared by snapshot and plan persistence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum


def _canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        instant = value.astimezone(UTC).replace(microsecond=0)
        return instant.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_dumps(value: object) -> bytes:
    """Encode JSON using the deterministic wire representation."""
    normalized = _canonical_value(value)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


__all__ = ["canonical_dumps"]
