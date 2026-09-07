# Decision log

Append-only. Newest entries at the bottom. One entry per ambiguity resolved,
dependency added, or blocker hit.

## 2026-09-02 — preflight — Toolchain recorded

`uv --version`: `uv 0.11.6 (65950801c 2026-04-09 aarch64-apple-darwin)`.
`uv python list`: CPython 3.12.13 is available at `/Users/karthik/.local/bin/python3.12`; no installation was needed.
`node --version`: `v24.14.0`.
`git --version`: `git version 2.50.1 (Apple Git-155)`.
`aws --version`: AWS CLI is not installed in this environment; no live AWS calls are needed for preflight.

## 2026-09-02 — preflight — frontend API surface observed

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
SPA API module is an in-memory mock and `AGENTS.md` §0.1 makes `frontend/` read-only.
**Chose:** Complete the static inventory and record `## BLOCKED: integration`; do not edit
components, use a browser shim, or pretend that in-memory calls prove backend integration.
**Rejected:** Modifying `frontend/src/lib/api.ts`, which is not a PRD-forced forbidden request,
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

## 2026-09-04 — workspace — Preserve the protected `frontend/` directory name

**Ambiguity:** The requested case-only rename to `frontend/` conflicts with the repository
standing contract, which marks `frontend/` read-only and protects the Makefile and CI linkage
that references that exact path.
**Chose:** Leave the directory and protected linkages unchanged; run verification against the
inherited path and avoid a partial case-only rename.
**Rejected:** Moving or recreating the frontend directory, editing protected Makefile/CI files,
or adding a lowercase alias that would make repository ownership and guard checks ambiguous.
**Reversal cost:** Low; a human can rename the directory and re-bless all protected path
references in one coordinated change when the standing contract is updated.

## 2026-09-05 — integration — Connect the SPA through a fixture-backed backend API

**Ambiguity:** The inherited SPA had no transport client, while the repository contract made
`frontend/` read-only; the owner explicitly approved a narrow API-client exception.
**Chose:** Edit only `frontend/src/lib/api.ts`, add a standard-library HTTP adapter, and default
the browser rehearsal to the deterministic Priya fixture scenario. Approval and rollback calls
exercise the broker contract but perform no external mutation.
**Rejected:** Editing UI components, adding a runtime web framework dependency, or defaulting the
browser to live AWS credentials.
**Reversal cost:** Low; point `VITE_DEADBOLT_API_URL` at an authenticated production adapter and
replace the fixture service once the graph/executor API is deployed.

## 2026-09-05 — integration — Add the official Python MCP SDK

**Ambiguity:** Deadbolt needs a portable MCP endpoint for Claude Code and ChatGPT, but the
repository has no MCP runtime dependency.
**Chose:** Add the official `mcp` Python SDK and use its Streamable HTTP transport at `/mcp`.
This keeps protocol negotiation, schemas, and transport behavior maintained by the SDK; the
fixture-backed service remains the default and no AWS call is introduced. The dependency adds a
small package/cold-start cost, accepted for interoperability and lower protocol risk.
**Rejected:** Hand-rolling MCP JSON-RPC and Streamable HTTP with the standard library, which
would reduce dependencies but increase interoperability and security risk.
**Reversal cost:** Low; remove the adapter and dependency if the project later standardizes on a
different MCP host or SDK.

## 2026-09-06 — m9 — Bundle Python dependencies for Lambda HTTP adapters

**Ambiguity:** The existing CDK asset copied Python source but not third-party runtime packages,
while the dashboard API and MCP Lambda need `mcp`, `mangum`, and their transitive dependencies.
**Chose:** Use CDK local bundling with `uv pip install --target` for Python 3.12 ARM64 Linux
artifacts, and copy the scenario and budget-handler packages into the same bundle. This keeps
the deployment serverless and avoids a VPC, container registry, or always-on host; bundling adds
build time and a larger Lambda asset but no idle AWS compute charge.
**Rejected:** Packaging macOS `.venv` contents, which would not be a reliable Lambda artifact,
or adding an always-on container service that would exceed the low-cost demo target.
**Reversal cost:** Medium; a future CI release pipeline can produce and publish the same bundle
as a versioned Lambda layer or container image.
## 2026-09-06 — m9 — Include deterministic scenario seeds in Lambda assets
**Ambiguity:** The fixture-backed API imports its scenario manifest and seed files at module load, but the first deployment bundle copied only Python packages.
**Chose:** Copy `tests/fixtures/scenario/` into the Lambda asset so the deployed demo has the same deterministic inputs as local runs.
**Rejected:** Replacing the manifest read with hard-coded production values or making the API silently fall back to empty data.
**Reversal cost:** Low; remove one local-bundling copy operation when the API moves to durable persisted state.

