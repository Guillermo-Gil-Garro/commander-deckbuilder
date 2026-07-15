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
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.errors import (
    DECK_BUILD_INFEASIBLE,
    EDHREC_UNAVAILABLE,
    INVALID_DIALS,
    Violation,
    candidate_reason,
    card_not_in_deck,
    card_not_in_pool,
    commander_banned,
    commander_not_found,
    deck_size_mismatch,
    edhrec_not_found,
    relaxed_stage_message,
    violation_message,
)
from app.schemas import (
    CandidateView,
    DeckCardView,
    DeckRequest,
    DeckResponse,
    ExportRequest,
    NoticeView,
    SolverView,
    SwapCandidatesRequest,
    SwapCandidatesResponse,
    SwapRequest,
    SwapValidateRequest,
    SwapValidateResponse,
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
from selector.constraints import CardFacts
from selector.cp_sat import CpSatResult, build_deck_cpsat
from selector.deck_rules import (
    DeckRulesError,
    RuleContext,
    archetype_for,
    boost_for,
    preferred_boosts,
    resolve_always,
    resolve_never,
)
from selector.export import format_archidekt
from selector.greedy import (
    DECK_SIZE,
    SYNERGY_CATEGORY,
    DeckEntry,
    ScoreWeights,
    SelectorError,
)
from selector.swap import swap_candidates, swap_is_feasible

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


class SwapRequestInvalid(ServiceError):
    """The swap request does not describe a coherent deck.

    Not "the swap is illegal" — that is a verdict, and it comes back as a 200.
    This is a request that cannot be evaluated at all: an unknown card, an
    ``out`` the deck does not hold, a deck that is not 99 cards.
    """


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


# --- swap --------------------------------------------------------------------
#
# NOTHING here calls the solver. That is the entire reason `selector.swap`
# exists: a swap re-counts, it never re-solves (0.05-10 s vs the 100 ms the
# live quota panel has). An import of `build_deck_cpsat` under this line is a
# bug, not an optimisation.


@dataclass(frozen=True)
class _SwapContext:
    """Everything both swap endpoints rederive from one request."""

    row: CommanderRow
    commander: CardFacts
    bands: dict[str, QuotaBand]
    deck: list[tuple[CardFacts, int]]
    out_card: CardFacts
    never_names: frozenset[str]
    always_names: frozenset[str]


def swap_candidates_for(
    state: AppState, request: SwapCandidatesRequest
) -> SwapCandidatesResponse:
    """Rank the feasible replacements for ``out``. Blocking (reads EDHREC)."""
    ctx = _swap_context(state, request)
    data = edhrec_data(state, ctx.row.name)
    candidates, feasible_count = swap_candidates(
        deck=ctx.deck,
        out_card=ctx.out_card,
        pool_candidates=_pool_candidates(state, ctx, data),
        bands=ctx.bands,
        commander=ctx.commander,
        banned_names=state.banned_names,
        never_names=ctx.never_names,
        watchlist_names=state.watchlist_names,
        always_names=ctx.always_names,
        limit=request.limit,
    )
    return SwapCandidatesResponse(
        out=card_view(state.pool.by_name[ctx.out_card.name]),
        candidates=[
            CandidateView(
                **card_view(state.pool.by_name[candidate.name]).model_dump(),
                score=candidate.score,
                reason=candidate_reason(candidate.primary_category, candidate.score),
            )
            for candidate in candidates
        ],
        feasible_count=feasible_count,
        limit=request.limit,
    )


def validate_swap(state: AppState, request: SwapValidateRequest) -> SwapValidateResponse:
    """Verdict on one swap plus the quota traffic light after it. No I/O.

    "Not feasible" is a domain result and comes back as a 200: only an
    incoherent request (a card outside the pool, an ``out`` the deck does not
    hold, a deck that is not 99) raises. This is the <100 ms path — it does
    not touch EDHREC, the disk or the solver.
    """
    ctx = _swap_context(state, request)
    in_card = _facts(state, _pool_card(state, request.card_in), ctx.bands)
    verdict = swap_is_feasible(
        deck=ctx.deck,
        out_card=ctx.out_card,
        in_card=in_card,
        bands=ctx.bands,
        commander=ctx.commander,
        banned_names=state.banned_names,
        never_names=ctx.never_names,
        watchlist_names=state.watchlist_names,
        always_names=ctx.always_names,
    )
    return SwapValidateResponse(
        feasible=verdict.feasible,
        blockers=[_notice(v) for v in verdict.blockers],
        warnings=[_notice(v) for v in verdict.warnings],
        counts=dict(verdict.counts_after.by_category),
        statuses={
            category: status.value
            for category, status in verdict.statuses_after.items()
        },
        karsten_floor=verdict.counts_after.karsten_floor,
        deck_size=verdict.counts_after.total,
    )


def _swap_context(state: AppState, request: SwapRequest) -> _SwapContext:
    row = resolve_commander(state, request.commander)
    bands = bands_for(state, row.name, request.dials)
    deck = [
        (_facts(state, _pool_card(state, ref.name), bands), ref.count)
        for ref in request.deck
    ]
    total = sum(count for _, count in deck)
    if total != DECK_SIZE:
        raise SwapRequestInvalid(deck_size_mismatch(total, DECK_SIZE))
    out_card = _facts(state, _pool_card(state, request.out), bands)
    if not any(facts.name == out_card.name for facts, _ in deck):
        raise SwapRequestInvalid(card_not_in_deck(request.out))

    rule_ctx = RuleContext(
        commander_name=row.name,
        color_identity=frozenset(row.color_identity),
        archetype=archetype_for(state.quotas, row.name),
    )
    return _SwapContext(
        row=row,
        commander=_facts(state, state.pool.by_name[row.name], bands),
        bands=bands,
        deck=deck,
        out_card=out_card,
        never_names=resolve_never(state.rules, rule_ctx),
        always_names=frozenset(
            rule.name
            for rule in resolve_always(state.rules, rule_ctx, state.banned_names)
        ),
    )


def _pool_card(state: AppState, name: str) -> Mapping[str, Any]:
    card = state.pool.resolve(name)
    if card is None:
        raise SwapRequestInvalid(card_not_in_pool(name))
    return card


def _facts(
    state: AppState, card: Mapping[str, Any], bands: Mapping[str, QuotaBand]
) -> CardFacts:
    """Rederive a card's facts from the pool + tagger. NEVER from the client.

    The category projection is the selector's, verbatim (``cp_sat.py``): the
    tagger's labels restricted to the banded categories, ``synergy`` as the
    fallback bucket. Anything else and the swap checker would be counting a
    different deck than the one the solver built.
    """
    name = card["name"]
    categories = state.tagger(name) & (set(bands) - {SYNERGY_CATEGORY})
    if not categories:
        categories = {SYNERGY_CATEGORY}
    return CardFacts(
        name=name,
        oracle_id=card["oracle_id"],
        categories=frozenset(categories),
        cmc=float(card.get("cmc") or 0.0),
        mana_cost=card.get("mana_cost") or "",
        color_identity=frozenset(card.get("color_identity") or ()),
        is_basic="Basic" in (card.get("type_line") or ""),
    )


def _pool_candidates(
    state: AppState, ctx: _SwapContext, data: EdhrecCommanderData
) -> list[tuple[CardFacts, float]]:
    """The commander's EDHREC recommendations as ``(facts, score)``.

    Scored exactly as the build scored them, boosts included, so the ranking
    the player sees here agrees with the deck they are editing. The policy
    filtering (banned, never, watchlist, off-identity, already in the deck) is
    ``swap_candidates``' job and is not duplicated here.
    """
    weights = ScoreWeights()
    boosts = preferred_boosts(state.rules, ctx.row.color_identity)
    candidates: list[tuple[CardFacts, float]] = []
    seen: set[str] = set()
    for rec in data.recommendations:
        card = state.pool.resolve(rec.name)
        if card is None:
            continue
        name = card["name"]
        # Basics are never candidates: they are the only legal duplicate and
        # enter a deck through the solver's per-color counters, not a swap.
        if name in seen or name == ctx.row.name or "Basic" in (card.get("type_line") or ""):
            continue
        seen.add(name)
        candidates.append(
            (
                _facts(state, card, ctx.bands),
                weights.score(rec.synergy, rec.inclusion) + boost_for(boosts, name),
            )
        )
    return candidates


# --- export ------------------------------------------------------------------


@dataclass(frozen=True)
class DeckExport:
    """A rendered decklist and the name to save it under."""

    filename: str
    content: str


@dataclass(frozen=True)
class _ExportResult:
    """The ``export.DeckResultLike`` surface, built from a request instead of
    a build result: after a few swaps the client's deck is not any build."""

    commander_name: str
    mainboard: list[DeckEntry]
    maybeboard: list[DeckEntry]
    new_cards: list[DeckEntry]


def export_deck(state: AppState, request: ExportRequest) -> DeckExport:
    """Render a deck as a decklist. Formatting only — nothing is re-decided.

    Exporting on the server and not in the client is what keeps
    ``CATEGORY_LABELS`` defined once: a copy in TypeScript would be the same
    debt again, in another language.

    Card names are resolved against the pool and exported canonically. The
    decklist is the artifact the player pastes into Archidekt, and a name we
    cannot resolve would silently break their import — so an unknown card is a
    422 here rather than a broken file. ``slot`` is *not* validated: it is the
    client's own grouping and falls back to its raw label.
    """
    row = resolve_commander(state, request.commander)
    result = _ExportResult(
        commander_name=row.name,
        mainboard=[
            _export_entry(state, ref.name, slot=ref.slot, count=ref.count)
            for ref in request.deck
        ],
        maybeboard=[_export_entry(state, ref.name) for ref in request.maybeboard],
        new_cards=[_export_entry(state, ref.name) for ref in request.new_cards],
    )
    return DeckExport(
        filename=f"{slugify_commander(row.name)}.txt",
        content=format_archidekt(result),
    )


def _export_entry(
    state: AppState, name: str, *, slot: str = SYNERGY_CATEGORY, count: int = 1
) -> DeckEntry:
    # categories/score/reason are not read by any exporter: a decklist says
    # what to buy, never why. Inventing values for them would be noise.
    return DeckEntry(
        name=_pool_card(state, name)["name"],
        categories=(),
        score=None,
        reason="",
        slot=slot,
        count=count,
    )


def _notice(violation: Violation) -> NoticeView:
    return NoticeView(
        code=violation.code,
        severity=violation.severity.value,
        message=violation_message(violation),
    )
