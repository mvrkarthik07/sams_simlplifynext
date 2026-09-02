"""Audit persistence helpers."""

from deadbolt.audit.otel import (
    LOG_RETENTION_DAYS,
    PIPELINE_STAGES,
    RedactingSpanProcessor,
    Stage,
    TelemetryConfig,
    build_tracer,
    configure_telemetry,
    extract_trace_context,
    inject_trace_context,
    propagate_event,
    set_log_retention,
    stage_span,
)
from deadbolt.audit.writer import AuditConfig, AuditWriter, write_audit

__all__ = [
    "LOG_RETENTION_DAYS",
    "PIPELINE_STAGES",
    "AuditConfig",
    "AuditWriter",
    "RedactingSpanProcessor",
    "Stage",
    "TelemetryConfig",
    "build_tracer",
    "configure_telemetry",
    "extract_trace_context",
    "inject_trace_context",
    "propagate_event",
    "set_log_retention",
    "stage_span",
    "write_audit",
]
