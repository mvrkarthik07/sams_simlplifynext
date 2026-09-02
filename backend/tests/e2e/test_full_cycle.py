"""M8 seeded full-cycle evidence: recall, safety, rollback, and determinism."""

from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from deadbolt.audit.writer import AuditWriter
from deadbolt.executor.idempotency import IdempotencyStore
from deadbolt.executor.rollback import RollbackExecutor
from deadbolt.executor.run import ExecutionResult, Executor
from deadbolt.metrics import MetricKey, calculate_metrics, metrics_table, write_metrics
from scenarios.priya import DEFAULT_EVALUATED_AT, SCENARIO_SYSTEMS, build_scenario

pytestmark = [pytest.mark.e2e, pytest.mark.m8]
MIN_RECALL_COUNT = 19
MIN_RECALL_PERCENT = 95.0
PERFECT_PERCENT = 100.0


def _table(ddb: object, name: str) -> None:
    ddb.create_table(
        TableName=name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _key(identity_id: str, system: str, resource: str, scope: str) -> MetricKey:
    return identity_id, system, resource, scope


def _finding_key(finding: object) -> MetricKey:
    entitlement = finding.entitlement
    return _key(
        entitlement.identity_id,
        entitlement.system,
        entitlement.resource,
        entitlement.scope.value,
    )


def _action_keys(plan: object, findings: tuple[object, ...]) -> tuple[MetricKey, ...]:
    actions = plan.actions
    result: list[MetricKey] = []
    for action in actions:
        matches = tuple(
            finding
            for finding in findings
            if finding.entitlement.system == action.system
            and finding.entitlement.resource == action.resource
            and finding.entitlement.scope.value == action.scope
        )
        assert len(matches) == 1
        result.append(_finding_key(matches[0]))
    return tuple(result)


@pytest.mark.e2e
@pytest.mark.m8
@mock_aws
def test_full_cycle_emits_metric_evidence_and_proves_determinism(
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = build_scenario()
    assert tuple(provider.system for provider in scenario.providers) == SCENARIO_SYSTEMS
    evaluated_at = DEFAULT_EVALUATED_AT
    findings = scenario.findings(evaluated_at)
    planted = {item.key for item in scenario.expected_findings}
    detected = {_finding_key(item) for item in findings}
    missing = sorted(planted - detected)
    print(f"M1 miss list: {missing}")
    assert len(detected & planted) >= MIN_RECALL_COUNT

    first_plan = scenario.plan(evaluated_at)
    second_plan = scenario.plan(evaluated_at)
    assert first_plan.plan_hash == second_plan.plan_hash
    assert first_plan.plan_id != second_plan.plan_id

    ddb = boto3.client("dynamodb", region_name="us-east-1")
    _table(ddb, "m8-locks")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="m8-artifacts")
    locks = IdempotencyStore("m8-locks", ddb, wait_seconds=1)
    audit = AuditWriter("m8-artifacts", s3)
    executors = {
        provider.system: Executor(
            provider,
            locks,
            s3,
            "m8-artifacts",
            audit_writer=audit,
            audit_bucket="m8-artifacts",
        )
        for provider in scenario.providers
    }
    execution_results: list[ExecutionResult] = []
    for action in first_plan.actions:
        execution_results.append(executors[action.system].execute(first_plan, action))
    assert all(item.ok for item in execution_results)

    # M2 is calculated against the ratified template set, not against the detector's output.
    executed_keys = _action_keys(first_plan, findings)
    in_policy = {item.key for item in scenario.ratified_entitlements}
    assert not set(executed_keys) & in_policy

    rollback_results = []
    for action in first_plan.actions:
        rollback_results.append(
            RollbackExecutor(
                scenario.provider_for(action.system),
                locks,
                s3,
                "m8-artifacts",
                audit_writer=audit,
                audit_bucket="m8-artifacts",
            ).rollback(first_plan.plan_id, action.seq)
        )
    rollback_successes = sum(result.ok for result in rollback_results)
    assert rollback_successes == len(execution_results)

    metrics = calculate_metrics(
        planted,
        detected,
        executed_keys,
        in_policy,
        rollback_successes,
        hr_event_at=scenario.hr_event_at,
        execution_times=(scenario.hr_event_at for _ in execution_results),
    )
    artifact = write_metrics(metrics, Path(__file__).parents[2] / "artifacts" / "m8-metrics.json")
    print(metrics_table(metrics))
    print(f"metrics artifact: {artifact}")
    assert metrics.m1_recall_percent >= MIN_RECALL_PERCENT
    assert metrics.m2_false_revocations == 0
    assert metrics.m5_reversibility_percent == PERFECT_PERCENT
    assert artifact.exists()
    assert capsys.readouterr().out.count("M1 miss list:") == 1
