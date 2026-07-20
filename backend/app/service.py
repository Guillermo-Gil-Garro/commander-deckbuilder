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
import math
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

import httpx
from fpdf import FPDF

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
    why_not_reason,
)
from app.schemas import (
    BAND_CEILING_ONLY,
    BAND_HARD,
    BAND_SOFT_NO_LOWER,
    AuditFlagView,
    AuditRequest,
    AuditResponse,
    BanlistCardView,
    BanlistResponse,
    BanlistWatchlistView,
    CardPrintsResponse,
    CardPrintView,
    CategoryRow,
    ReplacementView,
    ColorSourceRow,
    CurveRow,
    DecisionView,
    DeckCardView,
    DeckRequest,
    DeckResponse,
    EvaluateRequest,
    EvaluateResponse,
    ExportRequest,
    LegalCardSearchResponse,
    MaybeboardRequest,
    MaybeboardResponse,
    NoticeView,
    PrintDefaultsRequest,
    PrintDefaultsResponse,
    ProxyPdfRequest,
    SequentialStartResponse,
    StructureResponse,
    SwapCandidatesRequest,
    SwapCandidatesResponse,
    SwapOutsRequest,
    SwapOutsResponse,
    SwapReplacementsResponse,
    SwapRequest,
    SwapValidateRequest,
    SwapValidateResponse,
    TokenListRequest,
    TokenListResponse,
    TokenView,
    WhyNotResponse,
    band_view,
    bench_card_view,
    card_view,
    deck_card_view,
)
from app.state import AppState, CommanderRow
from pipeline.card_images import fetch_card_image
from pipeline.prints import (
    PrintsError,
    fetch_fullart_basics,
    fetch_prints,
    fetch_token_prints,
)
from pipeline.edhrec import (
    EdhrecCommanderData,
    EdhrecError,
    EdhrecNotFound,
    fetch_commander,
    slugify_commander,
)
from quotas.config import CEILING_ONLY_CATEGORIES, QuotaBand, QuotasError
from quotas.color_sources import pool_color_source_targets
from rules.banlist import BANNED_STATUSES
from rules.resolve import ResolutionError
from quotas.lands import curve_bucket
from quotas.resolver import resolve_bands
from quotas.validator import CategoryStatus
from selector.audit import flag_conditionals, flag_low_synergy_filler
from selector.constraints import CardFacts
from selector.cp_sat import CpSatResult, build_deck_cpsat, _produced_colors
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
    LANDS_CATEGORY,
    SYNERGY_CATEGORY,
    DeckEntry,
    ScoreWeights,
    SelectorError,
    _name_variants,
)
from selector.swap import primary_category, swap_candidates, swap_is_feasible

logger = logging.getLogger(__name__)

# Bracket 4, not cEDH (Guille, 2026-07-14): the group's power ceiling. Every
# EDHREC read in the API goes through this variant, so the memo, the disk
# cache and the deck all speak about the same recommendation set.
EDHREC_VARIANT = "optimized"

# EDHREC's "expensive" subpage: the cards the moneyed decks of a commander run.
# Diffed against `optimized`, it surfaces the strong-but-pricey cards the
# popularity-based list underweights *because* of their price. Feeds the
# `expensive_cards` maybeboard section (see `_expensive_cards_section`).
EDHREC_EXPENSIVE_VARIANT = "expensive"

# Below this Scryfall USD the `expensive − optimized` diff is just a cheap card
# the moneyed lists happen to run (Gruul Signet ~0.4, Explore ~0.3, Growth
# Spiral ~0.3), not a card underweighted *for its price*; drop it. A `null`
# price is NOT cheap: it is a Reserved List staple (dual lands, Wheel of
# Fortune) that carries no Scryfall USD yet is among the format's priciest, so
# nulls are always kept.
EXPENSIVE_PRICE_FLOOR = 5.0
# Cap on the surfaced section, in the spirit of NEW_CARDS_CAP/MAYBEBOARD_SIZE:
# enough to show the interesting duals/rocks without turning into a price list.
EXPENSIVE_CARDS_CAP = 12
EXPENSIVE_CARDS_REASON = (
    "cara y buena: el mazo con dinero la juega; la lista optimizada la "
    "infrapondera por precio, no por potencia"
)

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


class CardNotFound(ServiceError):
    """No pool card with that oracle_id (the prints endpoints)."""


class PrintsUnavailable(ServiceError):
    """Scryfall's printings search is down or unreadable. Ours, not the player's."""


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


def structure_for(
    state: AppState, commander_name: str, dials: Mapping[str, str | None]
) -> StructureResponse:
    """The bands a build would use for this commander, without building.

    Pure config: ``resolve_bands`` over ``quotas.yaml``, exactly what
    ``build_deck`` resolves before it calls the solver. No pool scan, no
    EDHREC, no solver — so this is the cheap way to preview what a dial does.
    """
    row = resolve_commander(state, commander_name)
    bands = bands_for(state, row.name, dials)
    return StructureResponse(
        commander=card_view(state.pool.by_name[row.name]),
        dials=dict(dials),
        categories={category: band_view(band) for category, band in bands.items()},
        archetype=archetype_for(state.quotas, row.name),
        source=_structure_source(state, row.name),
    )


def _structure_source(state: AppState, commander_name: str) -> str:
    """Which layer of ``quotas.yaml`` these bands came from.

    ``commander`` iff the file names this commander *and* says something about
    it — an entry that pins neither an archetype nor an override changes
    nothing, so reporting it as commander-specific would be a lie.
    """
    entry = state.quotas.commanders.get(commander_name)
    if entry is not None and (entry.archetype is not None or entry.overrides):
        return "commander"
    return "archetype"


def why_not(state: AppState, commander_name: str, card_name: str) -> WhyNotResponse:
    """Why ``card_name`` is (or is not) a candidate for ``commander_name``.

    Answers the **per-card** filter only: the checks ``cp_sat`` applies while
    it walks the recommendation list (banlist, ``never``, watchlist, colour
    identity — see ``build_deck_cpsat``'s candidate loop). Those rules are
    read from the same config the build reads; none of them is restated here.

    Deliberately cheap: no solver, no EDHREC, no disk. That is also its
    limit — ``eligible`` cannot mean "the solver would offer you this card",
    because our candidate universe is additionally restricted to EDHREC's
    recommendations for the commander, which this endpoint does not fetch.
    See ``WhyNotResponse``.

    Raises ``CommanderNotFound`` for an unknown commander (a 404). An unknown
    *card* is not an error: it is the ``not_commander_legal`` verdict, since
    our pool is exactly the Commander-legal set and a card that is not in it
    is either illegal or misspelled — and nothing here can tell those apart.
    """
    row = resolve_commander(state, commander_name)
    card = state.pool.resolve(card_name)
    if card is None:
        return _why_not(commander_name=row.name, card_name=card_name, bucket="not_commander_legal")

    name = card["name"]
    rule_ctx = RuleContext(
        commander_name=row.name,
        color_identity=frozenset(row.color_identity),
        archetype=archetype_for(state.quotas, row.name),
    )
    # Face names and full names both count, exactly as the selector matches
    # them ("Fire" must hit a banlist entry for "Fire // Ice").
    variants = _name_variants(name) | {card_name}
    # cp_sat tests banned/watchlist/never as one condition, so their order here
    # is ours to choose: rules.yaml declares the precedence ban > never, and
    # the watchlist is the weakest of the three. Colour identity comes last
    # because "your group threw this out" is a better answer than "wrong
    # colours" for a card that is both.
    buckets: tuple[tuple[str, bool], ...] = (
        ("banned", bool(variants & set(state.effective_banned_names(rule_ctx.archetype)))),
        ("never_rule", bool(variants & _canonical(state, resolve_never(state.rules, rule_ctx)))),
        ("watchlist", bool(variants & set(state.watchlist_names))),
        (
            "color_identity",
            not set(card.get("color_identity") or ()) <= set(row.color_identity),
        ),
    )
    bucket = next((name_ for name_, hit in buckets if hit), "not_selected")
    return _why_not(commander_name=row.name, card_name=name, bucket=bucket)


def _why_not(*, commander_name: str, card_name: str, bucket: str) -> WhyNotResponse:
    return WhyNotResponse(
        commander_name=commander_name,
        card_name=card_name,
        eligible=bucket == "not_selected",
        reason_bucket=bucket,
        reason=why_not_reason(bucket),
    )


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


