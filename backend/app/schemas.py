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


def commander_view(row: CommanderRow) -> CardView:
    """Project a pre-indexed commander row onto the same card shape."""
    return CardView(
        name=row.name,
        oracle_id=row.oracle_id,
        scryfall_id=row.scryfall_id,
        color_identity=list(row.color_identity),
    )


# --- deck build --------------------------------------------------------------


class BandView(BaseModel):
    """One resolved ``[min, max]`` quota band, as published."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min: int
    max: int


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
        # Clamped, not rejected: same call as /api/commanders — a bad limit is
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
    """Replacements for ``out``, best first. ``feasible_count`` is the total.

    ``feasible_count`` counts every feasible candidate, before ``limit``
    trimmed the list — so the UI can say "37 options" while showing ten.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    out: CardView
    candidates: list[CandidateView]
    feasible_count: int
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


def band_view(band: QuotaBand) -> BandView:
    return BandView(min=band.min, max=band.max)


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
