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

# OMP_NUM_THREADS/OPENBLAS_NUM_THREADS/MKL_NUM_THREADS/VECLIB_MAXIMUM_THREADS/
# NUMEXPR_NUM_THREADS=1 pin BLAS/OpenMP thread counts ahead of the numerical stack
# (ADR-0003, V2-P0B-009): unpinned, BLAS/OpenMP floating-point reduction order changes
# with thread count, a direct reproducibility hazard for a content-addressed system.
# No numeric library is imported yet, so this has no observable effect today -- see
# runtime/seeding.py -- but is set here, at container start, so it is already correct
# before any Python code runs once P4 does introduce one. V2-P4-015 re-measured this
# row of ADR-0003's Consequence 6 and found it already correct: all five are pinned,
# and tests/unit/test_repository_assets.py has held them by literal since V2-P0B-009.
#
# PYTHONSAFEPATH=1 (V2-P4-015) stops CPython prepending the working directory to
# sys.path. It is what makes the `WORKDIR /data` below safe: `python -m uvicorn` puts
# the process's cwd first on sys.path, ahead of site-packages, so a working directory
# that is a user-writable volume would let a file dropped into that volume shadow an
# installed module. Measured in the shipped image: without this, `python -m site`
# under `-w /data` prints '/data' as sys.path[0]; with it, sys.path starts at the
# stdlib. Setting it while WORKDIR was /app would have changed nothing observable --
# /app is on the read-only layer -- which is why it arrives in the same change.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONSAFEPATH=1 \
    OPENALPHA_HOST=0.0.0.0 \
    OPENALPHA_PORT=8000 \
    OPENALPHA_RUNTIME_DIR=/data \
    OPENALPHA_WEB_DIR=/app/web-dist \
    OPENALPHA_MAX_REQUEST_BYTES=33554432 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

WORKDIR /app
# The service account is a regular account, not a system one. `adduser --system` asks for a uid
# at or below SYS_UID_MAX (999), and 10001 is chosen to sit far from that range, so the line this
# replaces contradicted itself and useradd said so on every build ("uid 10001 is greater than
# SYS_UID_MAX 999"). What --system supplied without saying so is spelled out instead: a home at
# /nonexistent that is never created, a nologin shell, a password field that is a bare lock ('!',
# no hash behind it), and neither of the two things /etc/login.defs gives every regular account
# unless told otherwise -- password aging (the PASS_* keys) and a 65,536-id subordinate uid/gid
# range for user namespaces (the SUB_* keys). Measured against the image the --system line built,
# the openalpha entries of passwd, group, shadow, gshadow, subuid and subgid are identical. Debian's
# `adduser` without --system is not the fix: it also puts the account in the `users` group, and
# it has no way to decline the subordinate range.
RUN groupadd --gid 10001 openalpha \
    && useradd --uid 10001 --gid openalpha \
        --home-dir /nonexistent --no-create-home \
        --shell /usr/sbin/nologin \
        --password '!' \
        --key PASS_MIN_DAYS=-1 --key PASS_MAX_DAYS=-1 --key PASS_WARN_AGE=-1 \
        --key SUB_UID_COUNT=0 --key SUB_GID_COUNT=0 \
        openalpha \
    && mkdir /data \
    && chown openalpha:openalpha /data
COPY --from=python-builder --chown=openalpha:openalpha /build/.venv /app/.venv
COPY --from=web-builder --chown=openalpha:openalpha /build/web/dist /app/web-dist

USER 10001:10001

# The process's working directory is the one writable filesystem this container has, and
# that is a fix rather than a preference (V2-P4-015). DuckDB defaults an in-memory
# connection's `temp_directory` to the *relative* path `.tmp`, resolved against the cwd --
# and `panel/store.py` opens `duckdb.connect(":memory:")` on every `write_panel_batch` to
# stage a partition. With `WORKDIR /app` and `deploy/compose.yml`'s own `read_only: true`,
# a staging query that outgrows `memory_limit` fails outright. Reproduced in this image,
# same query and same 200 MB limit, three working directories:
#
#   /app  (the layer, read-only)  IO Error: Failed to create directory ".tmp":
#                                 Read-only file system
#   /tmp  (the 64 MB tmpfs)       Out of Memory Error: failed to offload data block ...
#                                 (57.3 MiB/57.5 MiB used). This limit was set by the
#                                 'max_temp_directory_size' setting.
#   /data (the runtime volume)    the query completes, and `.tmp` exists afterwards.
#
# The middle row is why the seam audit's prescription -- enlarge the tmpfs -- is not the
# fix and no number was guessed for it: a tmpfs is RAM, so a spill that lands in one has
# not spilled, and raising 64 MB only moves the same wall. A spill needs a filesystem, and
# this container has exactly one. `.tmp` is dot-prefixed and cannot collide with the
# runtime layout beside it (`state.sqlite3`, `evidence/`, `backups/`).
#
# The audit's other two claims about this container were measured to be wrong: there IS a
# `/dev/shm`, writable, at Docker's 64 MB default (the audit says there is none), and the
# thread pinning it asks for was already there. `libgomp1` is in *neither* stage rather
# than in the wrong one, and stays out: ADR-0003's V2-P4-015 section records why the
# dependency that needs it was not taken.
WORKDIR /data

EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# Exec-form CMD invoking `sh -c` so the container's OPENALPHA_HOST/OPENALPHA_PORT
# ENV declarations above actually take effect instead of being silently shadowed
# by a hardcoded --host/--port -- see ADR-0004. `exec` still replaces this shell
# as PID 1, preserving normal signal forwarding. Falls back to the same
# 0.0.0.0:8000 the ENV lines already default to when either is unset.
CMD ["sh", "-c", "exec python -m uvicorn openalpha_cn.api.app:app --host \"${OPENALPHA_HOST:-0.0.0.0}\" --port \"${OPENALPHA_PORT:-8000}\" --no-server-header"]
