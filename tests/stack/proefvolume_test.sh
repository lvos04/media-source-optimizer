#!/usr/bin/env bash
# Deze negatieve proef maakt een eigen leeg volume. Er staan nog geen appgegevens in.
set -euo pipefail
source tests/stack/proefvolume.sh
start_proefvolume "${1:?containernaam}" "${2:?proefimage}"
if melding=$(docker volume rm "$proefvolume" 2>&1); then
    echo "FOUT: Docker kon het gekoppelde proefvolume verwijderen." >&2
    exit 1
fi
if [[ "$melding" != *"volume is in use"* || "$melding" != *"$proefhouder"* ]]; then
    printf '%s\n' "$melding" >&2
    exit 1
fi
docker volume inspect "$proefvolume" >/dev/null
echo "Docker weigert het gekoppelde proefvolume te verwijderen."
