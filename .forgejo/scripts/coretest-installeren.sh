#!/usr/bin/env bash
set -euo pipefail

if [[ ! -x "$MSO_PG_BIN/initdb" ]]; then
    apt=()
    if [[ $(id -u) != 0 ]]; then apt=(sudo); fi
    "${apt[@]}" apt-get update -q
    "${apt[@]}" apt-get install -yq --no-install-recommends postgresql-common
    # Het pakket mag bij installatie geen standaardcluster op de gedeelde poort starten.
    "${apt[@]}" pg_conftool /etc/postgresql-common/createcluster.conf set create_main_cluster false
    "${apt[@]}" apt-get install -yq --no-install-recommends postgresql-16
fi
"$MSO_PG_BIN/pg_dump" --version
"$MSO_PG_BIN/pg_restore" --version
python -m pip install -e 'apps/core[test]'
