# Bookr Analyst: one image serving the FastAPI backend and the built React UI.
# Build context is the repo root (monorepo): docker build -t bookr-analyst .

# 1. Build the frontend ------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# 2. Python runtime ----------------------------------------------------------
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Layout mirrors the repo, because config.py resolves PROJECT_ROOT as two levels above the package:
#   /app/backend  /app/config  /app/frontend/dist
# All data lives in Postgres (ANALYST_DATABASE_URL); the container keeps no state.
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ backend/
COPY config/ config/
COPY --from=frontend /app/frontend/dist frontend/dist

WORKDIR /app/backend
EXPOSE 8000
# Render injects PORT. Keep a single worker: sync state and the daily scheduler live in-process.
CMD ["sh", "-c", "exec uvicorn bookr_analyst.api:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
