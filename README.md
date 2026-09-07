<p align="center">
  <img src="frontend/public/brand/logo-tile.svg" alt="Deadbolt" width="72" />
</p>

<h1 align="center">Deadbolt</h1>

<p align="center">
  Access governance for teams that need to see entitlement drift, decide safely, and prove what happened.
</p>

<p align="center">
  <a href="http://deadboltstack-spabucket48e1059f-lkktwfgep3hp.s3-website-us-east-1.amazonaws.com">Open the live dashboard</a>
  ·
  <a href="https://drive.google.com/file/d/DEMO_VIDEO_ID/view?usp=sharing">Watch the demo video</a>
  ·
  <a href="backend/docs/DEMO_RUNBOOK.md">Run the demo locally</a>
</p>

<p align="center">
  <img src="frontend/src/assets/hero.png" alt="Deadbolt layered access-control mark" width="220" />
</p>

Deadbolt turns access drift into a reviewable queue. It normalizes entitlements from connected systems, scores risk with deterministic rules, builds a reproducible remediation plan, and records every decision in an audit trail. The approval broker can explain a finding or propose a narrower scope, while deterministic code owns scoring, ordering, authorization, execution, and rollback.

## See it in action

| Surface | Link | What to show |
| --- | --- | --- |
| Dashboard | [Open Entitlement register](http://deadboltstack-spabucket48e1059f-lkktwfgep3hp.s3-website-us-east-1.amazonaws.com) | Capture provenance, risk rails, grouped identities, plans, and review decisions |
| MCP | `https://thabg6ly2uehff7x2yxvtci2qy0gscfp.lambda-url.us-east-1.on.aws/mcp` | Query findings, inspect plans, read metrics, and review the audit log |
| Demo video | [Watch on Google Drive](https://drive.google.com/file/d/DEMO_VIDEO_ID/view?usp=sharing) | A short operator walkthrough from capture to audited decision |

The hosted demo uses Cognito login and a redacted GitHub capture. It contains no customer data or provider credentials. The Google Drive URL is the shared-video slot for the team demo; replace `DEMO_VIDEO_ID` with the file ID of the uploaded recording before distribution.

## The product loop

1. **Connect** a provider through the operator-only Connections page. Secrets are stored as subject-scoped SSM SecureStrings and never returned to the browser.
2. **Capture** a read-only snapshot. Deadbolt promotes only the verified snapshot into the review dataset.
3. **Detect** drift across identity, resource, scope, dormancy, role mismatch, and blast radius signals.
4. **Decide** from a finding detail panel. Reviewers see evidence, the proposed plan, raw identifiers, and the plan hash before acting.
5. **Verify** the result and retain the decision in the append-only audit trail. Reversible actions retain a pre-image for rollback.

## Why Deadbolt

- **A queue instead of a spreadsheet.** Findings are grouped by identity, filterable by tier and source, and readable at a glance.
- **Risk with evidence.** Scores are shown with their band, tier, supporting signals, and a non-color risk rail that remains legible in grayscale.
- **Plans you can reproduce.** The same graph and evaluation time produce the same `plan_hash`, while each run receives a distinct `plan_id`.
- **Human control at the boundary.** Approval actions require an explicit review decision. Observe-only entitlements can be retained with a reason without pretending that a provider mutation occurred.
- **A bounded model role.** The LLM may draft an explanation or propose a non-widening scope adjustment. It does not score findings, order actions, authorize writes, hash plans, execute revocations, or verify rollback.

## A five-minute demo

Start at the dashboard and use this sequence:

1. Open the Entitlement register and point out the capture timestamp, source status, health line, and result count.
2. Filter to a high-risk tier, then open a finding such as `FIND-001` to show evidence and the deterministic plan hash.
3. Open a grouped identity with team access to show the difference between direct repository access and team membership.
4. Open an observe-only team entitlement, enter a reason, and record **Keep access**. This records the human decision without mutating GitHub.
5. Open Audit Trail and show the decision, approver, reason, plan hash, and timestamp.
6. In an MCP client, call `get_metrics`, `list_findings`, `get_plan`, and `get_audit_log` to show the same evidence through an agent interface.

For a safe action preview, ask the MCP client to prepare an approval or a narrower-scope change and explain the current plan hash. Do not pass `confirm=true` during a presentation unless the audience has explicitly approved the demo action.

## What is live and what is rehearsed

| System | Read path | Write path | Current demo mode |
| --- | --- | --- | --- |
| GitHub | Provider and redacted capture loader | Direct repository, team, and organization operations are capability-aware | Capture-backed dashboard; live credentials are never bundled |
| AWS IAM | Provider and sandbox rehearsal | Policy detach with rollback | Fixture-backed dashboard; live test requires a disposable IAM user |
| Slack | Provider | Reversible provider path | Fixture-backed |
| Notion | Provider | Reversible provider path | Fixture-backed |
| Salesforce | JWT/SOQL provider path | Permission-set assignment path | Fixture-backed until Connected App credentials are rehearsed |
| GitHub Enterprise | Cloud/Server provider path | Capability-aware | Fixture-backed until enterprise credentials are rehearsed |
| Workday | Read-only worker and security-group path | None | Fixture-backed |

The hosted dashboard and MCP surface are authenticated. API and MCP Function URLs use transport-level `NONE` so clients can reach them, while the application boundary returns `401` without a valid Cognito token when deployed with `requireAuth=true`. Schedules are disabled.

## Architecture

```text
provider snapshots -> normalized entitlement graph -> deterministic drift engine
        |                                               |
        v                                               v
   capture store -> findings -> plan builder -> approval broker
                                             |
                                             v
                                  executor -> verify -> audit / rollback
```

The engine and plan builder are pure and deterministic. Provider adapters handle I/O. The broker is the only request-path LLM boundary. The executor takes pre-images, honors idempotency, performs dry-run and apply phases, verifies the result, and writes a hash-chained audit record.

## Run locally

The fixture demo needs no provider credentials and does not mutate AWS or a SaaS provider.

```bash
git clone https://github.com/mvrkarthik07/sams_simlplifynext.git
cd sams_simlplifynext/backend
uv sync --all-extras --dev
make demo-reset
make demo-run
```

In another terminal, start the SPA:

```bash
cd frontend
npm ci --no-audit --no-fund
npm run dev
```

Open `http://localhost:5173`. For the redacted GitHub capture instead of the seeded fixture, start the API with:

```bash
DEADBOLT_CAPTURE_DIR=backend/artifacts/captures \
  uv run python -m deadbolt.api
```

The SPA uses the HTTP API configured by `VITE_API_BASE`; it has no in-memory mock transport.

## Deploy the authenticated demo

Use the [deployment runbook](backend/infra/DEPLOY.md). The short version is:

```bash
export AWS_PROFILE=hackathon
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity

cd backend/infra
npx cdk deploy DeadboltStack \
  --profile "$AWS_PROFILE" \
  --region "$AWS_DEFAULT_REGION" \
  --require-approval never \
  -c requireAuth=true
```

Build the frontend with the emitted API and Cognito values, then sync `frontend/dist` to the emitted `SpaBucketName`. Keep `enableSchedules` disabled for a demo account. Never place GitHub PATs, Salesforce keys, Workday tokens, or Cognito secrets in source, build output, chat, or CloudFormation parameters.

## Verification

Frontend checks:

```bash
cd frontend
npm run lint
npm run typecheck
npm run build
```

Backend checks:

```bash
cd backend
make verify
make guard
```

Milestone gates are available from the repository root:

```bash
make -f backend/GNUmakefile gate-m10
make -f backend/GNUmakefile gate-m11
make -f backend/GNUmakefile gate-m12
make -f backend/GNUmakefile gate-m12r
make -f backend/GNUmakefile gate-m13
```

## Repository map

```text
frontend/                 authenticated React dashboard
backend/src/deadbolt/    providers, graph, engine, plans, broker, executor, API, MCP
backend/infra/            AWS CDK stack and deployment runbook
backend/tests/            unit, integration, gate, fixture, and live rehearsals
backend/artifacts/        redacted captures and contract evidence
```

## Cost and cleanup

The demo stack uses on-demand Lambda, DynamoDB pay-per-request, a small S3 site, and one-day CloudWatch retention. No NAT Gateway, EC2, RDS, OpenSearch, SageMaker endpoint, or load balancer is provisioned. Destroy the rehearsal stack when the demo is over:

```bash
cd backend/infra
npx cdk destroy DeadboltStack \
  --profile hackathon \
  --region us-east-1 \
  --force
```

Deadbolt is designed to make access decisions reviewable, reversible where possible, and accountable by default.
