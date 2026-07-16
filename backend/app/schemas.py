"""Pydantic response models for the API.

``extra="forbid"`` everywhere, like the rest of the repo: a typo in a field
name is a test failure, not a silently dropped key.

``CardView`` is the *only* shape a card takes anywhere in this API — deck,
maybeboard, candidates, commanders. One shape means the frontend writes one
card component and every endpoint keeps ``scryfall_id``, which is what
Fase 5 builds the card images from.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quotas.config import QuotaBand
from selector.greedy import DeckEntry

# Swap candidate list size. Same policy as the commander search: clamped, not
# rejected, and never unbounded.
SWAP_CANDIDATES_LIMIT_DEFAULT = 20
SWAP_CANDIDATES_LIMIT_MIN = 1
SWAP_CANDIDATES_LIMIT_MAX = 50

# Maybeboard bench depth, applied *per category* rather than to the whole
# response: the bench is read one category at a time, and a global cap would
# let `synergy` (by far the widest bucket) starve every other role.
MAYBEBOARD_LIMIT_DEFAULT = 10
MAYBEBOARD_LIMIT_MIN = 1
MAYBEBOARD_LIMIT_MAX = 50


class HealthResponse(BaseModel):
    """Startup diagnosis. ``degraded`` means the card pool never loaded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok", "degraded"]
    cards_loaded: int
    commanders: int
    banned: int
    tags: int


class CardView(BaseModel):
    """One card as the API publishes it, everywhere.

    Everything here is a *fact about the card*, straight from the pool: the
    printing (``scryfall_id``, images), the mana (``mana_cost``, ``cmc``) and
    the identity. Nothing here depends on a deck, which is why the commander,
    a maybeboard card and a mainboard card can all be this shape.

    ``score``, ``categories`` and the rest of the "what is this card doing in
    *this* deck" fields live in ``DeckCardView``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    oracle_id: str
    scryfall_id: str
    color_identity: list[str]
    type_line: str | None
    mana_cost: str
    cmc: float
    image_uri_normal: str | None
    image_uri_art_crop: str | None


def card_view(card: Mapping[str, Any]) -> CardView:
    """Project a pool card onto the public card shape.

    Raises ``KeyError`` for a card missing a required field: the pool is
    built by our own pipeline, so a gap is a bug to surface, not to paper
    over with a default.

    The image URIs are ``None`` for the handful of cards Scryfall has no art
    for; they are nullable rather than absent so a client always reads the
    same key. ``mana_cost`` is ``""`` for lands, which have none — that is the
    card's real mana cost, not a missing value.
    """
    return CardView(
        name=card["name"],
        oracle_id=card["oracle_id"],
        scryfall_id=card["scryfall_id"],
        color_identity=list(card.get("color_identity") or ()),
        type_line=card.get("type_line"),
        mana_cost=card.get("mana_cost") or "",
        cmc=float(card.get("cmc") or 0.0),
        image_uri_normal=card.get("image_uri_normal"),
        image_uri_art_crop=card.get("image_uri_art_crop"),
    )


class CommanderView(CardView):
    """A commander as the pickers publish it: the card shape plus its archetype.

    ``archetype`` is the quota archetype the deck would be built against
    (``quotas.yaml``), resolved with the same layering ``resolve_bands`` uses.
    It is a coarse descriptor for the picker's benefit, **not** a promise: it
    says which bands the build starts from, never how the deck will play.
    """

    archetype: str


class CommandersResponse(BaseModel):
    """A list of commanders plus its size.

    ``count`` is the length of ``commanders`` — the list is already whole, so
    this is a convenience, never a total the list was trimmed from. For the
    search endpoint that means ``count`` reflects what ``limit`` returned, and
    says nothing about how many commanders matched.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    count: int
    commanders: list[CommanderView]


