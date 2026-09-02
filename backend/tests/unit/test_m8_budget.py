"""M8 budget guard test using the moto AWS boundary and a fake Cost Explorer response."""

from datetime import date
from decimal import Decimal

import pytest
from moto import mock_aws

from budget_guard.handler import SCHEDULE_EXPRESSION, evaluate_budget

pytestmark = pytest.mark.m8
EXPECTED_NOTIFICATIONS = 2


class _CostExplorer:
    def get_cost_and_usage(self, **kwargs: object) -> dict[str, object]:
        assert kwargs["TimePeriod"] == {"Start": "2026-09-02", "End": "2026-09-03"}
        return {
            "ResultsByTime": [
                {"Total": {"UnblendedCost": {"Amount": "4.00", "Unit": "USD"}}},
                {"Total": {"UnblendedCost": {"Amount": "10.25", "Unit": "USD"}}},
            ]
        }


class _Slack:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def post(self, message: str) -> None:
        self.messages.append(message)


@mock_aws
def test_budget_guard_sends_each_new_threshold_and_is_idempotent() -> None:
    slack = _Slack()
    result = evaluate_budget(
        _CostExplorer(),
        slack,
        start=date(2026, 9, 2),
        end=date(2026, 9, 3),
        already_notified=(Decimal("5"),),
    )
    assert SCHEDULE_EXPRESSION == "rate(6 hours)"
    assert result.amount == Decimal("14.25")
    assert result.crossed_thresholds == (Decimal("5"), Decimal("10"), Decimal("14"))
    assert result.notified_thresholds == (Decimal("10"), Decimal("14"))
    assert len(slack.messages) == EXPECTED_NOTIFICATIONS
    assert "$10" in slack.messages[0]
    assert "$14" in slack.messages[1]