def edhrec_expensive_data(
    state: AppState, commander_name: str
) -> EdhrecCommanderData | None:
    """The commander's ``expensive`` EDHREC page, or ``None`` if unavailable.

    Same RAM-memo → disk → network path as ``edhrec_data``, but for the
    ``expensive`` variant and **degrading to ``None`` instead of raising**: it
    feeds an optional maybeboard section, so a commander whose expensive page
    is missing (404) or a transient EDHREC hiccup must leave the section empty,
    never fail a build that already succeeded on the optimized page. Only
    successful fetches are memoized; a 404 is not cached, so it is re-attempted
    on the next build (rare, and cheaper than a negative-cache to reason about).

    ``fetch_commander`` blocks (httpx + disk), so callers must already be off
    the event loop — ``build_deck`` runs in the threadpool.
    """
    slug = slugify_commander(commander_name)
    memoized = state.edhrec_memo.get(slug, EDHREC_EXPENSIVE_VARIANT)
    if memoized is not None:
        return memoized
    try:
        data = fetch_commander(commander_name, variant=EDHREC_EXPENSIVE_VARIANT)
    except EdhrecNotFound as exc:
        logger.info("No EDHREC expensive page for %r: %s", commander_name, exc)
        return None
    except EdhrecError as exc:
        # The optimized page already succeeded (same host), so this is unlikely;
        # if it happens the "expensive & good" section is simply omitted rather
        # than sinking a deck the player asked for.
        logger.warning(
            "EDHREC expensive page unavailable for %r: %s", commander_name, exc
        )
        return None
    state.edhrec_memo.put(slug, data, EDHREC_EXPENSIVE_VARIANT)
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
    archetype = archetype_for(state.quotas, row.name)
    try:
        result = build_deck_cpsat(
            row.name,
            pool=state.pool,
            recommendations=data.recommendations,
            bands=bands,
            tagger=state.tagger,
            # Archetype-scoped: a card exempted for this archetype (e.g. Rhystic
            # Study in enchantress) drops out of the ban here, but stays banned
            # for every other archetype.
            banned_names=state.effective_banned_names(archetype),
            watchlist_names=state.watchlist_names,
            rules=state.rules,
            archetype=archetype,
            time_limit_s=state.solver_time_limit_s,
        )
    except (SelectorError, DeckRulesError) as exc:
        logger.error("Deck build failed for %r: %s", row.name, exc)
        raise DeckBuildFailed(DECK_BUILD_INFEASIBLE) from exc
    # The "expensive & good" section (EDHREC expensive − optimized) is retired:
    # Ur-Dragon showed the diff surfaces generic power staples, not commander
    # tech, and its only good picks (the ABUR duals) now enter the mainboard as
    # forced autoincludes. Its intent — surfacing good-but-missing cards by
    # quality, not price — moves to the deck audit. The plumbing below
    # (`edhrec_expensive_data`, `_expensive_cards_section`) is left dormant so it
    # is one call to bring back; the second EDHREC fetch is skipped meanwhile.
    return _deck_response(state, row, request.dials, bands, result, ())


def _expensive_cards_section(
    state: AppState,
    row: CommanderRow,
    bands: Mapping[str, QuotaBand],
    optimized: EdhrecCommanderData,
    expensive: EdhrecCommanderData | None,
    result: CpSatResult,
    archetype: str,
) -> list[DeckEntry]:
    """The "expensive & good" diff ``expensive − optimized``, price-cleaned.

    EDHREC's ``expensive`` page is what the moneyed decks of this commander
    run; the ``optimized`` page is Bracket 4 by popularity, which underweights
    a strong-but-pricey card *because* price depresses its play rate. The
    difference surfaces those cards (dual lands, Gaea's Cradle, the Moxen) for
    a proxy group to decide for itself. Pure post-process: it never enters the
    99 and never re-runs the solver — same contract as ``new_cards``.

    Subtracted from the raw name diff (raw EDHREC names, exactly the diff Guille
    validated), each survivor then resolved against the pool and dropped unless
    it clears every filter:

    - already shown: in the 99, the maybeboard or the new-cards section;
    - the effective (archetype-scoped) banlist and the watchlist;
    - outside the commander's colour identity;
    - basics (placed by the solver's counters, never a suggestion);
    - **cheap noise**: ``price_usd`` below ``EXPENSIVE_PRICE_FLOOR``. A ``null``
      price is kept — Reserved List staples carry no Scryfall USD yet are the
      priciest cards in the format.

    Ordered null-price first (Reserved List: the format's priciest), then by
    price descending, then by name so the order is stable across rebuilds.
    Capped at ``EXPENSIVE_CARDS_CAP``. Score and slot are rederived exactly as
    the maybeboard rederives them, so the section speaks the deck's vocabulary.
    """
    if expensive is None:
        return []

    optimized_names = {rec.name for rec in optimized.recommendations}
    already_shown = (
        {entry.name for entry in result.mainboard}
        | {entry.name for entry in result.maybeboard}
        | {entry.name for entry in result.new_cards}
    )
    effective_banned = state.effective_banned_names(archetype)
    commander_identity = frozenset(row.color_identity)
    weights = ScoreWeights()
    boosts = preferred_boosts(state.rules, row.color_identity)

    scored: list[tuple[bool, float, str, DeckEntry]] = []
    seen: set[str] = set()
    for rec in expensive.recommendations:
        if rec.name in optimized_names:
            continue
        card = state.pool.resolve(rec.name)
        if card is None:
            continue
        name = card["name"]
        if name in seen or name == row.name:
            continue
        if "Basic" in (card.get("type_line") or ""):
            continue
        if (
            name in already_shown
            or name in effective_banned
            or name in state.watchlist_names
        ):
            continue
        if not frozenset(card.get("color_identity") or ()) <= commander_identity:
            continue
        price = card.get("price_usd")
        if price is not None and price < EXPENSIVE_PRICE_FLOOR:
            continue
        seen.add(name)
        facts = _facts(state, card, bands)
        category = primary_category(facts)
        entry = DeckEntry(
            name=name,
            categories=tuple(sorted(facts.categories)),
            score=weights.score(rec.synergy, rec.inclusion) + boost_for(boosts, name),
            reason=EXPENSIVE_CARDS_REASON,
            slot=category,
        )
        # (has_price, -price, name): False sorts before True, so nulls lead;
        # -price then puts the dearest first; name is the deterministic tiebreak.
        scored.append((price is not None, -(price or 0.0), name, entry))

    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return [entry for *_, entry in scored[:EXPENSIVE_CARDS_CAP]]


def _deck_response(
    state: AppState,
    row: CommanderRow,
    dials: Mapping[str, str | None],
    bands: Mapping[str, QuotaBand],
    result: CpSatResult,
    expensive_cards: Sequence[DeckEntry],
) -> DeckResponse:
    """Project a solver result onto the flat wire shape. Nothing is re-decided.

    The three breakdowns are pure projections of what the solver already
    reported: ``category_breakdown`` fuses ``counts`` + ``bands`` +
    ``statuses``, ``curve_breakdown`` histograms the selected non-lands, and
    ``color_source_breakdown`` renames the ``target`` key of
    ``penalties["color_sources"]`` to ``demand``. No new judgement is formed
    here — this layer must never become a second opinion on the deck.
    """
    warnings: list[NoticeView] = []
    if result.relaxation_stage != "none":
        warnings.append(
            NoticeView(
                code=RELAXED_STAGE_CODE,
                severity=AMBER,
                message=relaxed_stage_message(result.relaxation_stage),
            )
        )
    basics, nonbasics = _split_basics(state, result.mainboard)
    return DeckResponse(
        commander_id=row.oracle_id,
        commander_name=row.name,
        commander=card_view(state.pool.by_name[row.name]),
        dials=dict(dials),
        status=result.solver_status,
        deck_size=result.total_cards,
        selected_count=len(nonbasics),
        nonbasic_cards=_cards(state, nonbasics),
        basic_lands=_cards(state, basics),
        maybeboard=_cards(state, result.maybeboard),
        new_cards=_cards(state, result.new_cards),
        expensive_cards=_cards(state, expensive_cards),
        category_breakdown=_category_breakdown(bands, result),
        curve_breakdown=_curve_breakdown(state, nonbasics),
        color_source_breakdown=_color_source_breakdown(result),
        karsten_floor=result.karsten_floor,
        lands_target=result.lands_target,
        target_structure_source=_structure_source(state, row.name),
        relaxation_stage=result.relaxation_stage,
        objective_value=result.objective_value,
        solve_time_seconds=result.solve_time_s,
        # A deck the solver could not build is a 422 (DeckBuildFailed), so a
        # response only ever exists for a deck that resolved.
        infeasible_reason=None,
        warnings=warnings,
        unresolved=list(result.unresolved),
    )


def _split_basics(
    state: AppState, mainboard: Sequence[DeckEntry]
) -> tuple[list[DeckEntry], list[DeckEntry]]:
    """Split the mainboard into ``(basic lands, everything else)``.

    Tested on the type line and not on ``count > 1``: a deck can legitimately
    run a single Mountain, and it is still a basic.
    """
    basics: list[DeckEntry] = []
    nonbasics: list[DeckEntry] = []
    for entry in mainboard:
        card = state.pool.by_name[entry.name]
        target = basics if "Basic" in (card.get("type_line") or "") else nonbasics
        target.append(entry)
    return basics, nonbasics


def _category_breakdown(
    bands: Mapping[str, QuotaBand], result: CpSatResult
) -> dict[str, CategoryRow]:
    return {
        category: CategoryRow(
            count=result.counts.get(category, 0),
            lo=band.min,
            hi=band.max,
            band=_band_kind(category),
            # The solver's own verdict, which for `lands` already accounts for
            # the Karsten floor that `lo` cannot show. See `CategoryRow`.
            within_band=result.statuses.get(category) == CategoryStatus.IN_RANGE,
        )
        for category, band in bands.items()
    }


def _band_kind(category: str) -> str:
    """How this category's band binds the solver. See the ``BAND_*`` constants.

    ``lands`` is asked by name because its floor is the one constraint
    ``_assemble_model`` places outside the ``stage.composition`` guard;
    ``CEILING_ONLY_CATEGORIES`` is asked as the constant rather than by
    testing ``band.min == 0``, because it is a statement about the category's
    nature (the config *forbids* it a floor), not about today's numbers.
    """
    if category == LANDS_CATEGORY:
        return BAND_HARD
    if category in CEILING_ONLY_CATEGORIES:
        return BAND_CEILING_ONLY
    return BAND_SOFT_NO_LOWER


def _curve_breakdown(
    state: AppState, nonbasics: Sequence[DeckEntry]
) -> dict[str, CurveRow]:
    """Mana-curve histogram of the selected non-lands, by ``curve_bucket``.

    Buckets ("0".."6", "7+") and not raw CMCs, because that is the vocabulary
    the Karsten land floor already speaks (``quotas.lands``) and a second one
    would be a second thing to keep in sync. Lands are excluded: they have no
    meaningful cost and would swamp bucket 0.
    """
    curve: dict[str, int] = {}
    for entry in nonbasics:
        if LANDS_CATEGORY in entry.categories:
            continue
        card = state.pool.by_name[entry.name]
        bucket = curve_bucket(card.get("cmc"))
        curve[bucket] = curve.get(bucket, 0) + entry.count
    return {bucket: CurveRow(count=count) for bucket, count in sorted(curve.items())}


