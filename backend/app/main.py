"""FastAPI app entrypoint.

Run from ``backend/`` as ``uvicorn app.main:app``. The API lives at the root
(``/build``, ``/commanders``, ...) with no prefix, and when a frontend build
exists at ``frontend/dist/`` the SPA is served from ``/`` too, with an
index.html fallback for client-side routes.

Both at the root means an unmatched path is the SPA shell, not a 404 — see
``SPAStaticFiles``. Routes are registered before the mount, so the API always
wins where it claims a path.

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
from typing import Annotated, AsyncIterator, Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import Scope

from app import service
from app.errors import POOL_UNAVAILABLE, invalid_dial_param
from app.schemas import (
    CardSearchResponse,
    CommanderListResponse,
    CommandersResponse,
    DeckRequest,
    DeckResponse,
    ExportRequest,
    HealthResponse,
    MaybeboardRequest,
    MaybeboardResponse,
    StructureResponse,
    SwapCandidatesRequest,
    SwapCandidatesResponse,
    SwapValidateRequest,
    SwapValidateResponse,
    WhyNotResponse,
    commander_list_item,
    commander_view,
)
from app.state import (
    CARD_SEARCH_LIMIT_DEFAULT,
    COMMANDER_SEARCH_LIMIT_DEFAULT,
    AppState,
    build_app_state,
)
from selector.deck_rules import archetype_for

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
            "Startup DEGRADED: no card pool. /health reports 'degraded' "
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


@app.get("/health")
def health(request: Request) -> HealthResponse:
    """Service diagnosis, straight from the state loaded at startup.

    ``degraded`` means the card pool never loaded: the app is up (so you can
    read this) but every deck endpoint will answer 503. Everything else that
    could go wrong is versioned config and aborts startup instead, so a
    running app that reports ``ok`` has all of its artifacts.

    ``ok`` is about artifacts, not about EDHREC: this never touches the
    network, so it cannot tell you a build will succeed.
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


@app.get("/commanders")
def list_commanders(request: Request) -> CommanderListResponse:
    """**Every** selectable commander, with art and archetype. The whole picker.

    The entire list in one response — thousands of rows, a few MB — because
    the picker pages, filters by colour identity and searches by name entirely
    in the client, and doing any of that server-side would put a round trip
    between the player and every keystroke. Cheap to serve: it is a projection
    of the pool loaded at startup, with no solver, no EDHREC and no disk.

    "Selectable" is commander-eligible and absent from both of the group's ban
    sets (``banned`` and ``banned_as_commander``): a banned card can never
    appear here, so this list is exactly what ``/build`` will accept.

    ``featured`` marks the group's curated shortlist
    (``featured_commanders.yaml``) — a starting point for players who arrive
    without a commander in mind, **not** a claim that these are the strongest.
    Ordered featured first, then alphabetically.

    ``archetype`` is the quota archetype the build would start from
    (``quotas.yaml``), **not** a claim about how the deck actually plays: it
    says which bands the solver begins with, nothing more.

    Rows here carry only what the picker draws (name, identity, art crop,
    archetype). The full card shape — mana cost, type line, both images —
    comes back from the deck endpoints once a commander is chosen.

    503 if the card pool never loaded.
    """
    state = _state(request)
    featured = {c.name for c in state.featured}
    commanders = [
        commander_list_item(
            state.pool.by_name[row.name],
            archetype_for(state.quotas, row.name),
            featured=row.name in featured,
        )
        # `state.commanders` is already sorted by name, so a stable sort on
        # "not featured" is the whole ordering: shortlist first, each group
        # alphabetical.
        for row in sorted(state.commanders, key=lambda r: r.name not in featured)
    ]
    return CommanderListResponse(count=len(commanders), commanders=commanders)


@app.get("/commanders/search")
def search_commanders(
    request: Request, q: str, limit: int = COMMANDER_SEARCH_LIMIT_DEFAULT
) -> CommandersResponse:
    """Search selectable commanders by name. Typeahead source for the picker.

    ``q`` is a query parameter, not a path segment, because commander names
    carry commas and apostrophes ("Atraxa, Praetors' Voice"). Matching is
    case-insensitive exact-substring, with prefix matches ranked first and
    ties broken alphabetically; there is no fuzzy matching, so a typo returns
    nothing rather than a guess.

    A ``q`` shorter than 2 characters returns an empty list rather than
    thousands of rows — that is not an error and not "no such commander".
    ``limit`` is clamped to ``[1, 50]``, so ``count`` is what was *returned*
    and never how many matched. Banned commanders are absent from the index
    and can never appear here. 503 if the card pool never loaded.
    """
    state = _state(request)
    commanders = [
        commander_view(state.pool.by_name[row.name], archetype_for(state.quotas, row.name))
        for row in state.search_commanders(q, limit)
    ]
    return CommandersResponse(count=len(commanders), commanders=commanders)


