#!/usr/bin/env bash
set -euo pipefail

image=postgres:16.15-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab
loopback=$(getent ahostsv4 localhost | awk 'NR == 1 {print $1}')
test -n "$loopback"
PGPASSWORD=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
export PGPASSWORD POSTGRES_PASSWORD="$PGPASSWORD"

# Elke job heeft een eigen database in begrensd geheugen en een vrije loopbackpoort.
# PostgreSQL houdt fsync, synchronous_commit en full_page_writes ingeschakeld.
database=$(docker run --detach --rm --memory=640m --cpus=2 \
    --tmpfs /var/lib/postgresql/data:rw,size=384m --publish "$loopback::5432" \
    --env POSTGRES_USER=mso --env POSTGRES_DB=postgres --env POSTGRES_PASSWORD \
    "$image" -c max_wal_size=128MB -c min_wal_size=32MB)
opruimen() {
    if docker container inspect "$database" >/dev/null 2>&1; then
        docker container rm --force --volumes "$database" >/dev/null
    fi
}
trap opruimen EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

adres=$(docker port "$database" 5432/tcp)
export MSO_TEST_DATABASE_URL="postgresql://mso@localhost:${adres##*:}/postgres"
for ((poging=0; poging<60; poging++)); do
    if "$MSO_PG_BIN/pg_isready" --dbname "$MSO_TEST_DATABASE_URL" --quiet; then break; fi
    sleep 1
done
"$MSO_PG_BIN/pg_isready" --dbname "$MSO_TEST_DATABASE_URL"
cd apps/core
python -m pytest -p no:warnings "$@"
