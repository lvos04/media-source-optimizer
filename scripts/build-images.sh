#!/usr/bin/env bash
# Build the Media Source Optimizer images locally.
#
#   scripts/build-images.sh [core|web|mocks|all] (default: all)
#
# Environment:
#   MSO_IMAGE_TAG   tag for the images (default: local) → media-source-optimizer-{core,web}:<tag>, mso-mocks:<tag>
#   MSO_BUILD_CA    CA bundle for a TLS-inspecting proxy; passed as BuildKit secret (never stored in a layer)
#   HTTPS_PROXY     when set, builds run with --network=host and receive the proxy as build-arg
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${MSO_IMAGE_TAG:-local}"
WHAT="${1:-all}"
args=()
if [[ -n "${HTTPS_PROXY:-${https_proxy:-}}" ]]; then
  p="${HTTPS_PROXY:-${https_proxy:-}}"
  args+=(--network=host --build-arg "HTTPS_PROXY=$p" --build-arg "https_proxy=$p")
  [[ -n "${NO_PROXY:-}" ]] && args+=(--build-arg "NO_PROXY=$NO_PROXY" --build-arg "no_proxy=$NO_PROXY")
fi
if [[ -n "${MSO_BUILD_CA:-}" ]]; then
  args+=(--secret "id=ca_bundle,src=$MSO_BUILD_CA")
fi

build() {  # name context
  echo "==> docker build $1:$TAG ($2)"
  local start=$SECONDS
  DOCKER_BUILDKIT=1 docker build "${args[@]}" -t "$1:$TAG" "$2"
  echo "==> $1:$TAG klaar in $((SECONDS - start)) s, $(docker image inspect "$1:$TAG" --format '{{.Size}}' | awk '{printf "%.0f MB", $1/1e6}')"
}

case "$WHAT" in
  core)  build media-source-optimizer-core apps/core ;;
  web)   build media-source-optimizer-web apps/web ;;
  mocks) build mso-mocks apps/mocks ;;
  all)   build media-source-optimizer-core apps/core; build mso-mocks apps/mocks; build media-source-optimizer-web apps/web ;;
  *) echo "gebruik: $0 [core|web|mocks|all]" >&2; exit 2 ;;
esac
