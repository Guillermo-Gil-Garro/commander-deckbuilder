"""Tests for the Karsten land floor (ported from the TFM target_structure tests)."""

from __future__ import annotations

import pytest

from quotas.lands import (
    CURVE_BUCKETS,
    KARSTEN_AVGMV_COEF,
    KARSTEN_LAND_FLOOR_INTERCEPT,
    KARSTEN_RAMP_DRAW_COEF,
    curve_bucket,
    expected_curve_mv,
    karsten_land_floor,
    land_count,
)


def test_karsten_constants_are_preserved() -> None:
    # The TFM regression numbers must survive the port exactly.
    assert KARSTEN_LAND_FLOOR_INTERCEPT == 31.42
    assert KARSTEN_AVGMV_COEF == 3.13
    assert KARSTEN_RAMP_DRAW_COEF == 0.28


# --- curve buckets -------------------------------------------------------------


def test_curve_bucket_collapses_tail() -> None:
    assert curve_bucket(0.0) == "0"
    assert curve_bucket(3) == "3"
    assert curve_bucket(7) == "7+"
    assert curve_bucket(12.0) == "7+"
    assert curve_bucket(None) == "0"
    assert curve_bucket(-2) == "0"


def test_curve_buckets_cover_all_labels() -> None:
    assert CURVE_BUCKETS == ("0", "1", "2", "3", "4", "5", "6", "7+")


def test_expected_curve_mv_collapses_tail() -> None:
    # 7+ contributes with cost 7; weighted mean over the buckets.
    assert expected_curve_mv({"2": 0.5, "7+": 0.5}) == 4.5
    assert expected_curve_mv({"0": 1.0}) == 0.0


# --- Karsten land floor ----------------------------------------------------------


def test_karsten_land_floor_formula() -> None:
    # avg_mv=3, ramp_plus_draw=15 -> 31.42 + 3.13*3 - 0.28*15 = 36.61.
    assert karsten_land_floor(3.0, 15) == pytest.approx(36.61)
    # High ramp_plus_draw over-discounts (the declared simplification).
    assert karsten_land_floor(0.0, 3) < 31.42


def test_land_count_rounds_the_floor() -> None:
    assert land_count(3.0, 15) == 37  # round(36.61)
    assert land_count(3.0, 10) == 38  # round(38.01)
    assert land_count(0.0, 0) == 31  # round(31.42)


def test_land_count_clamps_to_deck_size_and_zero() -> None:
    assert land_count(30.0, 0) == 99  # floor ~125 clamps to the 99-card library
    assert land_count(30.0, 0, deck_size=60) == 60
    assert land_count(0.0, 200) == 0  # absurd discount clamps to zero, never negative


@pytest.mark.parametrize("avg_mv", [2.5, 3.0, 3.5])
@pytest.mark.parametrize("ramp_plus_draw", [10, 15])
def test_land_count_typical_commander_range(avg_mv: float, ramp_plus_draw: int) -> None:
    # Typical Commander curves land in a sane 35-42 band.
    assert 35 <= land_count(avg_mv, ramp_plus_draw) <= 42


def test_land_count_from_expected_curve() -> None:
    # End-to-end: curve fractions -> avg MV -> floor.
    curve = {"1": 0.1, "2": 0.25, "3": 0.25, "4": 0.2, "5": 0.1, "6": 0.05, "7+": 0.05}
    avg_mv = expected_curve_mv(curve)
    assert avg_mv == pytest.approx(3.3)
    assert land_count(avg_mv, 12) == 38  # 31.42 + 3.13*3.3 - 0.28*12 = 38.389
