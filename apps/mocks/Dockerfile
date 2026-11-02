# syntax=docker/dockerfile:1.7
# Media Source Optimizer — TEST-ONLY image with the fake services under msomocks/ (Prowlarr, SABnzbd,
# qBittorrent, MediaGuard, Newznab/Torznab, NNTP …). Never part of the production stack.
#
#   docker build [--secret id=ca_bundle,src=/pad/naar/ca.crt] -t mso-mocks:test apps/mocks
#   docker run --rm mso-mocks:test <name> --port 8080 [--fault key=value]
ARG PYTHON_IMAGE=mirror.gcr.io/library/python:3.13.18-slim-bookworm
FROM ${PYTHON_IMAGE}
ARG UID=568
ARG GID=568
# Same pinned versions as apps/core/pyproject.toml (mocks are FastAPI/uvicorn apps).
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore \
        fastapi==0.162.0 starlette==1.10.0 "uvicorn[standard]==0.54.0" httpx==0.28.1 defusedxml==0.7.1 \
        pydantic==2.14.2 python-multipart==0.0.20 \
    && groupadd -g ${GID} mso && useradd -u ${UID} -g ${GID} -M -d /tmp -s /usr/sbin/nologin mso
WORKDIR /opt/mocks
COPY msomocks ./msomocks
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/mocks HOME=/tmp
USER ${UID}:${GID}
EXPOSE 8080
ENTRYPOINT ["python", "-m", "msomocks"]
