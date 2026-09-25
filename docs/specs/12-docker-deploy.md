# 12 Docker & deployment

**Purpose** Run Supdev as a container with sane, safe defaults.

**Design** `Dockerfile`: multi-stage; build stage uses `uv sync --frozen --no-dev --no-editable` from `uv.lock`; runtime stage is `python:3.13-slim` + `git` (the GitHub adapter works in a local clone), non-root uid 10001, `/data` volume (SQLite DB, audit log, master key file, git work dirs), `HEALTHCHECK` on `/api/health`, `CMD supdev serve`. Container env defaults: `SUPDEV_AUTH=token`, host `0.0.0.0`. `docker-compose.yml`: binds `127.0.0.1:${SUPDEV_HOST_PORT:-8100}`, requires `SUPDEV_BOOTSTRAP_TOKEN`, read-only rootfs + tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`, operator flags passed through (all off). `.env.example`, `.dockerignore` (no tests/docs/DB/keys/`.secrets` in the image). The API serves UI files with `Cache-Control: no-cache`.

**First run** `cp .env.example .env` → set `SUPDEV_BOOTSTRAP_TOKEN` (`openssl rand -hex 32`) → `docker compose up --build` → open the UI, paste the token in the "API token" box → Settings.

**Verification status** The image has **not been built** (no Docker daemon / Podman VM available when this was written). What *was* verified: the wheel builds and contains prompts + web assets; a clean venv with the wheel installed (non-editable, like the image) runs `supdev serve` in token mode with the container's env vars; health, auth, settings page and admin API all work.
**Test gaps** `docker build`, healthcheck, read-only-rootfs behaviour (git needs a writable `$HOME`; `HOME` is the user's home dir — verify with `read_only: true`), volume permissions on first run, multi-arch build.
**Out of scope** Kubernetes manifests/Helm, TLS termination (use a reverse proxy), horizontal scaling (SQLite + in-process locks ⇒ single instance), HA/backup of the `/data` volume.
