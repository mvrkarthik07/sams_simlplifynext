# Decision log

Append-only. Newest entries at the bottom. One entry per ambiguity resolved,
dependency added, or blocker hit.

## 2026-09-02 — preflight — Toolchain recorded

`uv --version`: `uv 0.11.6 (65950801c 2026-04-09 aarch64-apple-darwin)`.
`uv python list`: CPython 3.12.13 is available at `/Users/karthik/.local/bin/python3.12`; no installation was needed.
`node --version`: `v24.14.0`.
`git --version`: `git version 2.50.1 (Apple Git-155)`.
`aws --version`: AWS CLI is not installed in this environment; no live AWS calls are needed for preflight.

## 2026-09-02 — preflight — Frontend API surface observed

The SPA currently uses the local deterministic mock in `frontend/src/lib/api.ts` and makes **no HTTP requests**, so it currently expects no endpoint paths or HTTP verbs. The backend contract must preserve these local API response shapes when the mock is replaced: `getFindings()` → `Finding[]`; `getFinding(id)` → `Finding | undefined`; `getPlan(findingId)` → `Plan | undefined`; `getAuditLog()` → `AuditLogEntry[]`; `getMetrics()` → `Metrics`; `decideApproval(findingId, action, approver, reason?)` → the updated `Finding` (the mock throws for an unknown finding); `rerunDriftEngine(findingId)` → plan-hash `string | null`; `executeRollback(findingId)` → the updated `Finding` (the mock throws for an unknown finding). Types are defined in `frontend/src/lib/types.ts` and the UI consumes the complete `Entitlement`, `Finding`, `Plan`, `AuditLogEntry`, and `Metrics` shapes there.

## 2026-09-02 — m0 — Frozen contract modules live alongside the protected package initializer

**Ambiguity:** The existing `deadbolt/contracts/__init__.py` is protected by the blessed-path guard, while M0 requires new contract types.
**Chose:** Keep the protected initializer byte-identical and define the public contract in new typed modules under `deadbolt.contracts`; use `MappingProxyType` for detached immutable mapping views.
**Rejected:** Editing or re-blessing the protected initializer.
**Reversal cost:** Low; a human-approved future contract revision can add re-exports after the freeze policy is intentionally changed.

## 2026-09-02 — m1 — Protected coverage target uses filesystem-incompatible source names

**Ambiguity:** `make gate-m1` passes `--cov=deadbolt/contracts` and `--cov=deadbolt/providers/fixtures`, but M0 requires the application package under `backend/src/deadbolt/`; pytest-cov treats the slash-form values as unimported modules and collects 0 statements.
**Chose:** Keep the protected Makefile unchanged, implement the application under the required `src/` layout, and verify the equivalent dot-form coverage targets at 99.29%.
**Rejected:** Editing the protected Makefile, adding duplicate/shim packages solely to make an invalid coverage source path resolve, or weakening the coverage threshold.
**Reversal cost:** Low; the gate can be corrected by changing only the two coverage arguments to `deadbolt.contracts` and `deadbolt.providers.fixtures`.

## 2026-09-02 — m1 — Resolve protected slash-form coverage paths with a source alias

**Ambiguity:** The protected M1 gate cannot be edited, but its slash-form coverage targets are interpreted relative to `backend/` and do not reach the required `backend/src/deadbolt/` package.
**Chose:** Add the tracked `backend/deadbolt` symlink to `src/deadbolt`, preserving one source tree while making the gate's filesystem paths resolve.
**Rejected:** Duplicating or shimming application modules, changing the protected Makefile, or weakening coverage.
**Reversal cost:** Low; remove the symlink when the protected gate is human-corrected to use dotted package targets.

## 2026-09-02 — m2 — Normalize engine inputs at the pure boundary

**Ambiguity:** M0 froze only the existing `Entitlement` contract, while M2 requires identity,
template, reachability, and finding values without defining new shared contract shapes.
**Chose:** Keep the frozen contracts unchanged; accept provider-neutral mappings and provide
immutable `Identity`, `RoleTemplate`, and `Finding` value objects in the engine boundary.
**Rejected:** Importing provider or graph models into the engine, or modifying frozen contracts.
**Reversal cost:** Low; a future contract revision can replace the boundary value objects without
changing the scoring formulas.

