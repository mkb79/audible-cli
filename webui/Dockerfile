# Build with the REPO ROOT as context (not webui/), since the backend
# depends on the parent audible-cli package via an editable path
# dependency ("../" relative to webui/pyproject.toml):
#   docker build -f webui/Dockerfile -t audible-cli-webui .

FROM node:20-slim AS frontend-builder
WORKDIR /build/frontend
COPY webui/frontend/package.json webui/frontend/package-lock.json ./
RUN npm ci
COPY webui/frontend/ ./
RUN npm run build

FROM python:3.12-slim AS backend-runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Needs the whole repo, not just webui/, so the editable "../" dependency
# on audible-cli resolves against real source rather than a missing path.
COPY . /app
WORKDIR /app/webui
RUN uv sync --frozen --no-dev

COPY --from=frontend-builder /build/frontend/dist /app/webui/src/audible_cli_webui/static

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

ENV WORK_DIR=/data/work \
    DATABASE_URL=sqlite:////data/webui.db \
    PATH="/app/webui/.venv/bin:${PATH}"
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

# Invoke the venv's alembic/uvicorn directly (via PATH) rather than `uv
# run`, which would implicitly re-sync on every container start — re-pulling
# the dev dependency group over the network and undoing the --no-dev build
# above. Migrations run on every start, not just the first, so a schema
# change on an existing deployment is picked up automatically too.
CMD ["sh", "-c", "alembic upgrade head && uvicorn audible_cli_webui.main:app --host 0.0.0.0 --port 8000"]