## 2026-09-06 — m10 — Replace the frontend transport mock with the deployed API client
**Ambiguity:** The inherited SPA had no network transport, while the deployed demo API already
existed but returned bare payloads and broad public CORS.
**Chose:** Keep the mock behind `VITE_USE_MOCK=1`, select the HTTP client once at module load,
configure `VITE_API_BASE`, and use a stable `data`/`error` envelope through a dedicated HTTP
handler. This is the owner-approved PRD §7 integration exception and is limited to the API client,
types/configuration, HTTP handler, infrastructure CORS, integration contract test, and gate.
**Rejected:** Editing components, routes, hooks, stores, or styles; silently falling back to the
mock after a network failure; or exposing provider `raw` payloads in error messages.
**Reversal cost:** Low; set `VITE_USE_MOCK=1` or remove the API client/handler and restore the
previous local-only transport without changing component code.

**Ambiguity:** The browser needs CORS for the deployed S3 website and local Vite development.
**Chose:** Restrict the API Function URL to the S3 website origin plus localhost Vite origins,
with only GET, POST, OPTIONS, and `content-type`; this was verified by a real preflight before
client changes and requires a CDK redeploy.
**Rejected:** Wildcard methods and headers, which were present in the initial demo deployment.
**Reversal cost:** Low; update the API Function URL CORS properties and redeploy.

**Ambiguity:** A transient browser network failure should be recoverable without replaying writes
after an HTTP response.
**Chose:** Use an 8-second AbortController timeout and exactly one retry for network errors only;
never retry 4xx/5xx responses, and expose typed client errors.
**Rejected:** Retrying all failures or silently switching transports, which could duplicate a
state-changing fixture action and hide deployment errors.
**Reversal cost:** Low; change the request helper policy without changing the wire contract.

## 2026-09-06 — m10 — Register the M10 pytest marker
**Ambiguity:** The requested gate selects `-m m10`, but strict pytest configuration registered
only M0 through M9.
**Chose:** Add the single `m10` marker declaration to the existing pytest configuration so the
requested acceptance command runs rather than failing during collection.
**Rejected:** Removing strict marker checking or changing the gate to run unmarked tests.
**Reversal cost:** Trivial; remove the marker declaration when M10 is retired.

## 2026-09-06 — m10 — Model Lambda Function URL preflight semantics
**Ambiguity:** M10 names GET, POST, and OPTIONS, but Lambda Function URL CloudFormation rejects
`OPTIONS` in `allowedMethods` because it is the preflight method handled by the service.
**Chose:** Configure GET and POST as allowed application methods, retain the handler's explicit
OPTIONS response headers, and verify the deployed preflight against the S3 origin.
**Rejected:** Using an invalid CloudFormation value or wildcard methods to force OPTIONS into the
resource property.
**Reversal cost:** Low; change the two Function URL CORS properties and redeploy.

## 2026-09-06 — m11 — Serve a redacted captured GitHub snapshot
**Ambiguity:** The hosted dashboard must show real GitHub evidence, but a public demo Lambda must
not hold or use a GitHub PAT.
**Chose:** Capture the normalized read-only provider output locally, redact it, canonicalize it,
and serve the committed artifact as a provenance-labelled dataset. The deployed path never calls
GitHub and never receives the PAT.
**Rejected:** Live GitHub calls from Lambda or putting the PAT in CDK, SSM, Lambda environment,
source, or frontend code.
**Reversal cost:** Medium; replace the captured provider with an authenticated snapshot worker
and durable graph write after submission.

**Ambiguity:** Provider payloads contain secrets, email addresses, headers, avatar URLs, and
large nested response bodies that are not needed for the demo.
**Chose:** Strip token/header/avatar/user payload keys, hash email addresses to stable pseudonyms,
and retain only normalized repository, login, permission, scope, and timestamp fields.
**Rejected:** Storing the raw provider response and attempting to rely on access controls alone.
**Reversal cost:** Low; expand the explicit allowlist only after a data-review decision.

**Ambiguity:** GitHub free organizations do not expose collaborator last-access timestamps.
**Chose:** Preserve `last_used_at=None` when no provider evidence exists; the existing scoring rule
 treats this as fully dormant and the capture manifest documents the limitation.
