#!/usr/bin/env bash
set -euo pipefail

proef=${1:?web of e2e}
werkmap=$(mktemp -d "$RUNNER_TEMP/mso-web.XXXXXXXX")
trap 'rm -rf -- "$werkmap"' EXIT
# npm start ook native hulpprogramma's. Gebruik daarvoor de tijdelijke jobmap
# en neem de Peil-bronnen en gedeelde testvectoren in hun normale indeling mee.
bronnen=(apps/web design/ontwerptaal tests/vectoren)
if [[ "$proef" == e2e ]]; then
    bronnen+=(Makefile .forgejo/scripts/stackproef.sh scripts/build-images.sh apps/core apps/mocks
              deploy/truenas tests/stack release/VERSION)
fi
git archive HEAD -- "${bronnen[@]}" | tar -x -C "$werkmap"
cd "$werkmap/apps/web"
npm ci --no-audit --no-fund

case "$proef" in
    web)
        npm run check:peil
        npm run typecheck
        npm run lint
        npm run test:unit
        npm run build
        ;;
    e2e)
        npx playwright install --with-deps chromium
        cd "$werkmap"
        bash .forgejo/scripts/stackproef.sh e2e
        ;;
    *) exit 2 ;;
esac