@app.get("/cards/search")
def search_cards(
    request: Request, q: str, limit: int = CARD_SEARCH_LIMIT_DEFAULT
) -> CardSearchResponse:
    """Search **every** Commander-legal card by name. Typeahead for "add a card".

    The whole pool, not just commanders and not just a deck's cards, so this
    is the box a player types into to look a card up. Same matching policy as
    ``/commanders/search``: case-insensitive exact-substring, prefix matches
    first, ties alphabetical, no fuzzy matching — a typo returns nothing
    rather than a guess.

    A ``q`` shorter than 2 characters returns an empty list rather than 31k
    rows; that is not an error and not "no such card". ``limit`` is clamped to
    ``[1, 50]``, so ``count`` is what was *returned* and never how many
    matched.

    **A name here is not a playable card.** Unlike ``/commanders``, this index
    is not filtered by the group's banlist: a banned card can be found and
    typed. Ask ``/why-not`` whether it can actually go in a deck.

    Names come back canonical ("Fire // Ice", never "Fire" alone), which is
    what the other endpoints echo back. 503 if the card pool never loaded.
    """
    state = _state(request)
    names = list(state.search_cards(q, limit))
    return CardSearchResponse(count=len(names), names=names)


@app.get("/why-not")
def why_not(request: Request, commander: str, card: str) -> WhyNotResponse:
    """Why a card is (or is not) a candidate for this commander's deck.

    **``eligible: true`` does not mean the card is in the deck.** It means the
    card enters the *candidate set*: nothing that can be decided by looking at
    that one card rejects it. Whether it makes the 99 is the solver's
    aggregate decision — quotas, curve and the scores of every rival card —
    and this endpoint never runs it. This is the single most misreadable
    answer in the API, which is why ``reason`` spells it out in Spanish.

    ``reason_bucket`` is stable and machine-readable, ``reason`` is for the
    player. The buckets, in the order they are tested: ``not_commander_legal``
    (absent from our pool), ``banned`` (the group's banlist), ``never_rule``
    (``rules.yaml`` for this commander), ``watchlist``, ``color_identity``,
    and ``not_selected`` — the eligible verdict.

    ``commander`` and ``card`` are query params because card names carry
    commas and apostrophes. Cheap by design: no solver, no EDHREC, no disk.
    Its blind spot follows from that — our candidate universe is also limited
    to EDHREC's recommendations for the commander, which this does not fetch,
    so an eligible card may still never be offered.

    An unknown *card* is a 200 with ``not_commander_legal``, not a 404: our
    pool is exactly the Commander-legal set, so "not in it" is the answer.
    404 is only for an unknown commander; 503 if the card pool never loaded.
    """
    state = _state(request)
    return service.why_not(state, commander, card)


@app.get("/structure")
def structure(
    request: Request,
    commander: str,
    dial: Annotated[list[str] | None, Query()] = None,
) -> StructureResponse:
    """The quota bands a build would use for ``commander``. Nothing is built.

    A preview of ``/build``'s first step: the same ``resolve_bands`` over
    ``quotas.yaml``, with none of the cost (no solver, no EDHREC, no pool
    scan). Use it to see what a dial does before paying for a deck.

    ``commander`` is a query param because names carry commas and apostrophes.
    Dials are repeatable ``dial=<category>:<position>`` pairs — e.g.
    ``?commander=Krenko, Mob Boss&dial=ramp:high&dial=removal:low`` — chosen
    over a JSON blob because Swagger renders them as a list of text boxes you
    can actually fill in. ``position`` is ``low``, ``center`` or ``high``.

    ``categories`` maps each category to its inclusive ``{lo, hi}`` band.
    ``source`` is ``"commander"`` when ``quotas.yaml`` individualises this
    commander, ``"archetype"`` when it falls back to the archetype block.

    **These bands are not a deck's verdict.** There is deliberately no
    ``karsten_floor``: the land floor is computed from a deck's non-land curve
    and its ramp+draw count, so it does not exist before a deck does, and the
    effective ``lands`` minimum at build time is ``max(lo, karsten_floor)`` —
    which can be higher than the ``lo`` you read here. ``/build`` and
    ``/sequential/validate`` report the real floor.

    404 for an unknown commander, 422 for a malformed ``dial`` or a position
    ``quotas.yaml`` does not define, 503 if the card pool never loaded.
    """
    state = _state(request)
    return service.structure_for(state, commander, _parse_dials(dial))