**Rejected:** Fabricating a timestamp from capture time or repository activity.
**Reversal cost:** Low; add an evidence-derived timestamp field when GitHub exposes one.

**M14 boundary:** Authenticated live GitHub reads remain post-submission only. M14 prerequisites
are not met in this session: submission/tagging is not verified, the public endpoint is not
authenticated, no expiring fine-grained PAT is provisioned in SSM, and no separate rehearsal is
recorded. Do not implement the M14 live path until all prerequisites are documented.

## 2026-09-06 — m11 — Stop the live-capture acceptance at missing owner credentials

**Ambiguity:** M11 requires a real redacted GitHub capture and a deployed endpoint serving it,
but this workspace has no owner-provided `GITHUB_ORG` and `GITHUB_TOKEN` environment values.
**Chose:** Finish and test the capture/redaction/loader path, retain the fixture fallback, and do
not fabricate a capture artifact or claim live GitHub evidence.
**Rejected:** Committing synthetic GitHub records, reading credentials from `.env`, or sending a
PAT to the public Lambda.
**Reversal cost:** Low; run `make capture-github` with owner-supplied credentials, review the
redacted manifest, then explicitly install the artifact and redeploy.

## 2026-09-06 — m12 — Keep the UI evidence fixture-safe

**Ambiguity:** The UI must distinguish live evidence from simulation while the hosted endpoint
remains unauthenticated and fixture-backed.
**Chose:** Add provenance and evaluated-at fields to the backend wire response, show explicit
Live/Simulated badges, expose deterministic hash/ID comparisons, and inventory every control.
**Rejected:** Enabling real-provider writes from the dashboard or hiding network/error states.
**Reversal cost:** Low; remove the presentation fields once an authenticated graph API supplies
the same provenance contract.

## 2026-09-06 — m13 — Package the submission around verified fixture evidence

**Ambiguity:** The public links predate the M10 transport redeploy, while M13 requires honest
submission documentation and no unverified live-provider claim.
**Chose:** Document the existing stack outputs as unauthenticated fixture-and-capture-backed
links, make the local gates and demo the source of truth, and defer the single deployment attempt
until M10–M13 gates complete.
**Rejected:** Claiming the new envelope/CORS client is deployed before the final deploy, or
presenting Salesforce, GitHub Enterprise, Workday, or Notion as credentialed.
**Reversal cost:** Low; redeploy the same stack and refresh the three output links if AWS changes
the Function URL or website resource.

## 2026-09-06 — m14 — Defer authenticated GitHub reads until post-submission prerequisites

**Ambiguity:** M14 is explicitly post-submission-only and its mandatory security prerequisites
are not verifiable from this workspace.
**Chose:** Do not add the live snapshot route, bearer authentication, or PAT retrieval path now;
record the blocker and keep the public demo read-only fixture-backed.
**Rejected:** Treating an unauthenticated Function URL or a laptop PAT as production auth, or
storing a token in Lambda environment variables.
**Reversal cost:** Medium; after submission, add auth and SSM IAM policy, rehearse separately,
then promote the existing read-only provider path.

## 2026-09-06 — deployment — Defer the post-gate deploy until the AWS SSO session is refreshed

**Ambiguity:** All runnable M10–M13 gates pass, but the requested final CDK deploy uses an
expired `hackathon` profile session.
**Chose:** Make one post-gate deploy attempt, which stopped at AWS role assumption with
`ExpiredToken`; make no resource changes and do not retry automatically.
**Rejected:** Reusing personal credentials, embedding keys, or repeatedly retrying a known-expired
session.
**Reversal cost:** Trivial; run `aws sso login --profile hackathon` and repeat the documented
single deploy command when the owner is ready.

## 2026-09-06 — m12r — Refactor the dashboard into an operator-facing entitlement register

**Ambiguity:** The inherited SPA had a dark demo dashboard, compressed metrics with no measurement
honesty, and several controls that were difficult to use during a live presentation.
**Chose:** Keep routes, API signatures, state containers, and wire data unchanged while replacing
presentation with tokenized light surfaces, a sortable/filterable findings table, provenance labels,
determinism evidence, responsive states, and explicit measured-versus-target metric labels.
**Rejected:** Replacing the router, introducing a UI kit, adding charts, or inventing backend metrics.
**Reversal cost:** Medium; presentation can be reverted independently, but the operator copy and
token classes would need to be re-applied to any new component set.

