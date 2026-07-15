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
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.errors import POOL_UNAVAILABLE
from app.schemas import CardView, HealthResponse, card_view, commander_view
from app.state import COMMANDER_SEARCH_LIMIT_DEFAULT, AppState, build_app_state

LOG_LEVEL_ENV = "DECKBUILDER_LOG_LEVEL"

# uvicorn only configures its own loggers, so without this the startup summary
# -- and, worse, the "degraded, no card pool" diagnosis -- would go nowhere.
# A degraded Space that cannot say why is the failure this whole design exists
# to avoid. main.py is the entrypoint, so owning logging setup belongs here.
logging.basicConfig(
    level=os.environ.get(LOG_LEVEL_ENV, "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

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


@app.get("/api/commanders/featured")
def featured_commanders(request: Request) -> list[CardView]:
    """The group's curated commanders, in ``featured_commanders.yaml`` order.

    This is the landing-page list: a hand-picked starting point for players
    who do not arrive with a commander already in mind. File order is the
    group's ordering and is preserved verbatim — do not sort it.
    """
    state = _state(request)
    # load_featured resolved these to canonical pool names, and startup proved
    # each one is selectable, so both lookups are total.
    return [card_view(state.pool.by_name[c.name]) for c in state.featured]


@app.get("/api/commanders")
def search_commanders(
    request: Request, q: str, limit: int = COMMANDER_SEARCH_LIMIT_DEFAULT
) -> list[CardView]:
    """Search selectable commanders by name.

    ``q`` is a query parameter, not a path segment, because commander names
    carry commas and apostrophes ("Atraxa, Praetors' Voice"). Matching is
    case-insensitive exact-substring, with prefix matches ranked first and
    ties broken alphabetically; there is no fuzzy matching.

    A ``q`` shorter than 2 characters returns an empty list rather than
    thousands of rows. ``limit`` is clamped to ``[1, 50]``. Banned commanders
    are absent from the index and can never appear here.
    """
    state = _state(request)
    return [commander_view(row) for row in state.search_commanders(q, limit)]


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