def _parse_dials(dials: list[str] | None) -> dict[str, str | None]:
    """Parse repeated ``category:position`` query params into a dial mapping.

    Only the syntax is checked here; whether the category has a dial and the
    position exists is ``quotas.resolver``'s call, so the config stays the one
    authority on what a dial means.
    """
    parsed: dict[str, str | None] = {}
    for raw in dials or ():
        category, separator, position = raw.partition(":")
        if not separator or not category.strip() or not position.strip():
            raise HTTPException(status_code=422, detail=invalid_dial_param(raw))
        parsed[category.strip()] = position.strip()
    return parsed


@app.post("/build")
async def build_deck(request: Request, payload: DeckRequest) -> DeckResponse:
    """Build a 99-card mainboard plus maybeboard for a commander.

    POST and not GET: ``dials`` is a nested mapping, and the operation is not
    cacheable — it takes 0.05-10 s, reads the disk and may call EDHREC. The
    shareable link to a deck is a frontend route, not this URL.

    The 99 comes back as **two lists**: ``nonbasic_cards`` and ``basic_lands``.
    Basics are the only legal duplicate and the only rows with ``count > 1``.
    ``deck_size`` is the whole 99; ``selected_count`` is the non-basics alone
    — the cards the solver *chose*, since basics are placed by its per-color
    counters.

    ``dials`` is echoed back. The bands are **derived** from ``quotas.yaml`` +
    commander + dials on every request and are **never** accepted from the
    client (sending ``bands`` is a 422). They come back inside
    ``category_breakdown`` as information only: the server does not trust the
    client's copy and neither should you. ``/structure`` returns the same
    bands without building anything.

    Three breakdowns explain the deck:

    - ``category_breakdown``: ``{count, lo, hi, band, within_band}`` per
      category. ``band`` says how the quota binds the solver — ``hard`` (never
      relaxed: ``lands``), ``ceiling_only`` (no floor at all: ``synergy``) or
      ``soft_no_lower`` (floor becomes a penalty when the solver relaxes).
      **``within_band`` is not ``lo <= count <= hi``**: ``lands`` is really
      bound by ``max(lo, karsten_floor)``, which the deck's own curve decides.
    - ``curve_breakdown``: ``{count}`` per curve bucket of the non-lands.
      Only a count — unlike the TFM, our solver has no curve target to deviate
      from, so a ``target`` here would be fiction.
    - ``color_source_breakdown``: ``{sources, demand, deficit}`` per color.
      Fixing is a *soft* objective term, so a small deficit is a price the
      solver chose to pay, not a broken deck.

    ``maybeboard`` here is this build's bench, frozen at build time. Once the
    player starts swapping it goes stale — ``POST /maybeboard`` recomputes it
    for the deck's current state.

    A deck at a relaxed solver stage is a 200, not an error: read
    ``relaxation_stage`` and the amber ``relaxed_stage`` warning. Only an
    input no relaxation can satisfy is a 422, which is why
    ``infeasible_reason`` is always null here. ``unresolved`` lists EDHREC
    recommendations absent from our pool, which were simply skipped.

    404 for an unknown commander or one EDHREC has no page for, 422 for a
    banned commander / a bad dial / an unbuildable input, 502 if EDHREC is
    unreachable, 503 if the card pool never loaded.
    """
    state = _state(request)
    async with _build_slots:
        return await run_in_threadpool(service.build_deck, state, payload)


