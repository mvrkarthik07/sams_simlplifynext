# Deadbolt

The backend terminal rehearsal is intentionally independent of the SPA:

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

The IAM and GitHub connectors are Tier A real providers. Salesforce and Workday remain
deterministic fixture providers behind the same `EntitlementProvider` contract.

## Audit and telemetry configuration

Audit events are written to an S3 bucket with Object Lock and a per-plan SHA-256 chain. The
sandbox must set `AUDIT_OBJECT_LOCK_MODE=GOVERNANCE`; production deployments must set
`AUDIT_OBJECT_LOCK_MODE=COMPLIANCE`. The writer reads the mode from configuration and never
chooses a production mode implicitly. `AUDIT_RETENTION_DAYS` controls the object retention
period (the default is one day for the low-cost rehearsal).

Lambda functions use the AWS Distro for OpenTelemetry (ADOT) Lambda layer. Configure its OTLP
collector endpoint with `OTEL_EXPORTER_OTLP_ENDPOINT`; the default local collector endpoint is
`http://localhost:4318`. CloudWatch log groups use a one-day retention policy.
