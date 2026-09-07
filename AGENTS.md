# AGENTS.md — Deadbolt backend

Codex loads this file automatically on every invocation. It is the standing contract.
Milestone prompts add scope; they never override anything here.

---

## 0. Non-negotiable boundaries

1. **`frontend/` is read-only.** Never create, edit, delete, or move any file under `frontend/`.
   You may read it to learn the API shape the SPA expects. If a backend change would break the
   frontend contract, write the mismatch to `DECISIONS.md` and adapt the *backend*.
2. **No live AWS calls outside `infra/` and `tests/live/`.** All AWS interaction in unit and
   integration tests goes through `moto`. No `boto3` client is constructed at import time.
   No test may read `AWS_PROFILE`, `~/.aws`, or real credentials.
3. **Protected paths.** You may not modify:
   - `backend/Makefile`
   - `backend/.github/workflows/**`
   - `backend/scripts/guard_protected.sh`, `backend/scripts/agent_loop.sh`
   - `backend/tests/gates/**`
   - `backend/src/deadbolt/contracts/**` after the freeze commit tagged `contract-v1`
   `scripts/guard_protected.sh` enforces this by SHA-256. If a gate looks wrong, do **not**
   edit it — write the objection to `DECISIONS.md` and solve the problem in application code.
4. **Never weaken a gate to pass it.** Forbidden: deleting or `xfail`-ing a failing test,
   lowering `--cov-fail-under`, adding `# type: ignore` without a one-line justification comment,
   adding `# noqa` blanket suppressions, loosening a golden hash file.

---

## 0b. Source of truth

`backend/docs/PRD_Deadbolt.pdf` is the product spec. Where this file and the PRD disagree on
*engineering* method, this file wins. Where they disagree on *product* behaviour — tiers,
weights, connector split, demo requirements — the PRD wins and you log the conflict in
`DECISIONS.md`.

---

## 1. Repository map

```
backend/
  pyproject.toml
  src/deadbolt/
    contracts/        # FROZEN after contract-v1. Entitlement, EntitlementProvider,
                      # ActionResult, Finding, Plan, Action, Clock, Tier
    providers/
      aws_iam.py      # Tier A, real
      github.py       # Tier A, real
      slack.py        # Tier A, real (also the approval channel)
      notion.py       # Tier A, real
      fixtures/       # Tier B: salesforce.py, workday.py — same Protocol, seeded data
      registry.py     # config-driven provider construction; swapping Tier B -> Tier A
                      # must be a one-line config change and zero engine change
    graph/
      store.py        # DynamoDB single-table repository (see §4.4 of the PRD)
      snapshot.py     # provider fan-out -> normalized Entitlement records -> store + S3
    engine/           # PURE. No I/O, no clock reads, no network, no randomness.
      scoring.py      # integer basis-point risk function
      drift.py        # graph + templates -> Finding[]
      tiers.py        # score -> Tier, plus break-glass exclusions
    plan/
      canonical.py    # canonical JSON encoder + sha256 plan hashing
      builder.py      # Finding[] -> Plan (ordered Action[], pre-image slots)
    broker/
      card.py         # Slack Block Kit payload construction (pure)
      statemachine.py # Step Functions ASL definition + taskToken glue
      negotiate.py    # LangGraph node: the ONLY LLM call in the request path
    executor/
      idempotency.py  # conditional-write lock on sha256(plan_id|seq|resource|scope)
      run.py          # pre-image -> dry_run -> apply -> verify -> audit
    audit/
      writer.py       # S3 Object Lock audit records
      otel.py         # span helpers
    handlers/         # thin Lambda entrypoints; logic lives in the modules above
  tests/
    unit/  integration/  gates/  fixtures/  live/
frontend/             # READ-ONLY
backend/infra/        # CDK/SAM. Touch only in M9.
backend/prompts/      # milestone prompts (input to backend/scripts/agent_loop.sh)
backend/DECISIONS.md  # append-only decision log
```

---

## 2. Determinism law

The pitch claims byte-identical plans across runs. Everything below is load-bearing for that.

- **No floats anywhere in `engine/` or `plan/`.** All scores are integers in basis points
  (`0..10000`). Where a logarithm is required, use `decimal.Decimal` inside
  `decimal.localcontext(prec=28, rounding=ROUND_HALF_EVEN)` — `math.log10` is libm-dependent
  and is banned in these packages. A ruff rule + an import-linter contract must enforce this.
- **No implicit clock.** Every function that needs "now" takes an explicit `evaluated_at: datetime`
  (UTC, tz-aware). `datetime.now()`/`time.time()` are banned in `engine/` and `plan/`.
  A single `Clock` Protocol is injected at the handler boundary.
- **Canonical encoding.** `json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=True)` over a dict of primitives only. Datetimes serialize as
  `YYYY-MM-DDTHH:MM:SSZ` (second precision, UTC, no offset forms).
- **Action ordering.** Sort actions by `(system, resource, scope)` compared as UTF-8 bytes.
  Never rely on dict or set iteration order.