## 2026-09-06 — auth — Add optional Cognito boundary authentication for API, MCP, and the SPA

**Ambiguity:** The hackathon demo needs to visibly use AWS without making local fixture demos depend
on an unavailable SSO session or a live provider credential.
**Chose:** Provision a low-cost Cognito user pool and web client in CDK, validate Cognito ID-token
JWTs at the API and MCP Lambda boundaries when `-c requireAuth=true` is selected, attach the token
from session storage in the browser client, and keep an explicit local/demo fallback when auth is
disabled. The live GitHub path remains separately gated and read-only.
**Rejected:** Function URL IAM auth (poor judge experience), cookies (unnecessary CORS and CSRF
surface), storing tokens in localStorage, or putting provider PATs in the frontend.
**Reversal cost:** Medium; removing Cognito requires a stack change and a hosted-UI rebuild, while
the boundary adapter can be disabled with the existing context flag without changing domain code.

## 2026-09-06 — m12r — Use the supplied Deadbolt brand system in the operator UI

**Ambiguity:** The inherited dashboard could render the data but read as a generic generated admin
template, and the supplied logo assets were not connected to the application shell.
**Chose:** Copy the supplied vector mark and favicon into the static brand bundle, use the mark in
the navigation shell, and refine the dashboard around a restrained source header, review-queue
hierarchy, compact metric rule, and denser professional table treatment. No component or API
contract changed.
**Rejected:** Replacing the supplied geometry, adding gradients or decorative illustration, or
introducing a UI framework.
**Reversal cost:** Low; the asset copy and presentation classes can be reverted without affecting
the data transport or provider capture.

## 2026-09-06 — m12r — Make the operator surface capture-only and theme-aware

**Ambiguity:** The inherited browser mock was useful during early UI work, but it could hide a
broken API and make the dashboard disagree with the owner-provided GitHub capture.
**Chose:** Remove the frontend mock selection entirely, use the capture-only scenario whenever
`DEADBOLT_CAPTURE_DIR` is configured, persist an explicit light/dark theme choice in the browser,
and treat a read-scope reduction to `none` as a verified staged revoke. Entitlements without an
executable plan remain visible but expose no destructive controls. The deployed MCP Lambda uses
the same capture and validates Cognito bearer authentication when `requireAuth=true`; local MCP
remains intentionally unauthenticated for localhost rehearsal.
**Rejected:** Silently falling back to fabricated browser data, treating a no-op plan hash as a
successful UI action without state feedback, or placing provider credentials in the frontend.
**Reversal cost:** Medium; restoring the mock would require reintroducing a separate client path,
while theme and capture selection are independently reversible. Reverting the staged-revoke rule
would require updating the browser/MCP action contract and its integration tests.

## 2026-09-06 — connections — Add authenticated provider onboarding with server-side secrets

**Ambiguity:** New operators need to connect their own platforms, but the current demo service is
capture-backed and must not accept provider credentials on an unauthenticated public path.
**Chose:** Add a Cognito-authenticated Connections page and API for GitHub, Salesforce, and Workday.
Configuration is stored as per-operator SSM SecureString data keyed by a hash of the Cognito subject;
the API returns metadata only. Connection tests perform read-only provider snapshots, with Workday
remaining permanently read-only. Local rehearsal uses an in-memory store and does not contact AWS.
**Rejected:** Browser localStorage, frontend environment variables, query-string credentials,
CloudFormation plaintext secrets, or enabling automatic scans immediately after saving a credential.
**Reversal cost:** Medium; remove the Connections route and SSM IAM grant, then delete the parameter
path. Existing provider implementations and the capture-backed dashboard remain unaffected.

## 2026-09-06 — m12r — Preserve score semantics and disclose uniform capture evidence

**Ambiguity:** The owner-provided GitHub capture contains one repository, read permission, and no
provider activity timestamps, so all four findings legitimately score 49; one non-revocable PAT is
forced to T0 by the immutable observe-only rule even though its numeric score is 49.
**Chose:** Keep the PRD weights and tier function unchanged, expose the non-revocable override as
an explicit Observe only label, render zero-denominator metrics with counts and dashes, and show a
data-quality notice when the source produces no score variance. The runbook now documents a
throwaway three-repository capture with admin/write/read permissions for a meaningful ranked demo.
**Rejected:** Tuning weights, fabricating activity timestamps, or silently changing the tier policy
to make the current capture look more varied.
**Reversal cost:** Low; a new owner-approved capture changes only the artifact and resulting plan
hashes. A policy change would require a separate semantic decision and golden-hash review.

