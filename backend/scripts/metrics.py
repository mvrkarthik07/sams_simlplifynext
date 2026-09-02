#!/usr/bin/env python3
"""Print M1/M2/M3/M5 evidence and write the JSON artifact used by the pitch."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from deadbolt.metrics import calculate_metrics, metrics_table, write_metrics  # noqa: E402


def _instant(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=_ROOT / "artifacts" / "m8-metrics.json")
    parser.add_argument("--planted", type=int, default=20)
    parser.add_argument("--detected", type=int, default=20)
    parser.add_argument("--false-revocations", type=int, default=0)
    parser.add_argument("--executed", type=int, default=20)
    parser.add_argument("--rolled-back", type=int, default=20)
    parser.add_argument("--hr-event-at")
    parser.add_argument("--executed-at")
    args = parser.parse_args(argv)
    key = lambda prefix, index: (prefix, "demo", str(index), "admin")
    planted = {key("planted", index) for index in range(args.planted)}
    detected = {key("planted", index) for index in range(args.detected)}
    executed = tuple(key("executed", index) for index in range(args.executed))
    metrics = calculate_metrics(
        planted,
        detected,
        executed,
        set(),
        args.rolled_back,
        hr_event_at=_instant(args.hr_event_at),
        execution_times=tuple(
            item for item in (_instant(args.executed_at),) if item is not None
        )
        * args.executed,
    )
    if args.false_revocations:
        # CLI count inputs are already aggregate evidence; preserve it in the artifact.
        metrics = metrics.__class__(
            metrics.m1_recall_percent,
            args.false_revocations,
            metrics.m3_mean_minutes,
            metrics.m5_reversibility_percent,
            metrics.planted_count,
            metrics.detected_count,
            metrics.executed_count,
            metrics.rollback_success_count,
        )
    print(metrics_table(metrics))
    print(f"\nJSON artifact: {write_metrics(metrics, args.artifact)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