def commander_view(card: Mapping[str, Any], archetype: str) -> CommanderView:
    """Project a pool card onto the commander shape (the card shape + archetype).

    Takes the pool card and not the pre-indexed ``CommanderRow`` because the
    card shape now carries the printing and mana facts, and ``CommanderRow``
    only ever held the fields the search index needs. Callers hold a row and
    reach the card through ``state.pool.by_name[row.name]``, which is a dict
    hit — cheap at this endpoint's scale (``/commanders/search`` caps at 50).
    ``GET /commanders`` ships thousands of rows and uses the slimmer
    ``CommanderListItem`` instead.
    """
    return CommanderView(**card_view(card).model_dump(), archetype=archetype)


class StructureResponse(BaseModel):
    """The quota bands a build would use, without building anything.

    ``source`` says which layer of ``quotas.yaml`` produced them:
    ``"commander"`` if the file individualises this commander (its own
    archetype and/or per-category overrides), ``"archetype"`` if it fell back
    to an archetype block.

    There is deliberately **no** ``karsten_floor`` here: the land floor is a
    function of a deck's non-land curve and its ramp+draw count, so it does
    not exist until a deck does. See the endpoint docstring.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    commander: CardView
    dials: dict[str, str | None]
    categories: dict[str, BandView]
    archetype: str
    source: Literal["commander", "archetype"]


# --- deck build --------------------------------------------------------------


class BandView(BaseModel):
    """One resolved quota band, as published: inclusive ``[lo, hi]``.

    ``lo``/``hi`` and not ``min``/``max``: this is the wire name, and it is the
    one the TFM API published. The internal ``quotas.config.QuotaBand`` keeps
    ``min``/``max`` — the selectors and the whole quota engine speak that
    dialect — and ``band_view`` is the single place the two meet. Renaming the
    domain model to match the wire would have touched the solver; translating
    at the frontier costs one function.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lo: int
    hi: int


class DeckCardView(CardView):
    """A card *in a deck or on a bench*: the card shape plus its role here.

    The one shape for every list of cards this API returns that is not the
    commander itself: ``nonbasic_cards``, ``basic_lands``, ``maybeboard``,
    ``new_cards`` and the swap candidates. One shape means the frontend writes
    one card component.

    ``categories`` are **all** the card's quota categories (the tagger's
    labels restricted to the banded ones); ``slot`` is the single one it is
    displayed under. A removal that is also a creature counts against removal
    *and* is shown there. Both are rederived server-side, never accepted from
    a client.
    """

    categories: list[str]
    count: int  # > 1 only for basic lands
    slot: str  # the category it is displayed under
    reason: str  # why the selector put it here, in the selector's own words
    score: float | None  # None for basics: the solver places them, no score


# --- breakdowns --------------------------------------------------------------

# How a category's band binds the solver. Reported per category so a client can
# explain *why* a count sits outside its band instead of just flagging it red.
# Verified against `selector/cp_sat.py::_assemble_model` and `_STAGES`:
#
# - `hard`: the floor holds at EVERY relaxation stage. Only `lands`
#   (`_assemble_model` adds `lands_count >= lands_min` outside the
#   `stage.composition` guard, and the module docstring says so: "never
#   relaxed by design"). `lands_min` is the band min raised to the deck's
#   Karsten floor by the outer fixpoint.
# - `ceiling_only`: no floor at all, by config — `quotas.config`'s
#   CEILING_ONLY_CATEGORIES have `min == 0`, and the model guards its floor
#   with `if band.min > 0`. Today: `synergy`.
# - `soft_no_lower`: floor is hard at stage `none` and becomes a penalised
#   deficit from stage `soft_category_floors` on. Every other category.
#
# Ceilings are not what this field describes: they are hard through stage
# `drop_ceilings` for every category alike, so they would not tell the
# categories apart.
BAND_HARD = "hard"
BAND_CEILING_ONLY = "ceiling_only"
BAND_SOFT_NO_LOWER = "soft_no_lower"