## 2026-09-06 — m16 — Widen the Priya scenario scope mix so risk scores span all four tiers

**Ambiguity:** m16/01 required tracing whether the dashboard's near-constant risk score was a
degenerate scorer, a hardcoded fixture value, or a real score on unrepresentative fixture data.
`engine/scoring.py::risk` varies correctly with its inputs, but every planted finding in
`scenarios/priya.py` had dormancy pegged to the maximum (both identities' `last_used_at` dates are
far past the 90-day ceiling) and role mismatch pegged to the maximum (neither role template permits
the write/admin scopes the manifest uses) and a uniform reachability of 5. Only scope severity
varied, across exactly two values, so every planted finding landed in T2 at one of two totals
(6791 or 8391 basis points) — real scores on a fixture with no discriminating variance. The prompt's
literal instruction to shrink the fixture to 8-12 findings was rejected because
`PLANTED_FINDING_COUNT = 20` is a tested invariant referenced by the M1 recall gate, the e2e
20-planted-finding rehearsal, and the README's recall claim.
**Chose:** Change the `scope` field of three existing planted findings only (no identity, system,
or resource changes): `leaver@example.com` `aws-iam/breakglass-policy` and
`salesforce/billing` from `admin` to `iam` and `billing` respectively (severity 10000, pushes total
to 8791, crossing the T3 threshold of 8500 — and both scope names now match their resource
semantics), and `leaver@example.com` `notion/roadmap` from `write` to `read` (severity 1000, drops
total to 5191, landing in T1). Updated the matching `scope` field in `manifest.json` and the
corresponding provider fixture JSON so planted/detected keys still match. Verified via
`scenario.findings()`: T0×1 (540), T1×1 (5191), T2×17 (6791/8391), T3×2 (8791). Ran the full
non-live suite (130 passed) and `make lint types arch` after the change; nothing hardcodes the old
per-finding scores or tiers.
**Rejected:** Reducing the planted-finding count to 8-12 (breaks the recall/README invariant above);
changing `last_used_at` or reachability instead of scope (touches more fixture files for the same
result and further discounts the dormancy/blast-radius factors' apparent influence in the demo).
**Reversal cost:** Low; revert the four JSON field values to restore the prior two-tier
distribution. No golden hash file, code path, or test literal encodes the old per-finding scores.

## 2026-09-06 — m16 — Derive real-capture blast radius from the capture itself instead of zero

**Ambiguity:** The literal symptoms in m16/01 and m16/07 ("every row renders risk score 49",
"github:pat:mvrkarthik07" as a visible label) were reproducible against the actual local
rehearsal, not just illustrative: a live `uv run python -m deadbolt.api` process (started in an
earlier session, `DEADBOLT_CAPTURE_DIR=artifacts/captures`) served four real GitHub findings that
all scored 49 (T1×3, T0×1). Tracing `_scenario_from_capture` in `api.py` showed `templates={}`
(no role baseline exists for a bare PAT scan, so `role_mismatch` is honestly always maximal) and
`reachability={}` (hardcoded to zero for every real capture, even though the capture itself
contains a real, computable co-occurrence signal: how many other identities hold an entitlement on
the same resource). `last_used_at` is `null` for all four because GitHub's collaborator API does
not expose it — also an honest absence, not a bug.
**Chose:** Add `_reachability_from_snapshot` in `api.py`, deriving `{(system, resource): count of
other identities on that resource}` directly from the capture's own entitlements, and use it in
place of the hardcoded `{}`. This is a real, non-fabricated signal already present in the data.
Left `templates={}` and the null `last_used_at` alone — synthesizing either would fabricate a
measurement the codebase's own honesty rules (e.g. the KPI strip's NOT_WIRED state) forbid.
Verified: the three `deadboltSAMS/testrepo123` collaborator findings now score 5078 (T1, up from
4900) reflecting 3 co-holders; the singleton PAT self-entitlement correctly stays at blast radius 0
(no other identity holds that specific resource). Ran the non-live suite (130 passed) and `make
lint types arch` after the change.
**Rejected:** Fabricating role templates or synthetic `last_used_at` values for real captures to
force more score variance — this specific throwaway demo repo genuinely has one resource and one
uniform permission level, so three of the four rows staying identical is an honest reflection of
that repo's actual access pattern, not a defect to paper over.
**Reversal cost:** Low; revert `_scenario_from_capture` to `reachability={}`. No test or golden
hash encodes the old zero-reachability values.

