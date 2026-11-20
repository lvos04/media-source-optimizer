#!/usr/bin/env bash
set -euo pipefail

if [[ ! -x "$MSO_PG_BIN/pg_dump" ]]; then
    apt=()
    if [[ $(id -u) != 0 ]]; then apt=(sudo); fi
    "${apt[@]}" apt-get update -q
    "${apt[@]}" apt-get install -yq --no-install-recommends postgresql-client-16
fi
"$MSO_PG_BIN/pg_dump" --version
"$MSO_PG_BIN/pg_restore" --version
python -m pip install -e 'apps/core[test]'
