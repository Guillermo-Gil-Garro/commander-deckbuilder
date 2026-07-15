# Commander Deckbuilder — image for Hugging Face Spaces (Docker SDK, port 7860).

# --- Stage 1: build the frontend ---
FROM node:22-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: backend runtime ---
FROM python:3.11-slim

# HF Spaces requires running as a non-root user with uid 1000.
RUN useradd --uid 1000 --create-home appuser

# Directory layout must mirror the repo (backend/ + data/ + frontend/dist/ +
# the root YAML config) because the backend packages anchor REPO_ROOT with
# Path(__file__).resolve().parents[2], i.e. /app.
WORKDIR /app

# Every package declared in backend/pyproject.toml must be copied before the
# install: setuptools resolves them from disk, and a missing one only shows up
# at import time inside the Space.
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
COPY backend/pipeline backend/pipeline
COPY backend/quotas backend/quotas
COPY backend/rules backend/rules
COPY backend/selector backend/selector
COPY backend/tags backend/tags
# Editable install keeps the code under /app/backend so REPO_ROOT resolves to /app.
RUN pip install --no-cache-dir -e ./backend

# Versioned config read from REPO_ROOT (= /app): the app refuses to start
# without any of these, so they belong in the image, not in a volume.
COPY banlist.yaml quotas.yaml rules.yaml featured_commanders.yaml ./
COPY data/tags/llm_tags.jsonl data/tags/llm_tags.jsonl

COPY --from=frontend-build /build/frontend/dist frontend/dist

# data/processed/cards.jsonl (16 MB) is gitignored and excluded from the build
# context, so it is NOT in this image: the Space starts *degraded* on purpose —
# /health reports {"status":"degraded","cards_loaded":0} and every deck
# endpoint answers 503 with an explicit message. That is the intended,
# diagnosable failure until Fase 6 ships the pool. Create the layout and let
# the pipeline (or an upload) fill it in.
RUN mkdir -p data/cache data/processed && chown -R appuser:appuser data

USER appuser
WORKDIR /app/backend
EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
