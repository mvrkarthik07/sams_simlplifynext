# frontend/backend contract gaps

Inventory source: `frontend/src` static search, 2026-09-04.

The inherited SPA makes zero HTTP calls. Its eight exported API operations are backed by an
in-memory database in `frontend/src/lib/api.ts`; therefore there is no frontend HTTP row that a
backend route can satisfy. The backend schema export currently contains `ActionResult`,
`Entitlement`, and `FixedClock`, and those schemas are UNUSED by the SPA.

| Class | Row | Evidence | Severity | Resolution |
|---|---|---|---:|---|
| UNUSED | `ActionResult` schema | `python -m deadbolt.contracts.export` | low | retain; CLI/broker use it |
| UNUSED | `Entitlement` schema | `python -m deadbolt.contracts.export` | low | retain; provider/graph use it |
| UNUSED | `FixedClock` schema | `python -m deadbolt.contracts.export` | low | retain; tests use it |

## BLOCKED: integration

The integration objective requires the SPA to drive a real HTTP backend, but the standing
contract in `AGENTS.md` §0.1 makes `frontend/` read-only and the integration adaptation rule
only permits frontend API-client edits when the frontend requests PRD-forbidden behavior. The
frontend requests no forbidden behavior; it simply has no transport layer. A backend-only change
cannot turn an in-memory module into browser network traffic. Per the integration instructions,
work stops here rather than hiding the mismatch with a test-only shim.
