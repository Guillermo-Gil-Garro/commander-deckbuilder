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

from pydantic import BaseModel, ConfigDict, Field

from app.state import CommanderRow
from quotas.config import QuotaBand
from selector.greedy import DeckEntry


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


class WarningView(BaseModel):
    """Something the player should read. Never a reason to refuse anything.

    ``severity`` is always ``amber`` here by construction: a ``red`` finding
    is a blocker and travels in ``blockers``, never in ``warnings``.
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
    warnings: list[WarningView]
    unresolved: list[str]


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
