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

from pydantic import BaseModel, ConfigDict

from app.state import CommanderRow


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
