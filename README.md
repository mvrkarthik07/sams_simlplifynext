# Deadbolt

Deadbolt helps security and identity teams detect entitlement drift and negotiate safe, reversible access cleanup across SaaS systems.

## Live demo links

These are the last deployed fixture-and-capture-backed endpoints. The infrastructure now supports
an authenticated redeploy; until that redeploy is completed, treat these legacy links as public and
do not put customer data or provider credentials behind them:

- Dashboard: http://deadboltstack-spabucket48e1059f-lkktwfgep3hp.s3-website-us-east-1.amazonaws.com
- API base: https://lxrh6ikc4jyhzphnvvupuffkhq0uhuhk.lambda-url.us-east-1.on.aws/api
- MCP endpoint: https://thabg6ly2uehff7x2yxvtci2qy0gscfp.lambda-url.us-east-1.on.aws/mcp

The currently published links are legacy fixture-and-capture demo endpoints and require the next
authenticated redeploy to receive the latest dashboard and capture-only behavior. Do not put
production credentials or customer data into these public demo URLs.

For the submission-ready deployment, run the [auth-enabled deployment runbook](backend/infra/DEPLOY.md)
with `-c requireAuth=true`, then rebuild the dashboard with the emitted Cognito pool and client IDs.
The browser signs in through Cognito and sends a short-lived ID-token bearer header to both the API
and MCP endpoint.

## What is real

| System | Read | Revoke | Rollback | Data source | Why |
|---|---|---|---|---|---|
| AWS IAM | Implemented provider and sandbox rehearsal | Implemented, reversible policy detach | Implemented | Fixture in normal demo; throwaway live test only | Safe live rehearsal is limited to a disposable IAM user. |
| GitHub | Implemented provider; redacted capture loader | Implemented for the real provider, disabled in demo | Implemented for the real provider | Fixture unless an owner-provided capture is installed | The public demo must not carry a live PAT. |
| Slack | Implemented provider | Implemented, reversible | Implemented | Fixture | No live token is bundled. |
| Notion | Implemented provider | Implemented, reversible | Implemented | Fixture | Not credentialed for this submission. |
| Salesforce | Real-shaped JWT/SOQL provider | Permission-set assignment path implemented | Assignment restore implemented | Fixture | No Connected App credentials were supplied. |
| GitHub Enterprise | Real-shaped Cloud/Server provider | Capability-aware path implemented | Key path only where reversible | Fixture | No enterprise PAT or Server base URL was supplied. |
| Workday | Real-shaped read-only provider | No | No | Fixture | No tenant or sandbox credentials were supplied. |

The browser approval and rollback controls are fixture-only in this milestone. They do not call a live provider.

## Operator connections

After the authenticated redeploy, operators can open **Connections** in the dashboard and configure
GitHub, Salesforce, or Workday. The API stores each operator's configuration in SSM SecureString
under a subject-hashed path and returns status metadata only. The connection test performs a
read-only provider snapshot; Workday is always read-only. Operators can explicitly choose
**Scan into dashboard** to promote that verified snapshot into the review dataset; the scan is
read-only and does not change provider access.

## Deterministic plans

For the same graph and `evaluated_at`, Deadbolt produces the same `plan_hash`; each run still receives a distinct `plan_id`. Reproduce that claim with:

```bash
cd backend
uv run pytest -q -m e2e -s tests/e2e/test_full_cycle.py
```

The test asserts equal hashes and different IDs, alongside the 20-planted-finding rehearsal evidence.

## 90-second local quickstart

Clone this repository, then run:

```bash
cd sams_simlplifynext/backend
uv sync --all-extras --dev
make demo-run
```

Open the dashboard at `http://localhost:5173` after starting the local API and frontend:

```bash
uv run python -m deadbolt.api
```

In a second terminal:

```bash
cd sams_simlplifynext/frontend
npm ci --no-audit --no-fund
npm run dev
```

The local SPA always uses the HTTP API configured by `VITE_API_BASE`. For a real-data local rehearsal,
start the API with `DEADBOLT_CAPTURE_DIR=backend/artifacts/captures`; the checked-in fixture scenario
remains available through `make demo-run` for deterministic tests.

## Live AWS rehearsal

Use only the hackathon account in `us-east-1` and a throwaway IAM user. Never run the live test against an employee, production, or personal identity. The test revokes one disposable policy and restores it before finishing.

```bash
cd backend
export AWS_DEFAULT_REGION=us-east-1
export DEADBOLT_LIVE_IAM_USER=<throwaway-iam-user>
aws sts get-caller-identity --region us-east-1
uv run pytest -q -m live tests/live/test_sandbox_iam.py
```

The GitHub capture command requires owner-provided `GITHUB_ORG` and `GITHUB_TOKEN` environment variables. It writes only the redacted canonical artifact and manifest; it never prints the token.

## Architecture and agent boundary

```text
provider snapshots -> graph store -> pure drift engine -> canonical plan/hash
        |                                  |
        v                                  v
     findings <- HTTP dashboard       approval broker <- one bounded LLM proposal
        |                                  |
        +---------------------------> executor -> verify -> audit/rollback
```

The LLM may propose an explanation or a non-widening scope adjustment inside the broker. It does not score risk, choose plan ordering, hash plans, authorize actions, execute revocations, or verify rollback. Those boundaries are deterministic code. See [the architecture note](backend/docs/ARCHITECTURE.md) and [the demo runbook](backend/docs/DEMO_RUNBOOK.md).

## Cost posture

The deployed rehearsal is near-zero idle cost: Lambda and Function URLs are on demand, DynamoDB uses pay-per-request capacity, S3 stores a small static site and audit artifacts, and CloudWatch logs retain for one day. Recurring schedules are disabled by default.

The stack deliberately has no NAT Gateway, ALB, EC2, RDS, OpenSearch, SageMaker endpoint, or provisioned capacity. Destroy the stack after the demo to avoid storage and retained-object charges:

```bash
cd backend/infra
npx cdk destroy DeadboltStack --force
```

## Tests and gates

The current non-live suite passes 125 tests with 85.02% coverage. Run the milestone gates from the repository root:

```bash
make -f backend/GNUmakefile gate-m10
make -f backend/GNUmakefile gate-m11
make -f backend/GNUmakefile gate-m12
make -f backend/GNUmakefile gate-m13
make -f backend/GNUmakefile gate-m12r
```

The seeded end-to-end rehearsal is also available directly:

```bash
cd backend
make demo-reset
make demo-run
```

M14 is intentionally not enabled: its mandatory post-submission prerequisites—authenticated public endpoints, a least-privilege SSM PAT, an expiring fine-grained token, and a separate rehearsal—are not satisfied.
