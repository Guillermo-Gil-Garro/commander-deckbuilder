"""Deck validation against resolved quota bands (pure counting, O(categories)).

The Karsten land floor is applied here, on top of the resolved bands: the
effective ``lands`` minimum is ``max(band.min, karsten_floor)``. The floor is
unbreachable by design (Guille, 2026-07-12) — a low lands dial may bring the
band down to the floor, but a deck below the floor NEVER validates.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

from quotas.config import QuotaBand
from quotas.lands import expected_curve_mv, land_count

LANDS_CATEGORY = "lands"


class CategoryStatus(str, Enum):
    BELOW = "below"
    IN_RANGE = "in_range"
    ABOVE = "above"


def validate_deck(
    counts: Mapping[str, int],
    bands: Mapping[str, QuotaBand],
    *,
    curve: Mapping[str, float] | None = None,
    ramp_plus_draw: int | None = None,
) -> dict[str, CategoryStatus]:
    """Status per category in ``bands`` given the deck's per-category counts.

    A category absent from ``counts`` counts as 0. When both ``curve`` (the
    non-land curve fractions, see ``quotas.lands.expected_curve_mv``) and
    ``ramp_plus_draw`` are provided, the Karsten land floor raises the
    effective ``lands`` minimum; passing only one of them is a usage error.
    ``synergy`` bands have min 0, so they can never be ``below``.
    """
    if (curve is None) != (ramp_plus_draw is None):
        raise ValueError(
            "curve and ramp_plus_draw must be provided together "
            "(both or neither) to apply the Karsten land floor"
        )
    karsten_floor: int | None = None
    if curve is not None and ramp_plus_draw is not None:
        karsten_floor = land_count(expected_curve_mv(curve), ramp_plus_draw)

    statuses: dict[str, CategoryStatus] = {}
    for category, band in bands.items():
        count = counts.get(category, 0)
        minimum = band.min
        if category == LANDS_CATEGORY and karsten_floor is not None:
            minimum = max(minimum, karsten_floor)
        if count < minimum:
            statuses[category] = CategoryStatus.BELOW
        elif count > band.max:
            statuses[category] = CategoryStatus.ABOVE
        else:
            statuses[category] = CategoryStatus.IN_RANGE
    return statuses
