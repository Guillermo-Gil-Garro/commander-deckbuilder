"""Domain services behind the HTTP frontier: everything except transport.

``app.main`` owns status codes and the event loop; this module owns *what the
answer is*. It knows nothing about FastAPI: it takes an ``AppState`` and a
request model, and either returns a response model or raises a
``ServiceError`` whose message is already the Spanish sentence the player
reads (built from ``app.errors``). Mapping each error type to a status code is
``main.py``'s job, in one table.

**Bands are derived, never received.** ``resolve_bands(quotas, commander,
dials)`` runs on every request. The client sends dial positions and gets the
resulting bands back as information; it can never send the bands themselves
(``DeckRequest``/``SwapRequest`` are ``extra="forbid"``). If it could, it
would relax any quota and validate any swap — which is the whole point of
validating server-side.

**Card facts are rederived, never received.** Same reason: a deck travels as
``[{name, count}]`` and nothing else. Categories, scores and color identity
come out of the pool + tagger here (``_facts``), so a client cannot declare a
card's category to make its deck legal.
"""

from __future__ import annotations

import logging
from typing import Mapping, Sequence

from app.errors import (
    DECK_BUILD_INFEASIBLE,
    EDHREC_UNAVAILABLE,
    INVALID_DIALS,
    commander_banned,
    commander_not_found,
    edhrec_not_found,
    relaxed_stage_message,
)
from app.schemas import (
    DeckCardView,
    DeckRequest,
    DeckResponse,
    SolverView,
    WarningView,
    band_view,
    card_view,
    deck_card_view,
)
from app.state import AppState, CommanderRow
from pipeline.edhrec import (
    EdhrecCommanderData,
    EdhrecError,
    EdhrecNotFound,
    fetch_commander,
    slugify_commander,
)
from quotas.config import QuotaBand, QuotasError
from quotas.resolver import resolve_bands
from selector.cp_sat import CpSatResult, build_deck_cpsat
from selector.deck_rules import DeckRulesError, archetype_for
from selector.greedy import DeckEntry, SelectorError

logger = logging.getLogger(__name__)

# Bracket 4, not cEDH (Guille, 2026-07-14): the group's power ceiling. Every
# EDHREC read in the API goes through this variant, so the memo, the disk
# cache and the deck all speak about the same recommendation set.
EDHREC_VARIANT = "optimized"

# The relaxed-stage warning's code. Not a Violation: no rule is broken, the
# solver simply could not honour every quota (see errors.relaxed_stage_message).
RELAXED_STAGE_CODE = "relaxed_stage"
AMBER = "amber"


class ServiceError(Exception):
    """Base error of this layer. ``str(exc)`` is the message the player reads."""


class CommanderNotFound(ServiceError):
    """No such commander, not commander-eligible, or no EDHREC page for it."""


class CommanderNotAllowed(ServiceError):
    """A real commander the group's banlist refuses (banned / banned_as_commander)."""


class InvalidDials(ServiceError):
    """Dial positions the quotas config rejects."""


class DeckBuildFailed(ServiceError):
    """The selector cannot produce a 99 from this input, at any relaxation stage."""


class EdhrecUnavailable(ServiceError):
    """EDHREC is down or unreadable. Ours to fix, not the player's."""


def resolve_commander(state: AppState, name: str) -> CommanderRow:
    """The selectable commander a name refers to, by canonical or face name.

    Raises ``CommanderNotFound`` for an unknown or non-eligible card and
    ``CommanderNotAllowed`` for a banned one — the two are different answers:
    "that is not a commander" vs "the group threw that one out".
    """
    card = state.pool.resolve(name)
    if card is None:
        raise CommanderNotFound(commander_not_found(name))
    row = state.commander_by_name(card["name"])
    if row is not None:
        return row
    banlist = state.resolved_banlist
    if card.get("oracle_id") in (banlist.banned | banlist.banned_as_commander):
        raise CommanderNotAllowed(commander_banned(card["name"]))
    raise CommanderNotFound(commander_not_found(name))


