"""Tests for selector.staples: loader, auto-include resolution and boosts."""

from __future__ import annotations

from pathlib import Path

import pytest

from selector.staples import (
    DEFAULT_PREFERRED_BOOST,
    DEFAULT_STAPLES_PATH,
    StaplesConfig,
    StaplesError,
    boost_for,
    load_staples,
    preferred_boosts,
    resolve_auto_includes,
)


def write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "staples.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def config_fixture() -> StaplesConfig:
    return StaplesConfig.model_validate(
        {
            "auto_includes": [
                {"name": "Sol Ring", "condition": "always"},
                {
                    "name": "Arcane Signet",
                    "condition": "multicolor_or_listed_mono",
                    "mono_exceptions": [
                        "Urza, Lord High Artificer",
                        "Emry, Lurker of the Loch",
                    ],
                },
            ],
            "preferred": [
                {"name": "Mana Drain", "colors_any": ["U"]},
                {"name": "Swords to Plowshares", "colors_any": ["W"], "boost": 0.5},
                {"name": "Solemn Simulacrum", "colors_any": []},
            ],
        }
    )


# ── loader ───────────────────────────────────────────────────────────────────


def test_real_staples_yaml_loads_with_the_agreed_decision() -> None:
    config = load_staples(DEFAULT_STAPLES_PATH)
    by_name = {s.name: s for s in config.auto_includes}
    assert by_name["Sol Ring"].condition == "always"
    signet = by_name["Arcane Signet"]
    assert signet.condition == "multicolor_or_listed_mono"
    assert "Urza, Lord High Artificer" in signet.mono_exceptions
    assert "Emry, Lurker of the Loch" in signet.mono_exceptions
    assert config.preferred == ()  # Guille fills this list from another session


def test_missing_file_raises() -> None:
    with pytest.raises(StaplesError, match="not found"):
        load_staples("no/such/staples.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "auto_includes: [unclosed")
    with pytest.raises(StaplesError, match="invalid YAML"):
        load_staples(path)


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(StaplesError, match="mapping"):
        load_staples(path)


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "auto_includes: []\npreferred: []\nextra_key: 1\n")
    with pytest.raises(StaplesError, match="extra_key"):
        load_staples(path)


def test_unknown_condition_is_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "auto_includes:\n  - name: Sol Ring\n    condition: sometimes\npreferred: []\n",
    )
    with pytest.raises(StaplesError, match="condition"):
        load_staples(path)


def test_mono_exceptions_forbidden_for_always(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "auto_includes:\n"
        "  - name: Sol Ring\n"
        "    condition: always\n"
        "    mono_exceptions: [Somebody]\n"
        "preferred: []\n",
    )
    with pytest.raises(StaplesError, match="mono_exceptions"):
        load_staples(path)


def test_unknown_color_and_bad_boost_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "auto_includes: []\npreferred:\n  - name: Mana Drain\n    colors_any: [X]\n",
    )
    with pytest.raises(StaplesError, match="unknown colors"):
        load_staples(path)
    path = write_yaml(
        tmp_path,
        "auto_includes: []\npreferred:\n"
        "  - name: Mana Drain\n    colors_any: [U]\n    boost: -1.0\n",
    )
    with pytest.raises(StaplesError, match="boost"):
        load_staples(path)


def test_duplicate_names_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "auto_includes:\n"
        "  - name: Sol Ring\n    condition: always\n"
        "  - name: Sol Ring\n    condition: always\n"
        "preferred: []\n",
    )
    with pytest.raises(StaplesError, match="duplicated names"):
        load_staples(path)


def test_empty_sections_are_valid(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "auto_includes: []\npreferred: []\n")
    config = load_staples(path)
    assert config.auto_includes == ()
    assert config.preferred == ()


# ── resolve_auto_includes ────────────────────────────────────────────────────


def test_always_applies_to_every_identity() -> None:
    config = config_fixture()
    for identity in ([], ["R"], ["W", "U"], ["W", "U", "B", "R", "G"]):
        names = resolve_auto_includes(config, identity, "Whoever", banned_names=set())
        assert "Sol Ring" in names, identity


def test_signet_multicolor_yes_mono_no_exception_yes() -> None:
    config = config_fixture()
    multicolor = resolve_auto_includes(config, ["U", "R"], "Niv-Mizzet, Parun", set())
    assert "Arcane Signet" in multicolor
    mono_red = resolve_auto_includes(config, ["R"], "Krenko, Mob Boss", set())
    assert "Arcane Signet" not in mono_red
    urza = resolve_auto_includes(config, ["U"], "Urza, Lord High Artificer", set())
    assert "Arcane Signet" in urza
    emry = resolve_auto_includes(config, ["U"], "Emry, Lurker of the Loch", set())
    assert "Arcane Signet" in emry
    colorless = resolve_auto_includes(config, [], "Kozilek, the Great Distortion", set())
    assert "Arcane Signet" not in colorless


def test_banlist_always_beats_auto_includes() -> None:
    config = config_fixture()
    names = resolve_auto_includes(
        config, ["U", "R"], "Niv-Mizzet, Parun", banned_names={"Sol Ring"}
    )
    assert "Sol Ring" not in names
    assert "Arcane Signet" in names  # only the banned staple drops


# ── preferred_boosts / boost_for ─────────────────────────────────────────────


def test_boost_applies_only_on_color_match() -> None:
    config = config_fixture()
    blue = preferred_boosts(config, ["U", "R"])
    assert blue["Mana Drain"] == DEFAULT_PREFERRED_BOOST
    assert "Swords to Plowshares" not in blue
    red = preferred_boosts(config, ["R"])
    assert "Mana Drain" not in red
    white = preferred_boosts(config, ["W", "G"])
    assert white["Swords to Plowshares"] == 0.5


def test_empty_colors_any_matches_every_deck() -> None:
    config = config_fixture()
    for identity in ([], ["R"], ["W", "U", "B", "R", "G"]):
        boosts = preferred_boosts(config, identity)
        assert boosts["Solemn Simulacrum"] == DEFAULT_PREFERRED_BOOST, identity


def test_boost_for_matches_face_names() -> None:
    boosts = {"Fire": 0.4, "Wear // Tear": 0.2}
    assert boost_for(boosts, "Fire // Ice") == 0.4
    assert boost_for(boosts, "Wear // Tear") == 0.2
    assert boost_for(boosts, "Lightning Bolt") == 0.0
