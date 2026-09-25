# syntax=docker/dockerfile:1.7
# ---- build: resolve deps from the lockfile into a venv, install the project as a normal (non-editable) package
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

# ---- runtime: slim, non-root, git only (the GitHub adapter works in a local clone)
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /home/supdev supdev \
    && mkdir -p /data && chown supdev:supdev /data
COPY --from=build --chown=supdev:supdev /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SUPDEV_HOST=0.0.0.0 SUPDEV_PORT=8000 \
    SUPDEV_AUTH=token \
    SUPDEV_DB=/data/supdev.db SUPDEV_AUDIT_PATH=/data/audit.jsonl \
    SUPDEV_MASTER_KEY_FILE=/data/master.key SUPDEV_WORKDIR_ROOT=/data/work
USER supdev
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"
CMD ["supdev", "serve"]
