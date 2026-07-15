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

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response
from starlette.types import Scope

from app import service
from app.errors import POOL_UNAVAILABLE
from app.schemas import (
    CardView,
    DeckRequest,
    DeckResponse,
    HealthResponse,
    SwapCandidatesRequest,
    SwapCandidatesResponse,
    SwapValidateRequest,
    SwapValidateResponse,
    card_view,
    commander_view,
)
from app.state import COMMANDER_SEARCH_LIMIT_DEFAULT, AppState, build_app_state

LOG_LEVEL_ENV = "DECKBUILDER_LOG_LEVEL"

# The solver runs on 1 worker and the Space's free tier has 2 shared vCPUs, so
# two concurrent builds is the whole machine. Without this bound, five friends
# generating at once turn a 10 s time limit into 50 s of wall clock for all of
# them; with it, the third waits and everyone's deck still arrives.
BUILD_CONCURRENCY = 2

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

_build_slots = asyncio.Semaphore(BUILD_CONCURRENCY)

# The one place where a domain error becomes a status code. `app.service` never
# names one: it raises a typed error carrying the Spanish message, and this
# table decides whose fault it was. 404 = "that commander does not exist here",
# 422 = "your input cannot produce a deck", 502 = "EDHREC let us down".
_ERROR_STATUS: tuple[tuple[type[service.ServiceError], int], ...] = (
    (service.CommanderNotFound, 404),
    (service.CommanderNotAllowed, 422),
    (service.InvalidDials, 422),
    (service.DeckBuildFailed, 422),
    (service.SwapRequestInvalid, 422),
    (service.EdhrecUnavailable, 502),
)


def _domain_error_handler(status_code: int) -> Callable[[Request, Exception], Response]:
    def handle(request: Request, exc: Exception) -> Response:
        # Same body shape as HTTPException's, so the frontend reads one field.
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handle


for _error_type, _status_code in _ERROR_STATUS:
    app.add_exception_handler(_error_type, _domain_error_handler(_status_code))


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


@app.post("/api/deck")
async def build_deck(request: Request, payload: DeckRequest) -> DeckResponse:
    """Build a 99-card mainboard plus maybeboard for a commander.

    POST and not GET: ``dials`` is a nested mapping, and the operation is not
    cacheable — it takes 0.05-10 s, reads the disk and may call EDHREC. The
    shareable link to a deck is a frontend route, not this URL.

    ``dials`` is echoed back; ``bands`` is **derived** from ``quotas.yaml`` +
    commander + dials on every request and is **never** accepted from the
    client (sending it is a 422). It is in the response as information only:
    the server does not trust it and neither should you.

    A deck at a relaxed solver stage is a 200, not an error: read
    ``solver.stage`` and the amber ``relaxed_stage`` warning. Only an input no
    relaxation can satisfy is a 422. ``statuses`` is the quota traffic light;
    ``unresolved`` lists EDHREC recommendations absent from our pool, which
    were simply skipped.
    """
    state = _state(request)
    async with _build_slots:
        return await run_in_threadpool(service.build_deck, state, payload)


@app.post("/api/deck/swap/candidates")
async def swap_candidates(
    request: Request, payload: SwapCandidatesRequest
) -> SwapCandidatesResponse:
    """Feasible replacements for ``out``, best first.

    The deck travels as ``[{name, count}]`` and nothing more: categories and
    scores are rederived server-side from the pool and the tagger. Sending
    them would be both untrustworthy and redundant.

    Candidates are ranked inside ``out``'s own primary category — swapping a
    removal for a removal is the question the panel is asking, and "anything
    that fits" would bury the answer. Cards already in the deck, banned,
    ``never``, watchlisted or off-identity ones never appear.
    ``feasible_count`` is the total *before* ``limit`` trimmed the list;
    ``limit`` is clamped to [1, 50] and echoed back.

    This never calls the solver.
    """
    state = _state(request)
    return await run_in_threadpool(service.swap_candidates_for, state, payload)


@app.post("/api/deck/swap/validate")
def validate_swap(
    request: Request, payload: SwapValidateRequest
) -> SwapValidateResponse:
    """Can ``out`` be replaced by ``in``? Plus the quota panel after the swap.

    **An infeasible swap is a 200.** ``feasible: false`` with its ``blockers``
    is a verdict about the deck, not an error about the request. A 422 means
    something else entirely: a card outside our pool, an ``out`` the deck does
    not hold, or a deck that is not 99 cards.

    ``blockers`` are ``red`` (the result would not be a legal 99) and refuse
    the swap; ``warnings`` are ``amber`` and never do. Only the banlist is a
    policy ``red``: ``never`` and watchlist cards warn, because "I do not
    recommend this" is not "this is illegal" (``rules.yaml``).

    ``counts`` and ``statuses`` describe the deck after the swap and come back
    even when it is feasible — the live quota panel needs them either way, and
    they cost nothing here. This never calls the solver, EDHREC or the disk.
    """
    state = _state(request)
    return service.validate_swap(state, payload)


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
