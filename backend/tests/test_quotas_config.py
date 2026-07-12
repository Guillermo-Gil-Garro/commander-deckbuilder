"""Tests for quotas.config: real quotas.yaml parity and loader error paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from quotas.config import (
    CATEGORIES,
    DEFAULT_QUOTAS_PATH,
    QuotaBand,
    QuotasError,
    load_quotas,
)

# Bands approved by Guille (docs/CUOTAS_PROPUESTA.md §3). The real quotas.yaml
# must stay in parity with this table; synergy is ceiling-only (min 0).
EXPECTED_BANDS: dict[str, dict[str, tuple[int, int]]] = {
    "midrange": {
        "lands": (36, 39),
        "ramp": (9, 12),
        "card_draw": (9, 12),
        "removal": (8, 11),
        "board_wipe": (3, 5),
        "wincons": (2, 4),
        "synergy": (0, 28),
    },
    "aggro": {
        "lands": (33, 36),
        "ramp": (7, 10),
        "card_draw": (8, 11),
        "removal": (6, 9),
        "board_wipe": (1, 3),
        "wincons": (2, 4),
        "synergy": (0, 35),
    },
    "control": {
        "lands": (36, 40),
        "ramp": (10, 13),
        "card_draw": (10, 14),
        "removal": (10, 14),
        "board_wipe": (4, 6),
        "wincons": (1, 3),
        "synergy": (0, 20),
    },
    "spellslinger": {
        "lands": (34, 37),
        "ramp": (8, 11),
        "card_draw": (10, 14),
        "removal": (8, 12),
        "board_wipe": (2, 4),
        "wincons": (2, 4),
        "synergy": (0, 30),
    },
    "voltron": {
        "lands": (34, 37),
        "ramp": (8, 11),
        "card_draw": (9, 12),
        "removal": (7, 10),
        "board_wipe": (1, 3),
        "wincons": (0, 2),
        "synergy": (0, 30),
    },
    "graveyard": {
        "lands": (35, 38),
        "ramp": (8, 11),
        "card_draw": (9, 13),
        "removal": (7, 10),
        "board_wipe": (2, 4),
        "wincons": (2, 4),
        "synergy": (0, 30),
    },
    "lands_matter": {
        "lands": (38, 42),
        "ramp": (12, 16),
        "card_draw": (9, 12),
        "removal": (6, 9),
        "board_wipe": (2, 4),
        "wincons": (2, 4),
        "synergy": (0, 18),
    },
}

EXPECTED_DIAL_DELTAS = {
    "lands": 3,
    "ramp": 3,
    "card_draw": 3,
    "removal": 3,
    "board_wipe": 2,
    "synergy": 6,
}


def minimal_payload() -> dict[str, Any]:
    """Smallest valid config payload, mutated by the error-path tests."""
    return {
        "defaults": {"archetype": "midrange"},
        "archetypes": {
            "midrange": {
                "lands": [36, 39],
                "ramp": [9, 12],
                "card_draw": [9, 12],
                "removal": [8, 11],
                "board_wipe": [3, 5],
                "wincons": [2, 4],
                "synergy": 28,
            }
        },
    }


def write_config(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "quotas.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


# --- real quotas.yaml -----------------------------------------------------------


def test_default_path_points_at_repo_root_file() -> None:
    assert DEFAULT_QUOTAS_PATH.name == "quotas.yaml"
    assert DEFAULT_QUOTAS_PATH.is_file()


def test_real_yaml_band_parity_with_approved_table() -> None:
    config = load_quotas()
    assert set(config.archetypes) == set(EXPECTED_BANDS)
    for archetype_name, expected in EXPECTED_BANDS.items():
        archetype = config.archetypes[archetype_name]
        for category in CATEGORIES:
            band = archetype.band(category)
            assert (band.min, band.max) == expected[category], (
                f"{archetype_name}.{category}"
            )


def test_real_yaml_defaults_and_commanders() -> None:
    config = load_quotas()
    assert config.defaults.archetype == "midrange"
    assert config.commanders == {}


def test_real_yaml_dials() -> None:
    config = load_quotas()
    assert {name: spec.delta for name, spec in config.dials.items()} == (
        EXPECTED_DIAL_DELTAS
    )
    # The meme labels live in the YAML as the single source for the UI.
    assert config.dials["lands"].low.startswith("Mamá se llevó las tierras")
    assert config.dials["lands"].high == "¡MOZÁ! ¡TENGO TIERRAS!"
    assert config.dials["removal"].low == "Soy pecifista"
    assert config.dials["synergy"].high == "Technologia!"
    for spec in config.dials.values():
        assert spec.low and spec.high
    assert "wincons" not in config.dials


# --- loader error paths -----------------------------------------------------------


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(QuotasError, match="not found"):
        load_quotas(tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "quotas.yaml"
    path.write_text("defaults: [unclosed", encoding="utf-8")
    with pytest.raises(QuotasError, match="invalid YAML"):
        load_quotas(path)


def test_non_mapping_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "quotas.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(QuotasError, match="must be a mapping"):
        load_quotas(path)


def test_inverted_band_raises(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["archetypes"]["midrange"]["lands"] = [39, 36]
    with pytest.raises(QuotasError, match="inverted band"):
        load_quotas(write_config(tmp_path, payload))


def test_negative_band_raises(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["archetypes"]["midrange"]["wincons"] = [-1, 4]
    with pytest.raises(QuotasError):
        load_quotas(write_config(tmp_path, payload))


def test_unknown_default_archetype_raises(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["defaults"]["archetype"] = "combo"
    with pytest.raises(QuotasError, match="not a defined archetype"):
        load_quotas(write_config(tmp_path, payload))


def test_commander_with_unknown_archetype_raises(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["commanders"] = {"Krenko, Mob Boss": {"archetype": "goblins"}}
    with pytest.raises(QuotasError, match="unknown archetype"):
        load_quotas(write_config(tmp_path, payload))


def test_unknown_category_in_archetype_raises(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["archetypes"]["midrange"]["counterspells"] = [3, 5]
    with pytest.raises(QuotasError):
        load_quotas(write_config(tmp_path, payload))


def test_missing_category_in_archetype_raises(tmp_path: Path) -> None:
    payload = minimal_payload()
    del payload["archetypes"]["midrange"]["removal"]
    with pytest.raises(QuotasError):
        load_quotas(write_config(tmp_path, payload))


def test_unknown_category_in_overrides_raises(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["commanders"] = {
        "Atraxa, Praetors' Voice": {"overrides": {"tutors": [1, 3]}}
    }
    with pytest.raises(QuotasError, match="unknown category"):
        load_quotas(write_config(tmp_path, payload))


def test_unknown_dial_category_raises(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["dials"] = {"tutors": {"delta": 2, "low": "a", "high": "b"}}
    with pytest.raises(QuotasError, match="dial for unknown category"):
        load_quotas(write_config(tmp_path, payload))


# --- synergy is ceiling-only -----------------------------------------------------------


def test_synergy_scalar_becomes_zero_min_band(tmp_path: Path) -> None:
    config = load_quotas(write_config(tmp_path, minimal_payload()))
    assert config.archetypes["midrange"].synergy == QuotaBand(min=0, max=28)


def test_synergy_band_with_nonzero_min_raises(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["archetypes"]["midrange"]["synergy"] = [5, 28]
    with pytest.raises(QuotasError, match="ceiling-only"):
        load_quotas(write_config(tmp_path, payload))


def test_synergy_override_accepts_scalar_ceiling(tmp_path: Path) -> None:
    payload = minimal_payload()
    payload["commanders"] = {"Radha": {"overrides": {"synergy": 32}}}
    config = load_quotas(write_config(tmp_path, payload))
    assert config.commanders["Radha"].overrides["synergy"] == QuotaBand(min=0, max=32)
