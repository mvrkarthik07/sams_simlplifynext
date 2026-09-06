# Deadbolt demo runbook

This is the low-cost, fixture-backed rehearsal for the dashboard and MCP endpoint. It does not require provider credentials and it does not mutate AWS or a SaaS provider.

The hosted submission build uses Cognito for operator authentication. The local build intentionally
uses demo mode, while the backend auth boundary is enabled only by the CDK `requireAuth=true`
context flag.

When `backend/artifacts/captures/github.json` exists, start the local HTTP API with
`DEADBOLT_CAPTURE_DIR=artifacts/captures`; both the local HTTP API and local MCP server then use
that redacted GitHub capture only. This gives the dashboard and MCP the same data.

## Before the demo

1. Select the hackathon AWS profile and confirm the account and `us-east-1` region.
2. Confirm schedules are disabled in the deployed stack.
3. Run the local gates and the seeded rehearsal.

```bash
cd backend
export DEADBOLT_CAPTURE_DIR=artifacts/captures
make verify
./scripts/guard_protected.sh
make demo-reset
make demo-run
```

The rehearsal emits the 20-planted-finding evidence and writes `backend/artifacts/m8-metrics.json`.

## Dashboard walkthrough

Open the deployed dashboard link and sign in with the Cognito operator. Start on Overview, sort or
filter the findings, open a finding, show its provenance badge and evaluated timestamp, then use
Re-run Engine. The panel should show the same plan hash and a new plan ID. Approval and rollback
controls are fixture-only and require the current plan hash through the backend API.

Use Audit Trail to show the engine rerun and decision records. Policy Tiers is explanatory and has no live write control.

## MCP walkthrough

Connect the MCP URL with the Cognito ID-token bearer header, then call `list_findings`, `get_plan`,
`get_metrics`, and `get_audit_log`. For a state-changing rehearsal tool, pass `confirm=true` and
the current `expected_plan_hash`. A stale hash or missing confirmation must be rejected.

## Optional live IAM rehearsal

Only use a throwaway IAM user with one reversible policy. Set `DEADBOLT_LIVE_IAM_USER`, verify the account, and run the marked live test:

```bash
export AWS_DEFAULT_REGION=us-east-1
export DEADBOLT_LIVE_IAM_USER=<throwaway-iam-user>
aws sts get-caller-identity --region us-east-1
uv run pytest -q -m live tests/live/test_sandbox_iam.py
```

Do not use the legacy public links for real-provider credentials. The Connections page is safe only
after the authenticated redeploy; it stores secrets in SSM SecureString and exposes read-only test
results. The current local rehearsal uses an in-memory connection store.

For a score-varied GitHub capture, use a throwaway organization with at least three repositories
and deliberately different permissions (admin, write, and read), then capture them explicitly:

```bash
cd backend
GITHUB_ORG=<throwaway-org> GITHUB_TOKEN=<short-lived-fine-grained-token> \
  uv run python -m deadbolt.cli capture \
  --github-repo <throwaway-org>/admin-repo \
  --github-repo <throwaway-org>/write-repo \
  --github-repo <throwaway-org>/read-repo
```

The command writes only the redacted capture artifact. Never commit the token or place it in the
frontend build.

## Cleanup

Disable any temporary credentials and destroy the stack after the event:

```bash
cd backend/infra
npx cdk destroy DeadboltStack --force
```

If an Object Lock retention window is active, S3 may require the retention window to expire before the bucket can be removed.
