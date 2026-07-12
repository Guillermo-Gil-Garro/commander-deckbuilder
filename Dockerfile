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

# Directory layout must mirror the repo (backend/ + data/ + frontend/dist/)
# because pipeline modules anchor REPO_ROOT with Path(__file__).parents[2].
WORKDIR /app

COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
COPY backend/pipeline backend/pipeline
# Editable install keeps the code under /app/backend so REPO_ROOT resolves to /app.
RUN pip install --no-cache-dir -e ./backend

COPY --from=frontend-build /build/frontend/dist frontend/dist

# data/processed/cards.jsonl is gitignored, so it is not part of the build
# context; create the layout and let the pipeline (or an upload) fill it in.
RUN mkdir -p data/cache data/processed && chown -R appuser:appuser data

USER appuser
WORKDIR /app/backend
EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
