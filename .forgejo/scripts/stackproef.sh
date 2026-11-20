#!/usr/bin/env bash
set -euo pipefail

proef=${1:?stack of e2e}
export COMPOSE_PROJECT_NAME="mso-$proef-$GITHUB_RUN_ID"
export MSO_STACK_IMAGE_TAG="$COMPOSE_PROJECT_NAME"
export MSO_STACK_DATA_DIR="$RUNNER_TEMP/$COMPOSE_PROJECT_NAME"
export MSO_STACK_RESULTS_ROOT="$GITHUB_WORKSPACE/test-results/$proef"
export MSO_STACK_PYTHON=python MSO_BUILD_GIT_SHA="$GITHUB_SHA" MSO_BUILD_CHANNEL=development
MSO_BUILD_VERSION=$(< release/VERSION)
MSO_BUILD_TIME=$(date -u +%FT%T.%3N+00:00)
export MSO_BUILD_VERSION MSO_BUILD_TIME
export MSO_POSTGRES_IMAGE=postgres:16.15-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab
case "$proef" in
    stack) tests/stack/run.sh ;;
    e2e) make e2e PY=python ;;
    *) exit 2 ;;
esac
