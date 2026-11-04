# Media Source Optimizer — developer entry points.
#
#   make lint     ruff (core, mocks, stack test)
#   make test     lint + core unit/contract/integration tests (needs PostgreSQL 16, see docs/dev/AGENT_BRIEF.md)
#   make images   build media-source-optimizer-{core,web}:local and mso-mocks:local
#   make stack    build + run the container stack with mocks + smoke/e2e (tests/stack/results/)
#   make e2e      fresh stack + Playwright browser tests against it (web on http://127.0.0.1:48081)
#   make down     remove the test stack (project msostack) and its data
#
# Behind a TLS-inspecting proxy: MSO_BUILD_CA=/pad/naar/ca.crt make images
SHELL := /bin/bash
PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: lint test images stack e2e down

lint:
	$(PY) -m ruff check apps/core apps/mocks tests/stack

test: lint
	cd apps/core && ../../$(PY) -m pytest -q -p no:warnings

images:
	scripts/build-images.sh all

stack:
	tests/stack/run.sh

e2e:
	MSO_STACK_UP_ONLY=1 tests/stack/run.sh
	cd apps/web && MSO_E2E_BASE_URL=http://127.0.0.1:48081 npx playwright test; rc=$$?; \
	  cd ../.. && $(MAKE) down; exit $$rc

down:
	-docker compose -p msostack down -v --remove-orphans
	-docker run --rm --user 0:0 --network none -v $(CURDIR)/tests/stack/.data:/d --entrypoint /bin/sh \
	  media-source-optimizer-core:stack -c 'rm -rf /d/appdata /d/work'
	-rm -rf tests/stack/.data
