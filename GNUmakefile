# M8 demo targets live here because the milestone's standing contract protects Makefile.
include Makefile

.PHONY: demo-reset demo-run

.PHONY: infra-install
infra-install:
	cd backend/infra && npm ci --no-audit --no-fund

# The protected gate invokes npx directly; install the pinned local CLI first so a clean
# checkout never falls through to an interactive package download.
gate-m9: infra-install

demo-reset:
	./backend/scripts/demo-reset

demo-run:
	./backend/scripts/demo-run
