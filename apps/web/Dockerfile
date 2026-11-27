# Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/Dockerfile — internally versioned copy, see PROVENANCE.md
# mso-web: Next.js UI + same-origin proxy (server.mjs) for /api/v1, /newznab and /torznab (MSO core).
#   docker build [--secret id=ca_bundle,src=/pad/naar/ca.crt] -t mso/web:dev apps/web
# Runtime als gebruiker 568, met alleen-lezen rootfs, een eigen /tmp en persistente websleutel in /appdata/web.
ARG NODE_IMAGE=mirror.gcr.io/library/node:22.23.5-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee

FROM ${NODE_IMAGE} AS deps
WORKDIR /src
COPY package.json package-lock.json ./
# Optional extra CA (only behind a TLS-inspecting proxy)
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca_bundle; fi; \
    npm ci --no-audit --no-fund --ignore-scripts

FROM ${NODE_IMAGE} AS build
WORKDIR /src
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /src/node_modules ./node_modules
COPY . .
RUN npm run build && node scripts/verpak-web.mjs

FROM ${NODE_IMAGE}
ARG UID=568
ARG GID=568
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 MSO_WEB_HOST=0.0.0.0 \
    MSO_CORE_URL=http://mso-core:8200
RUN groupadd -g ${GID} mso && useradd -u ${UID} -g ${GID} -M -s /usr/sbin/nologin mso \
    && install -d -m 0700 -o ${UID} -g ${GID} /appdata/web \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
       /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg
WORKDIR /app
COPY --from=build --chown=root:root /src/.next/standalone ./
ARG VERSION
ARG GIT_SHA
ARG BUILD_TIME
ARG CHANNEL
LABEL org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://git.medialeon.nl/leon/media-source-optimizer" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIME}" \
      io.github.lvos04.media-source-optimizer.channel="${CHANNEL}"
USER ${UID}:${GID}
VOLUME ["/appdata/web"]
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/login',{signal:AbortSignal.timeout(4000)}).then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
CMD ["node", "server.mjs"]
