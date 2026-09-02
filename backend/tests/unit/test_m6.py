"""M6 approval broker tests: ASL, Slack, bounded negotiation, and audit fields."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import urlencode

import boto3
import pytest
from moto import mock_aws

from deadbolt.broker.card import build_approval_card
from deadbolt.broker.handler import lambda_handler, verify_slack_signature
from deadbolt.broker.negotiate import (
    CLAUDE_HAIKU_MODEL,
    NOVA_LITE_MODEL,
    MemoryProposalStore,
    negotiate_decision,
)
from deadbolt.broker.statemachine import (
    HEARTBEAT_SECONDS,
    STATE_MACHINE_TYPE,
    TIER_TIMEOUT_POLICY,
    build_definition,
    definition_json,
)
from deadbolt.contracts.models import CredentialType, Entitlement, Scope, Tier
from deadbolt.engine.drift import Finding
from deadbolt.plan.builder import Action, Plan
from deadbolt.plan.preimage import preimage_key

pytestmark = pytest.mark.m6
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
HTTP_OK = 200


def _finding() -> Finding:
    entitlement = Entitlement(
        "alice",
        "github",
        "acme/repo",
        Scope.ADMIN,
        NOW - timedelta(days=180),
        NOW - timedelta(days=147),
        CredentialType.PAT,
        True,
        {},
    )
    return Finding(
        entitlement,
        9_000,
        10_000,
        10_000,
        7_740,
        9_154,
        Tier.T3,
        {
            "days_unused": 147,
            "template_version_absent": "v4",
            "blast_radius_count": 31,
        },
    )


def _plan() -> tuple[Plan, Action]:
    action = Action(0, "github", "acme/repo", "admin", "revoke", "admin", "none", "f1", 9_154, "T3")
    body = {
        "schema_version": "1",
        "evaluated_at": "2026-09-03T12:00:00Z",
        "template_version": "v4",
        "weights_version": "v1",
        "actions": [action.as_dict()],
    }
    plan = Plan(
        body,
        {
            "plan_id": "plan-m6",
            "created_at": body["evaluated_at"],
            "trace_id": "trace-m6",
            "attempt": 0,
        },
        (action,),
    )
    return (
        Plan(
            body,
            plan.envelope,
            (
                Action(
                    *action.as_dict().values(),
                    pre_image_key=preimage_key(plan.plan_hash, 0, "acme/repo", "admin"),
                ),
            ),
        ),
        action,
    )


def test_timeout_policy_is_exhaustive_and_asymmetric() -> None:
    assert set(TIER_TIMEOUT_POLICY) == set(Tier)
    assert TIER_TIMEOUT_POLICY[Tier.T1].timeout_seconds == 72 * 60 * 60
    assert TIER_TIMEOUT_POLICY[Tier.T1].on_timeout == "proceed"
    assert TIER_TIMEOUT_POLICY[Tier.T2].timeout_seconds == 24 * 60 * 60
    assert TIER_TIMEOUT_POLICY[Tier.T2].on_timeout == "escalate"
    assert TIER_TIMEOUT_POLICY[Tier.T3].on_timeout == "page"


@mock_aws
def test_generated_standard_state_machine_uses_callback_and_audits_declines() -> None:
    definition = build_definition()
    assert STATE_MACHINE_TYPE == "STANDARD"
    assert definition["StartAt"] == "RouteTier"
    states = cast(dict[str, object], definition["States"])
    notify = cast(dict[str, object], states["NotifyApprover"])
    assert notify["Resource"].endswith(".waitForTaskToken")
    assert notify["HeartbeatSeconds"] == HEARTBEAT_SECONDS
    assert "Wait" not in states
    assert cast(dict[str, object], states["EscalateSecurityAdmin"])["Next"] == "MarkDeclinedTimeout"
    assert cast(dict[str, object], states["PageSecurityOnCall"])["Next"] == "MarkDeclinedTimeout"
    stepfunctions = boto3.client("stepfunctions", region_name="us-east-1")
    created = stepfunctions.create_state_machine(
        name="deadbolt-m6",
        definition=definition_json(),
        roleArn="arn:aws:iam::123456789012:role/deadbolt",
        type="STANDARD",
    )
    assert created["stateMachineArn"].endswith(":deadbolt-m6")


def test_card_contains_evidence_and_exactly_four_actions() -> None:
    plan, action = _plan()
    callback_token = "-".join(("task", "1"))
    card = build_approval_card(_finding(), plan, action, task_token=callback_token)
    blocks = cast(list[dict[str, object]], card["blocks"])
    rendered = json.dumps(card, sort_keys=True)
    assert "147 days ago" in rendered
    assert "version v4" in rendered
    assert "31 reachable identities" in rendered
    actions_block = next(block for block in blocks if block["type"] == "actions")
    actions = cast(list[dict[str, object]], actions_block["elements"])
    assert [str(item["action_id"]) for item in actions] == [
        "approve",
        "reduce_further",
        "keep_with_reason",
        "defer_30_days",
    ]
    assert all("task-1" in str(item["value"]) for item in actions)


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    def complete(self, model_id: str, prompt: str) -> str:
        del prompt
        self.calls.append(model_id)
        return self.response


@pytest.mark.parametrize(
    "response",
    [
        '{"scope":"billing"}',
        '{"scope":"unknown"}',
        "read access is best",
        '{"scopes":["read"]',
    ],
)
def test_adversarial_reduce_responses_never_widen(response: str) -> None:
    plan, action = _plan()
    result = negotiate_decision(
        "Reduce further",
        _finding(),
        plan,
        graph=["read", "write", "admin"],
        llm_client=_FakeLLM(response),
        action=action,
        approver_id="manager-1",
    )
    assert result.scope == "write"
    assert result.accepted_model_scope is False
    assert result.plan is not None
    assert result.plan.actions[0].to_scope == "write"
    assert result.plan.actions[0].verb == "downgrade"


def test_reduce_accepts_only_narrow_graph_scope_and_calls_nova() -> None:
    plan, action = _plan()
    model = _FakeLLM('{"scope":"write"}')
    result = negotiate_decision(
        "Reduce further",
        _finding(),
        plan,
        graph=["read", "write", "admin"],
        llm_client=model,
        action=action,
    )
    assert result.scope == "write"
    assert result.accepted_model_scope is True
    assert model.calls == [NOVA_LITE_MODEL]
    assert result.audit_record["plan_hash"] == plan.plan_hash
    assert result.audit_record["trace_id"] == "trace-m6"


def test_keep_writes_unratified_proposal_using_claude() -> None:
    plan, action = _plan()
    model = _FakeLLM("Keep this access because of on-call rotation.")
    store = MemoryProposalStore()
    result = negotiate_decision(
        "Keep, with reason",
        _finding(),
        plan,
        llm_client=model,
        action=action,
        approver_id="manager-1",
        reason="on-call rotation",
        proposal_store=store,
    )
    assert model.calls == [CLAUDE_HAIKU_MODEL]
    assert result.proposal_id == "proposal-1"
    assert store.records[0]["ratified_template_mutated"] is False
    assert store.records[0]["status"] == "pending_human_ratification"
    assert result.audit_record["acted"] is False


class _FakeStepFunctions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_task_success(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {}


class _FakeAudit:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def write(self, record: dict[str, object]) -> str:
        self.records.append(record)
        return "audit-1"


def test_slack_signature_negative_cases_and_handler_audits_decision() -> None:
    signing_key = "secret"
    timestamp = "1778452800"
    body = urlencode(
        {
            "payload": json.dumps(
                {
                    "user": {"id": "U123"},
                    "actions": [
                        {
                            "action_id": "defer_30_days",
                            "value": json.dumps(
                                {
                                    "task_token": "token-1",
                                    "finding_id": "f1",
                                    "plan_hash": "hash-1",
                                    "trace_id": "trace-1",
                                }
                            ),
                        }
                    ],
                }
            )
        }
    )
    signature = (
        "v0="
        + hmac.new(
            signing_key.encode(), f"v0:{timestamp}:{body}".encode(), hashlib.sha256
        ).hexdigest()
    )
    assert verify_slack_signature(signing_key, timestamp, body, signature, now=float(timestamp))
    assert not verify_slack_signature(signing_key, timestamp, body, "v0=bad", now=float(timestamp))
    assert not verify_slack_signature(
        signing_key, str(int(timestamp) - 301), body, signature, now=float(timestamp)
    )
    stepfunctions = _FakeStepFunctions()
    audit = _FakeAudit()
    result = lambda_handler(
        {
            "headers": {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature},
            "body": body,
        },
        stepfunctions_client=stepfunctions,
        audit_writer=audit,
        signing_secret=signing_key,
        now=float(timestamp),
    )
    assert result["statusCode"] == HTTP_OK
    assert stepfunctions.calls[0]["taskToken"] == "token-1"
    assert audit.records[0]["approver_id"] == "U123"
    assert audit.records[0]["plan_hash"] == "hash-1"
    assert audit.records[0]["trace_id"] == "trace-1"
    assert audit.records[0]["acted"] is False
