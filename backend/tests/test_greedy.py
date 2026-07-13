"""Tests for selector.greedy with a synthetic mini-pool and recommendations."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from quotas.config import QuotaBand
from quotas.validator import CategoryStatus
from selector.greedy import (
    DECK_SIZE,
    GreedyResult,
    PoolIndex,
    ScoreWeights,
    SelectorError,
    build_deck_greedy,
)


@dataclass
class Rec:
    name: str
    synergy: float
    inclusion: float


def make_card(
    name: str,
    *,
    mana_cost: str = "{1}{R}",
    cmc: float = 2.0,
    type_line: str = "Creature — Goblin",
    color_identity: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "mana_cost": mana_cost,
        "cmc": cmc,
        "type_line": type_line,
        "oracle_text": "",
        "colors": ["R"],
        "color_identity": ["R"] if color_identity is None else color_identity,
        # Extra field on purpose: the selector must tolerate unknown fields.
        "some_future_field": True,
    }


def bands_fixture() -> dict[str, QuotaBand]:
    return {
        "lands": QuotaBand(min=10, max=40),
        "ramp": QuotaBand(min=2, max=4),
        "card_draw": QuotaBand(min=2, max=4),
        "removal": QuotaBand(min=2, max=4),
        "board_wipe": QuotaBand(min=1, max=2),
        "wincons": QuotaBand(min=1, max=2),
        "synergy": QuotaBand(min=0, max=90),
    }


TAGS: dict[str, set[str]] = {}


def tagger(name: str) -> set[str]:
    return set(TAGS.get(name, set()))


def build_inputs(
    n_synergy: int = 120,
) -> tuple[PoolIndex, list[Rec], dict[str, set[str]]]:
    """Mono-red commander, tagged specialists plus a sea of synergy filler."""
    # The real tagger derives "lands" from the type_line; mirror that here.
    tags: dict[str, set[str]] = {"Mountain": {"lands"}}
    cards = [
        make_card("Boss Goblin", type_line="Legendary Creature — Goblin"),
        make_card("Mountain", mana_cost="", cmc=0.0, type_line="Basic Land — Mountain"),
    ]
    recs: list[Rec] = []

    def add(name: str, categories: set[str], synergy: float, **kwargs) -> None:
        cards.append(make_card(name, **kwargs))
        tags[name] = categories
        recs.append(Rec(name=name, synergy=synergy, inclusion=0.5))

    for i in range(4):
        add(f"Ramp {i}", {"ramp"}, 0.4 - i * 0.01)
        add(f"Draw {i}", {"card_draw"}, 0.4 - i * 0.01)
        add(f"Removal {i}", {"removal"}, 0.4 - i * 0.01)
    for i in range(3):
        add(f"Wipe {i}", {"board_wipe"}, 0.3 - i * 0.01)
        add(f"Wincon {i}", {"wincons"}, 0.3 - i * 0.01)
    add("Utility Land", {"lands"}, 0.9, mana_cost="", cmc=0.0, type_line="Land")
    for i in range(n_synergy):
        add(f"Synergy {i:03d}", set(), 0.8 - i * 0.001)

    TAGS.clear()
    TAGS.update(tags)
    return PoolIndex(cards), recs, tags


def build(
    pool: PoolIndex,
    recs: list[Rec],
    *,
    bands: dict[str, QuotaBand] | None = None,
    banned: set[str] = frozenset(),
    watchlist: set[str] = frozenset(),
) -> GreedyResult:
    return build_deck_greedy(
        "Boss Goblin",
        pool=pool,
        recommendations=recs,
        bands=bands if bands is not None else bands_fixture(),
        tagger=tagger,
        banned_names=banned,
        watchlist_names=watchlist,
    )


def test_exactly_99_cards() -> None:
    pool, recs, _ = build_inputs()
    result = build(pool, recs)
    assert result.total_cards == DECK_SIZE


def test_minimums_respected_when_candidates_suffice() -> None:
    pool, recs, _ = build_inputs()
    result = build(pool, recs)
    bands = bands_fixture()
    for category in ("ramp", "card_draw", "removal", "board_wipe", "wincons"):
        assert result.counts.get(category, 0) >= bands[category].min, category


def test_maximums_and_synergy_ceiling_not_exceeded() -> None:
    pool, recs, _ = build_inputs()
    bands = bands_fixture()
    bands["synergy"] = QuotaBand(min=0, max=20)  # tight ceiling, lots of filler
    result = build(pool, recs, bands=bands)
    for category, band in bands.items():
        if category == "lands":
            continue  # the Karsten floor may legitimately exceed the band max
        assert result.counts.get(category, 0) <= band.max, category
    assert result.total_cards == DECK_SIZE  # ceiling overflow became basics


def test_banned_and_watchlist_excluded_everywhere() -> None:
    pool, recs, _ = build_inputs()
    banned = {"Synergy 000"}
    watchlist = {"Synergy 001"}
    result = build(pool, recs, banned=banned, watchlist=watchlist)
    all_names = {e.name for e in result.mainboard} | {e.name for e in result.maybeboard}
    assert not (banned | watchlist) & all_names


def test_commander_not_in_own_deck() -> None:
    pool, recs, _ = build_inputs()
    recs = recs + [Rec(name="Boss Goblin", synergy=9.9, inclusion=1.0)]
    result = build(pool, recs)
    assert "Boss Goblin" not in {e.name for e in result.mainboard}


def test_out_of_identity_candidates_rejected() -> None:
    pool, recs, _ = build_inputs()
    blue = make_card("Blue Intruder", color_identity=["U"])
    pool.by_name[blue["name"]] = blue
    recs = recs + [Rec(name="Blue Intruder", synergy=9.9, inclusion=1.0)]
    result = build(pool, recs)
    assert "Blue Intruder" not in {e.name for e in result.mainboard}


def test_basics_complete_lands_and_match_identity() -> None:
    pool, recs, _ = build_inputs()
    result = build(pool, recs)
    lands = [e for e in result.mainboard if "lands" in e.categories]
    total_lands = sum(e.count for e in lands)
    assert total_lands == result.lands_target
    assert total_lands >= result.karsten_floor
    basics = [e for e in lands if e.name == "Mountain"]
    assert len(basics) == 1 and basics[0].count > 0
    # Mono-red identity: no off-color basics.
    assert not any(
        e.name in ("Plains", "Island", "Swamp", "Forest") for e in lands
    )


def test_multi_category_card_counts_in_all_its_categories() -> None:
    pool, recs, tags = build_inputs(n_synergy=100)
    hybrid = make_card("Hybrid Engine")
    pool.by_name[hybrid["name"]] = hybrid
    TAGS["Hybrid Engine"] = {"ramp", "card_draw"}
    recs = recs + [Rec(name="Hybrid Engine", synergy=9.9, inclusion=1.0)]
    result = build(pool, recs)
    entry = next(e for e in result.mainboard if e.name == "Hybrid Engine")
    assert entry.categories == ("card_draw", "ramp")
    only_ramp = [e for e in result.mainboard if "ramp" in e.categories]
    only_draw = [e for e in result.mainboard if "card_draw" in e.categories]
    assert result.counts["ramp"] == len(only_ramp)
    assert result.counts["card_draw"] == len(only_draw)


def test_determinism_and_alphabetical_tiebreak() -> None:
    pool, recs, _ = build_inputs()
    first = build(pool, recs)
    second = build(pool, recs)
    assert [e.name for e in first.mainboard] == [e.name for e in second.mainboard]
    assert [e.name for e in first.maybeboard] == [e.name for e in second.maybeboard]
    # Equal-score synergy pair: alphabetical order decides.
    tie_a = make_card("AAA Tie")
    tie_b = make_card("ZZZ Tie")
    pool.by_name[tie_a["name"]] = tie_a
    pool.by_name[tie_b["name"]] = tie_b
    tied = [Rec(name="ZZZ Tie", synergy=2.0, inclusion=0.5), Rec(name="AAA Tie", synergy=2.0, inclusion=0.5)]
    result = build(pool, tied + recs)
    names = [e.name for e in result.mainboard]
    assert names.index("AAA Tie") < names.index("ZZZ Tie")


def test_land_with_extra_category_respects_that_max() -> None:
    # A cycling-style land also tagged card_draw must not blow the draw max.
    pool, recs, _ = build_inputs()
    for i in range(6):
        land = make_card(
            f"Cycling Land {i}", mana_cost="", cmc=0.0, type_line="Land"
        )
        pool.by_name[land["name"]] = land
        TAGS[land["name"]] = {"lands", "card_draw"}
        recs = recs + [Rec(name=land["name"], synergy=5.0, inclusion=1.0)]
    result = build(pool, recs)
    assert result.counts["card_draw"] <= bands_fixture()["card_draw"].max
    assert result.total_cards == DECK_SIZE


def test_basics_never_duplicated_nor_in_maybeboard() -> None:
    pool, recs, _ = build_inputs()
    # EDHREC does recommend basics; they must only enter via the distribution.
    recs = recs + [Rec(name="Mountain", synergy=5.0, inclusion=1.0)]
    result = build(pool, recs)
    mountain_entries = [e for e in result.mainboard if e.name == "Mountain"]
    assert len(mountain_entries) == 1
    assert "Mountain" not in {e.name for e in result.maybeboard}


def test_validator_statuses_reported() -> None:
    pool, recs, _ = build_inputs()
    result = build(pool, recs)
    assert set(result.statuses) == set(bands_fixture())
    assert result.statuses["ramp"] is CategoryStatus.IN_RANGE


def test_score_weights_change_ordering() -> None:
    pool, recs, _ = build_inputs()
    inclusion_only = build_deck_greedy(
        "Boss Goblin",
        pool=pool,
        recommendations=recs,
        bands=bands_fixture(),
        tagger=tagger,
        banned_names=set(),
        watchlist_names=set(),
        weights=ScoreWeights(synergy=0.0, inclusion=1.0),
    )
    # All inclusions are 0.5 in the fixture: pure alphabetical order now.
    synergy_names = [
        e.name for e in inclusion_only.mainboard if e.slot == "synergy"
    ]
    assert synergy_names == sorted(synergy_names)


def test_missing_commander_raises() -> None:
    pool, recs, _ = build_inputs()
    with pytest.raises(SelectorError, match="commander not found"):
        build_deck_greedy(
            "Nobody",
            pool=pool,
            recommendations=recs,
            bands=bands_fixture(),
            tagger=tagger,
            banned_names=set(),
            watchlist_names=set(),
        )


def test_otag_tagger_from_tmp_cache(tmp_path) -> None:
    import json

    from selector.provisional_tags import OTAG_TO_CATEGORY, TaggerError, otag_tagger

    for tag in OTAG_TO_CATEGORY:
        (tmp_path / f"{tag}.json").write_text("[]", encoding="utf-8")
    (tmp_path / "ramp.json").write_text(
        json.dumps(["Cultivate", "Growth Spiral // Whatever"]), encoding="utf-8"
    )
    (tmp_path / "draw.json").write_text(json.dumps(["Cultivate"]), encoding="utf-8")

    pool_cards = [
        {"name": "Command Tower", "type_line": "Land"},
        {"name": "Cultivate", "type_line": "Sorcery"},
    ]
    tag = otag_tagger(pool_cards, cache_dir=tmp_path)
    assert tag("Cultivate") == {"ramp", "card_draw"}
    assert tag("Growth Spiral") == {"ramp"}  # face-name match
    assert tag("Command Tower") == {"lands"}
    assert tag("Unknown Card") == set()

    (tmp_path / "ramp.json").unlink()
    with pytest.raises(TaggerError, match="not found"):
        otag_tagger(pool_cards, cache_dir=tmp_path)