@app.post("/sequential/candidates")
async def swap_candidates(
    request: Request, payload: SwapCandidatesRequest
) -> SwapCandidatesResponse:
    """Feasible replacements for ``out``, best first. ``current`` is that card.

    The deck travels as ``[{name, count}]`` and nothing more: categories and
    scores are rederived server-side from the pool and the tagger. Sending
    them would be both untrustworthy and redundant.

    Candidates are ranked inside ``out``'s own primary category — swapping a
    removal for a removal is the question the panel is asking, and "anything
    that fits" would bury the answer. Cards already in the deck, banned,
    ``never``, watchlisted or off-identity ones never appear.
    ``feasible_count`` is the total *before* ``limit`` trimmed the list;
    ``limit`` is clamped to [1, 50] and echoed back.

    One ``candidates`` list, not a ``synergy``/``power`` split: that split
    belongs to an API with two scorers, and this one has a single score.

    **A candidate is feasible, not advised.** Every card here would leave a
    legal 99 — that is all the list means; it does not rank how much the deck
    wants it beyond the EDHREC score. This never calls the solver.

    404 for an unknown commander, 422 for a deck that is not 99 cards, an
    ``out`` the deck does not hold or a card outside the pool, 502 if EDHREC
    is unreachable, 503 if the card pool never loaded.
    """
    state = _state(request)
    return await run_in_threadpool(service.swap_candidates_for, state, payload)


@app.post("/maybeboard")
async def maybeboard(request: Request, payload: MaybeboardRequest) -> MaybeboardResponse:
    """The bench for a deck in its current state, grouped by category.

    Unlike ``/build``'s ``maybeboard``, this one is **live**: it is derived
    from the ``deck`` you send, so a card you swapped in has already left the
    bench by the next call. Same inputs as ``/sequential/candidates`` and the
    same guarantee — the solver is never re-run.

    ``maybeboard`` maps each primary category to its best non-deck cards, best
    first, ``limit`` per category (clamped to [1, 50], default 10, echoed
    back). Per category and not overall, so ``synergy`` cannot starve the
    other roles. A category with nothing left to offer is absent entirely.

    Cards already in the deck, banned, ``never``, watchlisted and off-identity
    ones are all filtered out.

    **The bench is not a list of legal swaps.** Nothing here is checked for
    feasibility, because a bench card only becomes a swap once you choose what
    it replaces: ask ``/sequential/validate`` before trusting one. A card can
    sit in the maybeboard and still be refused by every swap you try.

    ``deck`` is not required to be a legal 99 — it is read only as "what to
    leave out", which is well defined for any non-empty list.

    404 for an unknown commander, 422 for a card outside the pool, 502 if
    EDHREC is unreachable, 503 if the card pool never loaded.
    """
    state = _state(request)
    return await run_in_threadpool(service.maybeboard_for, state, payload)


@app.post("/sequential/validate")
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


@app.post("/export", response_class=PlainTextResponse)
def export_deck(request: Request, payload: ExportRequest) -> Response:
    """Render a deck as a decklist file. Formatting only, nothing re-decided.

    Answers ``text/plain`` with a ``Content-Disposition`` attachment named
    after the commander — the response is a file to save, not a document to
    read.

    ``slot`` comes from the client here, unlike everywhere else: it is the
    section the player sees a card in, and after a few swaps only the client
    knows where each card ended up. An unrecognised slot is exported as its
    own raw label. Card names, in contrast, are resolved against the pool: an
    unresolvable name would silently break the import on the other side.

    Rendering here and not in the browser is what keeps ``CATEGORY_LABELS``
    defined exactly once. ``format`` is an enum with a single value today so
    that a second format is a new value, not a new endpoint.
    """
    state = _state(request)
    export = service.export_deck(state, payload)
    return PlainTextResponse(
        export.content,
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
    )


class SPAStaticFiles(StaticFiles):
    """Static files with index.html fallback for client-side routes.

    **Any** unmatched path returns the SPA shell with a 200, API typos
    included: ``GET /buildd`` serves index.html rather than a 404. That is a
    deliberate consequence of dropping the ``/api`` prefix — with the API at
    the root there is no longer a namespace to tell "a client-side route" and
    "a misspelled endpoint" apart, and it is how the TFM API behaved.

    Real API routes are unaffected: they are registered on the app *before*
    this mount, so they always match first. Only paths no route claimed reach
    this class. If a request that should hit the API renders the SPA instead,
    the URL is wrong — check ``/docs``.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
        return await super().get_response("index.html", scope)


if FRONTEND_DIST.is_dir():
    app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    logger.warning("Frontend build not found at %s; serving API only", FRONTEND_DIST)
