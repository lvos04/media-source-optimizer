#!/usr/bin/env bash
# Bouw de S-P3-205-weigerimage op schema 607 en geef het onveranderlijke image-ID terug.
set -euo pipefail
cd "$(dirname "$0")/../.."

revision=2512cd761ad30f42a4787935687388e9a3f0d428
base_pins=5e2c3af7cf01895ea526327ac8b6a21346c6d361
build_dir=$(mktemp -d "${TMPDIR:-/tmp}/mso-schema-guard.XXXXXXXX")
trap 'rm -rf -- "$build_dir"' EXIT
mkdir "$build_dir/source"
git archive "$revision" | tar -x -C "$build_dir/source"

args=()
for binding in PYTHON_IMAGE POSTGRES_IMAGE NODE_IMAGE; do
    base=$(git show "$base_pins:deploy/allinone/Dockerfile" | sed -n "s/^ARG $binding=//p")
    [[ "$base" =~ @sha256:[0-9a-f]{64}$ ]]
    args+=(--build-arg "$binding=$base")
done

tag=${1:-mso-schema-guard:stack}
DOCKER_BUILDKIT=1 docker build "${args[@]}" \
    --label "org.opencontainers.image.revision=$revision" \
    --tag "$tag" \
    --file "$build_dir/source/deploy/allinone/Dockerfile" "$build_dir/source" >&2
docker image inspect --format '{{.Id}}' "$tag"
