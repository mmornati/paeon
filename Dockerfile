# Paeon container image. Multi-stage: the builder holds uv and the resolved virtualenv, the final
# stage holds the interpreter and that virtualenv only.
#
# Runtime contract:
#   - runs as the numeric, non-root user 10001:10001
#   - works with `docker run --read-only`: the only writable place is /data (mount a volume there)
#   - the API has only the bearer token on its ingest endpoints: keep it on an internal network

# Same base images and digests as Horae, so one Renovate rule keeps both in step.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="paeon" \
      org.opencontainers.image.description="Alert triage, investigation and approved remediation for self-hosted servers" \
      org.opencontainers.image.source="https://github.com/mmornati/paeon" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

RUN groupadd --gid 10001 paeon \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
               --shell /usr/sbin/nologin paeon \
    && mkdir /data \
    && chown 10001:10001 /data \
    && chmod 0700 /data

COPY --from=builder /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONSAFEPATH=1 \
    PAEON_DB=/data/paeon.sqlite \
    PAEON_HOST=0.0.0.0 \
    PAEON_PORT=8080

WORKDIR /data
USER 10001:10001
EXPOSE 8080

# Standard library only (no curl in the image), no proxy. Follows PAEON_PORT.
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:' + os.environ.get('PAEON_PORT', '8080') + '/healthz', timeout=5)"]

ENTRYPOINT ["paeon"]
CMD ["serve"]