def bands_for(
    state: AppState, commander_name: str, dials: Mapping[str, str | None]
) -> dict[str, QuotaBand]:
    """Resolve the effective bands. Recomputed per request, never received."""
    try:
        return resolve_bands(state.quotas, commander_name, dials)
    except QuotasError as exc:
        logger.info("Rejected dials %r for %r: %s", dict(dials), commander_name, exc)
        raise InvalidDials(INVALID_DIALS) from exc


def edhrec_data(state: AppState, commander_name: str) -> EdhrecCommanderData:
    """Bracket-4 recommendations: RAM memo, then disk cache, then the network.

    The memo is what keeps the swap path off the JSON parser: re-parsing a
    200 KB EDHREC page costs 5-10 ms of the 100 ms budget for nothing.
    ``fetch_commander`` blocks (httpx + disk), so every caller of this must
    already be off the event loop.
    """
    slug = slugify_commander(commander_name)
    memoized = state.edhrec_memo.get(slug, EDHREC_VARIANT)
    if memoized is not None:
        return memoized
    try:
        data = fetch_commander(commander_name, variant=EDHREC_VARIANT)
    except EdhrecNotFound as exc:
        logger.info("No EDHREC page for %r: %s", commander_name, exc)
        raise CommanderNotFound(edhrec_not_found(commander_name)) from exc
    except EdhrecError as exc:
        logger.error("EDHREC unavailable for %r: %s", commander_name, exc)
        raise EdhrecUnavailable(EDHREC_UNAVAILABLE) from exc
    state.edhrec_memo.put(slug, data, EDHREC_VARIANT)
    return data


def build_deck(state: AppState, request: DeckRequest) -> DeckResponse:
    """Build a 99 + maybeboard for a commander. Blocking and CPU-bound.

    A deck built at a relaxed solver stage is a *result*, not an error: it
    comes back with its stage and an amber warning. Only an input no stage can
    satisfy raises ``DeckBuildFailed``.
    """
    row = resolve_commander(state, request.commander)
    bands = bands_for(state, row.name, request.dials)
    data = edhrec_data(state, row.name)
    try:
        result = build_deck_cpsat(
            row.name,
            pool=state.pool,
            recommendations=data.recommendations,
            bands=bands,
            tagger=state.tagger,
            banned_names=state.banned_names,
            watchlist_names=state.watchlist_names,
            rules=state.rules,
            archetype=archetype_for(state.quotas, row.name),
            time_limit_s=state.solver_time_limit_s,
        )
    except (SelectorError, DeckRulesError) as exc:
        logger.error("Deck build failed for %r: %s", row.name, exc)
        raise DeckBuildFailed(DECK_BUILD_INFEASIBLE) from exc
    return _deck_response(state, row, request.dials, bands, result)


def _deck_response(
    state: AppState,
    row: CommanderRow,
    dials: Mapping[str, str | None],
    bands: Mapping[str, QuotaBand],
    result: CpSatResult,
) -> DeckResponse:
    warnings: list[WarningView] = []
    if result.relaxation_stage != "none":
        warnings.append(
            WarningView(
                code=RELAXED_STAGE_CODE,
                severity=AMBER,
                message=relaxed_stage_message(result.relaxation_stage),
            )
        )
    return DeckResponse(
        commander=card_view(state.pool.by_name[row.name]),
        dials=dict(dials),
        bands={category: band_view(band) for category, band in bands.items()},
        mainboard=_cards(state, result.mainboard),
        maybeboard=_cards(state, result.maybeboard),
        new_cards=_cards(state, result.new_cards),
        counts=dict(result.counts),
        statuses={
            category: status.value for category, status in result.statuses.items()
        },
        karsten_floor=result.karsten_floor,
        lands_target=result.lands_target,
        solver=SolverView(
            status=result.solver_status,
            stage=result.relaxation_stage,
            solve_time_s=result.solve_time_s,
            objective=result.objective_value,
        ),
        warnings=warnings,
        unresolved=list(result.unresolved),
    )


def _cards(state: AppState, entries: Sequence[DeckEntry]) -> list[DeckCardView]:
    # Every entry came out of the selector, which only ever picks pool cards.
    return [deck_card_view(state.pool.by_name[e.name], e) for e in entries]
