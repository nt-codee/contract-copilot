# syntax=docker/dockerfile:1
# Contract Review Co-pilot: one image, FastAPI API + built React app.
#   docker build -t cc:dev .
#   docker run -p 8080:8080 -e CLOUD=local -e LLM=fake cc:dev

# ---- stage 1: web build (tolerates a placeholder web/ with no package.json) ------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/ ./
RUN if [ -f package.json ]; then \
      npm ci --no-audit --no-fund && npm run build; \
    else \
      echo "web/package.json missing: shipping an empty dist" && mkdir -p dist; \
    fi

# ---- stage 2: api runtime --------------------------------------------------------------------
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app/services/api
COPY services/api/pyproject.toml services/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/api/app ./app
COPY samples/contracts /app/samples/contracts
COPY samples/company /app/samples/company
COPY knowledge /app/knowledge
COPY --from=web /web/dist /app/web/dist

RUN useradd --create-home --uid 10001 app && mkdir -p /tmp/cc-data && chown app /tmp/cc-data
USER app

# APP_VERSION is set at runtime by the Cloud Run service (scripts/deploy.sh passes the image tag); this
# build arg only gives a locally built image a value: docker build --build-arg APP_VERSION=$(git rev-parse --short HEAD)
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8080 \
    SAMPLES_DIR=/app/samples/contracts \
    COMPANY_SAMPLES_DIR=/app/samples/company \
    KNOWLEDGE_DIR=/app/knowledge \
    WEB_DIST=/app/web/dist \
    DATA_DIR=/tmp/cc-data
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port ${PORT}"]
