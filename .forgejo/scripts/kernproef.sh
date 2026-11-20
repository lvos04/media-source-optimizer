#!/usr/bin/env bash
# De Forgejo-job gebruikt het hostnetwerk. Start uitsluitend onze eigen PostgreSQL op loopback.
set -euo pipefail

cluster=$(mktemp -d "$RUNNER_TEMP/mso-pg.XXXXXXXX")
pg=()
if [[ $(id -u) == 0 ]]; then
    chown postgres:postgres "$cluster"
    pg=(runuser -u postgres --)
fi
opruimen() {
    if [[ -e "$cluster/data/postmaster.pid" ]]; then
        "${pg[@]}" "$MSO_PG_BIN/pg_ctl" -D "$cluster/data" -m fast -w stop
    fi
    rm -rf -- "$cluster"
}
trap opruimen EXIT

PGPASSWORD=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
export PGPASSWORD
# initdb heropent /dev/stdin. Maak de pipe als dezelfde gebruiker die haar zal lezen.
"${pg[@]}" bash -euo pipefail -s -- "$MSO_PG_BIN" "$cluster/data" <<'INITDB'
printf '%s\n' "$PGPASSWORD" | "$1/initdb" -D "$2" -U mso \
    --no-locale --encoding=UTF8 --auth-local=peer --auth-host=scram-sha-256 --pwfile=/dev/stdin >/dev/null
INITDB
port=$(python -c 'import socket; s = socket.socket(); s.bind(("localhost", 0)); print(s.getsockname()[1])')
"${pg[@]}" "$MSO_PG_BIN/pg_ctl" -D "$cluster/data" -w -l "$cluster/postgres.log" \
    -o "-c listen_addresses=localhost -c unix_socket_directories='' -p $port" start
export MSO_TEST_DATABASE_URL="postgresql://mso@localhost:$port/postgres"
cd apps/core
python -m pytest -q -p no:warnings "$@"
