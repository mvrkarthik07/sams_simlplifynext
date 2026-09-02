"""OpenTelemetry tracing and AWS ADOT Lambda configuration.

The ADOT Lambda layer owns the collector/exporter in deployment.  This module
only creates spans, propagates W3C context, and supplies the layer's OTLP
environment contract so local tests never contact AWS.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    Tracer,
    set_span_in_context,
)
from opentelemetry.trace import Span as ApiSpan
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

Stage = Literal["snapshot", "detect", "plan", "broker.wait", "execute", "rollback"]
PIPELINE_STAGES: tuple[Stage, ...] = (
    "snapshot",
    "detect",
    "plan",
    "broker.wait",
    "execute",
    "rollback",
)
LOG_RETENTION_DAYS = 1
_PROPAGATOR = TraceContextTextMapPropagator()
_TRACE_ID_HEX_LENGTH = 32
_SENSITIVE_PARTS = frozenset(
    {"raw", "secret", "token", "pat", "password", "authorization", "credential"}
)


class _LogsClient(Protocol):
    def put_retention_policy(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    """Deployment settings consumed by the ADOT Lambda layer."""

    service_name: str = "deadbolt"
    otlp_endpoint: str = "http://localhost:4318"
    layer_arn: str | None = None

    def lambda_environment(self) -> dict[str, str]:
        """Return Lambda environment variables for OTLP-to-CloudWatch export."""
        return {
            "OTEL_SERVICE_NAME": self.service_name,
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_ENDPOINT": self.otlp_endpoint,
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_PROPAGATORS": "tracecontext,baggage",
            "AWS_LAMBDA_EXEC_WRAPPER": "/opt/otel-handler",
        }

    def lambda_layers(self) -> tuple[str, ...]:
        """Return the configured ADOT layer ARN for an infrastructure adapter."""
        return () if self.layer_arn is None else (self.layer_arn,)


def _sensitive(name: str) -> bool:
    pieces = set(name.lower().replace("-", ".").split("."))
    return bool(pieces & _SENSITIVE_PARTS)


def _attribute(value: object) -> str | int | float | bool:
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class RedactingSpanProcessor(SpanProcessor):
    """Remove sensitive and raw-payload attributes before export."""

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        del parent_context
        original_set_attribute = cast(Callable[[str, object], None], span.set_attribute)

        def set_attribute(name: str, value: object) -> None:
            if not _sensitive(name):
                original_set_attribute(name, value)

        # Span instances are intentionally mutable while a span is active. Wrapping
        # the setter closes the race between application code and exporter callbacks.
        object.__setattr__(span, "set_attribute", set_attribute)

    def on_end(self, span: ReadableSpan) -> None:
        # The SDK's ReadableSpan is the ended Span for the synchronous end path.
        # Its private mapping is the only mutable point before exporters observe it.
        attributes = getattr(span, "_attributes", None)
        if isinstance(attributes, dict):
            for name in tuple(attributes):
                if _sensitive(str(name)):
                    del attributes[name]

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        del timeout_millis
        return True


def build_tracer(
    config: TelemetryConfig | None = None,
    *,
    exporter: SpanExporter | None = None,
) -> tuple[TracerProvider, Tracer]:
    """Build an isolated provider suitable for Lambda or an in-memory test."""
    selected = config or TelemetryConfig()
    provider = TracerProvider(resource=Resource.create({"service.name": selected.service_name}))
    provider.add_span_processor(RedactingSpanProcessor())
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, provider.get_tracer(selected.service_name)


def configure_telemetry(
    config: TelemetryConfig | None = None,
    *,
    exporter: SpanExporter | None = None,
) -> Tracer:
    """Install a process tracer provider and return the application tracer."""
    selected = config or TelemetryConfig()
    provider, tracer = build_tracer(selected, exporter=exporter)
    trace.set_tracer_provider(provider)
    return tracer


def inject_trace_context(
    carrier: MutableMapping[str, str],
    *,
    context: Context | None = None,
) -> MutableMapping[str, str]:
    """Inject W3C traceparent and a plain trace_id for AWS event payloads."""
    active = otel_context.get_current() if context is None else context
    _PROPAGATOR.inject(carrier, context=active)
    span_context = trace.get_current_span(active).get_span_context()
    if span_context.is_valid:
        carrier["trace_id"] = f"{span_context.trace_id:032x}"
    return carrier


def extract_trace_context(event: Mapping[str, object]) -> Context:
    """Extract context from a Lambda event, EventBridge detail, or ASL input."""
    carrier: dict[str, str] = {key: value for key, value in event.items() if isinstance(value, str)}
    detail = event.get("detail")
    if isinstance(detail, Mapping):
        for key, value in detail.items():
            if isinstance(value, str) and key not in carrier:
                carrier[key] = value
    extracted = _PROPAGATOR.extract(carrier)
    if trace.get_current_span(extracted).get_span_context().is_valid:
        return extracted
    plain_trace_id = carrier.get("trace_id", "")
    normalized = "".join(
        character for character in plain_trace_id.lower() if character in "0123456789abcdef"
    )
    if len(normalized) != _TRACE_ID_HEX_LENGTH or int(normalized, 16) == 0:
        normalized = hashlib.sha256(plain_trace_id.encode("utf-8")).hexdigest()[
            :_TRACE_ID_HEX_LENGTH
        ]
    span_context = SpanContext(
        trace_id=int(normalized, 16),
        span_id=1,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return set_span_in_context(NonRecordingSpan(span_context), extracted)


def propagate_event(
    event: Mapping[str, object],
    *,
    context: Context | None = None,
) -> dict[str, object]:
    """Copy an EventBridge/Step Functions payload with trace context attached."""
    result = dict(event)
    top_level: dict[str, str] = {}
    inject_trace_context(top_level, context=context)
    result.update(top_level)
    detail = event.get("detail")
    if isinstance(detail, Mapping):
        detail_copy = dict(detail)
        inject_trace_context(detail_copy, context=context)
        result["detail"] = detail_copy
    return result


@contextmanager
def stage_span(  # noqa: PLR0913 — stage evidence fields are intentionally explicit.
    stage: Stage,
    *,
    tracer: Tracer | None = None,
    event: Mapping[str, object] | None = None,
    plan_hash: str | None = None,
    finding_id: str | None = None,
    tier: str | object | None = None,
    provider: str | None = None,
    attributes: Mapping[str, object] | None = None,
) -> Iterator[ApiSpan]:
    """Create one named span for a pipeline stage with safe audit attributes."""
    active_tracer = tracer or trace.get_tracer("deadbolt")
    parent = extract_trace_context(event) if event is not None else None
    with active_tracer.start_as_current_span(stage, context=parent) as span:
        values: dict[str, object] = {
            "plan_hash": plan_hash,
            "finding_id": finding_id,
            "tier": getattr(tier, "value", tier),
            "provider": provider,
        }
        if attributes is not None:
            values.update(attributes)
        for name, value in values.items():
            if value is not None and not _sensitive(name):
                span.set_attribute(name, _attribute(value))
        yield span


def set_log_retention(
    log_group_names: Sequence[str],
    logs_client: object,
) -> None:
    """Apply the one-day cost policy to every named CloudWatch log group."""
    client = cast(_LogsClient, logs_client)
    for name in log_group_names:
        client.put_retention_policy(
            logGroupName=name,
            retentionInDays=LOG_RETENTION_DAYS,
        )


__all__ = [
    "LOG_RETENTION_DAYS",
    "PIPELINE_STAGES",
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
]
