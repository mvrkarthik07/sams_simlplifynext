"""Append-only, raw-payload-free audit records."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Protocol, cast

from deadbolt.plan.canonical import canonical_dumps


class _S3Client(Protocol):
    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...


class AuditWriter:
    """Write one immutable JSON audit event to an S3 bucket."""

    def __init__(self, bucket: str, s3_client: object) -> None:
        self.bucket = bucket
        self._client = cast(_S3Client, s3_client)

    def write(self, record: Mapping[str, object]) -> str:
        """Persist a detached canonical record and return its object key."""
        plan_hash = record.get("plan_hash", "unknown")
        seq = record.get("seq", "unknown")
        event_id = uuid.uuid4().hex
        key = f"audit/{plan_hash}/{seq}/{event_id}.json"
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=canonical_dumps(dict(record)),
            ContentType="application/json",
        )
        return key


def write_audit(bucket: str, s3_client: object, record: Mapping[str, object]) -> str:
    """Functional convenience wrapper around :class:`AuditWriter`."""
    return AuditWriter(bucket, s3_client).write(record)


__all__ = ["AuditWriter", "write_audit"]