class CategoryRow(BaseModel):
    """One category's line in the quota panel: count, band and verdict.

    ``lo``/``hi`` are the band ``quotas.yaml`` + the dials resolved to — the
    same numbers ``/structure`` publishes.

    **``within_band`` is not ``lo <= count <= hi``.** For ``lands`` the
    effective minimum is ``max(lo, karsten_floor)``, which the deck's own
    curve decides and can exceed ``lo``; ``within_band`` reports the real
    verdict (``quotas.validator``), so it can be ``false`` on a count that
    looks inside the printed band. ``karsten_floor`` and ``lands_target`` on
    the build response are that missing number.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    count: int
    lo: int
    hi: int
    band: Literal["hard", "ceiling_only", "soft_no_lower"]
    within_band: bool


class CurveRow(BaseModel):
    """One mana-curve bucket of the deck's non-lands.

    **Only a count, deliberately.** The TFM's row carries ``target`` and
    ``deviation`` because its solver penalises deviation from a target curve.
    Ours does not: the curve is an *output* here — it feeds the Karsten land
    floor (``quotas.lands``) and nothing in the objective pulls toward a
    shape. Publishing a ``target`` would invent a goal the solver never had.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    count: int


class ColorSourceRow(BaseModel):
    """One color's mana-fixing line: how many sources, how many are wanted.

    ``demand`` is the pool-derived Karsten target (``quotas.color_sources``);
    ``sources`` counts the selected cards that produce this color, basics
    included; ``deficit`` is ``max(0, demand - sources)``.

    **A deficit is not an error.** Color fixing is a *soft* objective term
    (see ``cp_sat``'s convex penalty): the solver buys a source whenever it
    costs less than roughly 0.1 of score, so a small deficit means the cards
    were worth more than the fixing, not that the deck is broken.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: int
    demand: int
    deficit: int


class NoticeView(BaseModel):
    """One thing to tell the player about a deck or a swap.

    ``severity`` follows ``rules.yaml``: ``red`` blocks (the result would not
    be a legal 99), ``amber`` informs and never blocks. Which list a notice
    travels in says the same thing structurally — blockers block, warnings
    warn — so a client may branch on either.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    severity: str
    message: str


