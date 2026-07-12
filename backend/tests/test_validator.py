"""Tests for quotas.validator: category statuses and the Karsten land floor."""

from __future__ import annotations

import pytest

from quotas.config import QuotaBand
from quotas.validator import CategoryStatus, validate_deck


def band(low: int, high: int) -> QuotaBand:
    return QuotaBand(min=low, max=high)


# Avg MV 3.3 -> land_count(3.3, 12) == 38 (same fixture as test_lands).
CURVE_33 = {"1": 0.1, "2": 0.25, "3": 0.25, "4": 0.2, "5": 0.1, "6": 0.05, "7+": 0.05}


def test_below_in_range_above() -> None:
    bands = {"lands": band(36, 39)}
    assert validate_deck({"lands": 35}, bands) == {"lands": CategoryStatus.BELOW}
    assert validate_deck({"lands": 36}, bands) == {"lands": CategoryStatus.IN_RANGE}
    assert validate_deck({"lands": 39}, bands) == {"lands": CategoryStatus.IN_RANGE}
    assert validate_deck({"lands": 40}, bands) == {"lands": CategoryStatus.ABOVE}


def test_statuses_are_stable_strings() -> None:
    # The enum values are the API/UI contract.
    assert CategoryStatus.BELOW.value == "below"
    assert CategoryStatus.IN_RANGE.value == "in_range"
    assert CategoryStatus.ABOVE.value == "above"


def test_missing_category_counts_as_zero() -> None:
    bands = {"removal": band(8, 11), "synergy": band(0, 28)}
    statuses = validate_deck({}, bands)
    assert statuses["removal"] is CategoryStatus.BELOW
    # synergy min is 0, so below is impossible even with no synergy cards.
    assert statuses["synergy"] is CategoryStatus.IN_RANGE


def test_synergy_ceiling_still_triggers_above() -> None:
    statuses = validate_deck({"synergy": 29}, {"synergy": band(0, 28)})
    assert statuses["synergy"] is CategoryStatus.ABOVE


def test_only_categories_in_bands_are_reported() -> None:
    statuses = validate_deck({"lands": 37, "ramp": 50}, {"lands": band(36, 39)})
    assert set(statuses) == {"lands"}


# --- Karsten land floor -----------------------------------------------------------


def test_karsten_floor_raises_effective_lands_min() -> None:
    # Control-style band [36, 40]; the floor (38) sits inside it, so 37 is
    # below WITH the floor but in range without it.
    bands = {"lands": band(36, 40)}
    without_floor = validate_deck({"lands": 37}, bands)
    assert without_floor["lands"] is CategoryStatus.IN_RANGE
    with_floor = validate_deck(
        {"lands": 37}, bands, curve=CURVE_33, ramp_plus_draw=12
    )
    assert with_floor["lands"] is CategoryStatus.BELOW
    ok = validate_deck({"lands": 38}, bands, curve=CURVE_33, ramp_plus_draw=12)
    assert ok["lands"] is CategoryStatus.IN_RANGE


def test_low_dial_band_never_validates_below_the_floor() -> None:
    # Midrange lands [36, 39] after the low dial: [33, 36]. The floor (38) is
    # above the whole shifted band, so even the band max cannot validate.
    shifted = {"lands": band(33, 36)}
    statuses = validate_deck({"lands": 36}, shifted, curve=CURVE_33, ramp_plus_draw=12)
    assert statuses["lands"] is CategoryStatus.BELOW


def test_floor_never_lowers_the_band_min() -> None:
    # Flat curve -> land_count(0, 20) == 26, well under the band min of 36.
    low_curve = {"0": 1.0}
    statuses = validate_deck(
        {"lands": 36}, {"lands": band(36, 40)}, curve=low_curve, ramp_plus_draw=20
    )
    assert statuses["lands"] is CategoryStatus.IN_RANGE


def test_floor_only_applies_to_lands() -> None:
    bands = {"ramp": band(9, 12)}
    statuses = validate_deck({"ramp": 9}, bands, curve=CURVE_33, ramp_plus_draw=12)
    assert statuses["ramp"] is CategoryStatus.IN_RANGE


def test_curve_and_ramp_plus_draw_must_come_together() -> None:
    bands = {"lands": band(36, 40)}
    with pytest.raises(ValueError, match="together"):
        validate_deck({"lands": 37}, bands, curve=CURVE_33)
    with pytest.raises(ValueError, match="together"):
        validate_deck({"lands": 37}, bands, ramp_plus_draw=12)
