# Media Source Optimizer — developer entry points.
#
#   make lint     ruff (core, mocks, stack test, scripts) + offline check of the pinned contract/Peil copies
#   make test     lint + core unit/contract/integration tests (needs PostgreSQL 16, see docs/dev/AGENT_BRIEF.md)
#   make images   build media-source-optimizer-{core,web}:local and mso-mocks:local
#   make stack    build + run the container stack with mocks + smoke/e2e (tests/stack/results/)
#   make e2e      fresh stack, prepared (admin, connectors → mocks, backup, wizard completed), then ALL Playwright
#                 browser tests with credentials (web on http://127.0.0.1:48081); a skipped test fails the run
#   make down     remove the test stack (project msostack) and its data
#
# Behind a TLS-inspecting proxy: MSO_BUILD_CA=/pad/naar/ca.crt make images
SHELL := /bin/bash
PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: lint test images stack e2e down

lint:
	$(PY) -m ruff check apps/core apps/mocks tests/stack scripts
	$(PY) scripts/bronkopie.py controleer

test: lint
	cd apps/core && ../../$(PY) -m pytest -q -p no:warnings

images:
	scripts/build-images.sh all

stack:
	tests/stack/run.sh

# Lower bound for the number of Playwright tests (desktop + mobile); fewer found = failure, not a silent pass.
E2E_MIN_TESTS ?= 112

# The admin password is random per run and only passed through the environment (never echoed or stored).
e2e:
	set -uo pipefail; \
	export MSO_STACK_ADMIN_PW="E2e-$$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')"; \
	MSO_STACK_UP_ONLY=1 MSO_STACK_E2E_PREPARE=1 tests/stack/run.sh; rc=$$?; \
	if [[ $$rc -eq 0 ]]; then \
	  ( cd apps/web && MSO_E2E_BASE_URL=http://127.0.0.1:48081 MSO_E2E_USER=stackadmin \
	    MSO_E2E_PASSWORD="$$MSO_STACK_ADMIN_PW" MSO_E2E_STRICT=1 MSO_E2E_MIN_TESTS=$(E2E_MIN_TESTS) \
	    npx playwright test ); rc=$$?; \
	else echo "!! stack-voorbereiding voor e2e mislukt (rc $$rc) — Playwright niet gestart" >&2; fi; \
	$(MAKE) down; exit $$rc

down:
	-docker compose -p msostack down -v --remove-orphans
	-docker run --rm --user 0:0 --network none -v $(CURDIR)/tests/stack/.data:/d --entrypoint /bin/sh \
	  media-source-optimizer-core:stack -c 'rm -rf /d/appdata /d/work'
	-rm -rf tests/stack/.data
