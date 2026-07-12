"""Karsten land floor for a 99-card Commander deck (curve average -> lands).

Ported from the TFM ``optimizer/target_structure.py``. Only the pure
calculation lives here: expected mana value of a (non-land) curve and the
closed-form land floor with rounding/clamps. The EDHREC average-deck
classification and the per-category band application (``apply_land_floor``)
are deliberately NOT ported (they belong to a later phase).
"""

from __future__ import annotations

from typing import Mapping

CURVE_BUCKETS = ("0", "1", "2", "3", "4", "5", "6", "7+")
CURVE_TOP_BUCKET = 7

# Karsten mana-source land floor — Karsten 2022, "How Many Lands Do You Need in Your
# Deck? An Updated Analysis" (primary source, verified). The 99-card Commander floor is
# derived from his updated regression:
#   99/60 * (19.59 + 1.90*avgMV + 0.27) - 0.28*(draw+ramp) - 1.35
#     = 31.42 + 3.13*avgMV - 0.28*(draw+ramp)
# The -1.35 adjusts for the Commander free mulligan + turn-one card draw. The ``fast`` and
# ``mdfc`` terms are NOT part of this formula (MDFCs are counted as partial lands 0.38/0.74
# in the COUNT, not as a discount here), so omitting them is correct, not a simplification.
# Two declared simplifications remain vs. the original: (a) the ``7+`` curve tail is
# collapsed onto ``CURVE_TOP_BUCKET`` as its representative MV; (b) ``ramp_plus_draw`` uses
# the full ramp+card_draw counts (not only the MV<=2 slice Karsten describes), which can
# over-discount; harmless when the floor is used as a lower bound to raise.
KARSTEN_LAND_FLOOR_INTERCEPT = 31.42
KARSTEN_AVGMV_COEF = 3.13
KARSTEN_RAMP_DRAW_COEF = 0.28

DEFAULT_DECK_SIZE = 99


def curve_bucket(mana_value: float | int | None) -> str:
    """Return the curve bucket label for a mana value (``7+`` collapses the tail)."""
    if mana_value is None:
        value = 0
    else:
        value = int(mana_value)
    if value < 0:
        value = 0
    if value >= CURVE_TOP_BUCKET:
        return "7+"
    return str(value)


def expected_curve_mv(curve: Mapping[str, float]) -> float:
    """Expected mana value of a (non-land) target curve.

    ``Sum(cost * probability)`` over the buckets, using ``CURVE_TOP_BUCKET`` as the
    representative cost of the collapsed ``7+`` tail (declared approximation).
    """
    total = 0.0
    for bucket, probability in curve.items():
        cost = CURVE_TOP_BUCKET if bucket == "7+" else int(bucket)
        total += cost * float(probability)
    return total


def karsten_land_floor(avg_mv: float, ramp_plus_draw: int) -> float:
    """Karsten singleton-100 mana-source land floor (unrounded).

    ``avg_mv`` is the expected mana value of the non-land curve (see
    ``expected_curve_mv``); ``ramp_plus_draw`` is the combined count of ramp and
    card-draw slots discounted by the regression.
    """
    return (
        KARSTEN_LAND_FLOOR_INTERCEPT
        + KARSTEN_AVGMV_COEF * avg_mv
        - KARSTEN_RAMP_DRAW_COEF * ramp_plus_draw
    )


def land_count(
    avg_mv: float, ramp_plus_draw: int, deck_size: int = DEFAULT_DECK_SIZE
) -> int:
    """Rounded land floor clamped to ``[0, deck_size]``.

    Same rounding and clamps the TFM's ``apply_land_floor`` applied to the
    ``lands`` lower bound, without the category-band machinery.
    """
    floor = round(karsten_land_floor(avg_mv, ramp_plus_draw))
    return max(0, min(floor, deck_size))