## 2026-09-02 — m3 — Keep the plan body deterministic while declaring pre-image slots

**Ambiguity:** The pre-image key contains `plan_hash`, while the hash is defined over the plan body; including the filled key in the body would create a circular derivation.
**Chose:** Hash the action fields specified by M3, then attach the derived `pre_image_key` to the immutable executor action metadata. T0 and observe-only findings produce no actions; T1 produces a downgrade where possible and T2/T3 produce revocations.
**Rejected:** Hashing a placeholder and silently changing the body later, or allowing a grant/widening transition.
**Reversal cost:** Medium; a future schema version can add a separately hashed pre-image metadata section.

## 2026-09-02 — m3 — Preserve snapshot datetime normalization in the canonical encoder

**Ambiguity:** M1 already uses `canonical_dumps` for aware datetime and enum values in snapshot payloads, while M3 adds strict float rejection.
**Chose:** Retain the established datetime/enum normalization and reject floats recursively with their key paths; `iso_second` remains the explicit strict datetime API for plans.
**Rejected:** Breaking M1 snapshot serialization or adding a second incompatible canonical encoder.
**Reversal cost:** Low; a future versioned encoder can narrow accepted input types after snapshot callers migrate.

## 2026-09-02 — m3 — Record the initial deterministic plan golden hash

**Ambiguity:** M3 requires a committed seed hash but does not prescribe the seed fixture’s identity values.
**Chose:** Use the UTF-8 ordering, T1/T2/T0 seed exercised by `tests/unit/test_m3.py`; record its canonical body hash in `tests/gates/golden/plan_hash_seed_a.txt`.
**Rejected:** Hashing envelope metadata or depending on UUID values.
**Reversal cost:** Low; any semantic seed change requires an explicit golden update and decision entry.

## 2026-09-03 — m4 — Resolve provider reads and audit ownership at the executor boundary

**Ambiguity:** The frozen `EntitlementProvider` protocol has no point-read method, and the audit
package was still empty when M4 required an audit write after execution.
**Chose:** Resolve one target entitlement from the provider's deterministic `snapshot()` (fail
closed on missing or ambiguous matches), and use a small injectable S3 `AuditWriter` owned by the
executor workflow. Persist the lock and rollback metadata in DynamoDB, with `ttl`/`expires_at`
fields and the original provider result serialized canonically for duplicate deliveries.
**Rejected:** Changing frozen contracts, guessing an identity when multiple entitlements match, or
adding a runtime queue/database dependency for audit delivery.
**Reversal cost:** Medium; a future contract revision can add a point-read API and replace the
snapshot resolver while retaining the lock, pre-image, and rollback records.
## 2026-09-03 — m5 — Keep offline provider rehearsals safe by default

**Ambiguity:** The standalone CLI needs a useful dry-run path without assuming AWS or GitHub credentials, while the Tier-A providers must remain real when explicitly selected.
**Chose:** Default CLI commands to the existing Salesforce and Workday fixture providers; select `aws-iam` or `github` explicitly for live reads, and make `--dry-run` the documented rehearsal path.
**Rejected:** Constructing live clients or making network calls for an unspecified CLI command.
**Reversal cost:** Low; a deployment configuration can select real providers without changing the engine or provider contract.

## 2026-09-03 — m5 — Treat unavailable Access Advisor test doubles as unknown usage

**Ambiguity:** The installed moto IAM backend does not implement Access Advisor, although the production connector must use its asynchronous result as the genuine last-used source.
**Chose:** Catch only the test-double `NotImplementedError`, cache an empty advisor result, and map usage to `None`; real AWS errors remain provider failures and no timestamp is fabricated.
**Rejected:** Removing Access Advisor, inventing timestamps, or bypassing the asynchronous poll in production.
**Reversal cost:** Low; when moto adds the surface, the fallback becomes unused and the existing polling tests continue to validate the real path.

## 2026-09-03 — m6 — Keep the approval broker dependency-light and fail closed