class DeckRequest(BaseModel):
    """Build request: a commander plus, optionally, the user's dial positions.

    ``bands`` is deliberately absent and ``extra="forbid"`` keeps it that way:
    the bands are derived server-side from ``quotas.yaml`` + commander + dials
    on every request. A client that could send them could relax any quota.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    commander: str = Field(min_length=1)
    # Positions are validated by quotas.resolver against quotas.yaml, not by a
    # Literal here: the config owns which categories have a dial.
    dials: dict[str, str | None] = Field(default_factory=dict)


class DeckResponse(BaseModel):
    """A built deck plus everything needed to explain and re-validate it.

    Flat, like the TFM API this frontend was written against: the solver's
    verdict lives at the root (``status``, ``relaxation_stage``,
    ``objective_value``, ``solve_time_seconds``) rather than nested under a
    ``solver`` object, and the deck arrives as two lists — ``nonbasic_cards``
    and ``basic_lands`` — instead of one mixed mainboard. Basics are the only
    legal duplicate and the only rows with ``count > 1``; splitting them out
    is what lets a client render "8x Mountain" as one tile without inspecting
    type lines.

    ``deck_size`` is the whole 99 (basics counted with multiplicity);
    ``selected_count`` is just the non-basics — the cards the solver actually
    *chose*, since basics are placed by its per-color counters.

    ``infeasible_reason`` is always ``null`` here: an input no relaxation
    stage can satisfy is a 422, so a 200 is always a real deck. The field is
    part of the contract, not dead weight — read ``relaxation_stage`` and the
    amber ``relaxed_stage`` warning to find out what the solver gave up.

    ``dials`` is echoed back. The bands are **not** accepted from the client
    (``DeckRequest`` is ``extra="forbid"``): they are rederived from
    ``quotas.yaml`` + commander + dials on every request and come back inside
    ``category_breakdown``, as information only.

    ``maybeboard`` is this build's bench, frozen at build time; it goes stale
    the moment the player swaps, and ``POST /maybeboard`` is what recomputes
    it. ``unresolved`` lists EDHREC recommendations absent from our pool,
    which were simply skipped — not an error.

    Absent on purpose, and not as ``null``: ``price_eur``/``budget_total``/
    ``deck_cost`` (the group plays proxies, so price is meaningless here) and
    ``is_game_changer``/``bracket``/``gc_cap`` (WotC's power policy, which the
    group's banlist replaces). ``num_eligible_nonbasics``/
    ``num_eligible_basics`` are absent because the solver does not report the
    size of its eligible set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    commander_id: str
    commander_name: str
    commander: CardView
    dials: dict[str, str | None]
    status: str
    deck_size: int
    selected_count: int
    nonbasic_cards: list[DeckCardView]
    basic_lands: list[DeckCardView]
    maybeboard: list[DeckCardView]
    new_cards: list[DeckCardView]
    category_breakdown: dict[str, CategoryRow]
    curve_breakdown: dict[str, CurveRow]
    color_source_breakdown: dict[str, ColorSourceRow]
    karsten_floor: int
    lands_target: int
    target_structure_source: Literal["commander", "archetype"]
    relaxation_stage: str
    objective_value: float
    solve_time_seconds: float
    infeasible_reason: str | None
    warnings: list[NoticeView]
    unresolved: list[str]


# --- swap --------------------------------------------------------------------


class DeckCardRef(BaseModel):
    """One deck row as the client sends it: a name and how many copies.

    Deliberately the whole payload. Categories and scores are **rederived**
    server-side from the pool + tagger: accepting them would let a client
    declare any deck legal, and they are not needed anyway (this keeps the
    body at ~2 KB instead of ~4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    count: int = Field(default=1, ge=1)  # > 1 only for basic lands


class SwapRequest(BaseModel):
    """What both swap endpoints need: the deck, its context and the card out.

    ``bands`` is absent for the same reason as in ``DeckRequest``: the server
    recomputes them from ``commander`` + ``dials`` on every request.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    commander: str = Field(min_length=1)
    dials: dict[str, str | None] = Field(default_factory=dict)
    deck: list[DeckCardRef] = Field(min_length=1)
    out: str = Field(min_length=1)


class SwapCandidatesRequest(SwapRequest):
    """Ask for replacements for ``out``. ``limit`` is clamped to [1, 50]."""

    limit: int = SWAP_CANDIDATES_LIMIT_DEFAULT

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value: int) -> int:
        # Clamped, not rejected: same call as /commanders/search — a bad limit is
        # not worth a 422, and neither endpoint should ever dump the whole pool.
        return min(max(value, SWAP_CANDIDATES_LIMIT_MIN), SWAP_CANDIDATES_LIMIT_MAX)


class SwapValidateRequest(SwapRequest):
    """Ask whether replacing ``out`` with ``in`` is playable."""

    # `in` is a Python keyword, so the field is aliased. The wire name is the
    # one that matters and it stays `in`, the mirror of `out`.
    card_in: str = Field(alias="in", min_length=1)


