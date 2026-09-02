# PROTECTED FILE — agents may not modify. Enforced by scripts/guard_protected.sh.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
BE := .
PY := uv run
COV_MIN := 85

.PHONY: install lint types test verify guard preflight gate-preflight \
        gate-m0 gate-m1 gate-m2 gate-m3 gate-m4 gate-m5 gate-m6 gate-m7 gate-m8 gate-m9 \
        determinism fe-build

install:
	uv sync --all-extras --dev

lint:
	$(PY) ruff check src tests
	$(PY) ruff format --check src tests

types:
	$(PY) mypy --strict src

# Import-linter enforces the determinism law structurally: engine/ and plan/ must not
# reach datetime.now, time, random, math, or any provider/IO module.
arch:
	$(PY) lint-imports --config importlinter.ini

test:
	$(PY) pytest -q --cov=deadbolt --cov-report=term-missing --cov-fail-under=$(COV_MIN) \
		-m "not live"

determinism:
	$(PY) pytest -q -m determinism -p no:randomly

verify: lint types arch test guard

guard:
	./scripts/guard_protected.sh

fe-build:
	cd ../Frontend && npm ci --no-audit --no-fund && npm run build

# ---------------------------------------------------------------------------
# Preflight: workspace shape and toolchain, before a single line of Python.
# ---------------------------------------------------------------------------

preflight gate-preflight:
	@command -v uv   >/dev/null || { echo "MISSING: uv (https://docs.astral.sh/uv/)"; exit 1; }
	@command -v git  >/dev/null || { echo "MISSING: git"; exit 1; }
	@command -v node >/dev/null || { echo "MISSING: node 20 (needed for frontend + CDK)"; exit 1; }
	@test -d ../Frontend || { echo "MISSING: frontend/ — wrong repo root"; exit 1; }
	@test -d prompts  || { echo "MISSING: prompts/"; exit 1; }
	@test -f ../AGENTS.md || { echo "MISSING: AGENTS.md"; exit 1; }
	@test -f DECISIONS.md || { echo "MISSING: DECISIONS.md"; exit 1; }
	@test -f docs/PRD_Deadbolt.pdf || { echo "MISSING: docs/PRD_Deadbolt.pdf"; exit 1; }
	@git diff --quiet -- ../Frontend || { echo "VIOLATION: frontend/ has uncommitted edits"; exit 1; }
	@test -z "$$(git status --porcelain -- ../Frontend)" || { echo "VIOLATION: frontend/ dirty"; exit 1; }
	@./scripts/guard_protected.sh
	@echo "preflight: ok"

# ---------------------------------------------------------------------------
# Milestone gates. Each is cumulative: a later gate re-runs every earlier one.
# ---------------------------------------------------------------------------

gate-m0: gate-preflight
	uv sync --all-extras --dev
	$(MAKE) lint types
	$(PY) pytest -q -m m0
	./scripts/guard_protected.sh
	test -f DECISIONS.md

gate-m1: gate-m0
	$(PY) pytest -q -m "m0 or m1" --cov=deadbolt/contracts --cov=deadbolt/providers/fixtures \
		--cov-fail-under=95
	$(MAKE) arch

gate-m2: gate-m1
	$(PY) pytest -q -m "m2" --cov=deadbolt/engine --cov-fail-under=98
	$(PY) pytest -q -m "determinism and m2" -p no:randomly

gate-m3: gate-m2
	$(PY) pytest -q -m "m3" --cov=deadbolt/plan --cov-fail-under=98
	$(MAKE) determinism

gate-m4: gate-m3
	$(PY) pytest -q -m "m4" --cov=deadbolt/executor --cov-fail-under=95
	$(PY) pytest -q -m "rollback"

gate-m5: gate-m4
	$(PY) pytest -q -m "m5"
	$(MAKE) verify

gate-m6: gate-m5
	$(PY) pytest -q -m "m6"
	$(MAKE) verify

gate-m7: gate-m6
	$(PY) pytest -q -m "m7"
	$(MAKE) verify

gate-m8: gate-m7
	$(PY) pytest -q -m "m8"
	$(MAKE) verify
	$(PY) pytest -q -m e2e

gate-m9: gate-m8
	cd infra && npx cdk synth --quiet
	$(PY) pytest -q -m m9
	$(MAKE) verify