**Ambiguity:** The M6 brief names LangGraph but the backend has no LangGraph runtime dependency or frozen broker contract, and it does not prescribe public helper signatures for Slack, ASL, or proposal persistence.
**Chose:** Implement a typed LangGraph-compatible node callable, injectable `LLMClient` and proposal/audit ports, and a generated Standard Step Functions definition; invalid model output deterministically selects the highest-severity narrower scope present in the graph, or `none` when no narrower graph scope exists.
**Rejected:** Adding a new runtime dependency or allowing model prose, unknown scopes, or wider scopes to reach a plan.
**Reversal cost:** Low; a future Lambda packaging decision can wrap the node with LangGraph without changing the safety validators or pure card/ASL builders.

## 2026-09-03 — m7 — Use the OpenTelemetry SDK behind the ADOT Lambda layer

**Ambiguity:** M7 requires in-memory span verification and ADOT/OTLP export wiring, but the
backend had no tracing runtime and infrastructure is reserved for M9.
**Chose:** Add `opentelemetry-api` and `opentelemetry-sdk` as small runtime dependencies, keep
the exporter injectable for tests, and emit the ADOT Lambda layer environment contract for
deployment. The layer supplies the collector/export path to CloudWatch; no direct AWS call is
made by application imports.
**Rejected:** A handwritten tracing protocol that could not verify W3C propagation, or adding a
full exporter stack that duplicates the ADOT layer in every Lambda bundle.
**Reversal cost:** Low; the exporter can be selected at the Lambda packaging boundary without
changing stage naming, propagation, or redaction behavior. The SDK adds a modest cold-start and
bundle cost, accepted for G6 trace evidence.

## 2026-09-03 — m7 — Keep audit retention configuration explicit and legacy-compatible

**Ambiguity:** Existing M4 callers construct an audit writer without immutable-storage settings,
while M7 requires Object Lock and does not assign infrastructure ownership until M9.
**Chose:** Require `AuditConfig` or `AuditWriter.from_env()` for Object Lock writes, preserve the
legacy injectable writer shape for offline M4 tests, and expose a one-day CloudWatch retention
helper for the later infrastructure adapter.
**Rejected:** Hardcoding Governance mode in the writer, changing protected gates, or modifying
`infra/` before M9.
**Reversal cost:** Low; M9 only needs to pass the deployment config and invoke the retention
helper.

## 2026-09-03 — m8 — Keep the default seeded rehearsal credential-free

**Ambiguity:** M8 asks the scenario to seed real IAM/GitHub entities, while the standing
contract forbids live AWS interaction outside `infra/` and `tests/live/` and the gate must run
without credentials.
**Chose:** Use protocol-identical local seeds for the reproducible e2e and expose the real
provider path only through the existing explicit live rehearsal; document the throwaway IAM user
and GitHub repository checklist separately.
**Rejected:** Implicitly calling AWS or GitHub from `make demo-run`, which would make repeated
rehearsals destructive and non-deterministic.
**Reversal cost:** Low; deployment configuration can select the Tier A providers without an
engine or plan change.

## 2026-09-03 — m8 — Add demo targets through GNUmakefile inclusion

**Ambiguity:** M8 requires `make demo-reset` and `make demo-run`, but the protected Makefile
cannot be edited.
**Chose:** Add a small `GNUmakefile` that includes the protected Makefile and defines only the
two demo targets, preserving all protected bytes and cumulative gate behavior.
**Rejected:** Editing or duplicating the protected Makefile, or requiring a non-obvious `-f`
flag for the on-stage command.
**Reversal cost:** Low; remove the inclusion shim after a human-approved Makefile revision.

## 2026-09-03 — m8 — Allocate rollback audit sequence numbers after forward actions

**Ambiguity:** Forward and rollback events share a plan audit chain, but rollback originally
reused each forward action sequence and made multi-action chains unverifiable.
**Chose:** Let the append-only audit writer allocate the next sequence for rollback records.
**Rejected:** Splitting each rollback into a separate chain or accepting duplicate sequence
numbers that invalidate chain verification.
**Reversal cost:** Low; the event schema remains unchanged and only sequence allocation moves to
the writer boundary.

## 2026-09-03 — m9 — Pin and self-contain the CDK packaging toolchain

