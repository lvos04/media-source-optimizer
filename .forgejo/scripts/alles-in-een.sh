#!/usr/bin/env bash
# Bouw en beproef precies de image die de aanroeper eventueel publiceert.
set -euo pipefail

export MSO_BUILD_VERSION="${MSO_BUILD_VERSION:-$(< release/VERSION)}"
export MSO_BUILD_TIME="${MSO_BUILD_TIME:-$(date -u +%FT%T.%3N+00:00)}"
export MSO_BUILD_GIT_SHA="$GITHUB_SHA" MSO_BUILD_CHANNEL="${MSO_BUILD_CHANNEL:-development}"
export MSO_MOCKS_IMAGE="mso-mocks:$MSO_IMAGE_TAG"
export MSO_STACK_RESULTS_ROOT="$GITHUB_WORKSPACE/test-results/allinone"
export TMPDIR="$RUNNER_TEMP"
mkdir -p "$MSO_STACK_RESULTS_ROOT"

scripts/build-images.sh allinone
scripts/build-images.sh mocks
MSO_SCHEMA_GUARD_IMAGE=$(tests/stack/build-schema-guard.sh "mso-schema-guard:$MSO_IMAGE_TAG")
export MSO_SCHEMA_GUARD_IMAGE
python -m venv "$RUNNER_TEMP/aio-venv"
"$RUNNER_TEMP/aio-venv/bin/pip" install --disable-pip-version-check httpx==0.28.1
"$RUNNER_TEMP/aio-venv/bin/python" tests/stack/allinone_test.py "media-source-optimizer:$MSO_IMAGE_TAG"