## 2026-09-06 — m16 — Full frontend pass: 01 (score/tier/band/bar), 02 (KPI states), 03
(accessibility), 04 (bulk select + undo), 05 (filter axes), 06 (responsive), 07 (copy), 08
(token architecture), 09 (render performance)

**Ambiguity:** `backend/prompts/m16.md` is nine largely-independent frontend tasks against
`frontend/`, which AGENTS.md §0 marks read-only outside an owner-approved exception (the
precedent is the M12R presentation refactor already recorded above). The owner explicitly directed
execution of this prompt file in this session, which is treated as the same class of exception,
scoped to presentation/interaction — no API operation signature, route, or backend contract
changed.
**Chose, per task:**
- **01** Traced risk scoring to `engine/scoring.py::risk` (varies correctly) and separately to the
  live capture-fixture gap fixed above. Split the tier badge into its own sortable `<th>` between
  score and identity; added `RISK_BANDS` (single exported constant, thresholds 30/60/85) driving
  both a `"51 / 100 · elevated"` label and a 4px height-proportional bar (`Dashboard.tsx`).
- **02** Modeled `measured` / `no-events` / `not-wired` explicitly from `metrics.counts`; a metric
  never renders a bare `0` for "nothing happened" (e.g. "No revokes yet" instead), every measured
  value carries its sample size with a `LOW_SAMPLE_THRESHOLD = 10` qualifier, and the primary
  metric (drift recall) gets larger type while not-wired metrics are de-emphasized.
- **03** Measured contrast pairs before changing them (see the token-ramp decision below — only
  the old amber tier-2 at 4.23:1 actually failed the 4.5:1 floor; ink-muted was already compliant
  at 6.14:1/8.37:1 light/dark). Added per-row `aria-label`s naming the finding ID and identity to
  the "Review" button, `aria-sort` plus a `currently ascending/descending` label on sort headers,
  and relied on the existing global `button/a/input:focus-visible` rule (7.39:1) for the new
  checkboxes and chips rather than inventing a second focus style.
- **04** Built undo before bulk, per the constraint, as one shared primitive: confirming a bulk
  action starts a 6s countdown toast with an Undo button; the API call only fires if the countdown
  elapses, so "undo" is exact (nothing was sent) rather than a best-effort backend reversal. Bulk
  actions reuse the exact two existing single-finding action strings (`Approve`, `Reduce further`)
  from `ApprovalCard.tsx` via `api.decideApproval` — no new backend action vocabulary. Selection is
  gated to `stage_status === 'blocked-on-approval' && !observe_only` and persists across filter
  changes (a Set keyed by `finding_id`); "select all" only selects currently-visible decidable rows.
- **05** Split the single filter row into two `role="group"` axes (tier, source), counted in one
  traversal per axis against the *other* axis's current filter, disabled zero-count chips, and
  added a check glyph so the active chip isn't color-only.
- **06** Sidebar collapses to an icon rail below 768px via a visually-hidden (not `display:none`)
  `.sidebar-label` class, so nav labels stay in the accessibility tree; the table is a scoped
  horizontal-scroll region (`min-width: 900px` inside `overflow-x: auto`) rather than a stacked
  card layout, chosen for consistency with the existing dense-table aesthetic; KPI strip already
  had 3/2-column media queries, added a 1-column one at 480px given the longer new captions.
- **07** Removed the two ALL-CAPS eyebrows on the dashboard only (title and section heading), gave
  each KPI caption distinct sentence-level phrasing instead of the repeated "Target X — measured"
  template, replaced the two boilerplate subtitles, and removed monospace from tier badges (now
  sans, per "monospace for tabular numerals/identifiers only"). The literal
  `"github:pat:mvrkarthik07"` example was real (see above) and is now humanized via
  `resourceLabel()` in the table; the raw URN is not shown anywhere in the table (not even as a
  tooltip) and remains visible only on the finding detail page.
- **08** Restructured `tokens.css` into primitive → semantic → component layers. Replaced the
  three-arbitrary-hue tier ramp (gray/green/orange/red) with one contrast-verified warm ramp
  (neutral → amber → orange → red; computed and checked ≥4.5:1 against both `--paper` and
  `--paper-sunken` in both themes — see the ramp values in `tokens.css`). This also resolves the
  three-unrelated-uses-of-green complaint: connection status, the "Live" badge, and tier-1 no
  longer share a token. Used `color-mix()` for the row-selected/active backgrounds rather than a
  raw-HSL-triplet migration of every existing hex token, which would have touched ~15 call sites
  for the same opacity-composition capability. Added the full row state matrix (default/hover/
  active/selected/disabled) with a visible left-stripe on selected rows, distinct from hover.