- **Hash envelope split.** `plan_hash = sha256(canonical(plan.body))`. Mutable metadata —
  `plan_id`, `created_at`, `trace_id`, `attempt` — lives in `plan.envelope` and is **excluded**
  from the hash. Two runs over the same graph at the same `evaluated_at` must produce the same
  `plan_hash` and different `plan_id`s.
- Golden hashes live in `tests/gates/golden/`. They change only via an explicit
  `DECISIONS.md` entry naming the semantic change.

---

## 3. Engineering standards

- Python 3.12. `uv` for dependency management. `ruff` (lint + format), `mypy --strict`.
- Public functions are fully typed. `Any` requires a justification comment.
- Dataclasses are `frozen=True, slots=True`. Domain objects are immutable.
- Errors: raise typed exceptions from `deadbolt.errors`. No bare `except:`. No silent `pass`.
- Logging is structured JSON via `structlog`; never log credentials, PAT values, or `raw`
  payload contents at INFO or above.
- Tests are marked with the milestone that owns them: `@pytest.mark.m3` etc.
- Every provider write path implements `revoke(dry_run=True)` correctly — a dry run must
  perform zero mutations and return the same `ActionResult` shape as a real run.

---

## 4. Working protocol (how you run)

1. **Never ask a clarifying question.** If a requirement is ambiguous, choose the option that
   is (a) reversible, (b) cheaper on AWS, (c) simpler to test — in that order — implement it,
   and append to `DECISIONS.md`:
   `## <ISO date> — <milestone> — <one-line decision>` / `**Ambiguity:** …` / `**Chose:** …` /
   `**Rejected:** …` / `**Reversal cost:** …`
2. **Do not stop at a partial result.** A milestone is finished only when its gate command
   exits 0. Run the gate yourself before you end the turn. If it fails, keep working.
3. **Root-cause, do not paper over.** If a test fails, read the assertion, form a hypothesis,
   verify it with a targeted run, then fix. Do not retry the same edit twice.
4. **Small commits.** Conventional commits (`feat(engine): …`, `test(plan): …`). One commit per
   coherent unit. Never `--force`, never rewrite history, never commit to `main` directly —
   work on the branch you were started on.
5. **No new runtime dependency** without a line in `DECISIONS.md` covering cost and cold-start
   impact. Banned outright: anything requiring a VPC, OpenSearch, SageMaker endpoints,
   Secrets Manager (use SSM Parameter Store SecureString), `pandas`, `numpy` in Lambda paths.
6. **Cost discipline.** Lambda memory defaults to 512 MB, timeout 30 s unless justified.
   CloudWatch log retention is 1 day everywhere.

---

## 5. Definition of done (applies to every milestone)

```
cd backend && make gate-<milestone>   # exits 0
cd backend && make guard              # protected paths unchanged
```

Both must pass. `make gate-*` is the sole arbiter of completion — not your own judgement,
not a summary of what you did.

---

## 6. Current repository handoff — 2026-09-04

This section is the current source-of-truth handoff for the next agent. Read it before changing
the repository; it records what is implemented, what is verified, and what is deliberately not
claimed.

### 6.1 Repository layout and history

The repository root is `/Users/karthik/sams_simlplifynext` locally and the GitHub remote is
`https://github.com/mvrkarthik07/sams_simlplifynext.git`.

```text
AGENTS.md
README.md
PRD_Deadbolt_SaaS_Drift_Access_Negotiator.pdf
frontend/                 # inherited SPA; source is read-only by default
backend/                  # Python application, tests, infra, scripts, docs, prompts
  .github/workflows/ci.yml
  Makefile                # protected
  GNUmakefile             # unprotected extension shim
  src/deadbolt/
  tests/
  infra/
```

The case-only `Frontend/` → `frontend/` rename was owner-approved and committed in
`41628cb`. All repository path linkages were updated, including Makefiles, CI filters, build
commands, CDK asset lookup, prompts, runbooks, AGENTS documentation, and contract artifacts.
The protected manifest was re-blessed with the trailer
`Protected-Change-Approved-By: Karthik`.

The current `main` includes:

- `ee80219` — m5b connector expansion
- `e1f9100` — protected frontend-path decision record
- `41628cb` — lowercase frontend rename and CI typecheck script

The normal rule remains: future agents must not commit directly to `main` or modify protected
files without explicit owner approval and the required approval trailer. The current rename was
an explicit owner-approved exception.

### 6.2 Backend implementation status

The completed milestones are M0 through M9, plus m5b. The backend is deterministic, fixture-safe,
and uses the frozen `EntitlementProvider` contract in `src/deadbolt/contracts/`.

Important implementation areas:

- `engine/` — pure integer basis-point scoring, drift detection, tier selection, no I/O or
  implicit clock reads.
- `plan/` — canonical plan hashing, stable action ordering, distinct `plan_id`, and a guard that
  prevents entitlements marked `raw["reversible"] == false` from being scheduled at T1.
