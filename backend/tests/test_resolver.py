"""Tests for quotas.resolver: layer precedence and dial semantics."""

from __future__ import annotations

from typing import Any

import pytest

from quotas.config import CATEGORIES, QuotaBand, QuotasConfig, QuotasError
from quotas.resolver import resolve_bands

MIDRANGE = {
    "lands": [36, 39],
    "ramp": [9, 12],
    "card_draw": [9, 12],
    "removal": [8, 11],
    "board_wipe": [3, 5],
    "wincons": [2, 4],
    "synergy": 28,
}

AGGRO = {
    "lands": [33, 36],
    "ramp": [7, 10],
    "card_draw": [8, 11],
    "removal": [6, 9],
    "board_wipe": [1, 3],
    "wincons": [2, 4],
    "synergy": 35,
}


def make_config(**extra: Any) -> QuotasConfig:
    payload: dict[str, Any] = {
        "defaults": {"archetype": "midrange"},
        "archetypes": {"midrange": MIDRANGE, "aggro": AGGRO},
        "dials": {
            "lands": {"delta": 3, "low": "l", "high": "h"},
            "board_wipe": {"delta": 2, "low": "l", "high": "h"},
            "synergy": {"delta": 6, "low": "l", "high": "h"},
        },
        "commanders": {
            "Krenko, Mob Boss": {"archetype": "aggro"},
            "Atraxa, Praetors' Voice": {
                "archetype": "midrange",
                "overrides": {"card_draw": [10, 14]},
            },
            "Radha, Heart of Keld": {"overrides": {"synergy": 16}},
        },
    }
    payload.update(extra)
    return QuotasConfig.model_validate(payload)


def as_tuples(bands: dict[str, QuotaBand]) -> dict[str, tuple[int, int]]:
    return {category: (band.min, band.max) for category, band in bands.items()}


# --- layer precedence -----------------------------------------------------------


def test_no_commander_uses_default_archetype() -> None:
    bands = resolve_bands(make_config())
    assert set(bands) == set(CATEGORIES)
    assert as_tuples(bands) == {
        "lands": (36, 39),
        "ramp": (9, 12),
        "card_draw": (9, 12),
        "removal": (8, 11),
        "board_wipe": (3, 5),
        "wincons": (2, 4),
        "synergy": (0, 28),
    }


def test_unlisted_commander_falls_back_to_default_archetype() -> None:
    config = make_config()
    assert resolve_bands(config, "Unknown Commander") == resolve_bands(config)


def test_listed_commander_uses_its_archetype() -> None:
    bands = resolve_bands(make_config(), "Krenko, Mob Boss")
    assert as_tuples(bands)["lands"] == (33, 36)
    assert as_tuples(bands)["synergy"] == (0, 35)


def test_commander_override_beats_archetype_band() -> None:
    bands = resolve_bands(make_config(), "Atraxa, Praetors' Voice")
    assert (bands["card_draw"].min, bands["card_draw"].max) == (10, 14)
    # Only that category is overridden; the rest stays midrange.
    assert (bands["removal"].min, bands["removal"].max) == (8, 11)


def test_override_without_archetype_applies_on_default() -> None:
    bands = resolve_bands(make_config(), "Radha, Heart of Keld")
    assert (bands["synergy"].min, bands["synergy"].max) == (0, 16)
    assert (bands["lands"].min, bands["lands"].max) == (36, 39)


# --- dials -----------------------------------------------------------


def test_dial_low_and_high_shift_whole_band() -> None:
    config = make_config()
    low = resolve_bands(config, dials={"lands": "low"})
    high = resolve_bands(config, dials={"lands": "high"})
    assert (low["lands"].min, low["lands"].max) == (33, 36)
    assert (high["lands"].min, high["lands"].max) == (39, 42)


def test_dial_center_and_none_are_noops() -> None:
    config = make_config()
    baseline = resolve_bands(config)
    assert resolve_bands(config, dials={"lands": "center"}) == baseline
    assert resolve_bands(config, dials={"lands": None}) == baseline


def test_synergy_dial_moves_only_the_ceiling() -> None:
    config = make_config()
    low = resolve_bands(config, dials={"synergy": "low"})
    high = resolve_bands(config, dials={"synergy": "high"})
    assert (low["synergy"].min, low["synergy"].max) == (0, 22)
    assert (high["synergy"].min, high["synergy"].max) == (0, 34)


def test_dial_applies_after_commander_override() -> None:
    bands = resolve_bands(
        make_config(), "Radha, Heart of Keld", dials={"synergy": "high"}
    )
    assert (bands["synergy"].min, bands["synergy"].max) == (0, 22)  # 16 + 6


def test_dial_clamps_at_zero() -> None:
    bands = resolve_bands(
        make_config(), "Krenko, Mob Boss", dials={"board_wipe": "low"}
    )
    # aggro board_wipe [1, 3] shifted by -2 clamps the min at 0.
    assert (bands["board_wipe"].min, bands["board_wipe"].max) == (0, 1)


def test_dial_on_category_without_spec_raises() -> None:
    with pytest.raises(QuotasError, match="no dial defined"):
        resolve_bands(make_config(), dials={"wincons": "high"})


def test_invalid_dial_position_raises() -> None:
    with pytest.raises(QuotasError, match="invalid dial position"):
        resolve_bands(make_config(), dials={"lands": "max"})


def test_resolution_does_not_mutate_config() -> None:
    config = make_config()
    resolve_bands(config, "Krenko, Mob Boss", dials={"lands": "low"})
    assert as_tuples(resolve_bands(config))["lands"] == (36, 39)
