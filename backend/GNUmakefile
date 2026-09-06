# M8 demo targets live here because the milestone's standing contract protects Makefile.
GNUMAKE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(GNUMAKE_DIR)/..)
include $(GNUMAKE_DIR)Makefile

.PHONY: demo-reset demo-run gate-m5b gate-m10 gate-m11 gate-m12 gate-m12r gate-m13 capture-github

.PHONY: infra-install
infra-install:
	cd infra && npm ci --no-audit --no-fund

# The protected gate invokes npx directly; install the pinned local CLI first so a clean
# checkout never falls through to an interactive package download.
gate-m9: infra-install

demo-reset:
	./scripts/demo-reset

demo-run:
	./scripts/demo-run

gate-m5b:
	$(PY) pytest -q -m m5b
	$(PY) pytest -q -m "rollback or determinism"
	$(MAKE) verify

gate-m10:
	cd $(REPO_ROOT)/backend && $(MAKE) verify
	cd $(REPO_ROOT)/backend && uv run pytest -q -m m10 tests/integration/test_api_contract.py
	cd $(REPO_ROOT)/backend && ./scripts/guard_protected.sh
	cd $(REPO_ROOT)/frontend && npm run lint && npm run typecheck && npm run build

capture-github:
	cd $(REPO_ROOT)/backend && uv run python -m deadbolt.cli capture

gate-m11:
	cd $(REPO_ROOT)/backend && $(MAKE) verify
	cd $(REPO_ROOT)/backend && uv run pytest -q -m m11
	cd $(REPO_ROOT)/backend && uv run pytest -q -k "determinism or golden"
	cd $(REPO_ROOT)/backend && ./scripts/guard_protected.sh

gate-m12:
	cd $(REPO_ROOT)/frontend && npm run lint && npm run typecheck && npm run build
	cd $(REPO_ROOT)/backend && $(MAKE) verify && ./scripts/guard_protected.sh
	test -s $(REPO_ROOT)/backend/artifacts/ui-inventory.md

gate-m12r:
	cd $(REPO_ROOT)/frontend && npm run lint && npm run typecheck && npm run build
	cd $(REPO_ROOT)/backend && $(MAKE) verify && ./scripts/guard_protected.sh
	test -s $(REPO_ROOT)/backend/artifacts/ui-inventory.md
	! grep -rnE '#[0-9a-fA-F]{6}' $(REPO_ROOT)/frontend/src --include=*.tsx --include=*.ts

gate-m13:
	cd $(REPO_ROOT)/backend && $(MAKE) verify && ./scripts/guard_protected.sh
	cd $(REPO_ROOT)/backend && $(MAKE) demo-run
	grep -q "What is real" $(REPO_ROOT)/README.md
