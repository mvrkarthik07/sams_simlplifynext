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
