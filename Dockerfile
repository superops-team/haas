# syntax=docker/dockerfile:1
# HaaS sidecar image on top of OpenSandbox AIO.
#
# Production builds MUST pin the base image digest. HAAS_BASE_IMAGE is an
# explicit trusted mirror/cache override; release values must be digest-pinned.
ARG HAAS_BASE_IMAGE=ghcr.io/agent-infra/sandbox@sha256:5ca2cd5619ee1e18c5479301e740c1e35307ce85d4142a145aec65d459655eee
FROM ${HAAS_BASE_IMAGE}

# --- Codex CLI (P0 harness runtime) ---
# Bump CODEX_NPM_VERSION together with specs/codex-app-server-adapter schema
# fixture and re-run `make adk-compat` / schema drift check.
ARG CODEX_NPM_VERSION=0.150.1
RUN --mount=type=cache,target=/root/.npm npm install -g "@openai/codex@${CODEX_NPM_VERSION}" \
    && codex --version \
    && npm cache clean --force

# --- HaaS Python runtime (dependency layer before source copy) ---
WORKDIR /app/haas
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/uv \
    pip install uv \
    && uv venv --python python3.12 \
    && uv sync --frozen --no-dev --no-install-project

# --- HaaS source (after dependency layers) ---
# README.md is required: pyproject declares `readme = "README.md"`, so the
# project install fails without it.
COPY README.md ./
COPY haas ./haas
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# --- Runtime layout + entrypoint ---
# Preserve AIO /opt/gem/run.sh; HaaS entrypoint wraps and delegates to it.
# AIO's node22 REPL defaults to 8092, which AGENTS.md reserves for the HaaS
# sidecar. Move the REPL to 8093 via AIO's own documented override rather than
# patching the base image.
#
# Trim AIO services HaaS does not use (specs/runtime-trim/README.md) via AIO's
# official DISABLE_* / NODE_VERSION env vars — NOT by editing AIO scripts. This
# cuts memory/CPU/attack surface at runtime; it does not shrink image layers.
# Kept: browser (chromium), VNC/noVNC, MCP browser, sandbox/execd, credential
# vault, /opt/gem/run.sh. Disabled: code-server (VSCode), JupyterLab, and the
# multi-version Node.js REPL servers.
ENV NODEJS_REPL_PORT_22=8093 \
    HAAS_SIDECAR_PORT=8092 \
    HAAS_RUNTIME_ROOT=/tmp/haas \
    HAAS_DATA_ROOT=/data/haas \
    CODEX_HOME=/data/haas/harnesses/codex/home \
    DISABLE_CODE_SERVER=true \
    DISABLE_JUPYTER=true \
    DISABLE_NODEJS_REPL=true \
    NODE_VERSION=node22
COPY docker/ /opt/haas/
RUN mkdir -p /opt/gem/nginx \
    && cp /opt/haas/nginx.haas.conf /opt/gem/nginx/haas.conf \
    && chmod +x /opt/haas/run.sh

EXPOSE 8080
ENTRYPOINT ["/opt/haas/run.sh"]
