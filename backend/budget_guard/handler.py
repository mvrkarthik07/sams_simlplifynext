"""Six-hourly Cost Explorer guardrail with threshold Slack notifications."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast

import boto3  # type: ignore[import-untyped]  # boto3 has no strict typing metadata.
import httpx

BUDGET_THRESHOLDS: tuple[Decimal, ...] = (Decimal("5"), Decimal("10"), Decimal("14"))
SCHEDULE_EXPRESSION = "rate(6 hours)"


class CostExplorer(Protocol):
    """The Cost Explorer surface needed by the guard."""

    def get_cost_and_usage(self, **kwargs: object) -> Mapping[str, object]: ...


class SlackPoster(Protocol):
    """Minimal Slack port, injectable for moto-backed tests."""

    def post(self, message: str) -> None: ...


class WebhookSlackPoster:
    """Small webhook adapter; it is constructed only at Lambda invocation time."""

    def __init__(self, webhook_url: str, *, client: httpx.Client | None = None) -> None:
        self.webhook_url = webhook_url
        self._client = client or httpx.Client(timeout=10.0)

    def post(self, message: str) -> None:
        response = self._client.post(self.webhook_url, json={"text": message})
        response.raise_for_status()


@dataclass(frozen=True, slots=True)
class BudgetGuardResult:
    """Observable outcome of one budget check."""

    amount: Decimal
    crossed_thresholds: tuple[Decimal, ...]
    notified_thresholds: tuple[Decimal, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "amount": str(self.amount),
            "crossed_thresholds": [str(item) for item in self.crossed_thresholds],
            "notified_thresholds": [str(item) for item in self.notified_thresholds],
        }


def _money(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("Cost Explorer amount is not numeric") from exc
    raise ValueError("Cost Explorer amount is not text")


def _amount(response: Mapping[str, object]) -> Decimal:
    total = Decimal("0")
    raw_results = response.get("ResultsByTime", ())
    if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes)):
        return total
    for raw_result in raw_results:
        if not isinstance(raw_result, Mapping):
            continue
        raw_total = raw_result.get("Total", {})
        if not isinstance(raw_total, Mapping):
            continue
        raw_metric = raw_total.get("UnblendedCost", raw_total.get("BlendedCost", {}))
        if isinstance(raw_metric, Mapping):
            total += _money(raw_metric.get("Amount", "0"))
    return total


def _date(value: object, fallback: date) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value)
    return fallback


def evaluate_budget(
    cost_explorer: CostExplorer,
    slack: SlackPoster | None,
    *,
    start: date,
    end: date,
    already_notified: Iterable[Decimal] = (),
) -> BudgetGuardResult:
    """Query spend and notify once for each newly crossed threshold."""
    response = cost_explorer.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
    )
    amount = _amount(response)
    known = frozenset(already_notified)
    crossed = tuple(threshold for threshold in BUDGET_THRESHOLDS if amount >= threshold)
    newly_crossed = tuple(threshold for threshold in crossed if threshold not in known)
    if slack is not None:
        for threshold in newly_crossed:
            slack.post(
                f"Deadbolt sandbox budget alert: spend is ${amount:.2f}; "
                f"the ${threshold:.0f} threshold has been crossed."
            )
    return BudgetGuardResult(amount, crossed, newly_crossed)


def _thresholds(value: object) -> tuple[Decimal, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(_money(item) for item in value)


def lambda_handler(
    event: Mapping[str, object] | None = None, context: object | None = None
) -> dict[str, object]:
    """Lambda entrypoint; AWS clients are created at the invocation boundary only."""
    del context
    payload = event or {}
    today = datetime.now(UTC).date()
    start = _date(payload.get("start"), today - timedelta(days=1))
    end = _date(payload.get("end"), today)
    client = cast(
        CostExplorer,
        boto3.client("ce", region_name=os.environ.get("AWS_REGION", "us-east-1")),
    )
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    slack: SlackPoster | None = WebhookSlackPoster(webhook) if webhook else None
    result = evaluate_budget(
        client,
        slack,
        start=start,
        end=end,
        already_notified=_thresholds(payload.get("notified_thresholds", ())),
    )
    return result.as_dict()


__all__ = [
    "BUDGET_THRESHOLDS",
    "SCHEDULE_EXPRESSION",
    "BudgetGuardResult",
    "CostExplorer",
    "SlackPoster",
    "WebhookSlackPoster",
    "evaluate_budget",
    "lambda_handler",
]
