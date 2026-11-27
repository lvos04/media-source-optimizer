#!/usr/bin/env bash
# Bouw en beproef precies de image die de aanroeper eventueel publiceert.
set -euo pipefail
source tests/stack/proefvolume.sh

export MSO_AIO_NAME="mso-aio-$MSO_IMAGE_TAG"
if [[ ${1:-} == ruim ]]; then
    # Afsluitende stap: ook na afbreken de containers en het netwerk van deze proef weg.
    docker rm -fv "$MSO_AIO_NAME" "$MSO_AIO_NAME-sab" >/dev/null 2>&1 || true
    docker network rm "$MSO_AIO_NAME-net" >/dev/null 2>&1 || true
    ruim_proefvolume "$MSO_AIO_NAME-volume"
    ruim_proefvolume "$MSO_AIO_NAME-volumeproef"
    exit 0
fi
export MSO_BUILD_VERSION="${MSO_BUILD_VERSION:-$(< release/VERSION)}"
export MSO_BUILD_TIME="${MSO_BUILD_TIME:-$(date -u +%FT%T.%3N+00:00)}"
export MSO_BUILD_GIT_SHA="$GITHUB_SHA" MSO_BUILD_CHANNEL="${MSO_BUILD_CHANNEL:-development}"
export MSO_MOCKS_IMAGE="mso-mocks:$MSO_IMAGE_TAG"
export MSO_STACK_RESULTS_ROOT="$GITHUB_WORKSPACE/test-results/allinone"
export TMPDIR="$RUNNER_TEMP"
mkdir -p "$MSO_STACK_RESULTS_ROOT"

bash scripts/build-images.sh allinone
image=$(docker image inspect --format '{{.Id}}' "media-source-optimizer:$MSO_IMAGE_TAG")
start_proefvolume "$MSO_AIO_NAME-volume" "$image"
bash tests/stack/proefvolume_test.sh "$MSO_AIO_NAME-volumeproef" "$image"
bash scripts/build-images.sh mocks
MSO_SCHEMA_GUARD_IMAGE=$(bash tests/stack/build-schema-guard.sh "mso-schema-guard:$MSO_IMAGE_TAG")
export MSO_SCHEMA_GUARD_IMAGE
python -m venv "$RUNNER_TEMP/aio-venv"
"$RUNNER_TEMP/aio-venv/bin/pip" install --disable-pip-version-check httpx==0.28.1
export MSO_AIO_DATA_VOLUME="$proefvolume" MSO_AIO_TEST_PORT=0
"$RUNNER_TEMP/aio-venv/bin/python" tests/stack/allinone_test.py "$image"
