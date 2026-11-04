# syntax=docker/dockerfile:1.7
# Media Source Optimizer — ALL-IN-ONE image (database + API + worker + web in one container).
#
#   docker build -f deploy/allinone/Dockerfile [--secret id=ca_bundle,src=ca.crt] -t media-source-optimizer:dev .
#
# Install as a normal TrueNAS "Custom App": image ghcr.io/lvos04/media-source-optimizer:<versie>,
# one host path → /appdata, one port 38081 → 38081. No YAML, no passwords, no environment variables.
# PostgreSQL 16 runs inside, on a unix socket only (no network port, no password).
# Starts as root only to fix /appdata ownership; every process then runs as uid/gid 568 (TrueNAS "apps").

ARG PYTHON_IMAGE=mirror.gcr.io/library/python:3.13.18-slim-bookworm
ARG POSTGRES_IMAGE=mirror.gcr.io/library/postgres:16.15-bookworm
ARG NODE_IMAGE=mirror.gcr.io/library/node:22-bookworm-slim

# ---------------------------------------------------------------- PostgreSQL 16 server + client
FROM ${POSTGRES_IMAGE} AS pg
RUN set -eu; mkdir -p /out/lib /out/pg; \
    rm -f /usr/lib/postgresql/16/lib/llvmjit*.so; rm -rf /usr/lib/postgresql/16/lib/bitcode; \
    for f in /usr/lib/postgresql/16/bin/* /usr/lib/postgresql/16/lib/*.so; do ldd "$f" 2>/dev/null || true; done \
      | awk '/=>/ {print $3}' | sort -u \
      | grep -vE '/(libc|libm|libdl|libpthread|librt|libresolv|ld-linux[^/]*)\.so' \
      | while read -r lib; do cp -L "$lib" /out/lib/; done; \
    # Debian ships some share files as symlinks into /etc: copy the real files (-L)
    cp -rL /usr/lib/postgresql/16 /out/pg/lib; cp -rL /usr/share/postgresql/16 /out/pg/share; \
    test -f /out/pg/share/postgresql.conf.sample

# ---------------------------------------------------------------- web UI (Next.js standalone)
FROM ${NODE_IMAGE} AS web
WORKDIR /src
COPY apps/web/package.json apps/web/package-lock.json ./
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca_bundle; fi; \
    npm ci --no-audit --no-fund --ignore-scripts
COPY apps/web/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build \
    && cp server.mjs .next/standalone/ \
    && cp -r public .next/standalone/public \
    && cp -r .next/static .next/standalone/.next/static \
    && rm -rf .next/standalone/node_modules/@img .next/standalone/node_modules/sharp .next/standalone/.next/cache

# ---------------------------------------------------------------- Python core
FROM ${PYTHON_IMAGE} AS core
WORKDIR /src
COPY apps/core/pyproject.toml ./
COPY apps/core/mso ./mso
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore --prefix=/install \
        . dumb-init==1.2.5.post1

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE}
ARG VERSION=1.1.1
LABEL org.opencontainers.image.title="media-source-optimizer" \
      org.opencontainers.image.description="Media Source Optimizer — alles-in-één (database, API, worker, web)" \
      org.opencontainers.image.source="https://github.com/lvos04/media-source-optimizer" \
      org.opencontainers.image.version="${VERSION}"
COPY --from=pg /out/pg/lib /usr/lib/postgresql/16
COPY --from=pg /out/pg/share /usr/share/postgresql/16
COPY --from=pg /out/lib /opt/pglibs
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /src/.next/standalone /opt/mso-web
COPY --from=core /install /usr/local
COPY deploy/allinone/supervisor.py /usr/local/lib/mso/supervisor.py
COPY deploy/allinone/healthcheck.py /usr/local/lib/mso/healthcheck.py
RUN set -eu; \
    for b in postgres initdb pg_ctl pg_isready psql pg_dump pg_restore createdb; do \
      printf '#!/bin/sh\nLD_LIBRARY_PATH=/opt/pglibs exec /usr/lib/postgresql/16/bin/%s "$@"\n' "$b" > /usr/local/bin/$b; \
      chmod 0755 /usr/local/bin/$b; \
    done; \
    postgres --version | grep -q ' 16\.'; pg_dump --version | grep -q ' 16\.'; node --version; \
    groupadd -g 568 apps; useradd -u 568 -g 568 -M -d /tmp -s /usr/sbin/nologin apps; \
    mkdir -p /appdata; chown 568:568 /appdata; \
    printf '#!/bin/sh\nexec env MSO_DATABASE_URL="postgresql://mso@/mso?host=/tmp/mso-pg" python -m mso "$@"\n' \
      > /usr/local/bin/mso; chmod 0755 /usr/local/bin/mso; \
    python -c "import mso.app, psycopg, uvicorn, cryptography"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MSO_DATA_DIR=/appdata MSO_PORT=38081 TZ=Europe/Amsterdam
VOLUME ["/appdata"]
EXPOSE 38081
HEALTHCHECK --interval=30s --timeout=8s --start-period=90s --retries=3 \
  CMD ["python", "/usr/local/lib/mso/healthcheck.py"]
ENTRYPOINT ["/usr/local/bin/dumb-init", "--"]
CMD ["python", "/usr/local/lib/mso/supervisor.py"]
