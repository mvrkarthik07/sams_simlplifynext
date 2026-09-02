#!/usr/bin/env bash
# Run ONCE, by a human, from the root of the repo that already contains frontend/.
#   unzip deadbolt-agent-kit.zip && ./deadbolt-agent-kit/bootstrap.sh
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(pwd)"

[[ "$KIT" != "$ROOT" ]] || { echo "run this from the repo root, not from inside the kit" >&2; exit 1; }
[[ -d "$ROOT/.git" ]]   || { echo "not a git repo: $ROOT" >&2; exit 1; }
[[ -d "$ROOT/frontend" ]] || {
  echo "WARNING: no frontend/ here. Confirm this is the right repo root." >&2
  read -rp "continue anyway? [y/N] " a; [[ "$a" == "y" ]] || exit 1
}

echo "==> laying harness into $ROOT"
cp -R "$KIT/AGENTS.md" "$KIT/RUNBOOK.md" "$KIT/Makefile" "$ROOT/"
mkdir -p "$ROOT/scripts" "$ROOT/prompts" "$ROOT/docs" "$ROOT/.github/workflows"
cp -R "$KIT/scripts/." "$ROOT/scripts/"
cp -R "$KIT/prompts/." "$ROOT/prompts/"
cp -R "$KIT/docs/." "$ROOT/docs/"
cp "$KIT/.github/workflows/ci.yml" "$ROOT/.github/workflows/ci.yml"
chmod +x "$ROOT/scripts/"*.sh

echo "==> creating backend skeleton"
mkdir -p "$ROOT/backend/src/deadbolt"/{contracts,providers/fixtures,graph,engine,plan,broker,executor,audit,handlers}
mkdir -p "$ROOT/backend/tests"/{unit,integration,gates/golden,fixtures/seed,live,support}
mkdir -p "$ROOT/infra"
find "$ROOT/backend/src" -type d -exec touch {}/__init__.py \;
find "$ROOT/backend/tests" -type d -exec touch {}/.gitkeep \;
touch "$ROOT/infra/.gitkeep"

[[ -f "$ROOT/DECISIONS.md" ]] || cat > "$ROOT/DECISIONS.md" <<'EOF'
# Decision log

Append-only. Newest entries at the bottom. One entry per ambiguity resolved,
dependency added, or blocker hit.
EOF

cat >> "$ROOT/.gitignore" <<'EOF'

# deadbolt agent harness
.agent/
backend/.venv/
backend/.coverage*
backend/.pytest_cache/
artifacts/
EOF

echo "==> blessing protected-path manifest (human-only action)"
"$ROOT/scripts/guard_protected.sh" --bless

cat <<'EOF'

Harness installed. Next, as a human:

  git add -A
  git commit -m "chore: deadbolt agent harness, gates, backend skeleton

Protected-Change-Approved-By: <your name>"

Then session zero:

  codex exec --full-auto - < prompts/preflight.md
  # or, looped:
  ./scripts/agent_loop.sh preflight

Then the unattended chain:

  ./scripts/agent_loop.sh --chain m0 m1 m2 m3 m4

EOF
