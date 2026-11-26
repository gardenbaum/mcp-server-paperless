# mcp-server-paperless over Streamable HTTP (decision-28): the builder installs the locked
# dependencies and the project into a virtual environment, the base copies only that. The last stage,
# runtime, is the default target and the published image; stdio is built by name (decision-38).
# Both base images are pinned by digest; Renovate bumps tag and digest together.

# renovate: datasource=docker depName=ghcr.io/astral-sh/uv
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

# renovate: datasource=docker depName=python
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    UV_NO_CACHE=1
WORKDIR /app

# Dependencies first: this layer is rebuilt only when pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=bind,from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=bind,from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-dev --no-editable

# renovate: datasource=docker depName=python
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS base

# Security fixes Debian has shipped since the base image digest was built (the scan fails the
# image on fixed HIGH findings), and no pip: the server runs from /app/.venv and needs neither pip
# nor the copies of msgpack and urllib3 vendored in it, which the scan reports. Nothing can
# install packages into the running container afterwards.
# The workflow sets APT_REFRESH to the day of the build, so the upgrade never comes from a build
# cache older than a day (the cache had kept a libzstd1 that Debian had already fixed).
ARG APT_REFRESH=none
RUN echo "packages of ${APT_REFRESH}" \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/local/lib/python3.14/site-packages/pip* /usr/local/lib/python3.14/ensurepip \
        /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14

# A fixed, unprivileged user without a home or a login shell. The environment belongs to root and
# is read-only for the server, which writes nothing: the image runs with --read-only.
RUN groupadd --system --gid 10001 mcp-server-paperless \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin mcp-server-paperless
COPY --from=builder /app/.venv /app/.venv

# The Docker MCP Catalog builds this target and runs it over stdio: `docker build --target stdio`.
# Only metadata, so within one build its filesystem is the one the workflow scans (a later build, like
# the catalog's, gets newer packages from apt-get upgrade). No HTTP settings, port or health check,
# for nothing listens. Never published from here.
FROM base AS stdio
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
ENTRYPOINT ["mcp-server-paperless"]

FROM base AS runtime

# 0.0.0.0 is a remote bind, so the server refuses to start until PAPERLESS_MCP_HTTP_AUTH_TOKENS and
# PAPERLESS_MCP_HTTP_ALLOWED_HOSTS are set. No credentials and no unauthenticated mode are baked in.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PAPERLESS_MCP_HTTP_HOST=0.0.0.0 \
    PAPERLESS_MCP_HTTP_PORT=8000

USER 10001:10001
EXPOSE 8000

# The server has no health route (decision-27), so the probe only opens a TCP connection to the
# configured port: it says the process is serving, not that Paperless is reachable.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, socket; socket.create_connection(('127.0.0.1', int(os.environ['PAPERLESS_MCP_HTTP_PORT'])), timeout=3).close()"]

# Last, so that a different version or revision changes only the labels and a tag build reuses every
# layer that was scanned.
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="mcp-server-paperless" \
      org.opencontainers.image.description="MCP server for paperless-ngx 3.x over Streamable HTTP" \
      org.opencontainers.image.source="https://github.com/gardenbaum/mcp-server-paperless" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

ENTRYPOINT ["mcp-server-paperless-http"]
