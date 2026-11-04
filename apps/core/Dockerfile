# syntax=docker/dockerfile:1.7
# Media Source Optimizer — core image (API, worker, migrations, init; role = first argument)
#
#   docker build [--secret id=ca_bundle,src=/pad/naar/ca.crt] -t media-source-optimizer-core:dev apps/core
#
# Roles:   api (default) · worker · migrate [--down N | --to N] · init (root, one-shot) · backup
#          anything else is executed as a command (e.g. `sh`).
# Runtime: uid/gid 568 (TrueNAS "apps"), read-only rootfs compatible (writable: /tmp, /appdata, /work),
#          dumb-init as PID 1, PostgreSQL 16 client tools (pg_dump/pg_restore/psql) for backups.
# The optional ca_bundle build secret is only for TLS-inspecting proxies; it is never stored in a layer.

ARG PYTHON_IMAGE=mirror.gcr.io/library/python:3.13.18-slim-bookworm
ARG POSTGRES_IMAGE=mirror.gcr.io/library/postgres:16.15-bookworm

# ---------------------------------------------------------------- PostgreSQL 16 client tools
# Debian bookworm only ships client 15, and pg_dump must be >= the server major (16). Instead of
# adding the PGDG apt repo at build time we copy the client binaries + their shared libraries from
# the official postgres:16 image (same Debian release as the Python base). They live in
# /opt/pgclient and are started via small wrappers, so they never shadow the system libraries.
FROM ${POSTGRES_IMAGE} AS pgclient
RUN set -eu; mkdir -p /out/bin /out/lib; \
    for b in pg_dump pg_restore psql pg_isready; do cp /usr/lib/postgresql/16/bin/$b /out/bin/; done; \
    ldd /out/bin/* | awk '/=>/ {print $3}' | sort -u \
      | grep -vE '/(libc|libm|libdl|libpthread|librt|libresolv|ld-linux[^/]*)\.so' \
      | while read -r lib; do cp -L "$lib" /out/lib/; done; \
    /usr/lib/postgresql/16/bin/pg_dump --version > /out/VERSION

# ---------------------------------------------------------------- Python package
FROM ${PYTHON_IMAGE} AS build
WORKDIR /src
COPY pyproject.toml ./
COPY mso ./mso
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore --prefix=/install \
        . dumb-init==1.2.5.post1

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE}
ARG UID=568
ARG GID=568
LABEL org.opencontainers.image.title="media-source-optimizer-core" \
      org.opencontainers.image.source="https://github.com/lvos04/media-source-optimizer" \
      org.opencontainers.image.version="1.2.0"
COPY --from=pgclient /out/ /opt/pgclient/
COPY --from=build /install /usr/local
COPY docker/entrypoint.sh /usr/local/bin/mso-entrypoint
COPY docker/healthcheck.py /usr/local/lib/mso-docker/healthcheck.py
COPY docker/init.py /usr/local/lib/mso-docker/init.py
RUN set -eu; \
    for b in pg_dump pg_restore psql pg_isready; do \
      printf '#!/bin/sh\nLD_LIBRARY_PATH=/opt/pgclient/lib exec /opt/pgclient/bin/%s "$@"\n' "$b" > /usr/local/bin/$b; \
      chmod 0755 /usr/local/bin/$b; \
    done; \
    pg_dump --version | grep -q ' 16\.'; \
    chmod 0755 /usr/local/bin/mso-entrypoint; \
    ln -s /usr/local/bin/mso-entrypoint /usr/local/bin/mso; \
    printf '#!/bin/sh\nexec python /usr/local/lib/mso-docker/healthcheck.py "$@"\n' > /usr/local/bin/mso-healthcheck; \
    chmod 0755 /usr/local/bin/mso-healthcheck; \
    groupadd -g ${GID} mso; useradd -u ${UID} -g ${GID} -M -d /tmp -s /usr/sbin/nologin mso; \
    mkdir -p /appdata /work; chown ${UID}:${GID} /appdata /work; \
    python -c "import mso.app, psycopg, uvicorn, cryptography"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp \
    MSO_DATA_DIR=/appdata MSO_WORK_DIR=/work \
    MSO_DB_HOST=postgres MSO_DB_PORT=5432 MSO_DB_NAME=mso MSO_DB_USER=mso
USER ${UID}:${GID}
EXPOSE 8200
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["mso-healthcheck"]
ENTRYPOINT ["/usr/local/bin/dumb-init", "--", "/usr/local/bin/mso-entrypoint"]
CMD ["api"]
