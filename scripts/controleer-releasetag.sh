#!/usr/bin/env bash
# Zonder argument de versie teruggeven. Met een register ook alle drie de doeltags toetsen.
set -euo pipefail
version=$(< release/VERSION)
getal='(0|[1-9][0-9]*)'
if [[ ! "$version" =~ ^$getal\.$getal\.$getal$ || "${GITHUB_REF:-}" != "refs/tags/v$version" ]]; then
    echo "De git-tag komt niet exact overeen met de numerieke release/VERSION." >&2
    exit 1
fi
if (( $# )); then
    for name in media-source-optimizer{,-core,-web}; do
        image="$1/$name:$version"
        if melding=$(LC_ALL=C docker manifest inspect "$image" 2>&1 >/dev/null); then
            echo "Publicatie gestopt. De tag van $image bestaat al." >&2
            exit 1
        fi
        case "$melding" in
            'manifest unknown'|'manifest unknown: manifest unknown'|"no such manifest: $image") ;;
            *) echo "Publicatie gestopt. Het register bevestigt geen ontbrekend manifest voor $image." >&2; exit 1 ;;
        esac
    done
fi
printf '%s\n' "$version"
