#!/usr/bin/env bash
# PROTECTED FILE — agents may not modify.
#
# Usage:
#   ./scripts/agent_loop.sh m2                 # loop one milestone until gate-m2 passes
#   ./scripts/agent_loop.sh m2 20              # with a 20-attempt cap
#   ./scripts/agent_loop.sh --chain m0 m1 m2   # run milestones in sequence, stop on escalation
#
# Contract: the gate is truth. Codex is re-invoked with the *full failure output* appended to
# the milestone prompt, so each turn starts from evidence rather than from a summary.
set -uo pipefail

MAX_DEFAULT=15
RUN_DIR=".agent"
mkdir -p "$RUN_DIR"

CODEX_BIN="${CODEX_BIN:-codex}"
# --full-auto: edit + run commands in the workspace without per-action approval.
# Add --search only if a milestone genuinely needs live API docs.
CODEX_ARGS=(exec --full-auto --skip-git-repo-check)

run_milestone() {
  local M="$1" MAX="${2:-$MAX_DEFAULT}"
  local PROMPT="prompts/${M}.md"
  local GATE="gate-${M}"
  local LOG="${RUN_DIR}/${M}.gate.log"
  local TURN="${RUN_DIR}/${M}.turn.md"

  [[ -f "$PROMPT" ]] || { echo "missing $PROMPT" >&2; return 2; }

  for ((i = 1; i <= MAX; i++)); do
    echo "=== ${M}: checking ${GATE} (attempt ${i}/${MAX}) ==="
    if make "$GATE" >"$LOG" 2>&1; then
      echo "=== ${M}: GATE GREEN after $((i - 1)) agent turn(s) ==="
      git -C . add -A && git -C . commit -q -m "chore(${M}): gate green" || true
      return 0
    fi

    {
      cat "$PROMPT"
      cat <<EOF

---

## LOOP STATE — attempt ${i} of ${MAX}

\`make ${GATE}\` is currently FAILING. This is the authoritative failure output
(last 24 KB, stdout+stderr interleaved):

\`\`\`text
$(tail -c 24576 "$LOG")
\`\`\`

Rules for this turn:
1. Diagnose the first failure in that log, not the last one. Cascading errors have one root.
2. Fix the root cause in application code. You may not edit \`Makefile\`,
   \`.github/workflows/**\`, \`scripts/guard_protected.sh\`, \`tests/gates/**\`, or frozen
   contracts. You may not delete, skip, or xfail a test, and you may not lower a coverage
   threshold.
3. Before you end this turn, run \`make ${GATE}\` yourself and keep working until it exits 0
   or you have exhausted every hypothesis you can test.
4. If — and only if — you are blocked by something outside the repository (a missing
   credential, a rate limit, an upstream API that does not exist), write a section titled
   \`## BLOCKED: ${M}\` to \`DECISIONS.md\` stating the exact blocker and the smallest change
   that would unblock it, then stop.
EOF
    } >"$TURN"

    echo "--- ${M}: invoking codex (turn ${i}) ---"
    "$CODEX_BIN" "${CODEX_ARGS[@]}" - <"$TURN" || echo "codex exited nonzero; re-checking gate anyway"

    if grep -q "^## BLOCKED: ${M}" DECISIONS.md 2>/dev/null; then
      echo "=== ${M}: agent declared BLOCKED — escalating to human ==="
      sed -n "/^## BLOCKED: ${M}/,/^## /p" DECISIONS.md
      return 3
    fi
  done

  echo "=== ${M}: ESCALATE — ${MAX} attempts exhausted. Last gate output: ${LOG} ==="
  tail -n 60 "$LOG"
  return 1
}

if [[ "${1:-}" == "--chain" ]]; then
  shift
  for m in "$@"; do
    run_milestone "$m" || { echo "chain halted at ${m}"; exit 1; }
  done
  echo "chain complete: $*"
else
  run_milestone "$@"
fi
