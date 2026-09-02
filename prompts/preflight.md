# PREFLIGHT — session zero

You are the sole engineer on **Deadbolt**, an autonomous SaaS entitlement-drift detector and
brokered revocation system, built for the SimplifyNext IGNITE Agentic AI Hackathon 2026.
Submission is **7 September 2026**. Read `AGENTS.md` before anything else; it is the standing
contract and it outranks this file on every point of conflict.

Read `docs/PRD_Deadbolt.pdf` in full before you write code. The sections that constrain you most:
§4.1 determinism boundary, §4.3 the `EntitlementProvider` interface, §4.5 risk scoring,
§4.6 policy tiers and the safety asymmetry, §4.8 executor invariants.

## Repository state you are inheriting

- `frontend/` — **already built by someone else and read-only to you.** Do not create, edit,
  move, or delete anything inside it. You may read it to learn what API shapes the SPA expects.
- `backend/` — **empty. Everything you build lives here.** All Python, all tests, all packaging.
- `infra/` — empty. Do not touch until milestone m9.
- Harness at the repo root (`Makefile`, `scripts/`, `.github/workflows/`, `prompts/`,
  `AGENTS.md`, `DECISIONS.md`) — protected. See `AGENTS.md` §0.

The root is deliberately shared: CI must see both `frontend/` and `backend/` to isolate which
side of the house broke a commit, and `scripts/guard_protected.sh` hashes paths that span both.

## Your task this session

Establish the workspace and prove the toolchain. Do **not** start writing application code — that
is milestone m0, which runs next.

1. Create the directory skeleton under `backend/`, mirroring the repo map in `AGENTS.md` §1.
   Empty packages get an `__init__.py`; empty test dirs get a `.gitkeep`. Do not create stub
   modules with placeholder implementations — an empty package is honest, a stub that returns
   `None` is a lie that m2 will trip over.
2. Verify the toolchain and record versions in `DECISIONS.md`:
   `uv --version`, `uv python list`, `node --version`, `git --version`, `aws --version` if present.
   If Python 3.12 is unavailable, install it with `uv python install 3.12`.
3. Initialise `DECISIONS.md` with this header if it is empty:

   ```markdown
   # Decision log

   Append-only. Newest entries at the bottom. One entry per ambiguity resolved,
   dependency added, or blocker hit.
   ```

4. Read `frontend/` — specifically its `package.json` scripts and any `src/api`, `src/lib/api`,
   `src/types`, or equivalent — and write **one** `DECISIONS.md` entry titled
   `Frontend API surface observed` listing every endpoint path, HTTP verb, and response shape the
   SPA already expects. This is the contract m0's `contracts/export.py` must satisfy. If the
   frontend calls nothing yet, say so explicitly; do not invent endpoints.
5. Confirm `frontend/` is byte-identical to what you inherited: `git status --porcelain -- frontend`
   must be empty.
6. Run `make gate-preflight` and keep working until it exits 0.

## Standing rules for this and every later session

- **Never ask me a question.** Resolve ambiguity yourself using the rule in `AGENTS.md` §4.1
  (reversible > cheaper > simpler), implement it, and log it. I am not in the loop between turns.
- **Never stop at a partial result.** A session ends when its gate command exits 0, not when you
  have something to report. Run the gate yourself before you finish.
- **Never modify a gate to pass it.** No deleting tests, no `xfail`, no lowering coverage
  thresholds, no editing `Makefile` / `.github/workflows/**` / `tests/gates/**`.
  `scripts/guard_protected.sh` will catch it and the gate will fail anyway.
- **No live AWS.** `moto` for AWS, `respx` for HTTP. Nothing in the default test run may reach a
  real endpoint or read real credentials.
- If you are blocked by something genuinely outside this repository — a missing credential, a
  rate limit, an API that does not exist — write `## BLOCKED: preflight` to `DECISIONS.md` with
  the exact blocker and the smallest change that unblocks it, then stop. That is the only
  acceptable early exit.

## Gate

`make gate-preflight`

## What happens next

When this gate is green I will run `./scripts/agent_loop.sh --chain m0 m1 m2 m3 m4` unattended.
Each milestone prompt in `prompts/` is scoped and gated the same way. Build so that chain can
run without me.
