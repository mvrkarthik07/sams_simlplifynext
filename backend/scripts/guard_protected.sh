#!/usr/bin/env bash
# PROTECTED FILE — agents may not modify.
#
# Verifies that every BLESSED file still hashes to its recorded value. Files added under a
# protected directory after blessing are permitted (m3 writes new golden hashes); files that
# were blessed and are now modified or deleted are not.
#
#   ./scripts/guard_protected.sh            # verify
#   ./scripts/guard_protected.sh --bless    # human-only: record current state
#   ./scripts/guard_protected.sh --list     # show what is currently protected
set -euo pipefail

MANIFEST=".protected.sha256"
PATHS=(
  "Makefile"
  "scripts/guard_protected.sh"
  "scripts/agent_loop.sh"
  ".github/workflows/ci.yml"
  "tests/gates"
  "src/deadbolt/contracts"
)

hash_paths() {
  for p in "${PATHS[@]}"; do
    [[ -e "$p" ]] || continue
    if [[ -d "$p" ]]; then
      find "$p" -type f ! -name '*.pyc' ! -name '.gitkeep' -print0 |
        LC_ALL=C sort -z | xargs -0 -r sha256sum
    else
      sha256sum "$p"
    fi
  done
}

case "${1:-}" in
  --bless)
    hash_paths > "$MANIFEST"
    echo "blessed $(wc -l < "$MANIFEST") file(s) -> $MANIFEST"
    exit 0 ;;
  --list)
    cat "$MANIFEST"; exit 0 ;;
esac

[[ -f "$MANIFEST" ]] || {
  echo "guard: no $MANIFEST. A human must run './scripts/guard_protected.sh --bless' once." >&2
  exit 1
}

fail=0
while read -r want file; do
  [[ -n "${file:-}" ]] || continue
  if [[ ! -f "$file" ]]; then
    echo "GUARD: protected file DELETED -> $file" >&2; fail=1; continue
  fi
  got="$(sha256sum "$file" | cut -d' ' -f1)"
  if [[ "$got" != "$want" ]]; then
    echo "GUARD: protected file MODIFIED -> $file" >&2
    git diff -- "$file" 2>/dev/null | head -n 40 >&2 || true
    fail=1
  fi
done < "$MANIFEST"

if (( fail )); then
  echo "GUARD FAILURE — gates, CI, and frozen contracts are not editable by the agent." >&2
  echo "If the change is intentional, a human re-blesses with --bless and commits with" >&2
  echo "trailer 'Protected-Change-Approved-By: <name>'." >&2
  exit 1
fi
echo "guard: ok ($(wc -l < "$MANIFEST") protected files verified)"
