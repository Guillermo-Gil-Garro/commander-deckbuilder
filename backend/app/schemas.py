"""Pydantic response models for the API.

``extra="forbid"`` everywhere, like the rest of the repo: a typo in a field
name is a test failure, not a silently dropped key.

``CardView`` is the *only* shape a card takes anywhere in this API — deck,
maybeboard, candidates, commanders. One shape means the frontend writes one
card component and every endpoint keeps ``scryfall_id``, which is what
Fase 5 builds the card images from.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.state import CommanderRow
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
    """One card as the API publishes it, everywhere."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    oracle_id: str
    scryfall_id: str
    color_identity: list[str]


def card_view(card: Mapping[str, Any]) -> CardView:
    """Project a pool card onto the public card shape.

    Raises ``KeyError`` for a card missing a required field: the pool is
    built by our own pipeline, so a gap is a bug to surface, not to paper
    over with a default.
    """
    return CardView(
        name=card["name"],
        oracle_id=card["oracle_id"],
        scryfall_id=card["scryfall_id"],
        color_identity=list(card.get("color_identity") or ()),
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


def commander_view(row: CommanderRow, archetype: str) -> CommanderView:
    """Project a pre-indexed commander row onto the commander shape."""
    return CommanderView(
        name=row.name,
        oracle_id=row.oracle_id,
        scryfall_id=row.scryfall_id,
        color_identity=list(row.color_identity),
        archetype=archetype,
    )


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
    """A card *in a deck*: the one card shape plus its place in this build."""

    count: int  # > 1 only for basic lands
    slot: str  # the category it is displayed under
    reason: str  # why the selector put it here, in the selector's own words
    score: float | None  # None for basics: the solver places them, no score


class SolverView(BaseModel):
    """What the solver did. ``stage`` != "none" means quotas were relaxed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    stage: str
    solve_time_s: float
    objective: float


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
    """A built deck plus everything needed to explain and re-validate it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commander: CardView
    dials: dict[str, str | None]
    bands: dict[str, BandView]
    mainboard: list[DeckCardView]
    maybeboard: list[DeckCardView]
    new_cards: list[DeckCardView]
    counts: dict[str, int]
    statuses: dict[str, str]
    karsten_floor: int
    lands_target: int
    solver: SolverView
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


class CandidateView(CardView):
    """One feasible replacement: the card shape plus why it is being offered."""

    score: float
    reason: str


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

    current: CardView
    candidates: list[CandidateView]
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

    maybeboard: dict[str, list[CandidateView]]
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
        name=card["name"],
        oracle_id=card["oracle_id"],
        scryfall_id=card["scryfall_id"],
        color_identity=list(card.get("color_identity") or ()),
        count=entry.count,
        slot=entry.slot,
        reason=entry.reason,
        score=entry.score,
    )
