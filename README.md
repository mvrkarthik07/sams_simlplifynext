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