- **09** The filter/sort/count computation was already `useMemo`-based; consolidated per-chip
  counting into the same single traversal as filtering (previously chip counts weren't computed at
  all). Added `useDeferredValue` on the rendered row list and `startTransition` around filter/sort
  state updates, `content-visibility: auto` on table rows, and confirmed the row component was
  already a module-scope `memo`. Did not add virtualization/windowing (no new dependency); at the
  current fixture scale (21-25 rows) it isn't warranted — a reasonable threshold to revisit
  virtualization is roughly 500-1000 rows given `content-visibility` alone.
**Verified:** `npm run typecheck`, `npm run lint` (pre-existing `set-state-in-effect` warning
unchanged, same pattern as `AuditLog.tsx`/`Connections.tsx`), `npm run build`, and a live check
against the real captured-data local rehearsal (`DEADBOLT_ENVELOPE=1 DEADBOLT_CAPTURE_DIR=
artifacts/captures uv run python -m deadbolt.api`, frontend dev server) in both themes: tier
column/band/bar render correctly, bulk select → confirm → undo correctly leaves state untouched,
confirm-dialog Cancel button (found genuinely unreadable — dark-on-dark, fixed in the same pass)
now visible in both themes. Narrow-viewport (375-480px) rendering was implemented per standard
mobile-first media queries but not visually confirmed — the available browser-resize tool did not
resize this session's tab, and per guidance on browser-tool loops this was not retried further.
**Rejected:** Fabricating a five-tier or wider score spread that this repo's own data doesn't
support; adding a virtualization library; a raw-HSL-triplet token migration under deadline
pressure; changing any `frontend/src/lib/api.ts` operation signature.
**Reversal cost:** Medium; the CSS token restructuring and Dashboard rewrite are the largest single
change, but no API contract, route, or non-frontend file depends on their internal shape.
## 2026-09-06 — connections — Make dashboard promotion explicit and read-only

**Ambiguity:** A successful credential test should not silently replace the evidence currently
under review, but operators still need a one-click path from a real provider to the dashboard.

**Chose:** Add an explicit **Scan into dashboard** action. It reuses the authenticated provider
snapshot, rebuilds the deterministic findings and plan, and exposes only normalized records. The
scan adapter rejects provider writes; approvals and rollback remain fixture/capture-safe.

**Rejected:** Automatically replacing the dashboard after every connection test, or allowing a
scan to execute remediation actions.

**Reversal cost:** Low; remove the scan route/button and retain the existing connection-test path.

## 2026-09-06 — auth — Enable verified self-registration in Cognito

**Ambiguity:** New operators should not need AWS CLI access, but unrestricted account creation
would make an unaudited public operator surface.

**Chose:** Enable Cognito self-registration with email verification and add sign-up/confirmation
screens to the dashboard. Provider credentials remain protected behind the authenticated API and
operator-scoped SSM parameters.

**Rejected:** Keeping CLI-only account provisioning, or adding a custom password store in the SPA.

**Reversal cost:** Low; set `selfSignUpEnabled` back to false and remove the two Cognito client
operations from the auth gate.

## 2026-09-06 — api — Delegate deployed CORS to the Lambda Function URL

**Ambiguity:** The API handler and the Lambda Function URL both appeared able to emit CORS
headers, but duplicate `Access-Control-Allow-Origin` values are rejected by browsers.

**Chose:** Keep CORS configured in CDK with the S3 website and local development origins, and
return only `Content-Type` from the deployed handler. Lambda Function URL owns the preflight and
origin response.

**Rejected:** Returning `*` from the handler or manually concatenating origins into one header.

**Reversal cost:** Low; restore handler headers only if the HTTP surface moves behind a server that
does not provide CORS.

## 2026-09-06 — github-iam — Add authenticated GitHub IAM operations to MCP

**Ambiguity:** “All IAM” could include billing, organization ownership, security configuration,
credential management, and repository destruction, which are outside a safe onboarding demo.

**Chose:** Expose organization inventory, membership lookup, repository/team access inspection,
organization onboarding, repository-role assignment, team assignment, repository/team removal,
and organization-member removal. Every write has a deterministic preview plan and requires the
current plan hash plus `confirm=true`. Salesforce and Workday remain unchanged and read-only.

