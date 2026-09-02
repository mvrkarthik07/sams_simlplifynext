"""Metric calculations shared by the M8 test and the demo metrics command."""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

MetricKey = tuple[str, str, str, str]


def _percent(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 100.0
    return float((Decimal(numerator) * Decimal(100)) / Decimal(denominator))


def _mean_minutes(hr_event_at: datetime | None, execution_times: Iterable[datetime]) -> float:
    if hr_event_at is None:
        return 0.0
    values = tuple(execution_times)
    if not values:
        return 0.0
    total_seconds = sum((item - hr_event_at).total_seconds() for item in values)
    return float(Decimal(str(total_seconds)) / Decimal(60 * len(values)))


@dataclass(frozen=True, slots=True)
class Metrics:
    """M1/M2/M3/M5 evidence, with counts retained for auditability."""

    m1_recall_percent: float
    m2_false_revocations: int
    m3_mean_minutes: float
    m5_reversibility_percent: float
    planted_count: int
    detected_count: int
    executed_count: int
    rollback_success_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "M1": self.m1_recall_percent,
            "M2": self.m2_false_revocations,
            "M3": self.m3_mean_minutes,
            "M5": self.m5_reversibility_percent,
            "counts": {
                "planted": self.planted_count,
                "detected": self.detected_count,
                "executed": self.executed_count,
                "rollback_success": self.rollback_success_count,
            },
        }


def calculate_metrics(  # noqa: PLR0913 — metric evidence keeps each source count explicit.
    planted: Collection[MetricKey],
    detected: Collection[MetricKey],
    executed: Sequence[MetricKey],
    in_policy: Collection[MetricKey],
    rollback_successes: int,
    *,
    hr_event_at: datetime | None = None,
    execution_times: Iterable[datetime] = (),
) -> Metrics:
    """Calculate the pitch metrics from observed pipeline evidence."""
    detected_planted = set(planted) & set(detected)
    false_revocations = sum(item in in_policy for item in executed)
    return Metrics(
        _percent(len(detected_planted), len(planted)),
        false_revocations,
        _mean_minutes(hr_event_at, execution_times),
        _percent(rollback_successes, len(executed)),
        len(planted),
        len(detected_planted),
        len(executed),
        rollback_successes,
    )


def write_metrics(metrics: Metrics, artifact_path: str | Path) -> Path:
    """Write the machine-readable metric evidence artifact."""
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metrics.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def metrics_table(metrics: Metrics) -> str:
    """Render the compact table used by the demo command."""
    return "\n".join(
        (
            "Metric | Value",
            "--- | ---",
            f"M1 recall | {metrics.m1_recall_percent:.1f}% "
            f"({metrics.detected_count}/{metrics.planted_count})",
            f"M2 false revocations | {metrics.m2_false_revocations}",
            f"M3 mean time to revocation | {metrics.m3_mean_minutes:.2f} min",
            f"M5 reversibility | {metrics.m5_reversibility_percent:.1f}% "
            f"({metrics.rollback_success_count}/{metrics.executed_count})",
        )
    )


__all__ = ["MetricKey", "Metrics", "calculate_metrics", "metrics_table", "write_metrics"]