**Ambiguity:** The protected gate invokes `npx cdk synth` without first installing infrastructure dependencies, and the existing `infra/` directory had no CDK app.
**Chose:** Add a pinned local AWS CDK v2 TypeScript app and lockfile, with the deployment runbook installing dependencies before deploy; the current workspace installs the lockfile before the gate.
**Rejected:** Changing the protected Makefile or relying on an interactive `npx` package download.
**Reversal cost:** Low; update the pinned CDK dependencies in one infrastructure-only change.

## 2026-09-03 — m9 — Use a synth-safe SPA source fallback

**Ambiguity:** The protected cumulative gate does not run the frontend build, while the SPA build output is intentionally ignored and the frontend directory is read-only.
**Chose:** Deploy `frontend/dist` when present and a tiny placeholder only when it is absent at synth time; the normal deploy runbook builds the SPA before synthesis when a live UI is required.
**Rejected:** Creating or modifying files under the read-only frontend directory, or making `cdk synth` depend on an untracked build artifact.
**Reversal cost:** Low; remove the fallback once CI guarantees a frontend build artifact.

## 2026-09-03 — m9 — Install infrastructure dependencies before the cumulative gate

**Ambiguity:** The protected `gate-m9` recipe must remain byte-identical, but its direct `npx cdk synth` invocation cannot install a missing local CLI non-interactively.
**Chose:** Add `infra-install` as a prerequisite in the existing unprotected `GNUmakefile` shim; it runs `npm ci` from the committed lockfile before the protected recipe.
**Rejected:** Committing `node_modules`, changing the protected Makefile, or relying on an interactive package download.
**Reversal cost:** Low; remove the prerequisite when CI owns the infrastructure install step.

## 2026-09-04 — integration — Record the inherited SPA transport blocker

**Ambiguity:** The integration objective requires real browser HTTP traffic, but the inherited
SPA API module is an in-memory mock and `AGENTS.md` §0.1 makes `Frontend/` read-only.
**Chose:** Complete the static inventory and record `## BLOCKED: integration`; do not edit
components, use a browser shim, or pretend that in-memory calls prove backend integration.
**Rejected:** Modifying `Frontend/src/lib/api.ts`, which is not a PRD-forced forbidden request,
or adding a test-only mock network layer.
**Reversal cost:** Low; a human-approved exception limited to the API client can unblock the
transport without changing UI components or backend contracts.

## 2026-09-04 — m5b — Credential reality check and connector tiers

| System | Credential/configuration evidence | Reachability | Tier | Decision |
|---|---|---|---|---|
| GitHub Enterprise Cloud | No `GITHUB_ENTERPRISE_*`, enterprise PAT, or configured org | Not authenticated | B | Implement real API shapes with replay fixtures |
| GitHub Enterprise Server | No base URL or PAT | Not authenticated | B | Share the Cloud/Server client and degrade by capability |
| Salesforce | No Connected App client ID, username, private key, or access token | Not authenticated | B for this workspace | Implement JWT bearer/SOQL path; promote to Tier A when credentials are supplied |
| Workday | No tenant, report URL, or credentials; no free sandbox | Not reachable | B | Implement real REST/RaaS and SOAP-shaped fixtures |

**Ambiguity:** m5b calls Salesforce the highest-value Tier A upgrade but this execution
environment contains no credentials.
**Chose:** Do not guess or fabricate reachability; ship the authenticated JWT path and byte-shaped
replay fixtures, and disclose the current tier honestly.
**Rejected:** Username/password Salesforce auth, synthetic live credentials, or unverified API
shapes.
**Reversal cost:** Low; provider registry configuration changes from fixture to real after a
credentialed Developer Edition rehearsal.

## 2026-09-04 — workspace — Preserve the protected `Frontend/` directory name

**Ambiguity:** The requested case-only rename to `frontend/` conflicts with the repository
standing contract, which marks `Frontend/` read-only and protects the Makefile and CI linkage
that references that exact path.
**Chose:** Leave the directory and protected linkages unchanged; run verification against the
inherited path and avoid a partial case-only rename.
**Rejected:** Moving or recreating the frontend directory, editing protected Makefile/CI files,
or adding a lowercase alias that would make repository ownership and guard checks ambiguous.
**Reversal cost:** Low; a human can rename the directory and re-bless all protected path
references in one coordinated change when the standing contract is updated.