- `executor/` and `audit/` — idempotent execution, pre-images, rollback, audit records, hash
  chains, and OpenTelemetry helpers.
- `providers/aws_iam.py`, `github.py`, `slack.py`, and `notion.py` — existing Tier-A provider
  paths.
- `providers/github_enterprise.py` — Cloud/Server API-base abstraction, capability probing,
  PAT/SAML credential authorization/deploy-key surfaces, pagination, rate-limit handling, and
  irreversible PAT/SAML capability marking.
- `providers/salesforce.py` — pinned v61.0 REST/SOQL path, JWT bearer authentication, permission
  set assignments, object permissions, field permissions, and LoginHistory. Permission-set
  assignment delete/recreate is reversible; OAuth grants and shared permission sets are not
  mutated.
- `providers/workday.py` — real-shaped worker REST/RaaS and SOAP security-group reads plus
  normalized hire/job-change/termination events. Workday access is intentionally read-only.
- `graph/identity.py` — explicit SCIM → SAML → verified email → configured alias precedence;
  unresolved or ambiguous accounts are quarantined. Display-name similarity and fuzzy matching
  are forbidden.
- `providers/registry.py` — fixture/real selection is configuration-driven; adding a provider
  must not create engine coupling.

Credential reality at the last check:

| System | Current status | Tier decision |
|---|---|---|
| GitHub Enterprise Cloud/Server | No enterprise PAT, org, or Server base URL available | Tier B with real-shaped replay tests |
| Salesforce | No Connected App client ID, username, private key, or access token available | JWT/SOQL path implemented; Tier B until authenticated rehearsal |
| Workday | No tenant or credentials; no free sandbox | Tier B, read-only real-shaped path |

Do not claim a live Salesforce, GHE, or Workday integration until credentials are actually
authenticated. Do not implement Salesforce username/password authentication.

### 6.3 Frontend status and backend liaison

The SPA lives under `frontend/`. Its routes and API data shapes remain inherited; the M12R owner
exception permits the presentation refactor and auth shell. The package script remains:

```json
"typecheck": "tsc -b"
```

The SPA's `frontend/src/lib/api.ts` uses the stable HTTP envelope and has no in-memory mock path. It exports these operations:
`getFindings`, `getFinding`, `getPlan`, `getAuditLog`, `getMetrics`, `decideApproval`,
`rerunDriftEngine`, and `executeRollback`. The remote client uses `VITE_API_BASE`, an eight-second
timeout, one network-only retry, and a Cognito ID-token bearer header when auth is enabled.

Therefore the following are true:

- Filesystem/build linkages between `frontend/` and `backend/` are correct and verified.
- The frontend can be linted, typechecked, and built successfully.
- The SPA is wired to drive the backend over HTTP; the hosted browser rehearsal still needs to be
  repeated after the auth-enabled redeploy.
- A real browser → backend → provider end-to-end flow has **not** been completed.
- Keep API operation signatures and backend contracts stable. The M12R component-level exception
  is recorded in `backend/DECISIONS.md`; no component change should alter the transport schema.

The static contract evidence is in:

- `backend/artifacts/contract-inventory.json` — eight exported client operations and their backend
  routes.
- `backend/artifacts/contract-gaps.md` — remaining unused exported domain schemas and any live
  provider limitations.

### 6.4 Test and verification commands

Backend setup and cumulative verification:

```bash
cd backend
uv sync --all-extras --dev
make verify
./scripts/guard_protected.sh
```

m5b-specific gate:

```bash
cd backend
make gate-m5b
```

This gate currently passes m5b connector tests, rollback/determinism tests, the cumulative
backend verification, strict mypy, import architecture, non-live tests, coverage ≥85%, and the
protected-path guard. The last recorded result was 100 non-live tests passing at 85.15% coverage.

Frontend CI-equivalent commands:

```bash
cd frontend
npm ci --no-audit --no-fund
npm run lint
npm run typecheck
npm run build
```

Infrastructure and contract checks:

```bash
cd backend
uv run python -m deadbolt.contracts.export --out artifacts/schema
cd infra
npm ci --no-audit --no-fund
npx cdk synth --quiet
! grep -rniE 'NatGateway|OpenSearch|SageMaker.*Endpoint|LoadBalancerV2|\bec2\.Instance\b|DatabaseInstance|ProvisionedThroughput' lib bin
```

The checked-in workflow is `backend/.github/workflows/ci.yml`. Because the workflow is nested
under `backend/`, GitHub's native workflow discovery may require a human to move it to the root
`.github/workflows/` directory and re-bless the protected path; do not silently relocate or edit
it without owner approval.

### 6.5 Live AWS rehearsal

The safe live test is restricted to a throwaway IAM user and explicitly marked `live`. Configure
standard AWS credentials through the normal AWS SDK chain, use `us-east-1`, and set only the
throwaway target name:

```bash
cd backend
export AWS_DEFAULT_REGION=us-east-1
export DEADBOLT_LIVE_IAM_USER=<throwaway-iam-user>
aws sts get-caller-identity --region us-east-1
uv run pytest -q -m live tests/live/test_sandbox_iam.py
```