**Rejected:** Billing/ownership changes, PAT or SSH-key management, repository deletion, and
unconfirmed natural-language writes.

**Reversal cost:** Medium; remove the GitHub MCP tools and provider write methods, then redeploy
the MCP Lambda. Existing connection storage and read-only capture behavior remain independent.
## 2026-09-07 — m16 — Make GitHub repository scope optional for read-only organization scans

**Ambiguity:** Organization administrators need an organization-level connection, while the
existing GitHub scan required manually entering repository names.

**Chose:** Accept an empty GitHub repository list for connection tests and dashboard scans. The
provider discovers all visible organization repositories, then reads collaborators from each.
MCP onboarding and access-changing tools remain explicitly repository-scoped and still require
preview, the current plan hash, and confirmation.

**Rejected:** Automatically grant or remove access across every organization repository.

**Reversal cost:** Low; restore the required repository validation and remove the discovery branch,
then redeploy the API and frontend.

## 2026-09-07 — m12r — Refine the entitlement register under owner authorization

**Ambiguity:** The owner requests frontend implementation despite the standing read-only default; the requested 12-row review fixture conflicts with the PRD's 20-planted-finding demo. Some specified primitive colors cannot meet the requested text contrast floor on every interaction surface. The API has no general undo operation or anonymous live capture operation.

**Chose:** Treat this explicit screen-refinement request as the frontend exception. Preserve the PRD scenario and add a separate, clearly labeled 12-finding HTTP review fixture. Retain graphite primitives but raise semantic secondary/disabled/error text colors and use a readable neutral tier ramp. Use native select controls; collapse identity groups persistently across filters; sort groups by their first matching sorted finding. Combine detection/scoring into pipeline position 1 of 5. Use an eight-second, explicitly scheduled cancellation window before submitting decisions; cancel pending work on navigation. Refresh rereads persisted captures; Run capture uses configured read-only connection scans, or directs an unconfigured operator to Connections. Never restamp an old capture after a GET. Keep the five meaningful health metrics; omit uninstrumented sandbox billing from this review screen. New fonts are static OFL assets, not runtime dependencies; no Lambda dependency, cost, or cold-start impact. Browser and axe packages are development verification tools only.

**Rejected:** Altering frozen contracts/golden fixtures, inventing live scoring evidence, claiming provider access changed when the API only records a rehearsal decision, low-contrast disabled text, or relying on undo after an irreversible request has already been sent.

**Reversal cost:** Medium for the screen styles/components; low for the separate fixture tooling. Transport operation signatures and backend engine behavior stay stable.

## 2026-09-07 — m12r — Window large review queues after the measured filter regression

**Ambiguity:** Full DOM rendering initially met the 5,000-finding target but later repeatable isolated measurements reached 146ms. The screen must retain search/filter/count behavior over the full queue.

**Chose:** Window presentation above 200 findings with a 600px scroll buffer, using a scroll/resize external store. Keep filtering, sorting, grouping and per-option counts in their existing single derivation over all records. Test the top, middle and bottom at desktop/tablet/mobile widths and search for record 5,000. Use no runtime dependency. Mobile reserves the 106px content box plus padding/borders to match its 132px rendered card.

**Rejected:** Reporting only the earlier passing timing, adding pagination that changes the queue workflow, or reducing the measured source dataset.

**Reversal cost:** Low; remove WindowedFindings and its threshold after an alternative renderer meets the same measured budget.

## 2026-09-07 — register — Persist captured dashboard state across Lambda instances

**Ambiguity:** The browser approval response updated one Lambda execution environment, but the
subsequent automatic reload could reach another environment and restore the bundled capture from
many hours earlier. A new provider scan also reset all finding stages in memory.

**Chose:** Remove self-triggered browser reloads after mutations, preserve matching finding stages
when a scan replaces the snapshot, and persist the latest captured records plus decision stages as
one JSON payload in the existing DynamoDB graph table. The API role receives only `GetItem` and
`PutItem` for that table. Fixture-only local runs remain in memory and make no AWS calls.

**Rejected:** Treating a warm Lambda process as durable storage, storing provider decisions in the
browser alone, or rerunning a live GitHub scan on every GET request.

**Reversal cost:** Medium; remove the register state item, environment variable, IAM grant, and
restore the in-memory adapter if a dedicated capture/state repository replaces it.
