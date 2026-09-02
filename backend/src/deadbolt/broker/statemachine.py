"""Standard Step Functions definition for the approval broker.

The timeout table is deliberately the policy boundary.  The ASL generator only
translates those values into states; it does not make a second tier decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Literal

from deadbolt.contracts.models import Tier

TimeoutAction = Literal["proceed", "escalate", "page"]


@dataclass(frozen=True, slots=True)
class TierTimeoutPolicy:
    """The safe action and deadline associated with one policy tier."""

    timeout_seconds: int
    on_timeout: TimeoutAction
    destination: str


# T0 never enters the approval wait, but is present so policy coverage is
# exhaustive when the enum gains another tier.
TIER_TIMEOUT_POLICY: Final[dict[Tier, TierTimeoutPolicy]] = {
    Tier.T0: TierTimeoutPolicy(1, "proceed", "ObserveOnly"),
    Tier.T1: TierTimeoutPolicy(72 * 60 * 60, "proceed", "ProceedAfterTimeout"),
    Tier.T2: TierTimeoutPolicy(24 * 60 * 60, "escalate", "EscalateSecurityAdmin"),
    Tier.T3: TierTimeoutPolicy(1, "page", "PageSecurityOnCall"),
}

STATE_MACHINE_TYPE: Final[str] = "STANDARD"
DEFAULT_NOTIFY_FUNCTION_ARN: Final[str] = (
    "arn:aws:lambda:us-east-1:000000000000:function:deadbolt-notify-approver"
)
DEFAULT_AUDIT_FUNCTION_ARN: Final[str] = (
    "arn:aws:lambda:us-east-1:000000000000:function:deadbolt-write-audit"
)
DEFAULT_EXECUTOR_FUNCTION_ARN: Final[str] = (
    "arn:aws:lambda:us-east-1:000000000000:function:deadbolt-executor"
)
DEFAULT_NEGOTIATOR_FUNCTION_ARN: Final[str] = (
    "arn:aws:lambda:us-east-1:000000000000:function:deadbolt-negotiator"
)
DEFAULT_PAGER_FUNCTION_ARN: Final[str] = (
    "arn:aws:lambda:us-east-1:000000000000:function:deadbolt-page-security"
)
HEARTBEAT_SECONDS: Final[int] = 300

_TIER_ENTRY_STATE: Final[dict[Tier, str]] = {
    Tier.T0: "ObserveOnly",
    Tier.T1: "PrepareT1Approval",
    Tier.T2: "PrepareT2Approval",
    Tier.T3: "PageSecurityOnCall",
}
_TIMEOUT_STATE_BY_ACTION: Final[dict[TimeoutAction, str]] = {
    "proceed": "ProceedAfterTimeout",
    "escalate": "EscalateSecurityAdmin",
    "page": "PageSecurityOnCall",
}


def _lambda_task(function_arn: str, *, next_state: str) -> dict[str, object]:
    return {
        "Type": "Task",
        "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": {
            "FunctionName": function_arn,
            "Payload.$": "$",
        },
        "ResultPath": None,
        "Next": next_state,
    }


def _audit_task(function_arn: str, *, next_state: str) -> dict[str, object]:
    state = _lambda_task(function_arn, next_state=next_state)
    state["Parameters"] = {
        "FunctionName": function_arn,
        "Payload": {
            "approver_id.$": "$.approver_id",
            "decision.$": "$.decision",
            "plan_hash.$": "$.plan_hash",
            "trace_id.$": "$.trace_id",
            "acted.$": "$.acted",
        },
    }
    return state


def _choice(variable: str, value: str, next_state: str) -> dict[str, object]:
    return {"Variable": variable, "StringEquals": value, "Next": next_state}


def build_definition(
    *,
    notify_function_arn: str = DEFAULT_NOTIFY_FUNCTION_ARN,
    audit_function_arn: str = DEFAULT_AUDIT_FUNCTION_ARN,
    executor_function_arn: str = DEFAULT_EXECUTOR_FUNCTION_ARN,
    negotiator_function_arn: str = DEFAULT_NEGOTIATOR_FUNCTION_ARN,
    pager_function_arn: str = DEFAULT_PAGER_FUNCTION_ARN,
) -> dict[str, object]:
    """Build valid ASL for a Standard workflow using callback task tokens.

    The caller supplies ``approval_timeout_seconds`` in the input after
    applying the tier table.  The two preparation states below are generated
    from the same table, so T1 and T2 cannot silently drift apart.
    """

    states: dict[str, object] = {
        "RouteTier": {
            "Type": "Choice",
            "Choices": [
                _choice("$.tier", tier.value, _TIER_ENTRY_STATE[tier])
                for tier in (Tier.T0, Tier.T1, Tier.T2, Tier.T3)
            ],
            "Default": "ObserveOnly",
        },
        "PrepareT1Approval": {
            "Type": "Pass",
            "Result": TIER_TIMEOUT_POLICY[Tier.T1].timeout_seconds,
            "ResultPath": "$.approval_timeout_seconds",
            "Next": "NotifyApprover",
        },
        "PrepareT2Approval": {
            "Type": "Pass",
            "Result": TIER_TIMEOUT_POLICY[Tier.T2].timeout_seconds,
            "ResultPath": "$.approval_timeout_seconds",
            "Next": "NotifyApprover",
        },
        "NotifyApprover": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke.waitForTaskToken",
            "HeartbeatSeconds": HEARTBEAT_SECONDS,
            "TimeoutSecondsPath": "$.approval_timeout_seconds",
            "Parameters": {
                "FunctionName": notify_function_arn,
                "Payload": {
                    "task_token.$": "$$.Task.Token",
                    "request.$": "$",
                },
            },
            "Catch": [
                {"ErrorEquals": ["States.Timeout"], "Next": "TimeoutByTier"},
            ],
            "Next": "MarkDecisionDisposition",
        },
        "TimeoutByTier": {
            "Type": "Choice",
            "Choices": [
                _choice(
                    "$.tier",
                    tier.value,
                    _TIMEOUT_STATE_BY_ACTION[TIER_TIMEOUT_POLICY[tier].on_timeout],
                )
                for tier in (Tier.T0, Tier.T1, Tier.T2, Tier.T3)
            ],
            "Default": "ObserveOnly",
        },
        "MarkDecisionDisposition": {
            "Type": "Choice",
            "Choices": [_choice("$.decision", "Approve", "MarkApproved")],
            "Default": "MarkDeclined",
        },
        "MarkApproved": {
            "Type": "Pass",
            "Result": True,
            "ResultPath": "$.acted",
            "Next": "RecordDecision",
        },
        "MarkDeclined": {
            "Type": "Pass",
            "Result": False,
            "ResultPath": "$.acted",
            "Next": "RecordDecision",
        },
        "RecordDecision": _audit_task(audit_function_arn, next_state="RouteDecision"),
        "RouteDecision": {
            "Type": "Choice",
            "Choices": [
                _choice("$.decision", "Approve", "ExecuteApproved"),
                _choice("$.decision", "Reduce further", "ReduceFurther"),
                _choice("$.decision", "Keep, with reason", "KeepWithReason"),
                _choice("$.decision", "Defer 30 days", "Succeed"),
            ],
            "Default": "DeclinedToAct",
        },
        "ExecuteApproved": _lambda_task(executor_function_arn, next_state="Succeed"),
        "ReduceFurther": _lambda_task(negotiator_function_arn, next_state="Succeed"),
        "KeepWithReason": _lambda_task(negotiator_function_arn, next_state="Succeed"),
        "DeclinedToAct": {
            "Type": "Pass",
            "Result": False,
            "ResultPath": "$.acted",
            "Next": "RecordDeclinedDecision",
        },
        "RecordDeclinedDecision": _audit_task(audit_function_arn, next_state="Succeed"),
        "ObserveOnly": {
            "Type": "Pass",
            "Result": False,
            "ResultPath": "$.acted",
            "Next": "RecordObservation",
        },
        "RecordObservation": _audit_task(audit_function_arn, next_state="Succeed"),
        "ProceedAfterTimeout": {
            "Type": "Pass",
            "Result": "Approve",
            "ResultPath": "$.decision",
            "Next": "MarkApprovedTimeout",
        },
        "MarkApprovedTimeout": {
            "Type": "Pass",
            "Result": True,
            "ResultPath": "$.acted",
            "Next": "RecordTimeoutProceed",
        },
        "RecordTimeoutProceed": _audit_task(audit_function_arn, next_state="ExecuteApproved"),
        "EscalateSecurityAdmin": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
                "FunctionName": notify_function_arn,
                "Payload": {
                    "escalation": "security-admin",
                    "request.$": "$",
                },
            },
            "ResultPath": None,
            "Next": "MarkDeclinedTimeout",
        },
        "PageSecurityOnCall": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
                "FunctionName": pager_function_arn,
                "Payload.$": "$",
            },
            "ResultPath": None,
            "Next": "MarkDeclinedTimeout",
        },
        "MarkDeclinedTimeout": {
            "Type": "Pass",
            "Result": False,
            "ResultPath": "$.acted",
            "Next": "RecordTimeoutDecline",
        },
        "RecordTimeoutDecline": _audit_task(audit_function_arn, next_state="Succeed"),
        "Succeed": {"Type": "Succeed"},
    }
    return {
        "Comment": "Deadbolt Standard approval broker",
        "StartAt": "RouteTier",
        "States": states,
    }


def definition_json(**kwargs: str) -> str:
    """Return the generated ASL with stable JSON encoding for snapshots."""
    return json.dumps(build_definition(**kwargs), sort_keys=True, separators=(",", ":"))


def state_machine_config(**kwargs: str) -> dict[str, str]:
    """Return the CreateStateMachine fields that make the workflow Standard."""
    return {"type": STATE_MACHINE_TYPE, "definition": definition_json(**kwargs)}


def timeout_input(tier: Tier) -> dict[str, object]:
    """Return the deterministic timeout input required by ``NotifyApprover``."""
    policy = TIER_TIMEOUT_POLICY[tier]
    return {"tier": tier.value, "approval_timeout_seconds": policy.timeout_seconds}


build_state_machine = build_definition
state_machine_definition = build_definition


__all__ = [
    "DEFAULT_AUDIT_FUNCTION_ARN",
    "DEFAULT_EXECUTOR_FUNCTION_ARN",
    "DEFAULT_NEGOTIATOR_FUNCTION_ARN",
    "DEFAULT_NOTIFY_FUNCTION_ARN",
    "DEFAULT_PAGER_FUNCTION_ARN",
    "HEARTBEAT_SECONDS",
    "STATE_MACHINE_TYPE",
    "TIER_TIMEOUT_POLICY",
    "TierTimeoutPolicy",
    "build_definition",
    "build_state_machine",
    "definition_json",
    "state_machine_config",
    "state_machine_definition",
    "timeout_input",
]
