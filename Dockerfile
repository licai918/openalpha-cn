FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS web-builder

WORKDIR /build/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c AS uv

FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS python-builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /build
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENALPHA_HOST=0.0.0.0 \
    OPENALPHA_PORT=8000 \
    OPENALPHA_RUNTIME_DIR=/data \
    OPENALPHA_WEB_DIR=/app/web-dist \
    OPENALPHA_MAX_REQUEST_BYTES=8388608

WORKDIR /app
RUN addgroup --system --gid 10001 openalpha \
    && adduser --system --uid 10001 --ingroup openalpha --home /nonexistent openalpha \
    && mkdir /data \
    && chown openalpha:openalpha /data
COPY --from=python-builder --chown=openalpha:openalpha /build/.venv /app/.venv
COPY --from=web-builder --chown=openalpha:openalpha /build/web/dist /app/web-dist

USER 10001:10001
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# Shell form (not exec-form CMD) so the container's OPENALPHA_HOST/OPENALPHA_PORT
# ENV declarations above actually take effect instead of being silently shadowed
# by a hardcoded --host/--port -- see ADR-0004. `exec` still replaces this shell
# as PID 1, preserving normal signal forwarding. Falls back to the same
# 0.0.0.0:8000 the ENV lines already default to when either is unset.
CMD ["sh", "-c", "exec python -m uvicorn openalpha_cn.api.app:app --host \"${OPENALPHA_HOST:-0.0.0.0}\" --port \"${OPENALPHA_PORT:-8000}\" --no-server-header"]
