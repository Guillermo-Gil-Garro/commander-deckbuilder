"""Pydantic response models for the API.

``extra="forbid"`` everywhere, like the rest of the repo: a typo in a field
name is a test failure, not a silently dropped key.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Startup diagnosis. ``degraded`` means the card pool never loaded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok", "degraded"]
    cards_loaded: int
    commanders: int
    banned: int
    tags: int
