"""FastAPI app entrypoint.

Run from ``backend/`` as ``uvicorn app.main:app``. Serves the API under
``/api`` and, when a frontend build exists at ``frontend/dist/``, the SPA
at ``/`` with an index.html fallback for client-side routes.

The handlers here are transport only: they read the ``AppState`` that the
lifespan built (``app.state.deckbuilder``) and translate domain errors into
HTTP status codes. Anything that decides *what* a deck looks like lives in
``selector/``, ``quotas/``, ``rules/`` or ``tags/``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.errors import POOL_UNAVAILABLE
from app.schemas import HealthResponse
from app.state import AppState, build_app_state

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load every artifact once, before the first request is served."""
    state = build_app_state()
    app.state.deckbuilder = state
    if state is None:
        logger.error(
            "Startup DEGRADED: no card pool. /api/health reports 'degraded' "
            "and every deck endpoint returns 503."
        )
    else:
        logger.info(
            "Startup ready: %d cards, %d selectable commanders, %d featured, "
            "%d banned, %d tagged cards, solver limit %.1fs",
            len(state.pool.by_name),
            len(state.commanders),
            len(state.featured),
            len(state.banned_names),
            state.tags_count,
            state.solver_time_limit_s,
        )
    yield


app = FastAPI(title="Commander Deckbuilder", lifespan=lifespan)


def _state(request: Request) -> AppState:
    """The loaded ``AppState``, or 503 if the app came up degraded."""
    state: AppState | None = getattr(request.app.state, "deckbuilder", None)
    if state is None:
        raise HTTPException(status_code=503, detail=POOL_UNAVAILABLE)
    return state


@app.get("/api/health")
def health(request: Request) -> HealthResponse:
    """Service diagnosis, straight from the state loaded at startup.

    ``degraded`` means the card pool never loaded: the app is up (so you can
    read this) but every deck endpoint will answer 503. Everything else that
    could go wrong is versioned config and aborts startup instead, so a
    running app that reports ``ok`` has all of its artifacts.
    """
    state: AppState | None = getattr(request.app.state, "deckbuilder", None)
    if state is None:
        return HealthResponse(
            status="degraded", cards_loaded=0, commanders=0, banned=0, tags=0
        )
    return HealthResponse(
        status="ok",
        cards_loaded=len(state.pool.by_name),
        commanders=len(state.commanders),
        banned=len(state.banned_names),
        tags=state.tags_count,
    )


class SPAStaticFiles(StaticFiles):
    """Static files with index.html fallback for client-side routes.

    Unknown ``/api`` paths keep their 404 instead of returning the SPA shell.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # StaticFiles normalizes the path with os.sep, so use posix form.
            posix_path = path.replace("\\", "/")
            if exc.status_code != 404 or posix_path.startswith("api/"):
                raise
        return await super().get_response("index.html", scope)


if FRONTEND_DIST.is_dir():
    app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    logger.warning("Frontend build not found at %s; serving API only", FRONTEND_DIST)
