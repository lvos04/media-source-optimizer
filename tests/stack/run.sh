#!/usr/bin/env bash
# Build the images and run the full container stack + mocks, then the smoke/e2e test (smoke.py).
#
#   tests/stack/run.sh                 build images, start stack, run smoke test, tear down
#   MSO_STACK_KEEP=1 tests/stack/run.sh   leave the stack running afterwards (http://127.0.0.1:38081)
#   MSO_STACK_NO_BUILD=1 …             reuse existing :stack images
#   MSO_STACK_NO_WEB=1 …               run without the web container (API direct)
#   MSO_STACK_UP_ONLY=1 …              only start a fresh stack and wait until healthy (implies KEEP; for Playwright)
#   MSO_FEATURES=a,b …                 limit feature discovery (only to work around a broken in-progress module)
#   MSO_BUILD_CA=/pad/ca.crt …         CA bundle for a TLS-inspecting proxy (BuildKit secret, not stored)
#
# Compose project: msostack. Data: tests/stack/.data (wiped before and after the run).
# Results: tests/stack/results/{smoke-report.json,smoke-report.md,compose-ps.txt,logs/*.log}
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
HERE="$ROOT/tests/stack"
RESULTS="$HERE/results"
# a stack started only for Playwright keeps its logs apart, so the smoke report is not overwritten
[[ -n "${MSO_STACK_UP_ONLY:-}" ]] && RESULTS="$HERE/results/e2e-stack"
DATA="$HERE/.data"
PY="${MSO_STACK_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3)"

export MSO_IMAGE_TAG=stack
export MSO_CORE_IMAGE="media-source-optimizer-core:stack"
export MSO_WEB_IMAGE="media-source-optimizer-web:stack"
export MSO_MOCKS_IMAGE="mso-mocks:stack"
export MSO_POSTGRES_IMAGE="${MSO_POSTGRES_IMAGE:-mirror.gcr.io/library/postgres:16.15-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab}"
export APPDATA="$DATA/appdata" WORK="$DATA/work"
export MSO_WEB_BIND=127.0.0.1 MSO_WEB_PORT="${MSO_STACK_WEB_PORT:-38081}" MSO_STACK_API_PORT="${MSO_STACK_API_PORT:-38200}"
export MSO_FEATURES="${MSO_FEATURES:-}"

# credentials the mocks are started with (random per run → leak check in logs is meaningful)
QB_PW="qb-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"
SAB_KEY="sab$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
export MSO_STACK_MOCK_ARGS_QBITTORRENT="--fault username=msostack --fault password=$QB_PW"
PRL_KEY="prl$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
export MSO_STACK_MOCK_ARGS_SABNZBD="--fault api_key=$SAB_KEY"
export MSO_STACK_MOCK_ARGS_PROWLARR="--fault api_key=$PRL_KEY"
export MSO_STACK_SECRETS="{\"qbittorrent\": {\"username\": \"msostack\", \"password\": \"$QB_PW\"},
  \"sabnzbd\": {\"api_key\": \"$SAB_KEY\"}, \"prowlarr\": {\"api_key\": \"$PRL_KEY\"},
  \"mediaguard\": {\"username\": \"mso-operator\", \"password\": \"Operator-Wachtwoord1\"}}"

wipe_data() {  # files are owned by 568 → remove from inside a container
  if [[ -d "$DATA" ]]; then
    docker run --rm --user 0:0 --network none -v "$DATA":/d --entrypoint /bin/sh "$MSO_CORE_IMAGE" \
      -c 'rm -rf /d/appdata /d/work' >/dev/null 2>&1 || rm -rf "$DATA"/appdata "$DATA"/work
  fi
  mkdir -p "$APPDATA" "$WORK"
}

if [[ -z "${MSO_STACK_NO_BUILD:-}" ]]; then
  scripts/build-images.sh core
  scripts/build-images.sh mocks
  if [[ -z "${MSO_STACK_NO_WEB:-}" ]]; then
    scripts/build-images.sh web || { echo "!! web-image bouwen mislukt — stack draait zonder web" >&2; MSO_STACK_NO_WEB=1; }
  fi
fi
if [[ -z "${MSO_STACK_NO_WEB:-}" ]] && ! docker image inspect "$MSO_WEB_IMAGE" >/dev/null 2>&1; then
  MSO_STACK_NO_WEB=1
fi

rm -rf "$RESULTS"; mkdir -p "$RESULTS/logs"
MOCKS=$(MSO_STACK_MOCKS_OUT="$RESULTS/compose.mocks.yaml" "$PY" "$HERE/gen_mocks.py")
echo "mocks: $MOCKS"
COMPOSE="docker compose -p msostack -f deploy/truenas/compose.yaml -f tests/stack/compose.test.yaml -f $RESULTS/compose.mocks.yaml"
export MSO_STACK_COMPOSE="$COMPOSE"

SERVICES="init postgres migrate api worker"
[[ -z "${MSO_STACK_NO_WEB:-}" ]] && SERVICES="$SERVICES web"
for m in $MOCKS; do [[ "$m" == mock-* ]] && SERVICES="$SERVICES $m"; done
export MSO_STACK_SERVICES="$SERVICES"
export MSO_STACK_API="http://127.0.0.1:$MSO_STACK_API_PORT"
if [[ -z "${MSO_STACK_NO_WEB:-}" ]]; then
  export MSO_STACK_WEB="http://127.0.0.1:$MSO_WEB_PORT" MSO_STACK_BASE="http://127.0.0.1:$MSO_WEB_PORT"
else
  export MSO_STACK_WEB="" MSO_STACK_BASE="$MSO_STACK_API"
fi
[[ -n "$MSO_FEATURES" ]] && echo "!! LET OP: MSO_FEATURES=$MSO_FEATURES (niet alle modules geladen)" >&2

cleanup() {
  $COMPOSE ps -a > "$RESULTS/compose-ps.txt" 2>&1 || true
  for s in $($COMPOSE config --services 2>/dev/null); do
    $COMPOSE logs --no-color "$s" > "$RESULTS/logs/$s.log" 2>&1 || true
  done
  if [[ -z "${MSO_STACK_KEEP:-}" ]]; then
    $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    wipe_data; rm -rf "$DATA"
  else
    echo "stack blijft draaien: $MSO_STACK_BASE  (opruimen: $COMPOSE down -v)"
  fi
}
trap cleanup EXIT

$COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
wipe_data
$COMPOSE config -q
start=$SECONDS
# shellcheck disable=SC2086
$COMPOSE up -d $SERVICES
echo "compose up: $((SECONDS - start)) s"
if [[ -n "${MSO_STACK_UP_ONLY:-}" ]]; then
  MSO_STACK_KEEP=1
  "$PY" "$HERE/smoke.py" --wait-only
  exit 0
fi
"$PY" "$HERE/smoke.py"
