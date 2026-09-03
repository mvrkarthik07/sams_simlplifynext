# Deadbolt — agent runbook

## What this kit is

| File | Role |
| --- | --- |
| `AGENTS.md` | Standing contract. Codex loads it on every invocation. Boundaries, determinism law, working protocol. |
| `prompts/m0.md … m9.md` | One prompt per milestone. Scope only — never overrides `AGENTS.md`. |
| `Makefile` | `gate-mN` targets. The **sole** definition of "done". Protected. |
| `scripts/agent_loop.sh` | Re-invokes Codex with the failing gate output until the gate exits 0. |
| `scripts/guard_protected.sh` | SHA-256 manifest. Fails if the agent edits a gate, CI, or a frozen contract. |
| `.github/workflows/ci.yml` | Path-filtered, per-check matrix jobs so a failure names its own layer. |

## One-time human setup

```bash
git checkout -b feat/backend
cp -r deadbolt-agent-kit/* deadbolt-agent-kit/.github .        # into the repo root
chmod +x scripts/*.sh
mkdir -p backend prompts && touch DECISIONS.md
./scripts/guard_protected.sh --bless                            # records the manifest
git add -A && git commit -m "chore: agent harness + gates

Protected-Change-Approved-By: Karthik"
```

`--bless` is the only human-authored write to `.protected.sha256`. The guard verifies blessed
files still match; files *added* under a protected directory later are allowed, so re-bless once
after `m0` (to lock the frozen contracts) and once after `m3` (to lock the golden plan hashes):

```bash
./scripts/guard_protected.sh --bless
git commit -am "chore: re-bless protected manifest after m3

Protected-Change-Approved-By: Karthik"
```

Re-bless deliberately at those two points, never to unstick a loop.

## Running

```bash
./scripts/agent_loop.sh m0                       # single milestone, 15-attempt cap
./scripts/agent_loop.sh --chain m0 m1 m2 m3 m4   # unattended overnight run
```

The loop's exit codes: `0` gate green · `1` attempts exhausted · `2` missing prompt ·
`3` agent declared `## BLOCKED` in `DECISIONS.md`. Only `3` needs you, and it tells you exactly
what it needs.

## Why this does not require you to arbitrate every turn

Three properties do the work:

1. **The gate, not the agent, decides completion.** `make gate-mN` is a machine check. The agent
   cannot declare victory; it can only make the command exit 0.
2. **Failure output is fed back verbatim.** Each re-invocation carries the last 24 KB of the gate
   log, so turn *n+1* starts from the actual traceback rather than the agent's summary of it.
3. **The escape hatches are sealed.** `guard_protected.sh` prevents the classic loop pathology —
   an agent that deletes the failing test, `xfail`s it, or drops `--cov-fail-under`. Without that
   guard, an autonomous loop converges on a green gate over an empty test suite.

## Milestone dependency graph

```
m0 contracts ──┬─ m1 fixtures + graph store
               └─ m2 engine (pure) ── m3 plan + hashing ── m4 executor + rollback
                                                             ├─ m5 real IAM + GitHub
                                                             └─ m6 broker + Slack + LLM
                                                                  └─ m7 audit + OTEL
                                                                       └─ m8 scenario + e2e
                                                                            └─ m9 AWS infra
```

`m1` and `m2` are the only pair that can run in parallel worktrees (`git worktree add`), because
`m2` imports nothing `m1` produces. Everything else is strictly sequential — the later gates
re-run the earlier ones, so a parallel merge would fail the cumulative gate anyway.

## Calendar reality

Submission is 7 Sep. Today is 2 Sep. The PRD's day plan assumed a 30 Aug start, so it is behind
by whatever has not landed. Compress as follows and hold the two hard dates:

| Window | Target |
| --- | --- |
| 2 Sep | `m0` → `m3` chained, unattended. These are pure-Python and the loop converges fastest here. |
| 3 Sep | `m4`, then `m5`. `m5` is the one that needs your attention: sandbox permission boundaries and free-tier write paths are outside the agent's control. |
| 4 Sep | `m6`, `m7`. |
| 5 Sep | `m8` — scenario, metrics, rehearsal. |
| 6 Sep | `m9` infra, deploy, demo video, freeze. |
| 7 Sep | Buffer + submit. |

If `m5` slips, the PRD's own rule applies: four systems with a real revoke beats six with none.
If `m8` slips, you have no metrics to cite on stage — protect it ahead of `m6`'s polish.

## Failure modes of this harness, and the counter

| Mode | Symptom | Counter |
| --- | --- | --- |
| Agent loops on an unsatisfiable gate | Same traceback across 15 attempts | Attempt cap + escalation; read `.agent/mN.gate.log` and fix the prompt, not the code |
| Agent invents a passing implementation | Gate green, behaviour wrong | Property tests and golden hashes in `tests/gates/`, which are protected |
| Coverage gamed by trivial tests | High coverage, low assertion density | `m2`/`m3` gates require property tests by name; review `git log -p` on the branch each morning |
| Cost drift on AWS | Silent spend | Budget guard is `m8` item 5, but build it on day 1 if you touch AWS earlier |
| frontend regression | SPA breaks on a backend merge | `contract` CI job diffs the exported JSON Schema against the committed snapshot |