The test snapshots the target, revokes one attached or inline policy, and restores it. Never use
an employee, production, or otherwise non-throwaway identity. `make demo-run` remains entirely
fixture-backed and does not touch AWS.

The live-demo README checklist also expects `GITHUB_ORG`, `GITHUB_TOKEN`, and a throwaway
repository for the existing GitHub provider. GHE, Salesforce, and Workday remain unavailable
until their credential reality checks are repeated and successful.

### 6.6 Protected files and generated files

The protected set is enforced by `backend/scripts/guard_protected.sh` and currently includes:

- `backend/Makefile`
- `backend/.github/workflows/ci.yml`
- `backend/scripts/guard_protected.sh`
- `backend/scripts/agent_loop.sh`
- `backend/tests/gates/**`
- `backend/src/deadbolt/contracts/**`

`backend/DECISIONS.md` is append-only. `backend/artifacts/` is mostly runtime output; the
contract inventory, contract gaps, CI artifact, and Makefile integration note are intentionally
tracked. `frontend/node_modules/`, `frontend/dist/`, Python caches, coverage files, and other
build output are generated/ignored and must not be committed.

When a future agent starts, first run `git status --short`, confirm the branch, read this section,
read `backend/DECISIONS.md`, and run the smallest relevant gate before editing. If a requirement
conflicts with this handoff or the PRD, record the decision before implementation.

### 6.7 Current deployed demo handoff — 2026-09-06

The owner deployed `DeadboltStack` successfully to AWS account `623234912950`, Region
`us-east-1`, using the `hackathon` profile. The final CloudFormation status was
`UPDATE_COMPLETE`. Schedules are disabled: `SchedulesEnabled=false`. Do not enable the hourly
snapshot, budget, or HR schedules until real provider handlers, credentials, and authentication
have been reviewed.

Verified public demo outputs:

```text
Dashboard: http://deadboltstack-spabucket48e1059f-lkktwfgep3hp.s3-website-us-east-1.amazonaws.com
API base:  https://lxrh6ikc4jyhzphnvvupuffkhq0uhuhk.lambda-url.us-east-1.on.aws/api
MCP:       https://thabg6ly2uehff7x2yxvtci2qy0gscfp.lambda-url.us-east-1.on.aws/mcp
```

The dashboard returned HTTP 200, the API returned 21 fixture findings, and two consecutive
MCP `initialize` requests returned HTTP 200. The MCP Lambda adapter was changed to construct a
fresh stateless Streamable HTTP application per invocation because the FastMCP session manager is
single-use and Lambda can reuse a warm process. The Lambda asset also includes
`tests/fixtures/scenario/`, which is required by the deterministic scenario manifest.

The deployed API and MCP endpoint are still fixture-only and unauthenticated public Function
URLs. They must not receive real GitHub, AWS, Salesforce, Workday, Slack, or Notion credentials or
customer data. The dashboard's GitHub rows are simulated findings. The real GitHub provider can
currently be rehearsed locally, read-only, with:

```bash
cd backend
export GITHUB_ORG=<org>
read -rsp "GitHub PAT: " GITHUB_TOKEN; echo
export GITHUB_TOKEN
uv run python -m deadbolt.cli --system github --github-org "$GITHUB_ORG" --dry-run snapshot
```

Do not paste that output or the PAT into chat, source files, frontend code, CloudFormation
parameters, or shell history. A future live GitHub dashboard requires authenticated API/MCP
access, provider selection in the hosted handler, least-privilege secret retrieval, and a
separate end-to-end rehearsal. Setting `GITHUB_TOKEN` on a laptop does not affect the deployed
Lambda.

#### Short-term cost estimate

For leaving the current stack idle from approximately 2026-09-06 00:00 through 2026-09-07
00:00 Asia/Singapore, the expected incremental cost is approximately **$0.00 to $0.05 USD**,
assuming no unusual traffic and no unrelated resources in the account. A few manual dashboard or
MCP requests should generally remain below **$0.10 USD** for that period. This is an estimate, not
a billing guarantee; account-level free-tier usage, existing resources, data transfer, and the
CDK bootstrap bucket are outside this stack's isolated estimate.

Cost controls currently in effect:

- Lambda has no idle hourly charge; functions are billed when invoked.
- DynamoDB is pay-per-request with no provisioned capacity and no PITR.
- S3 holds only small demo assets and one-day logs are configured for Lambda log groups.
- Step Functions and EventBridge are idle; no schedules are enabled.
- No EC2, NAT Gateway, RDS, OpenSearch, SageMaker endpoint, or load balancer is deployed by
  this stack.

Before leaving the account unattended, create a low-dollar AWS Budget alert. To stop charges and
remove demo resources after the rehearsal, run from `backend/infra` with a refreshed profile:

```bash
export AWS_PROFILE=hackathon
export AWS_DEFAULT_REGION=us-east-1
aws sso login --profile hackathon  # only if the token is expired
npx cdk destroy DeadboltStack --profile hackathon --region us-east-1 --force
```

