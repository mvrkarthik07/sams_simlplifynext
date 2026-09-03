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

The SPA lives under `frontend/`. Its source components and state/routing/styling remain inherited.
The only frontend code change made during the rename was adding the missing package script:

```json
"typecheck": "tsc -b"
```

The SPA's `frontend/src/lib/api.ts` is still an in-memory mock. It exports these operations:
`getFindings`, `getFinding`, `getPlan`, `getAuditLog`, `getMetrics`, `decideApproval`,
`rerunDriftEngine`, and `executeRollback`. It makes no `fetch`, Axios, WebSocket, SSE, or polling
requests and has no API base-URL environment variable.

Therefore the following are true:

- Filesystem/build linkages between `frontend/` and `backend/` are correct and verified.
- The frontend can be linted, typechecked, and built successfully.
- The SPA is **not** yet proven to drive the backend over HTTP.
- A real browser → backend → provider end-to-end flow has **not** been completed.
- Do not replace the frontend mock or edit components merely to make an integration test pass.
  The integration blocker is recorded in `backend/artifacts/contract-gaps.md` and
  `backend/DECISIONS.md` as `## BLOCKED: integration`. Any future exception must be limited to
  the API client/types, be separately reviewed, and cite the PRD reason.

The static contract evidence is in:

- `backend/artifacts/contract-inventory.json` — zero frontend network calls; eight in-memory
  client operations.
- `backend/artifacts/contract-gaps.md` — three currently unused exported domain schemas and the
  explicit transport blocker.

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