def _color_source_breakdown(result: CpSatResult) -> dict[str, ColorSourceRow]:
    """The solver's color-fixing rows, with ``target`` published as ``demand``.

    ``demand`` on the wire because that is the TFM's name for it and the
    frontend reads it; ``target`` internally because that is the solver's.
    This function is the only place the two meet. Empty at the
    ``base_size_and_lands`` stage, where the solver drops the color term.
    """
    rows: Mapping[str, Any] = result.penalties.get("color_sources") or {}
    return {
        color: ColorSourceRow(
            sources=row["sources"], demand=row["target"], deficit=row["deficit"]
        )
        for color, row in rows.items()
    }


def _cards(state: AppState, entries: Sequence[DeckEntry]) -> list[DeckCardView]:
    # Every entry came out of the selector, which only ever picks pool cards.
    return [deck_card_view(state.pool.by_name[e.name], e) for e in entries]


# --- sequential / guided build -----------------------------------------------
#
# The "switcheo semiinteractivo" of the charter: build the optimal deck once,
# then surface its weak cards so the player decides them against same-role
# candidates. Ported from the TFM's `src/api/sequential.py` with our data (our
# EDHREC score, our eight categories, our slots).
#
# This layer only *reads* a build result. It never re-solves and never
# re-scores: `/sequential/start` is one `build_deck` plus arithmetic.

# A category needs at least this many cards for an elbow to mean anything;
# below it, the "lower half" is one or two cards and the largest gap is noise.
MIN_CATEGORY_SIZE = 4
# Global cap on surfaced decisions: the guided flow has to stay short enough
# that a player finishes it.
MAX_DECISIONS = 12


def sequential_start(state: AppState, request: DeckRequest) -> SequentialStartResponse:
    """Build a deck and surface the cards worth deciding. Blocking and CPU-bound.

    One build, then arithmetic: ``decisions`` is derived from the cards the
    solver chose, so this costs exactly what ``POST /build`` costs. The solver
    is **never** re-run, here or on any later decision.
    """
    deck = build_deck(state, request)
    return SequentialStartResponse(
        deck=deck, decisions=compute_decisions(deck.nonbasic_cards)
    )


