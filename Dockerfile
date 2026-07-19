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

# The EDHREC popularity ranking (committed, ~70 KB): without it the commander
# picker degrades to alphabetical order.
COPY data/edhrec_ranking.json data/edhrec_ranking.json

# The card pool (26 MB, Git LFS) — the app starts *degraded* without it, so it
# ships in the image (Fase 6). Refresh = rebuild the LFS file and redeploy.
COPY data/processed/cards.jsonl data/processed/cards.jsonl

# Precached EDHREC pages for the 61 featured commanders, so their first build is
# instant and does not depend on egress to json.edhrec.com. Non-featured
# commanders still fetch on demand at request time.
COPY data/cache/edhrec data/cache/edhrec

COPY --from=frontend-build /build/frontend/dist frontend/dist

# data/cache must be writable by the runtime user: on-demand EDHREC pages and
# Scryfall print/image lookups are cached here at request time.
RUN mkdir -p data/cache/prints data/processed && chown -R appuser:appuser data

USER appuser
WORKDIR /app/backend
EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