The estimate is based on the official pricing models: [Lambda](https://aws.amazon.com/lambda/pricing/),
[DynamoDB](https://aws.amazon.com/dynamodb/pricing/), [S3](https://aws.amazon.com/s3/pricing/),
[CloudWatch](https://aws.amazon.com/cloudwatch/pricing/), [Step Functions](https://aws.amazon.com/step-functions/pricing/),
and [EventBridge](https://aws.amazon.com/eventbridge/pricing/).

### 6.8 M10–M14 completion handoff — 2026-09-06

M10 through M13 are implemented and their repository gates pass. M12R presentation and the
optional Cognito auth boundary are now implemented locally. The SPA API client uses the
stable `data`/`error` envelope, eight-second timeout, one network-only retry, and the deployed
API base from `VITE_API_BASE`; it always reads from the backend. The API Function URL CORS
configuration permits the S3 website origin and local Vite origins for GET/POST requests, with
Lambda Function URL preflight handling. The dashboard now shows loading, empty, retry, evaluated
time, provenance, and deterministic plan-hash/plan-ID evidence. `backend/docs/DEMO_RUNBOOK.md`,
`backend/docs/ARCHITECTURE.md`, and the root README describe the safe capture-backed public
boundary; the currently published links still require a redeploy to receive these changes.

Verified gates:

```text
make -f backend/GNUmakefile gate-m10  # pass
make -f backend/GNUmakefile gate-m11  # pass; live capture remains credential-blocked
make -f backend/GNUmakefile gate-m12  # pass
make -f backend/GNUmakefile gate-m13  # pass
```

The non-live suite is 130 passed with 85.22% coverage. A redacted GitHub capture is present at
`backend/artifacts/captures/github.json`; capture-configured API and MCP processes use that
four-record dataset, while the fixture scenario remains available for offline tests. The new stack
provisions Cognito and validates bearer tokens at API/MCP boundaries
when deployed with `-c requireAuth=true`; the current hosted links predate this change and remain
legacy public links until redeployed. M14's authenticated live GitHub provider path is still not
implemented because SSM PAT retrieval, expiring fine-grained PAT, and separate rehearsal
prerequisites are not verified. Deployment still requires `aws sso login --profile hackathon` when
the cached token is expired.

### 6.9 Connection onboarding and P0 review follow-up — 2026-09-06

The owner-approved frontend exception now includes `frontend/src/pages/Connections.tsx` and its
route. Authenticated operators can save GitHub, Salesforce JWT, and Workday read credentials through
the API; deployed storage is SSM SecureString under a subject-hashed path, and responses never
contain secret fields. The connection test invokes only provider `snapshot()`; it performs no write.
Unauthenticated Lambda requests to `/api/connections` are rejected even if the legacy auth flag is
off. Local rehearsals use an in-memory connection store.

The dashboard now exposes the non-revocable entitlement's forced T0 observe-only override, reports
zero-denominator metrics with explicit counts, and discloses uniform score evidence. The current
GitHub capture still has one repository and read-level permissions, so score variance requires a
new owner-approved throwaway capture; no synthetic variance was added.

### 6.10 m16 completion — dashboard scoring fix and frontend rework — 2026-09-06

`backend/prompts/m16.md` (nine dashboard-quality tasks) is implemented end to end, under the same
owner-approved frontend exception as M12R/6.9. Two backend scoring gaps were also fixed as part of
tracing m16/01 and m16/07 — real fixes, not synthetic variance, so the last paragraph of §6.9 is
superseded on the specific point that no variance had been added:

- `scenarios/priya.py`'s fixture data scored correctly but was structurally uniform (dormancy,
  role mismatch, and blast radius were all pegged to a constant across every planted finding), so
  every finding landed in tier T2. Three `scope` values in `manifest.json` and the matching provider
  fixture JSON were changed (not the identity/system/resource keys, not the planted-finding count)
  so the 20-finding fixture now spans all four tiers. See the `2026-09-06 — m16` entries in
  `backend/DECISIONS.md` for the exact fields and the score math.
- The **live capture path** (`DEADBOLT_CAPTURE_DIR` + a real GitHub capture) reproduced the m16
  prompt's literal symptoms verbatim against a real local rehearsal: every row scored 49, and one
  row's resource read as `github:pat:mvrkarthik07`. Root cause: `_scenario_from_capture` in
  `api.py` hardcoded `reachability={}` for every real capture, even though a real, computable
  co-occurrence signal exists in the capture itself. Added `_reachability_from_snapshot` to derive
  it instead of fabricating one. `templates={}` and the null `last_used_at` were left alone —
  GitHub's collaborator API doesn't expose usage timestamps and there is no role-template source
  for a bare PAT scan, so those stay honestly unmeasured rather than synthesized.

Frontend, per m16 task (`frontend/src/pages/Dashboard.tsx`, `frontend/src/styles/tokens.css`,
`frontend/src/components/Sidebar.tsx`, `frontend/src/App.tsx`):

- **01** Tier badge split into its own sortable column; risk score renders with a named-constant
  band label (`RISK_BANDS`, thresholds 30/60/85) and a non-color height-proportional bar.
- **02** KPI strip models `measured` / `no-events` / `not-wired` explicitly from `metrics.counts`;
  no metric ever renders a bare `0` for "nothing happened," measured values carry sample sizes with
  a low-sample qualifier under `n=10`, and the primary metric (drift recall) is visually emphasized.
- **03** Per-row accessible names naming the finding ID and identity, `aria-sort` plus an
  ascending/descending label on sort headers; contrast was measured before any change (only the old
  amber tier-2 badge at 4.23:1 actually failed the 4.5:1 floor — recorded, then fixed by the new
  ramp below).
- **04** A bulk-select-with-undo feature was built, verified working (real 6-second undo-before-send
  window, confirmation dialog, selection persistence), then **removed entirely** at the owner's
  explicit direction after a live review — the table has no selection column and no bulk action bar.
- **05** Filter chips split into two labeled `role="group"` axes (tier, source) with live per-chip
  counts computed in one traversal, zero-count chips disabled, active chip marked with a check glyph.
- **06/09** Sidebar collapses to an icon rail below 768px (labels visually hidden, not removed from
  the accessibility tree). Fixed a real flexbox bug where the table's old `min-width` forced the
  entire page layout wider than the viewport and pushed the sidebar off-screen (`main` needed
  `min-width: 0`, since flex items default to `min-width: auto`). Per an explicit owner follow-up
  ("I DO NOT WANT TO SEE HORIZONTAL SCROLL AT ALL"), the table no longer scrolls horizontally at
  any width: `table-layout: fixed` plus `overflow-wrap: break-word` keeps it within its container
  above 860px, and below 860px it renders as one labeled card per finding instead of a table row.
  Verified via `document.documentElement.scrollWidth` at a live narrow viewport — no overflow.
- **07** Removed the two ALL-CAPS eyebrows on the dashboard, gave each KPI caption distinct
  phrasing instead of one repeated template, replaced the two boilerplate subtitles, restricted
  monospace to numerals/identifiers. `github:pat:mvrkarthik07` (a real value from the live capture,
  not a hypothetical) is now humanized to "Personal access token — mvrkarthik07" in the table; the
  raw URN is shown only on the finding detail page.
- **08** `tokens.css` restructured into primitive → semantic → component layers. Replaced the old
  three-arbitrary-hue tier ramp (gray/green/orange/red) with one contrast-verified warm ramp
  (neutral → amber → orange → red, ≥4.5:1 in both themes against both background tokens), which also
  separates the three previously-conflated uses of green (connection status, the "Live" badge,
  tier-1) into distinct semantic tokens.

Verified: `npm run typecheck`, `npm run lint` (one pre-existing `set-state-in-effect` warning,
unchanged, shared with `AuditLog.tsx`/`Connections.tsx`), `npm run build`, `uv run pytest -q -m
"not live"` (130 passed), and `make lint types arch`. Live-browser-checked against the real
captured-data local rehearsal (`DEADBOLT_ENVELOPE=1 DEADBOLT_CAPTURE_DIR=artifacts/captures uv run
python -m deadbolt.api`) in both themes, including catching and fixing one real bug live (an
unreadable dark-on-dark Cancel button, since removed along with the rest of the bulk-select UI).
Narrow-viewport behavior below roughly 600px was live-verified in-session; nothing was committed —
`git status` still shows the full set of modified/untracked files from this and prior sessions.

### 6.11 Current handoff — authenticated deployment and GitHub IAM MCP — 2026-09-06

This section supersedes stale deployment statements in sections 6.7–6.10. The repository now
contains an authenticated operator workflow and a scoped live GitHub IAM MCP surface. The latest
local code has been fully checked but must be redeployed before the hosted URLs contain these
latest changes.

#### Hosted AWS outputs

The hackathon account is `623234912950`, region `us-east-1`, and the deployment profile is
`hackathon`. The last successful deployment emitted these URLs and identifiers:

```text
Dashboard: http://deadboltstack-spabucket48e1059f-lkktwfgep3hp.s3-website-us-east-1.amazonaws.com
API base:  https://lxrh6ikc4jyhzphnvvupuffkhq0uhuhk.lambda-url.us-east-1.on.aws/api
MCP:       https://thabg6ly2uehff7x2yxvtci2qy0gscfp.lambda-url.us-east-1.on.aws/mcp
S3 bucket: deadboltstack-spabucket48e1059f-lkktwfgep3hp
Cognito pool: us-east-1_BMRnSNVGV
Cognito client: 2b37s74k262a4lrr5t27i2q5kp
```

The stack was deployed with `requireAuth=true` and `enableSchedules` false. Lambda Function URLs
remain transport-level `NONE`, but the API and MCP handlers validate Cognito ID-token bearer
headers. The API CORS response is owned by the Lambda Function URL configuration; the handler must
not add a second `Access-Control-Allow-Origin` header.

#### Redeploy the current code

Build the frontend with the deployed API and Cognito values before CDK deployment. CDK packages
the existing `frontend/dist`; the explicit S3 sync makes the final asset publication unambiguous.

```bash
cd /Users/karthik/sams_simlplifynext/frontend
VITE_API_BASE="https://lxrh6ikc4jyhzphnvvupuffkhq0uhuhk.lambda-url.us-east-1.on.aws/api" \
VITE_AUTH_REQUIRED=1 \
VITE_COGNITO_REGION=us-east-1 \
VITE_COGNITO_USER_POOL_ID=us-east-1_BMRnSNVGV \
VITE_COGNITO_CLIENT_ID=2b37s74k262a4lrr5t27i2q5kp \
npm run build

cd ../backend/infra
npx cdk deploy DeadboltStack --profile hackathon --region us-east-1 \
  --require-approval never -c requireAuth=true
aws s3 sync ../../frontend/dist s3://deadboltstack-spabucket48e1059f-lkktwfgep3hp \
  --delete --profile hackathon --region us-east-1
```

Do not pass `-c enableSchedules=true` for the hackathon rehearsal. The CDK warning about the
DynamoDB `pointInTimeRecovery` property is a deprecation warning, not a deployment failure.

#### Operator authentication and provider storage

The login screen supports Cognito self-registration with email confirmation. Existing operator
accounts are stored in Cognito. The browser stores only the short-lived Cognito ID token in
`sessionStorage`; it is sent as a bearer token to the API and MCP and expires. Provider secrets
are separate: GitHub PATs, Salesforce keys, and Workday tokens are sent over HTTPS to the
authenticated API and stored as operator-scoped SSM `SecureString` parameters under:

```text
/deadbolt/connections/<sha256(cognito_subject)[:32]>/<provider>
```

Provider secrets must never be returned to the browser, logged, placed in frontend builds, or
pasted into chat. Local development uses `MemoryConnectionStore` and is intentionally not
persistent; deployed Lambda uses `SsmConnectionStore`.

#### Live GitHub IAM MCP surface

After an operator saves a GitHub connection in **Connections**, the authenticated MCP Lambda reads
that operator's GitHub configuration from SSM. The MCP Lambda now has the same least-privilege SSM
read permission as the API Lambda. Salesforce and Workday remain connected/read-only paths and do
not receive MCP write tools in this milestone.

Available GitHub tools:

- `github_inventory`: organization members, repositories, and teams.
- `github_user_access`: one user's organization membership and effective captured access.
- `github_onboard_user`: invite/activate an organization member, assign a repository permission,
  and add team memberships.
- `github_remove_repository_access`: remove direct repository collaborator access.
- `github_remove_team_access`: remove a user from a team.
- `github_remove_organization_member`: remove a user from the organization.

GitHub repository permissions supported by the onboarding tool are `pull`, `triage`, `push`,
`maintain`, and `admin`; organization roles are `member` and `admin`; team roles default to
`member` in this surface. Every write is two-step: call the tool without confirmation to receive a
canonical plan and `plan_hash`, then call it again with the same arguments, that hash, and
`confirm=true`. A natural-language request must never bypass this confirmation boundary.

The connected GitHub credential must have organization membership/team administration and
repository administration permissions sufficient for the requested operation. Use a throwaway
organization and short-lived credential for rehearsals. The implementation deliberately excludes
billing, organization ownership, security settings, PAT/SSH-key management, repository deletion,
and other unrelated destructive administration.

Example MCP rehearsal sequence:

```text
github_inventory()
github_user_access(username="new-dev")
github_onboard_user(
  username="new-dev",
  repositories=["deadboltSAMS/testrepo123"],
  permission="pull",
  organization_role="member",
  teams=[]
)
# inspect returned plan_hash, then repeat with confirm=true and expected_plan_hash=<hash>
```

The deployed MCP endpoint requires the Cognito ID token. A safe negative check is an unauthenticated
request that returns `401`. For an authenticated manual check, copy the token only locally from the
logged-in browser session and call MCP Streamable HTTP with `Accept: application/json, text/event-stream`.
Never commit or share the token.

#### Verification status

The latest local verification is:

```text
133 non-live backend tests passed
85.00% coverage threshold reached
ruff, mypy --strict, and import-linter passed
frontend lint, typecheck, and production build passed
infra tests: 5 passed
CDK synth passed
protected-path guard passed
```

The frontend lint command reports three existing `react(set-state-in-effect)` warnings in
`AuditLog.tsx`, `Connections.tsx`, and `Dashboard.tsx`; they do not fail the gate. The CDK synth
reports the existing DynamoDB deprecation and unconfigured feature-flag warnings; neither blocks
deployment.

#### Recommended demo narrative

Use a throwaway GitHub organization with at least one test repository and a test account. The
operator signs in, configures the GitHub connection, inspects the inventory, asks MCP to preview
onboarding for a new developer, reviews the returned organization/repository/team plan, and then
explicitly confirms it. Follow with `github_user_access` to verify the resulting membership and
repository role. Use the dashboard for the visual drift review and audit trail. Do not claim that
Salesforce or Workday perform live writes, and do not present fixture-only approval/rollback as a
real provider mutation.

#### Cost and cleanup

Schedules are disabled, Lambda is on-demand, DynamoDB is pay-per-request, and CloudWatch logs are
retained for one day. Idle cost should remain very low, but create a low-dollar AWS Budget alert
and destroy the rehearsal stack when finished:

```bash
cd /Users/karthik/sams_simlplifynext/backend/infra
npx cdk destroy DeadboltStack --profile hackathon --region us-east-1 --force
```

Object Lock buckets and the CDK bootstrap resources may require separate cleanup or retention
checks. Never destroy unrelated resources in the hackathon account.

### 6.12 Entitlement register UI completion — 2026-09-07

The entitlement-register presentation refinement is complete on `main` through commits
`e1ce071`, `d7f975a`, `3de83f7`, `9959329`, `46fc370`, and `fdd41dd`. These commits have not
changed backend contracts or provider behavior. The implementation
is a React 19.2/Vite 8 SPA with Tailwind 3.4 configuration; it is not a Next.js application.

The register now has self-hosted IBM Plex Sans/Mono fonts, graphite dark and warm-neutral light
themes, primitive → semantic → component tokens, accessible risk rails with tick-mark cues,
responsive sidebar/table/card layouts, grouped findings, native labeled filters, stale/failed/empty
states, a focus-managed detail sheet, optimistic decision feedback with an eight-second Undo window,
and a separate local 12-finding rehearsal fixture. Queues above 200 findings use viewport windowing;
derivation still traverses the complete source array once for filtering, sorting, grouping, and counts.
The fixture and browser scripts are local-only and do not make AWS or provider calls.

Verification for this work:

```text
make gate-m12r && make guard              passed
138 non-live backend tests                passed
coverage                                  85.12%
frontend lint, typecheck, production build passed
axe at 375/768/1024/1440 in both themes     0 violations
filter interaction, 12 findings             1 commit, 32.9ms visual feedback
filter interaction, 5,000 findings          1 commit, 49.2ms visual feedback
actual Chromium 200% zoom                   CSS width 720, no overflow
```

The detailed file list, contrast matrix, screenshots, generated-default checklist, state tests,
and reproduction commands are in `backend/docs/REGISTER_REVIEW.md`. The measured minimum text
contrast is 4.79:1 and the minimum tested UI boundary contrast is 3.03:1. The feature branch was
merged/pushed only after preserving unrelated existing working-tree edits; those edits remain
uncommitted and must not be swept into later commits without review. The hosted dashboard and API
still require the documented authenticated redeploy procedure before these local frontend changes
appear in AWS.

### 6.13 Capture decision persistence hotfix — 2026-09-07

The first deployed register version kept scan and approval state only in a Lambda execution
environment. A post-decision browser reload could therefore reach another environment, restore the
bundled older capture, and show approved findings as pending again. The hotfix removes mutation-
triggered self-reloads, preserves matching stages across a new provider scan, and stores the latest
captured records plus decision stages in the existing `deadbolt-graph` DynamoDB table. The API
Lambda has only `GetItem` and `PutItem` permission for the register state item. Local fixture runs
continue to use an injected in-memory store and do not contact AWS.

The regression test covers approve → scan → cold-start reload. `make gate-m12r && make guard` passes
with 139 non-live tests and 85.08% coverage. Redeploy the API stack with `requireAuth=true`, then
rebuild/sync the SPA if the deployed bucket needs the matching frontend bundle.

### 6.14 Decision submission timing hotfix — 2026-09-07

The register previously waited eight seconds before sending an approval, which meant a browser
reload during the Undo window silently discarded the decision and restored the last persisted
capture. Decisions now send immediately, update the row optimistically, and retain an eight-second
Undo action after the API confirms the mutation. The deployed SPA must be rebuilt and synced after
this change.

### 6.15 Live Bedrock broker wiring — 2026-09-07

The API now selects `BedrockLLMClient` when the deployed `DEADBOLT_LLM_MODE=bedrock` setting is
present. The local default remains `_DemoLLM` for fixture tests. The API Lambda role is restricted
to `bedrock:InvokeModel` (the permission Bedrock uses for Converse) on `amazon.nova-lite-v1:0` and
`anthropic.claude-3-haiku-20240307-v1:0`. Code and CDK synthesis are verified locally; a redeploy
is still required after refreshing the expired `hackathon` AWS credentials.

The deployed account currently reports Claude Haiku as unavailable and Nova Lite as available. The
Bedrock adapter therefore retries Claude prose requests with Nova Lite only on model access or
availability errors; once Claude access is enabled, it remains the preferred prose model.

The GitHub dashboard scan now includes paginated team memberships as observe-only entitlements
(`team:<org>/<slug>`). A new authenticated GitHub scan is required to promote newly added teams
into the persisted register; browser refresh alone cannot discover provider changes.