def compute_decisions(
    nonbasic_cards: Sequence[DeckCardView], *, max_decisions: int = MAX_DECISIONS
) -> list[DecisionView]:
    """The doubtful cards of a deck, worst score first, capped.

    The TFM's elbow, with our data. Per category (the card's ``slot``, which
    is what the deck already displays it under): sort by ``(score desc,
    oracle_id asc)``; skip categories with fewer than ``MIN_CATEGORY_SIZE``
    cards. Inside the **lower half** only, cut at the **largest score gap**
    between consecutive cards; everything from the elbow down is doubtful.

    Two deliberate conservative choices, both the TFM's:

    - Only the lower half is ever eligible. The gap between the first and
      second best removal can be huge and means nothing — nobody wants to be
      asked about their best card.
    - On a gap tie the **deepest** one wins (``>=`` as the loop walks down),
      which flags *fewer* cards.

    Basics never appear: they are interchangeable copies, not individual cards
    to weigh, and they are not in ``nonbasic_cards`` to begin with.
    """
    by_category: dict[str, list[DecisionView]] = {}
    for card in nonbasic_cards:
        # Basics are the only scoreless rows and are not in this list; a card
        # without a score cannot be placed on an elbow, so skipping it is the
        # only honest option.
        if card.score is None:
            continue
        by_category.setdefault(card.slot, []).append(
            DecisionView(
                oracle_id=card.oracle_id,
                name=card.name,
                category=card.slot,
                score=card.score,
            )
        )

    doubtful: list[DecisionView] = []
    for cards in by_category.values():
        cards.sort(key=lambda item: (-item.score, item.oracle_id))
        if len(cards) < MIN_CATEGORY_SIZE:
            continue
        lower = cards[len(cards) // 2 :]
        if len(lower) < 2:
            continue
        best_gap = -1.0
        cut = len(lower)  # nothing flagged
        for i in range(1, len(lower)):
            gap = lower[i - 1].score - lower[i].score
            if gap >= best_gap:  # >= keeps the deepest tie: fewer cards flagged
                best_gap = gap
                cut = i
        doubtful.extend(lower[cut:])

    doubtful.sort(key=lambda item: (item.score, item.oracle_id))
    return doubtful[:max_decisions]


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
    # The commander's quota archetype, so both swap paths can subtract the same
    # archetype-scoped banlist exceptions the build applied.
    archetype: str


def swap_candidates_for(
    state: AppState, request: SwapCandidatesRequest
) -> SwapCandidatesResponse:
    """Rank the feasible replacements for ``out``. Blocking (reads EDHREC)."""
    ctx = _swap_context(state, request)
    data = edhrec_data(state, ctx.row.name)
    pool_candidates = _pool_candidates(state, ctx.row, ctx.bands, data)
    candidates, feasible_count = swap_candidates(
        deck=ctx.deck,
        out_card=ctx.out_card,
        pool_candidates=pool_candidates,
        bands=ctx.bands,
        commander=ctx.commander,
        banned_names=state.effective_banned_names(ctx.archetype),
        never_names=ctx.never_names,
        watchlist_names=state.watchlist_names,
        always_names=ctx.always_names,
        limit=request.limit,
    )
    out_category = primary_category(ctx.out_card)
    # The outgoing card's own score, on the same scale as the candidates', so
    # the panel can put them side by side. 0.0 when EDHREC does not recommend
    # it for this commander at all -- which is a real answer ("nothing here
    # wants this card"), not a missing one.
    out_score = next(
        (score for facts, score in pool_candidates if facts.name == ctx.out_card.name),
        0.0,
    )
    return SwapCandidatesResponse(
        current=bench_card_view(
            state.pool.by_name[ctx.out_card.name],
            categories=sorted(ctx.out_card.categories),
            score=out_score,
            slot=out_category,
            reason=candidate_reason(out_category, out_score),
        ),
        candidates=[
            bench_card_view(
                state.pool.by_name[candidate.name],
                categories=candidate.categories,
                score=candidate.score,
                slot=candidate.primary_category,
                reason=candidate_reason(candidate.primary_category, candidate.score),
            )
            for candidate in candidates
        ],
        feasible_count=feasible_count,
        limit=request.limit,
    )


def swap_replacements_for(
    state: AppState, request: SwapCandidatesRequest
) -> SwapReplacementsResponse:
    """Audit-style, role-aware replacements for a manually chosen out card.

    The same palette the audit offers for a *doubtful* card — up to two of the
    out card's own role, the best card the deck is missing, and one that
    reinforces the thinnest category — applied to whatever card the player marks
    to leave. This is deliberately not the flat score ranking of
    ``swap_candidates_for``: Guille (2026-07-19) wanted the audit's guidance
    everywhere a card can be removed, not a generic top-N list.

    Blocking (reads EDHREC), never runs the solver.
    """
    ctx = _swap_context(state, request)
    data = edhrec_data(state, ctx.row.name)
    pool_candidates = _pool_candidates(state, ctx.row, ctx.bands, data)
    score_by_name = {facts.name: score for facts, score in pool_candidates}

    deck_names = {facts.name for facts, _ in ctx.deck}
    counts: dict[str, int] = {}
    for facts, count in ctx.deck:
        for category in facts.categories:
            counts[category] = counts.get(category, 0) + count
    thin_category = _thinnest_category(counts, ctx.bands)

    feasible = _feasible_replacements(
        flagged=ctx.out_card,
        deck=ctx.deck,
        deck_names=deck_names,
        pool_candidates=pool_candidates,
        bands=ctx.bands,
        commander=ctx.commander,
        banned=state.effective_banned_names(ctx.archetype),
        never=ctx.never_names,
        watchlist=state.watchlist_names,
        always=ctx.always_names,
    )
    replacements = _replacement_palette(
        state, flagged=ctx.out_card, feasible=feasible, thin_category=thin_category
    )

    out_category = primary_category(ctx.out_card)
    out_score = score_by_name.get(ctx.out_card.name, 0.0)
    return SwapReplacementsResponse(
        current=bench_card_view(
            state.pool.by_name[ctx.out_card.name],
            categories=sorted(ctx.out_card.categories),
            score=out_score,
            slot=out_category,
            reason=candidate_reason(out_category, out_score),
        ),
        replacements=replacements,
        feasible_count=len(feasible),
    )


# How many ranked names to pull before identity/banlist filtering trims them to
# the requested limit. Capped at the search's own [1, 50] clamp, so this is just
# "as many as the index will give"; a tighter query is the real answer to a
# mono-colour commander filtering out most of a broad match.
_LEGAL_SEARCH_OVERFETCH = 50


def search_legal_cards(
    state: AppState, *, commander: str, query: str, limit: int
) -> LegalCardSearchResponse:
    """Cards matching ``query`` that are legal to add for ``commander``.

    The advanced-mode "search any card to swap in" source: the whole pool by
    name, filtered to the commander's colour identity and minus the group
    banlist, so every result can actually go in the deck. No EDHREC read (this
    is on the keystroke path): ``score`` is 0.0 — an arbitrary card need not be
    a recommendation — and the category is the tagger's. Basics are excluded:
    they enter through the solver's per-colour counters, not a swap.
    """
    row = resolve_commander(state, commander)
    bands = bands_for(state, row.name, {})
    identity = frozenset(row.color_identity)
    banned = set(state.effective_banned_names(archetype_for(state.quotas, row.name)))
    cards: list[DeckCardView] = []
    # Over-fetch the ranked names, then trim by identity/banlist to fill `limit`.
    for name in state.search_cards(query, _LEGAL_SEARCH_OVERFETCH):
        card = state.pool.resolve(name)
        if card is None:
            continue
        facts = _facts(state, card, bands)
        # Basics are kept: they are the one legal duplicate, so a player must be
        # able to add another (Guille 2026-07-19). Identity still filters them —
        # a Forest carries green identity and cannot go in a colourless-of-green
        # deck. The banlist and off-identity cards are dropped.
        if not facts.color_identity <= identity:
            continue
        if _name_variants(facts.name) & banned:
            continue
        category = primary_category(facts)
        cards.append(
            bench_card_view(
                state.pool.by_name[facts.name],
                categories=sorted(facts.categories),
                score=0.0,
                slot=category,
                reason=candidate_reason(category, 0.0),
            )
        )
        if len(cards) >= limit:
            break
    return LegalCardSearchResponse(count=len(cards), cards=cards)


def swap_outs_for(state: AppState, request: SwapOutsRequest) -> SwapOutsResponse:
    """The best deck cards to take out for a chosen ``in`` card (advanced mode).

    The reverse of ``swap_replacements_for``: the player names the card they
    want in, and this ranks the deck cards worth cutting for it — every one a
    feasible swap, the ``in`` card's own role first, then weakest EDHREC score
    first (the natural cut), exactly as the audit's placing picker orders them.
    Blocking (reads EDHREC), never runs the solver.
    """
    row = resolve_commander(state, request.commander)
    bands = bands_for(state, row.name, request.dials)
    deck = [
        (_facts(state, _pool_card(state, ref.name), bands), ref.count)
        for ref in request.deck
    ]
    total = sum(count for _, count in deck)
    if total != DECK_SIZE:
        raise SwapRequestInvalid(deck_size_mismatch(total, DECK_SIZE))
    in_card = _facts(state, _pool_card(state, request.card_in), bands)

    archetype = archetype_for(state.quotas, row.name)
    rule_ctx = RuleContext(
        commander_name=row.name,
        color_identity=frozenset(row.color_identity),
        archetype=archetype,
    )
    effective_banned = state.effective_banned_names(archetype)
    never_names = resolve_never(state.rules, rule_ctx)
    always_names = frozenset(
        rule.name for rule in resolve_always(state.rules, rule_ctx, effective_banned)
    )
    commander = _facts(state, state.pool.by_name[row.name], bands)

    data = edhrec_data(state, row.name)
    score_by_name = {
        facts.name: score for facts, score in _pool_candidates(state, row, bands, data)
    }

    in_slot = primary_category(in_card)
    feasible_outs: list[tuple[CardFacts, float]] = []
    for facts, _count in deck:
        if facts.is_basic or facts.name == in_card.name:
            continue
        verdict = swap_is_feasible(
            deck=deck,
            out_card=facts,
            in_card=in_card,
            bands=bands,
            commander=commander,
            banned_names=effective_banned,
            never_names=never_names,
            watchlist_names=state.watchlist_names,
            always_names=always_names,
        )
        if verdict.feasible:
            feasible_outs.append((facts, score_by_name.get(facts.name, 0.0)))
    # In-role first, then weakest score first (the weakest link is the natural
    # cut), then name for a stable order.
    feasible_outs.sort(
        key=lambda item: (in_slot not in item[0].categories, item[1], item[0].name)
    )

    in_score = score_by_name.get(in_card.name, 0.0)
    return SwapOutsResponse(
        current=bench_card_view(
            state.pool.by_name[in_card.name],
            categories=sorted(in_card.categories),
            score=in_score,
            slot=in_slot,
            reason=candidate_reason(in_slot, in_score),
        ),
        outs=[
            bench_card_view(
                state.pool.by_name[facts.name],
                categories=sorted(facts.categories),
                score=score,
                slot=primary_category(facts),
                reason=candidate_reason(primary_category(facts), score),
            )
            for facts, score in feasible_outs[: request.limit]
        ],
        feasible_count=len(feasible_outs),
    )


_COLOR_ORDER = ("W", "U", "B", "R", "G")

_LAND_TYPE_TO_COLOR: dict[str, str] = {
    "Plains": "W",
    "Island": "U",
    "Swamp": "B",
    "Mountain": "R",
    "Forest": "G",
}


def _fetched_colors(card: Mapping[str, Any], identity: frozenset[str]) -> frozenset[str]:
    """Colours a *fetch* effect can supply, which ``_produced_colors`` ignores.

    The solver's supply heuristic skips fetches; the live manabase re-evaluation
    must count them or the export warning is nonsense (Guille 2026-07-19). A card
    that searches the library and puts a land onto the battlefield is a fetch.

    Modelling assumption (Guille): the deck runs the maximum useful fetches and
    **every dual it can** — trivially true here, where proxies make price
    irrelevant. Under that assumption a fetch reaches the deck's *whole* colour
    identity as long as it can grab even one in-identity land: a fetch naming a
    basic type of an in-identity colour can pull a dual of that type bridging to
    any other identity colour, and a generic "basic land" fetch reaches them all.
    A fetch that can reach none of the deck's colours (Flooded Strand, which
    names Plains/Island, in a B/R/G deck) contributes nothing.
    """
    text = card.get("oracle_text") or ""
    lowered = text.lower()
    if "search your library" not in lowered or "onto the battlefield" not in lowered:
        return frozenset()
    named = {color for land, color in _LAND_TYPE_TO_COLOR.items() if land in text}
    reaches_identity = bool(named & identity) or (
        not named and ("basic land" in lowered or "a land card" in lowered)
    )
    return identity if reaches_identity else frozenset()


def _source_colors(card: Mapping[str, Any], identity: frozenset[str]) -> frozenset[str]:
    """All identity colours a card is a mana source of: direct production
    (``_produced_colors``: basics, "Add …", any-colour lands like Command Tower)
    plus fetch effects (``_fetched_colors``)."""
    return _produced_colors(card, identity) | _fetched_colors(card, identity)


def evaluate_deck(state: AppState, request: EvaluateRequest) -> EvaluateResponse:
    """Re-evaluate the manabase's colour fixing on the deck's current state.

    The build's ``color_source_breakdown`` is the solver's and freezes at solve
    time; swaps never re-run the solver, so after edits it lies. This recomputes
    the **supply** (how many sources of each colour the current deck actually
    holds, via the same ``_produced_colors`` heuristic the solver uses) against
    the same **demand** the build targeted (the commander/pool Karsten target),
    so the numbers stay comparable. Blocking (reads EDHREC for the demand), never
    runs the solver. A deficit is soft — the caller shows it as a recommendation.
    """
    row = resolve_commander(state, request.commander)
    bands = bands_for(state, row.name, request.dials)
    identity = frozenset(row.color_identity)
    commander_card = state.pool.by_name[row.name]
    commander_cmc = float(commander_card.get("cmc") or 0.0)

    # Demand: the pool Karsten target over the non-land candidates, exactly as
    # the build computes it (so the demand column matches the constraints panel).
    data = edhrec_data(state, row.name)
    candidates = _pool_candidates(state, row, bands, data)
    targets = pool_color_source_targets(
        ((facts.mana_cost, facts.cmc) for facts, _ in candidates if not facts.is_land),
        commander_cmc,
    )
    targets = {color: k for color, k in targets.items() if color in identity}

    # Supply: sources per colour in the deck as it stands now (basics count once
    # per copy — N Forests are N green sources).
    supply: dict[str, int] = {}
    for ref in request.deck:
        card = _pool_card(state, ref.name)
        for color in _source_colors(card, identity):
            supply[color] = supply.get(color, 0) + ref.count

    rows = {
        color: ColorSourceRow(
            sources=supply.get(color, 0),
            demand=targets.get(color, 0),
            deficit=max(0, targets.get(color, 0) - supply.get(color, 0)),
        )
        for color in _COLOR_ORDER
        if color in identity
    }
    return EvaluateResponse(color_source_breakdown=rows)


def maybeboard_for(state: AppState, request: MaybeboardRequest) -> MaybeboardResponse:
    """The bench for a deck in its current state, grouped by category.

    Derived from ``{commander, dials, deck}`` exactly like the swap
    candidates, and for the same reason: **the solver is never re-run**. That
    is what makes the bench live — swap a card in and the next call has
    already dropped it, without paying 0.05-10 s to re-solve.

    The universe is the commander's EDHREC recommendations, scored as the
    build scored them, minus the cards already in the deck, the banned /
    ``never`` / watchlisted ones and anything outside the commander's colour
    identity. Note what is *not* filtered: unlike ``swap_candidates_for``,
    nothing here is checked for feasibility, because a bench card is not a
    swap — it becomes one only when the player picks a card to take out, and
    ``/sequential/validate`` is what answers that.

    Blocking: reads EDHREC (memo, then disk, then network).
    """
    row = resolve_commander(state, request.commander)
    bands = bands_for(state, row.name, request.dials)
    deck_names = {_pool_card(state, ref.name)["name"] for ref in request.deck}
    data = edhrec_data(state, row.name)

    rule_ctx = RuleContext(
        commander_name=row.name,
        color_identity=frozenset(row.color_identity),
        archetype=archetype_for(state.quotas, row.name),
    )
    # Canonicalized through the pool because rules.yaml may name a single face
    # ("Fire") of a card the pool calls "Fire // Ice". `selector.swap` solves
    # the same problem by expanding the candidate's name into its variants;
    # collapsing the other side to canonical names is the same comparison, and
    # keeps this layer off the selector's private helpers.
    excluded = deck_names | _canonical(
        state,
        resolve_never(state.rules, rule_ctx)
        | state.effective_banned_names(rule_ctx.archetype)
        | state.watchlist_names,
    )
    commander_identity = frozenset(row.color_identity)

    grouped: dict[str, list[DeckCardView]] = {}
    for facts, score in _pool_candidates(state, row, bands, data):
        if facts.name in excluded:
            continue
        if not facts.color_identity <= commander_identity:
            continue
        category = primary_category(facts)
        grouped.setdefault(category, []).append(
            bench_card_view(
                state.pool.by_name[facts.name],
                categories=sorted(facts.categories),
                score=score,
                slot=category,
                reason=candidate_reason(category, score),
            )
        )
    return MaybeboardResponse(
        maybeboard={
            # Ties broken by name so a redraw never reshuffles the bench.
            category: sorted(cards, key=lambda c: (-c.score, c.name))[: request.limit]
            for category, cards in sorted(grouped.items())
        },
        limit=request.limit,
    )


def _canonical(state: AppState, names: Iterable[str]) -> frozenset[str]:
    """The pool's canonical name for each name that resolves; others dropped.

    A name that does not resolve cannot match a pool card anyway, so it can be
    dropped rather than raise: ``banned_names`` and ``watchlist_names`` are
    already canonical, and ``rules.yaml`` names are pool-validated at startup.
    """
    resolved = (state.pool.resolve(name) for name in names)
    return frozenset(card["name"] for card in resolved if card is not None)


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
        banned_names=state.effective_banned_names(ctx.archetype),
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


# --- audit --------------------------------------------------------------------
#
# The audit points at a deck; it never changes it and never re-solves. Every
# judgement is swap-style arithmetic over the deck as it stands: curated flags
# (selector.audit), then feasibility checks for the replacement palette. One
# EDHREC read for the candidate universe, shared by the palette and `missing`.

# How many "good that's missing" cards to surface, and the same-role palette cap.
AUDIT_MISSING_LIMIT = 8
_AUDIT_SAME_ROLE_SLOTS = 2


def audit_deck(state: AppState, request: AuditRequest) -> AuditResponse:
    """Point out a deck's doubtful cards and the good cards it is missing.

    Blocking (reads EDHREC), but never runs the solver. ``doubtful`` is the
    curated layer-1 flags, each with a feasible replacement palette; ``missing``
    is the highest-scored cards the commander wants that are not in the deck.
    """
    row = resolve_commander(state, request.commander)
    bands = bands_for(state, row.name, request.dials)
    deck = [
        (_facts(state, _pool_card(state, ref.name), bands), ref.count)
        for ref in request.deck
    ]
    total = sum(count for _, count in deck)
    if total != DECK_SIZE:
        raise SwapRequestInvalid(deck_size_mismatch(total, DECK_SIZE))

    archetype = archetype_for(state.quotas, row.name)
    rule_ctx = RuleContext(
        commander_name=row.name,
        color_identity=frozenset(row.color_identity),
        archetype=archetype,
    )
    effective_banned = state.effective_banned_names(archetype)
    never_names = resolve_never(state.rules, rule_ctx)
    always_names = frozenset(
        rule.name for rule in resolve_always(state.rules, rule_ctx, effective_banned)
    )
    commander = _facts(state, state.pool.by_name[row.name], bands)

    data = edhrec_data(state, row.name)
    pool_candidates = _pool_candidates(state, row, bands, data)
    score_by_name = {facts.name: score for facts, score in pool_candidates}

    deck_names = {facts.name for facts, _ in deck}
    facts_by_name = {facts.name: facts for facts, _ in deck}
    counts: dict[str, int] = {}
    for facts, count in deck:
        for category in facts.categories:
            counts[category] = counts.get(category, 0) + count
    thin_category = _thinnest_category(counts, bands)

    # Layer-2 signals, keyed by canonical pool name (recs use EDHREC's names).
    synergy_by_name: dict[str, float] = {}
    inclusion_by_name: dict[str, float] = {}
    for rec in data.recommendations:
        card = state.pool.resolve(rec.name)
        if card is not None:
            synergy_by_name[card["name"]] = rec.synergy
            inclusion_by_name[card["name"]] = rec.inclusion
    land_names = {facts.name for facts, _ in deck if facts.is_land}

    layer1 = flag_conditionals(deck_names, commander.cmc)
    layer1_names = {flag.name for flag in layer1}
    layer2 = flag_low_synergy_filler(
        deck_names,
        synergy_by_name=synergy_by_name,
        inclusion_by_name=inclusion_by_name,
        land_names=land_names,
        # Layer 1 already explains these better; always-forced cards are the
        # player's own rules and outrank a statistical hunch.
        protected_names=layer1_names | always_names,
    )

    doubtful: list[AuditFlagView] = []
    for flag in [*layer1, *layer2]:
        flagged = facts_by_name.get(flag.name)
        if flagged is None:
            continue
        feasible = _feasible_replacements(
            flagged=flagged,
            deck=deck,
            deck_names=deck_names,
            pool_candidates=pool_candidates,
            bands=bands,
            commander=commander,
            banned=effective_banned,
            never=never_names,
            watchlist=state.watchlist_names,
            always=always_names,
        )
        doubtful.append(
            AuditFlagView(
                card=bench_card_view(
                    state.pool.by_name[flagged.name],
                    categories=sorted(flagged.categories),
                    score=score_by_name.get(flagged.name, 0.0),
                    slot=primary_category(flagged),
                    reason=flag.reason,
                ),
                reason=flag.reason,
                replacements=_replacement_palette(
                    state,
                    flagged=flagged,
                    feasible=feasible,
                    thin_category=thin_category,
                ),
            )
        )

    missing = _missing_cards(
        state,
        deck_names=deck_names,
        pool_candidates=pool_candidates,
        commander=commander,
        banned=effective_banned,
        never=never_names,
        watchlist=state.watchlist_names,
    )
    return AuditResponse(doubtful=doubtful, missing=missing)


def _thinnest_category(
    counts: Mapping[str, int], bands: Mapping[str, QuotaBand]
) -> str | None:
    """The spell category the deck is thinnest in (smallest headroom over its
    minimum), or ``None`` if no category has a minimum. Lands and the synergy
    filler are excluded: the audit reinforces roles, not the manabase."""
    thin: str | None = None
    thin_headroom: int | None = None
    for category, band in bands.items():
        if category in (LANDS_CATEGORY, SYNERGY_CATEGORY) or band.min <= 0:
            continue
        headroom = counts.get(category, 0) - band.min
        if thin_headroom is None or headroom < thin_headroom:
            thin_headroom = headroom
            thin = category
    return thin


def _feasible_replacements(
    *,
    flagged: CardFacts,
    deck: Sequence[tuple[CardFacts, int]],
    deck_names: Collection[str],
    pool_candidates: Sequence[tuple[CardFacts, float]],
    bands: Mapping[str, QuotaBand],
    commander: CardFacts,
    banned: Collection[str],
    never: Collection[str],
    watchlist: Collection[str],
    always: Collection[str],
) -> list[tuple[CardFacts, float]]:
    """Every candidate that is a feasible one-for-one swap for ``flagged``,
    best score first. Shared by the audit palette and the manual-swap picker so
    both offer the *same* role-aware, quota-valid replacements."""
    excluded = set(banned) | set(never) | set(watchlist)
    feasible: list[tuple[CardFacts, float]] = []
    for facts, score in pool_candidates:
        if facts.name == flagged.name or facts.name in deck_names:
            continue
        if _name_variants(facts.name) & excluded:
            continue
        if not facts.color_identity <= commander.color_identity:
            continue
        verdict = swap_is_feasible(
            deck=deck,
            out_card=flagged,
            in_card=facts,
            bands=bands,
            commander=commander,
            banned_names=banned,
            never_names=never,
            watchlist_names=watchlist,
            always_names=always,
        )
        if verdict.feasible:
            feasible.append((facts, score))
    feasible.sort(key=lambda item: (-item[1], item[0].name))
    return feasible


def _replacement_palette(
    state: AppState,
    *,
    flagged: CardFacts,
    feasible: Sequence[tuple[CardFacts, float]],
    thin_category: str | None,
) -> list[ReplacementView]:
    """Up to four replacements for ``flagged``, picked from ``feasible``.

    Two of the flagged card's own role, one "best you're missing" (any role,
    the upgrade axis) and one that reinforces the thinnest category (the balance
    axis). An axis with no legal, unused option is dropped. Overlap between axes
    is allowed, the same card twice is not.
    """
    flagged_category = primary_category(flagged)
    used: set[str] = set()
    slots: list[ReplacementView] = []

    same_role = 0
    for facts, score in feasible:
        if same_role >= _AUDIT_SAME_ROLE_SLOTS:
            break
        if primary_category(facts) == flagged_category and facts.name not in used:
            slots.append(_replacement_view(state, facts, score, "same_role", "Mismo rol"))
            used.add(facts.name)
            same_role += 1

    for facts, score in feasible:
        if facts.name not in used:
            slots.append(
                _replacement_view(
                    state, facts, score, "best_overall", "La mejor carta que te falta"
                )
            )
            used.add(facts.name)
            break

    if thin_category is not None and thin_category != flagged_category:
        for facts, score in feasible:
            if primary_category(facts) == thin_category and facts.name not in used:
                slots.append(
                    _replacement_view(
                        state,
                        facts,
                        score,
                        "reinforce",
                        f"Refuerza {thin_category}, vas justo",
                    )
                )
                used.add(facts.name)
                break

    return slots


def _replacement_view(
    state: AppState, facts: CardFacts, score: float, kind: str, note: str
) -> ReplacementView:
    category = primary_category(facts)
    return ReplacementView(
        kind=kind,
        note=note,
        card=bench_card_view(
            state.pool.by_name[facts.name],
            categories=sorted(facts.categories),
            score=score,
            slot=category,
            reason=candidate_reason(category, score),
        ),
    )


def _missing_cards(
    state: AppState,
    *,
    deck_names: Collection[str],
    pool_candidates: Sequence[tuple[CardFacts, float]],
    commander: CardFacts,
    banned: Collection[str],
    never: Collection[str],
    watchlist: Collection[str],
) -> list[DeckCardView]:
    """The highest-scored cards the commander wants that are not in the deck.

    The "good that's missing" side, filtered like the maybeboard bench (drop
    in-deck, banned, ``never``, watchlist and off-identity), best first, capped.
    Not feasibility-checked: these are pointers, not one-for-one swaps.
    """
    excluded = set(banned) | set(never) | set(watchlist)
    missing: list[DeckCardView] = []
    for facts, score in sorted(pool_candidates, key=lambda item: (-item[1], item[0].name)):
        if facts.name in deck_names or _name_variants(facts.name) & excluded:
            continue
        if not facts.color_identity <= commander.color_identity:
            continue
        category = primary_category(facts)
        missing.append(
            bench_card_view(
                state.pool.by_name[facts.name],
                categories=sorted(facts.categories),
                score=score,
                slot=category,
                reason=candidate_reason(category, score),
            )
        )
        if len(missing) >= AUDIT_MISSING_LIMIT:
            break
    return missing


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

    archetype = archetype_for(state.quotas, row.name)
    rule_ctx = RuleContext(
        commander_name=row.name,
        color_identity=frozenset(row.color_identity),
        archetype=archetype,
    )
    effective_banned = state.effective_banned_names(archetype)
    return _SwapContext(
        row=row,
        commander=_facts(state, state.pool.by_name[row.name], bands),
        bands=bands,
        deck=deck,
        out_card=out_card,
        never_names=resolve_never(state.rules, rule_ctx),
        always_names=frozenset(
            rule.name
            for rule in resolve_always(state.rules, rule_ctx, effective_banned)
        ),
        archetype=archetype,
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
    state: AppState,
    row: CommanderRow,
    bands: Mapping[str, QuotaBand],
    data: EdhrecCommanderData,
) -> list[tuple[CardFacts, float]]:
    """The commander's EDHREC recommendations as ``(facts, score)``.

    Scored exactly as the build scored them, boosts included, so the ranking
    the player sees here agrees with the deck they are editing. Shared by the
    swap candidates and the maybeboard, which is what keeps those two rankings
    from drifting apart.

    Only the universe is built here. Policy filtering (banned, never,
    watchlist, off-identity, already in the deck) belongs to each caller —
    ``swap_candidates`` does its own, and the maybeboard's differs.
    """
    weights = ScoreWeights()
    boosts = preferred_boosts(state.rules, row.color_identity)
    candidates: list[tuple[CardFacts, float]] = []
    seen: set[str] = set()
    for rec in data.recommendations:
        card = state.pool.resolve(rec.name)
        if card is None:
            continue
        name = card["name"]
        # Basics are never candidates: they are the only legal duplicate and
        # enter a deck through the solver's per-color counters, not a swap.
        if name in seen or name == row.name or "Basic" in (card.get("type_line") or ""):
            continue
        seen.add(name)
        candidates.append(
            (
                _facts(state, card, bands),
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


# --- card printings (art / language picker) ----------------------------------


def _card_by_oracle_id(state: AppState, oracle_id: str) -> Mapping[str, Any]:
    """The pool card with that oracle_id, or ``CardNotFound``.

    A linear scan over the pool (~31k rows, a few ms). Deliberate: the prints
    endpoints are click-driven and batched, so an oracle_id index on the
    frozen ``AppState`` would be a core change for no felt difference.
    """
    for card in state.pool.cards():
        if card.get("oracle_id") == oracle_id:
            return card
    raise CardNotFound(f"No hay ninguna carta con oracle_id {oracle_id!r}")


def _print_rows(oracle_id: str) -> list[CardPrintView]:
    try:
        return [CardPrintView(**row) for row in fetch_prints(oracle_id)]
    except PrintsError as exc:
        raise PrintsUnavailable(
            "No se pudieron consultar las ediciones en Scryfall; "
            "inténtalo de nuevo en un momento"
        ) from exc


def _default_print(
    prints: Sequence[CardPrintView], pool_scryfall_id: str
) -> CardPrintView | None:
    """The printing the default-art policy picks, or None to keep the pool's.

    Policy (Guille, 2026-07-18; low-res Spanish admitted same day — the
    high-res-only cut left almost no Spanish cards): the newest **Spanish
    high-res** scan wins; else the newest **Spanish low-res** (a real scan,
    just soft — placeholders never got this far); else keep the pool's English
    art if that printing is itself high-res, else the newest **English
    high-res**. ``prints`` arrives newest-first, so "first match" is "newest".
    """
    for row in prints:
        if row.lang == "es" and row.highres:
            return row
    for row in prints:
        if row.lang == "es":
            return row
    pool_row = next((r for r in prints if r.scryfall_id == pool_scryfall_id), None)
    if pool_row is not None and pool_row.highres:
        return None
    for row in prints:
        if row.lang == "en" and row.highres:
            return row
    return None


def card_prints(state: AppState, oracle_id: str) -> CardPrintsResponse:
    """The printings gallery for one card: every real scan, quality labelled.

    All rows are genuine scans (the fetcher drops placeholder/missing
    printings); low-res ones carry ``highres=False`` and the picker badges
    them, so choosing soft art is a visible decision rather than a trap.
    Blocking (Scryfall on a cold cache): call through a threadpool.
    """
    card = _card_by_oracle_id(state, oracle_id)
    rows = _print_rows(oracle_id)
    default = _default_print(rows, card.get("scryfall_id") or "")
    return CardPrintsResponse(
        oracle_id=oracle_id,
        name=card["name"],
        prints=rows,
        default_scryfall_id=default.scryfall_id if default else None,
    )


# The "dragon eye" full-art basics from Tarkir: Dragonstorm (TDM 2025) — the
# house default for Dragon decks (Guille 2026-07-20), used when the picker is
# asked with theme=dragon. Scryfall labels these lowres for now (recent set),
# but they print fine and are the ones Guille wants.
DRAGON_BASIC_IDS: dict[str, str] = {
    "Forest": "7e33e540-2828-46ad-a441-366552843d9c",
    "Island": "b300be80-6618-4284-b5c3-95c1ab373e6f",
    "Plains": "3e8c67e5-587a-43b2-af47-bbad1f8b52e9",
    "Swamp": "57da24a0-89a7-4756-b4ca-4dea132e8f67",
    "Mountain": "a4db1b7a-93f2-40a5-b649-80a099ddeb62",
}


def _theros_basic_id(name: str) -> str | None:
    """The Theros full-art printing's scryfall_id, parsed from its CDN URL —
    the house default the basics picker highlights and the export falls back to."""
    url = THEROS_BASIC_IMAGES.get(name)
    if not url:
        return None
    return url.rsplit("/", 1)[-1].split(".", 1)[0]


def fullart_basics(
    state: AppState, name: str, *, theme: str | None = None
) -> CardPrintsResponse:
    """The full-art printings a player may choose for one basic land.

    Only full-art (Guille 2026-07-20): the house look is full-art, so the
    gallery offers those alone. The default is the Theros printing, or the TDM
    "dragon eye" one when ``theme == "dragon"`` (Dragon decks). Blocking
    (Scryfall on a cold cache): call through a threadpool.
    """
    card = state.pool.resolve(name)
    if card is None or "Basic" not in (card.get("type_line") or ""):
        raise CardNotFound(f"{name!r} no es una tierra básica")
    try:
        rows = [CardPrintView(**row) for row in fetch_fullart_basics(name)]
    except PrintsError as exc:
        raise PrintsUnavailable(
            "No se pudieron consultar las ediciones en Scryfall; "
            "inténtalo de nuevo en un momento"
        ) from exc
    preferred = (
        DRAGON_BASIC_IDS.get(name) if theme == "dragon" else _theros_basic_id(name)
    )
    default = (
        preferred
        if preferred and any(r.scryfall_id == preferred for r in rows)
        else (rows[0].scryfall_id if rows else None)
    )
    return CardPrintsResponse(
        oracle_id=card["oracle_id"],
        name=name,
        prints=rows,
        default_scryfall_id=default,
    )


def token_prints(state: AppState, scryfall_id: str) -> CardPrintsResponse:
    """The art options for a token, given one of its printing ids.

    Tokens are not in the pool (only a printing id is stored on the maker), so
    the gallery is fetched by the token's oracle_id. The default is the base
    printing the request came in with. Blocking (Scryfall on a cold cache).
    """
    try:
        rows = [CardPrintView(**row) for row in fetch_token_prints(scryfall_id)]
    except PrintsError as exc:
        raise PrintsUnavailable(
            "No se pudieron consultar las ediciones del token en Scryfall; "
            "inténtalo de nuevo en un momento"
        ) from exc
    default = (
        scryfall_id
        if any(r.scryfall_id == scryfall_id for r in rows)
        else (rows[0].scryfall_id if rows else None)
    )
    # `oracle_id`/`name` are unused by the picker (the client supplies the token
    # name); the id given is echoed so the response is self-describing.
    return CardPrintsResponse(
        oracle_id=scryfall_id, name="", prints=rows, default_scryfall_id=default
    )


def print_defaults(
    state: AppState, request: PrintDefaultsRequest
) -> PrintDefaultsResponse:
    """Resolve the default (Spanish-first) printing for a batch of cards.

    One pool pass validates the whole batch; an unknown oracle_id is a 404
    for the lot (the client only ever sends ids it got from us, so a miss is
    a bug worth surfacing, not skipping). Blocking on a cold cache — each
    unseen card costs one Scryfall search — so call through a threadpool.
    """
    wanted = set(request.oracle_ids)
    cards: dict[str, Mapping[str, Any]] = {}
    for card in state.pool.cards():
        oid = card.get("oracle_id")
        if oid in wanted:
            cards[oid] = card
    missing = wanted - set(cards)
    if missing:
        raise CardNotFound(
            f"No hay ninguna carta con oracle_id {sorted(missing)[0]!r}"
        )
    defaults: dict[str, CardPrintView | None] = {}
    for oid in request.oracle_ids:
        rows = _print_rows(oid)
        defaults[oid] = _default_print(rows, cards[oid].get("scryfall_id") or "")
    return PrintDefaultsResponse(defaults=defaults)


# --- proxy PDF export --------------------------------------------------------
#
# A print-and-cut proxy sheet: real-size cards laid 3x3 on A4 portrait, glued
# edge to edge so one straight guillotine cut goes through every shared border.
# A Magic card is 63 mm wide x 88 mm tall (taller than wide), so the 3x3 block
# is 189x264 mm, centred on A4 (210x297) with a 10.5 mm horizontal and 16.5 mm
# vertical margin. Double-faced cards print both faces as two consecutive cells
# so the proxy is actually playable.
#
# Nothing is drawn on the cards themselves: the cut guides are short ticks in the
# outer margin, aligned with each grid line. A line drawn over the shared border
# tempts a cut on each side of it (two cuts, double the border to trim); ticks in
# the discarded margin let one guillotine pass ride the shared border cleanly.

CARD_W_MM = 63.0
CARD_H_MM = 88.0
GRID_COLS = 3
GRID_ROWS = 3
CARDS_PER_PAGE = GRID_COLS * GRID_ROWS
A4_W_MM = 210.0
A4_H_MM = 297.0
BLOCK_W_MM = CARD_W_MM * GRID_COLS
BLOCK_H_MM = CARD_H_MM * GRID_ROWS
MARGIN_X_MM = (A4_W_MM - BLOCK_W_MM) / 2
MARGIN_Y_MM = (A4_H_MM - BLOCK_H_MM) / 2
# Crop-mark ticks in the margin, aligned with each grid line. 0.1 mm is the
# thinnest line that still prints; the ticks live on the discarded margin, so
# a darker grey is fine (visible for alignment, thrown away with the trim). The
# 5 mm length fits inside both margins (10.5 mm horizontal, 16.5 mm vertical).
CROP_MARK_WIDTH_MM = 0.1
CROP_MARK_GREY = 120
CROP_MARK_LEN_MM = 5.0

# Guille's basic lands print with the Theros Beyond Death full-art (the nyx
# starfield), not the pool's default printing: it is the group's house look for
# proxies. Keyed by the pool's canonical basic name; basics are single-faced,
# so one URL each. To refresh after a bulk update, re-query Scryfall
# `set:thb is:fullart type:basic` (set THB, collector 250-254) and paste the
# `image_uris.normal` of each of the five basics here.
THEROS_BASIC_IMAGES: dict[str, str] = {
    "Plains": "https://cards.scryfall.io/normal/front/a/9/a9891b7b-fc52-470c-9f74-292ae665f378.jpg?1783931510",
    "Island": "https://cards.scryfall.io/normal/front/a/c/acf7b664-3e75-4018-81f6-2a14ab59f258.jpg?1783931508",
    "Swamp": "https://cards.scryfall.io/normal/front/0/2/02cb5cfd-018e-4c5e-bef1-166262aa5f1d.jpg?1783931508",
    "Mountain": "https://cards.scryfall.io/normal/front/5/3/53fb7b99-9e47-46a6-9c8a-88e28b5197f1.jpg?1783931511",
    "Forest": "https://cards.scryfall.io/normal/front/3/2/32af9f41-89e2-4e7a-9fec-fffe79cae077.jpg?1783931507",
}


@dataclass(frozen=True)
class ProxyPdf:
    """A rendered proxy sheet and the name to save it under."""

    filename: str
    content: bytes


def build_proxy_pdf(
    state: AppState,
    request: ProxyPdfRequest,
    *,
    fetch_image: Callable[[str], bytes] | None = None,
) -> ProxyPdf:
    """Render a deck as a 3x3, real-size, print-and-cut proxy sheet. Blocking.

    Resolves each name against the pool (an unknown one is a
    ``SwapRequestInvalid`` -> 422), fetches every card image (disk cache, then
    Scryfall) and lays them 9 per A4 page. The commander comes first, then the
    cards in the order received, each expanded to its ``count``; a double-faced
    card contributes two consecutive cells (front then back). With
    ``include_tokens`` the tokens the deck can create follow the cards, filling
    the last page's empty cells and spilling to fresh pages.

    ``fetch_image`` defaults to the module-level ``fetch_card_image`` and is
    injectable so tests can render without touching the network.
    """
    if fetch_image is None:
        fetch_image = fetch_card_image

    commander = _pool_card(state, request.commander)
    overrides = request.art_overrides
    face_urls: list[str] = list(
        _face_urls(commander, override_id=overrides.get(commander["name"]))
    )
    producers: list[Mapping[str, Any]] = [commander]
    for ref in request.cards:
        card = _pool_card(state, ref.name)
        producers.append(card)
        urls = list(_face_urls(card, override_id=overrides.get(card["name"])))
        for _ in range(ref.count):
            face_urls.extend(urls)

    # One fetch per distinct URL; duplicate copies and shared faces reuse the
    # bytes instead of re-reading the cache for every cell.
    fetched: dict[str, bytes] = {}
    faces: list[bytes] = []
    for url in face_urls:
        if url not in fetched:
            fetched[url] = fetch_image(url)
        faces.append(fetched[url])

    # Tokens ride after the cards, filling the last page's empty cells and
    # spilling to fresh pages. Best-effort: a card image that fails to download
    # is a real problem (above), but a missing token image just drops that token
    # with a warning rather than sinking the whole sheet.
    if request.include_tokens:
        for token in _deck_tokens_to_print(producers):
            # Per-copy art: token_overrides maps the base scryfall_id to the id
            # to print for each copy (so two copies can differ). A copy with no
            # override, or an empty one, prints the base printing.
            chosen = request.token_overrides.get(token.scryfall_id, [])
            for copy in range(token.copies):
                art_id = chosen[copy] if copy < len(chosen) and chosen[copy] else token.scryfall_id
                url = TOKEN_IMAGE_URL.format(scryfall_id=art_id)
                if url not in fetched:
                    try:
                        fetched[url] = fetch_image(url)
                    except httpx.HTTPError as exc:
                        logger.warning(
                            "No token image for %r (%s); leaving it off the sheet",
                            token.name,
                            exc,
                        )
                        continue
                faces.append(fetched[url])

    return ProxyPdf(
        filename=f"{slugify_commander(commander['name'])}_proxies.pdf",
        content=_render_proxy_pdf(faces),
    )


def _face_urls(card: Mapping[str, Any], override_id: str | None = None) -> list[str]:
    """The image URLs to print for one card: front, then back for a DFC.

    A double-faced card (``image_uri_back_normal`` non-empty: Kefka, Etali)
    yields both faces so the proxy has a real back. A card Scryfall has no art
    for (a null ``image_uri_normal``) yields nothing and is skipped with a
    warning — there is no image to draw.

    ``override_id`` is the art picker's chosen printing (a scryfall_id, never a
    URL — the id resolves through Scryfall's own image endpoint, so a client
    cannot point the fetcher anywhere else). It wins over everything, including
    the Theros basics override: an explicit choice IS the house style for that
    card. Whether the printing is double-faced follows the pool card — a DFC's
    printings are all DFCs.

    Basic lands otherwise get the Theros Beyond Death full-art
    (``THEROS_BASIC_IMAGES``): the group prints its manabase in that frame, not
    in whatever printing the pool happens to store. Basics are single-faced, so
    that override replaces the front and there is no back to add.
    """
    if override_id:
        urls = [PRINT_IMAGE_URL.format(scryfall_id=override_id)]
        if card.get("image_uri_back_normal"):
            urls.append(PRINT_IMAGE_URL.format(scryfall_id=override_id) + "&face=back")
        return urls
    theros = THEROS_BASIC_IMAGES.get(card.get("name") or "")
    if theros is not None:
        return [theros]
    urls = []
    front = card.get("image_uri_normal")
    if front:
        urls.append(front)
    else:
        logger.warning("No image for %r; it will not appear in the proxy PDF", card.get("name"))
    back = card.get("image_uri_back_normal") or ""
    if back:
        urls.append(back)
    return urls


# Scryfall serves any printing's art straight from its id; format=image
# answers 302 to the CDN JPEG (fetch_card_image follows the redirect), and
# version=normal matches the 63x88 frame the rest of the sheet uses. Used for
# tokens and for the art picker's chosen printings (+"&face=back" for backs).
TOKEN_IMAGE_URL = "https://api.scryfall.com/cards/{scryfall_id}?format=image&version=normal"
PRINT_IMAGE_URL = TOKEN_IMAGE_URL

# A single maker earns a second (tapped) copy of a *creature* token when its
# text implies it makes several, or makes them turn after turn. Two or more
# makers already show the deck leans on that token, so they skip this check.
_MULTIPLE_TOKEN_SIGNALS = (
    "create two",
    "create three",
    "create four",
    "create five",
    "create six",
    "create x",
    "number of",
    "for each",
    "whenever",
    "at the beginning",
    "populate",
    "one or more",
)


@dataclass(frozen=True)
class _TokenToPrint:
    name: str
    scryfall_id: str
    type_line: str
    copies: int


def _token_copies(type_line: str, producer_texts: Sequence[str]) -> int:
    """Copies to print for one token: 2 for a creature the deck leans on, else 1.

    A non-creature token (Treasure, Clue, Food) is always one — you tap-sac it,
    you never attack with it. A creature token gets two (one upright, one to sit
    tapped) when two or more cards make it, or when its lone maker's text implies
    several or recurring creation; a one-off like Beast Within's Beast gets one.
    """
    if "Creature" not in type_line:
        return 1
    if len(producer_texts) >= 2:
        return 2
    text = producer_texts[0] if producer_texts else ""
    if any(signal in text for signal in _MULTIPLE_TOKEN_SIGNALS):
        return 2
    return 1


def _deck_tokens_to_print(
    producers: Sequence[Mapping[str, Any]],
) -> list[_TokenToPrint]:
    """The tokens a deck can create, deduplicated, each with its copy count.

    ``producers`` is every card whose tokens count (the commander and the deck).
    Tokens collapse by (name, type_line) so reprints of the same token become
    one; the copy count follows ``_token_copies``. Ordered most-copies first,
    then by name, so the tokens the deck relies on fill the first empty cells.
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for card in producers:
        text = (card.get("oracle_text") or "").lower()
        for tok in card.get("tokens") or ():
            key = (tok["name"], tok["type_line"])
            if key not in seen:
                seen[key] = {
                    "name": tok["name"],
                    "scryfall_id": tok["scryfall_id"],
                    "type_line": tok["type_line"],
                    "texts": [],
                }
                order.append(key)
            seen[key]["texts"].append(text)

    tokens = [
        _TokenToPrint(
            name=seen[key]["name"],
            scryfall_id=seen[key]["scryfall_id"],
            type_line=seen[key]["type_line"],
            copies=_token_copies(seen[key]["type_line"], seen[key]["texts"]),
        )
        for key in order
    ]
    tokens.sort(key=lambda t: (-t.copies, t.name))
    return tokens


def tokens_for(state: AppState, request: TokenListRequest) -> TokenListResponse:
    """The tokens a deck can create in its current state, for the art picker.

    Same producers as the PDF (the commander + the deck's cards), so the list
    matches exactly what ``include_tokens`` would print. Live, like the
    maybeboard: recomputed from the deck sent, so a swap that drops a token
    maker drops its token on the next call. No network — the token data is baked
    into the pool.
    """
    producers: list[Mapping[str, Any]] = [_pool_card(state, request.commander)]
    for ref in request.deck:
        card = state.pool.resolve(ref.name)
        if card is not None:
            producers.append(card)
    tokens = [
        TokenView(
            name=token.name,
            type_line=token.type_line,
            scryfall_id=token.scryfall_id,
            copies=token.copies,
            image_uri_normal=TOKEN_IMAGE_URL.format(scryfall_id=token.scryfall_id),
        )
        for token in _deck_tokens_to_print(producers)
    ]
    return TokenListResponse(tokens=tokens)


def _render_proxy_pdf(faces: Sequence[bytes]) -> bytes:
    """Lay ``faces`` 3x3 across A4 pages, each image filling a 63x88 mm cell."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    # Every cell is placed by hand at an absolute (x, y); auto page breaks would
    # fight that and shove images onto a new page.
    pdf.set_auto_page_break(False)

    # At least one page, so a commander-only request still renders a sheet.
    page_count = max(1, math.ceil(len(faces) / CARDS_PER_PAGE))
    for page in range(page_count):
        pdf.add_page()
        page_faces = faces[page * CARDS_PER_PAGE : (page + 1) * CARDS_PER_PAGE]
        for index, image_bytes in enumerate(page_faces):
            col = index % GRID_COLS
            row = index // GRID_COLS
            x = MARGIN_X_MM + col * CARD_W_MM
            y = MARGIN_Y_MM + row * CARD_H_MM
            # fpdf2 embeds the JPEG straight from the bytes (no Pillow); w and h
            # both given means it fills the cell exactly, no aspect fitting.
            pdf.image(BytesIO(image_bytes), x=x, y=y, w=CARD_W_MM, h=CARD_H_MM)
        _draw_crop_marks(pdf)
    return bytes(pdf.output())


def _draw_crop_marks(pdf: FPDF) -> None:
    """Draw cut-guide ticks in the margin, aligned with every grid line.

    Nothing is drawn over the cards: each of the four vertical grid lines gets a
    short tick in the top and bottom margin, and each of the four horizontal grid
    lines a tick in the left and right margin. Drawn on every page (the empty
    cells of a short last page included) so the guides always line up for a
    single guillotine pass along each shared border.
    """
    pdf.set_draw_color(CROP_MARK_GREY, CROP_MARK_GREY, CROP_MARK_GREY)
    pdf.set_line_width(CROP_MARK_WIDTH_MM)
    top, bottom = MARGIN_Y_MM, MARGIN_Y_MM + BLOCK_H_MM
    left, right = MARGIN_X_MM, MARGIN_X_MM + BLOCK_W_MM
    for col in range(GRID_COLS + 1):
        x = MARGIN_X_MM + col * CARD_W_MM
        pdf.line(x, top - CROP_MARK_LEN_MM, x, top)
        pdf.line(x, bottom, x, bottom + CROP_MARK_LEN_MM)
    for row in range(GRID_ROWS + 1):
        y = MARGIN_Y_MM + row * CARD_H_MM
        pdf.line(left - CROP_MARK_LEN_MM, y, left, y)
        pdf.line(right, y, right + CROP_MARK_LEN_MM, y)


def _notice(violation: Violation) -> NoticeView:
    return NoticeView(
        code=violation.code,
        severity=violation.severity.value,
        message=violation_message(violation),
    )


# --- banlist -----------------------------------------------------------------


def banlist_view(state: AppState) -> BanlistResponse:
    """The group's banlist and watchlist, resolved to pool cards. No I/O.

    Reads the whole parsed banlist (``state.banlist``): the manual card bans
    *and* every card a programmatic rule resolves to, each carried with the
    reason that applies (the card's own note, or its rule's). The set of banned
    cards is taken from ``resolved_banlist.banned`` — the same projection the
    rest of the API trusts — so rule exceptions are already subtracted and the
    count matches what ``/build`` refuses. Each entry is resolved to its
    canonical name, ``oracle_id`` and art from the pool; both lists are sorted
    alphabetically by name.
    """
    reasons, names = _banned_reasons(state)
    # Invert the archetype -> exempted names map to name -> archetypes, so each
    # banned card can carry the archetypes it is nonetheless legal in.
    legal_by_name: dict[str, list[str]] = {}
    for archetype, exempt_names in state.banned_exceptions_by_archetype.items():
        for name in exempt_names:
            legal_by_name.setdefault(name, []).append(archetype)
    banned = [
        BanlistCardView(
            name=names[oracle_id],
            reason=reasons[oracle_id],
            image_uri_normal=state.pool.by_name[names[oracle_id]].get(
                "image_uri_normal"
            )
            or None,
            oracle_id=oracle_id,
            legal_in_archetypes=sorted(legal_by_name.get(names[oracle_id], [])),
        )
        for oracle_id in state.resolved_banlist.banned
    ]

    watchlist: list[BanlistWatchlistView] = []
    for entry in state.banlist.watchlist:
        resolved = _resolve_banlist_name(state, entry.name)
        if resolved is None:
            continue
        watchlist.append(
            BanlistWatchlistView(
                name=resolved.canonical_name,
                reason=entry.reason,
                image_uri_normal=state.pool.by_name[resolved.canonical_name].get(
                    "image_uri_normal"
                )
                or None,
                oracle_id=resolved.oracle_id,
                scope=entry.scope,
            )
        )

    banned.sort(key=lambda card: card.name)
    watchlist.sort(key=lambda card: card.name)
    return BanlistResponse(banned=banned, watchlist=watchlist)


def _banned_reasons(state: AppState) -> tuple[dict[str, str], dict[str, str]]:
    """Map each banned oracle_id to its reason and its canonical pool name.

    Rules first, then manual card bans override — an explicitly listed card
    carries a more specific reason than the rule that also catches it. Only
    oracle_ids present in ``resolved_banlist.banned`` are ever emitted by the
    caller, so exception cards (subtracted there) fall away on their own.
    """
    reasons: dict[str, str] = {}
    names: dict[str, str] = {}

    def record(name: str, reason: str, *, override: bool) -> None:
        resolved = _resolve_banlist_name(state, name)
        if resolved is None:
            return
        names[resolved.oracle_id] = resolved.canonical_name
        if override or resolved.oracle_id not in reasons:
            reasons[resolved.oracle_id] = reason

    for rule in state.banlist.rules:
        if rule.status not in BANNED_STATUSES:
            continue
        for name in rule.resolved_cards:
            record(name, rule.reason, override=False)
    for card in state.banlist.cards:
        if card.status in BANNED_STATUSES:
            record(card.name, card.reason, override=True)
    return reasons, names


def _resolve_banlist_name(state: AppState, name: str):
    """Resolve a banlist name to a pool card, or ``None`` if it cannot resolve.

    The banlist resolved cleanly against this same pool at startup (else the
    app would not have started), so a miss here is not expected; it is handled
    defensively rather than raised, because a banlist view is never worth a
    500.
    """
    try:
        return state.name_index.resolve(name)
    except ResolutionError:
        logger.warning("banlist name %r no longer resolves against the pool", name)
        return None