class SwapCandidatesResponse(BaseModel):
    """Replacements for ``current``, best first. ``feasible_count`` is the total.

    ``current`` is the card being evaluated — the one the request called
    ``out``. There is one ``candidates`` list and not the TFM's
    ``synergy``/``power`` split: that split exists because it has two scorers,
    and we have one. An empty ``power`` would be paperwork, not parity.

    ``feasible_count`` counts every feasible candidate, before ``limit``
    trimmed the list — so the UI can say "37 options" while showing ten.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    current: DeckCardView
    candidates: list[DeckCardView]
    feasible_count: int
    limit: int


# --- maybeboard --------------------------------------------------------------


class MaybeboardRequest(BaseModel):
    """The bench for a deck in its current state. Same inputs as a swap.

    Carries the live ``deck`` and not just a commander, because the whole
    point is that the bench moves as the deck does: a card swapped in leaves
    the maybeboard on the next call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    commander: str = Field(min_length=1)
    dials: dict[str, str | None] = Field(default_factory=dict)
    deck: list[DeckCardRef] = Field(min_length=1)
    limit: int = MAYBEBOARD_LIMIT_DEFAULT

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value: int) -> int:
        # Per category, and clamped rather than rejected — same policy as the
        # commander search and the swap candidate list.
        return min(max(value, MAYBEBOARD_LIMIT_MIN), MAYBEBOARD_LIMIT_MAX)


class MaybeboardResponse(BaseModel):
    """The bench, grouped by the category each card is displayed under.

    ``maybeboard`` maps a primary category to its best non-deck cards, best
    first. A category with nothing left to offer is simply absent — an empty
    list would say the same thing and make the client check twice.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    maybeboard: dict[str, list[DeckCardView]]
    limit: int


class SwapValidateResponse(BaseModel):
    """The verdict on one swap, plus the full quota traffic light after it.

    ``feasible`` false is a **result**, not an HTTP error. ``statuses`` and
    ``counts`` describe the deck *after* the swap and come back whether or not
    it is feasible: the live quota panel needs them either way, and they are
    free in this same request.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    feasible: bool
    blockers: list[NoticeView]
    warnings: list[NoticeView]
    counts: dict[str, int]
    statuses: dict[str, str]
    karsten_floor: int
    deck_size: int


# --- export ------------------------------------------------------------------


class ExportCardRef(DeckCardRef):
    """One mainboard row to export, with the section the player sees it in.

    Unlike everywhere else, ``slot`` **is** taken from the client here: it is
    the visual grouping, and after a few swaps the client is the one that
    knows which section it put each card in. Nothing is validated against it —
    an unknown slot is exported as its own raw label.
    """

    slot: str = Field(min_length=1)


class CardRef(BaseModel):
    """A card referenced by name only (the export's sideboard sections)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)


class ExportRequest(BaseModel):
    """A finished deck to render as a decklist."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commander: str = Field(min_length=1)
    deck: list[ExportCardRef] = Field(min_length=1)
    maybeboard: list[CardRef] = Field(default_factory=list)
    new_cards: list[CardRef] = Field(default_factory=list)
    # An enum with one member today. It is here so that adding a second format
    # is a new value and not a new endpoint.
    format: Literal["archidekt"] = "archidekt"


def band_view(band: QuotaBand) -> BandView:
    """Translate the domain's ``min``/``max`` band onto the wire's ``lo``/``hi``.

    The only bridge between the two vocabularies. See ``BandView``.
    """
    return BandView(lo=band.min, hi=band.max)


def deck_card_view(card: Mapping[str, Any], entry: DeckEntry) -> DeckCardView:
    """Project a pool card plus its ``DeckEntry`` onto the deck card shape."""
    return DeckCardView(
        **card_view(card).model_dump(),
        categories=list(entry.categories),
        count=entry.count,
        slot=entry.slot,
        reason=entry.reason,
        score=entry.score,
    )


def bench_card_view(
    card: Mapping[str, Any],
    *,
    categories: Sequence[str],
    score: float,
    slot: str,
    reason: str,
) -> DeckCardView:
    """Project a pool card that is *not* in a deck onto the deck card shape.

    The swap candidates and the maybeboard: cards with a category, a score and
    a reason, but no ``DeckEntry`` behind them because no selector placed them.
    ``count`` is 1 — basics are never candidates (they are the only legal
    duplicate and enter through the solver's per-color counters, not a swap).
    """
    return DeckCardView(
        **card_view(card).model_dump(),
        categories=sorted(categories),
        count=1,
        slot=slot,
        reason=reason,
        score=score,
    )
