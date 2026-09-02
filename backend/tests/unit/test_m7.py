"""M7 immutable audit and OpenTelemetry tests."""

from __future__ import annotations

import io
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from deadbolt.audit.otel import (
    LOG_RETENTION_DAYS,
    PIPELINE_STAGES,
    Stage,
    TelemetryConfig,
    build_tracer,
    extract_trace_context,
    inject_trace_context,
    set_log_retention,
    stage_span,
)
from deadbolt.audit.writer import AuditConfig, AuditWriter

pytestmark = pytest.mark.m7
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


class _MemoryS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        key = cast(str, kwargs["Key"])
        if key in self.objects and kwargs.get("IfNoneMatch") == "*":
            raise RuntimeError("already exists")
        self.objects[key] = cast(bytes, kwargs["Body"])
        self.puts.append(kwargs)
        return {}

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        prefix = cast(str, kwargs["Prefix"])
        return {
            "Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)]
        }

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        return {"Body": io.BytesIO(self.objects[cast(str, kwargs["Key"])])}


def _record(seq: int, *, plan_id: str = "plan-m7", tier: str = "T2") -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "seq": seq,
        "ts": NOW,
        "actor": "system",
        "action": "observe" if tier == "T0" else "revoke",
        "identity_id": "alice@example.com",
        "system": "github",
        "resource": "acme/repo",
        "scope": "admin",
        "plan_hash": "hash-m7",
        "finding_id": "finding-m7",
        "tier": tier,
        "decision": "observe" if tier == "T0" else "Approve",
        "trace_id": "trace-m7",
        "pre_image_key": None,
        "result": {"ok": True},
    }


def test_writer_uses_configured_object_lock_and_records_t0_observation() -> None:
    s3 = _MemoryS3()
    writer = AuditWriter(
        "audit-bucket",
        s3,
        config=AuditConfig("GOVERNANCE", retention_days=7),
        clock=lambda: NOW,
    )
    key = writer.write(_record(0, tier="T0"))
    assert writer.verify_chain("plan-m7")
    stored = s3.objects[key]
    assert b'"decision":"observe"' in stored
    assert b'"tier":"T0"' in stored
    assert "ObjectLockMode" in s3.puts[0]
    assert s3.puts[0]["ObjectLockMode"] == "GOVERNANCE"
    assert s3.puts[0]["ObjectLockRetainUntilDate"] == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_mutating_any_audit_record_breaks_the_chain() -> None:
    s3 = _MemoryS3()
    writer = AuditWriter("audit-bucket", s3, config=AuditConfig("COMPLIANCE"), clock=lambda: NOW)
    first = writer.write(_record(0))
    writer.write(_record(1))
    assert writer.verify_chain("plan-m7")
    s3.objects[first] = s3.objects[first].replace(b"alice@example.com", b"mallory@example.com")
    assert writer.verify_chain("plan-m7") is False


def test_writer_never_persists_raw_payload_fields() -> None:
    s3 = _MemoryS3()
    writer = AuditWriter("audit-bucket", s3, config=AuditConfig("GOVERNANCE"), clock=lambda: NOW)
    key = writer.write({**_record(0), "raw": {"pat": "do-not-store"}, "result": {"raw": "secret"}})
    payload = s3.objects[key]
    assert b"do-not-store" not in payload
    assert b'"raw"' not in payload


def test_writer_assigns_sequence_for_schema_records_without_legacy_seq() -> None:
    s3 = _MemoryS3()
    writer = AuditWriter("audit-bucket", s3, config=AuditConfig("GOVERNANCE"), clock=lambda: NOW)
    record = {key: value for key, value in _record(0).items() if key != "seq"}
    writer.write(record)
    writer.write(record)
    assert writer.verify_chain("plan-m7")


def test_trace_propagates_across_eventbridge_stepfunctions_and_lambda() -> None:
    exporter = InMemorySpanExporter()
    _, tracer = build_tracer(TelemetryConfig(service_name="deadbolt-test"), exporter=exporter)
    with tracer.start_as_current_span("eventbridge"):
        carrier: dict[str, str] = {}
        inject_trace_context(carrier)
    event: dict[str, object] = {"detail": carrier}

    for stage in PIPELINE_STAGES:
        with stage_span(
            cast(Stage, stage),
            tracer=tracer,
            event=event,
            plan_hash="hash-m7",
            finding_id="finding-m7",
            tier="T2",
            provider="github",
        ):
            assert extract_trace_context(event)

    spans = [span for span in exporter.get_finished_spans() if span.name in PIPELINE_STAGES]
    assert [span.name for span in spans] == list(PIPELINE_STAGES)
    assert len({span.context.trace_id for span in spans}) == 1
    assert all(span.attributes["plan_hash"] == "hash-m7" for span in spans)
    assert all(span.attributes["finding_id"] == "finding-m7" for span in spans)


def test_redaction_removes_secrets_from_exported_spans() -> None:
    exporter = InMemorySpanExporter()
    _, tracer = build_tracer(exporter=exporter)
    with stage_span(
        "execute",
        tracer=tracer,
        attributes={"raw": "payload", "provider": "github"},
    ) as span:
        span.set_attribute("credential.token", "pat-value")
        span.set_attribute("safe.attribute", "kept")
    exported = exporter.get_finished_spans()[0]
    assert "raw" not in exported.attributes
    assert "credential.token" not in exported.attributes
    assert exported.attributes["safe.attribute"] == "kept"


def test_adot_configuration_and_log_retention_are_explicit() -> None:
    config = TelemetryConfig(
        service_name="deadbolt",
        otlp_endpoint="https://cloudwatch-otlp.example",
        layer_arn="arn:aws:lambda:us-east-1:123:layer:adot:1",
    )
    environment = config.lambda_environment()
    assert environment["OTEL_EXPORTER_OTLP_ENDPOINT"].startswith("https://")
    assert environment["OTEL_TRACES_EXPORTER"] == "otlp"
    assert config.lambda_layers() == ("arn:aws:lambda:us-east-1:123:layer:adot:1",)

    class Logs:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def put_retention_policy(self, **kwargs: object) -> Mapping[str, object]:
            self.calls.append(kwargs)
            return {}

    logs = Logs()
    set_log_retention(("/aws/lambda/a", "/aws/lambda/b"), logs)
    assert LOG_RETENTION_DAYS == 1
    assert [call["retentionInDays"] for call in logs.calls] == [1, 1]
