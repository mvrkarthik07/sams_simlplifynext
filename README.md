# Deadbolt

Deadbolt is a deterministic entitlement-drift detector and reversible remediation broker for
the joiner–mover–leaver lifecycle. The architecture diagram and data model are maintained in
the [PRD architecture section](PRD_Deadbolt_SaaS_Drift_Access_Negotiator.pdf); the runtime path is connector snapshot →
DynamoDB/S3 graph → pure drift engine → hashed plan → approval/executor → verified rollback.

## Reproducible demo

The complete seeded rehearsal is two commands from `backend/` and is safe to repeat:

```bash
cd backend
make demo-reset
make demo-run
```

`demo-run` executes `tests/e2e/test_full_cycle.py`, prints M1/M2/M3/M5, and writes
`artifacts/m8-metrics.json`. The scenario contains one mover, one leaver, and 20 planted findings
across AWS IAM, GitHub, Slack, Notion, Salesforce, and Workday, plus one ratified in-policy
entitlement that must not be revoked.

The AWS IAM, GitHub, Slack, and Notion connectors are Tier A real-provider interfaces; Salesforce
and Workday are Tier B deterministic fixtures behind the same `EntitlementProvider` contract.
The offline demo uses local seeds for every system, while the explicit live rehearsal uses the
throwaway IAM user and GitHub repository configured for the sandbox. No live credentials are
needed by CI or `make demo-run`.

The `budget_guard` Lambda is intended to run on EventBridge’s `rate(6 hours)` schedule, query
Cost Explorer, and notify Slack once at each newly crossed $5, $10, and $14 threshold.

The lower-level terminal rehearsal remains available:

```bash
cd backend
uv run python -m deadbolt.cli snapshot --dry-run
uv run python -m deadbolt.cli detect --dry-run
uv run python -m deadbolt.cli plan --dry-run
uv run python -m deadbolt.cli execute --dry-run
uv run python -m deadbolt.cli rollback --dry-run
```

For the sandbox IAM revoke/restore test, configure a throwaway user and run the live test
explicitly; it is never part of CI:

```bash
cd backend && AWS_DEFAULT_REGION=us-east-1 uv run pytest -q -m live tests/live/test_sandbox_iam.py
```

## Exact live-demo rehearsal checklist

1. Confirm the AWS account is the Innovation Sandbox in `us-east-1` and the budget guard is active.
2. Confirm `DEADBOLT_LIVE_IAM_USER` is a throwaway user with a reversible attached policy.
3. Confirm `GITHUB_ORG`, `GITHUB_TOKEN`, and one throwaway `owner/repository` are set.
4. From `backend/`, run `make demo-reset` and then `make demo-run`; show the 20/20 recall and the JSON artifact.
5. Point to the in-policy GitHub read entitlement and show M2 is exactly zero.
6. Run the live IAM test with `AWS_DEFAULT_REGION=us-east-1 uv run pytest -q -m live tests/live/test_sandbox_iam.py`.
7. On stage, run the deterministic test twice at the same `evaluated_at`; show equal `plan_hash`
   values and different `plan_id` values.
8. Show one approved action’s dry-run, apply, audit record, and verified rollback; leave the
   sandbox with the throwaway IAM user restored.
9. Confirm `artifacts/m8-metrics.json` is the artifact cited in the pitch and delete no audit data.

## Audit and telemetry configuration

Audit events are written to an S3 bucket with Object Lock and a per-plan SHA-256 chain. The
sandbox must set `AUDIT_OBJECT_LOCK_MODE=GOVERNANCE`; production deployments must set
`AUDIT_OBJECT_LOCK_MODE=COMPLIANCE`. The writer reads the mode from configuration and never
chooses a production mode implicitly. `AUDIT_RETENTION_DAYS` controls the object retention
period (the default is one day for the low-cost rehearsal).

Lambda functions use the AWS Distro for OpenTelemetry (ADOT) Lambda layer. Configure its OTLP
collector endpoint with `OTEL_EXPORTER_OTLP_ENDPOINT`; the default local collector endpoint is
`http://localhost:4318`. CloudWatch log groups use a one-day retention policy.

## Deploying the AWS packaging

M9 provisions the low-cost `us-east-1` stack: the on-demand graph table, Object-Lock snapshot,
pre-image, and audit buckets, ARM64 Lambdas, the Standard approval broker, hourly snapshots,
HR-event refresh, SecureString connector parameters, and the static SPA bucket. The CDK app
uses the Vite build output at `frontend/dist` (the repository's case-insensitive checkout may
show this directory as `Frontend/dist`); if it is absent at synth time, it deploys a harmless
placeholder and can be repopulated after `npm run build`.

```bash
cd backend/infra
npm ci
npx cdk synth
npx cdk deploy --all --require-approval never
```

Set each `/deadbolt/connectors/*/credential` SSM SecureString to the real connector credential
after deployment. Do not put credentials in `cdk.json`, source control, or CloudFormation
parameters. The audit writer's object retention mode remains an application deployment setting:
use `GOVERNANCE` in the sandbox and `COMPLIANCE` in production.

To remove the complete rehearsal stack, including its buckets and retained objects, run this
single command from `backend/infra/`:

```bash
npx cdk destroy --all --force
```
