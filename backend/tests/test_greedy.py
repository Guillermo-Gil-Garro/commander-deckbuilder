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
from selector.staples import StaplesConfig


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


# ── staples y correcciones de sesgo del score (COMPARATIVA_EDHREC_B4) ───────


def staples_fixture() -> StaplesConfig:
    return StaplesConfig.model_validate(
        {
            "auto_includes": [
                {"name": "Sol Ring", "condition": "always"},
                {
                    "name": "Arcane Signet",
                    "condition": "multicolor_or_listed_mono",
                    "mono_exceptions": ["Urza, Lord High Artificer"],
                },
            ],
        }
    )


def add_staple_cards(pool: PoolIndex) -> None:
    for name, cmc in (("Sol Ring", 1.0), ("Arcane Signet", 2.0)):
        card = make_card(
            name, mana_cost=f"{{{int(cmc)}}}", cmc=cmc, type_line="Artifact",
            color_identity=[],
        )
        pool.by_name[card["name"]] = card
        TAGS[name] = {"ramp"}


def build_with(
    pool: PoolIndex,
    recs: list[Rec],
    *,
    commander: str = "Boss Goblin",
    staples: StaplesConfig | None = None,
    banned: set[str] = frozenset(),
    weights: ScoreWeights = ScoreWeights(),
    bands: dict[str, QuotaBand] | None = None,
) -> GreedyResult:
    return build_deck_greedy(
        commander,
        pool=pool,
        recommendations=recs,
        bands=bands if bands is not None else bands_fixture(),
        tagger=tagger,
        banned_names=banned,
        watchlist_names=set(),
        weights=weights,
        staples=staples,
    )


def test_sol_ring_always_present_signet_not_in_plain_mono() -> None:
    pool, recs, _ = build_inputs()
    add_staple_cards(pool)
    result = build_with(pool, recs, staples=staples_fixture())
    entry = next(e for e in result.mainboard if e.name == "Sol Ring")
    assert entry.reason == "staple (auto-include)"
    assert entry.slot == "ramp"
    # Mono-red without artifact-theme exception: no Arcane Signet.
    assert "Arcane Signet" not in {e.name for e in result.mainboard}
    # The staple counts toward its quota category like any other card.
    ramp_members = [e for e in result.mainboard if "ramp" in e.categories]
    assert result.counts["ramp"] == len(ramp_members)
    assert result.counts["ramp"] <= bands_fixture()["ramp"].max


def test_signet_enters_multicolor_deck() -> None:
    pool, recs, _ = build_inputs()
    add_staple_cards(pool)
    boss = make_card(
        "Two Color Boss",
        type_line="Legendary Creature — Elemental",
        color_identity=["R", "G"],
    )
    pool.by_name[boss["name"]] = boss
    result = build_with(
        pool, recs, commander="Two Color Boss", staples=staples_fixture()
    )
    entry = next(e for e in result.mainboard if e.name == "Arcane Signet")
    assert entry.reason == "staple (auto-include)"
    assert "Sol Ring" in {e.name for e in result.mainboard}


def test_signet_enters_listed_mono_exception_commander() -> None:
    pool, recs, _ = build_inputs()
    add_staple_cards(pool)
    urza = make_card(
        "Urza, Lord High Artificer",
        type_line="Legendary Creature — Human Artificer",
    )
    pool.by_name[urza["name"]] = urza
    result = build_with(
        pool, recs, commander="Urza, Lord High Artificer", staples=staples_fixture()
    )
    entry = next(e for e in result.mainboard if e.name == "Arcane Signet")
    assert entry.reason == "staple (auto-include)"


def test_banlist_beats_auto_include() -> None:
    pool, recs, _ = build_inputs()
    add_staple_cards(pool)
    result = build_with(
        pool, recs, staples=staples_fixture(), banned={"Sol Ring"}
    )
    all_names = {e.name for e in result.mainboard} | {e.name for e in result.maybeboard}
    assert "Sol Ring" not in all_names


def test_auto_include_missing_from_pool_raises() -> None:
    pool, recs, _ = build_inputs()
    config = StaplesConfig.model_validate(
        {"auto_includes": [{"name": "Ghost Card", "condition": "always"}]}
    )
    with pytest.raises(SelectorError, match="Ghost Card"):
        build_with(pool, recs, staples=config)


def test_staples_none_and_empty_config_are_identical() -> None:
    pool, recs, _ = build_inputs()
    base = build(pool, recs)  # staples omitted: legacy call signature
    pool2, recs2, _ = build_inputs()
    empty = build_with(pool2, recs2, staples=StaplesConfig())
    assert [(e.name, e.count) for e in base.mainboard] == [
        (e.name, e.count) for e in empty.mainboard
    ]
    assert [e.name for e in base.maybeboard] == [e.name for e in empty.maybeboard]


def test_preferred_boost_applies_only_on_color_match() -> None:
    pool, recs, _ = build_inputs()
    matching = StaplesConfig.model_validate(
        {"preferred": [{"name": "Synergy 119", "colors_any": ["R"], "boost": 5.0}]}
    )
    result = build_with(pool, recs, staples=matching)
    entry = next(e for e in result.mainboard if e.name == "Synergy 119")
    assert entry.score == pytest.approx((0.8 - 0.119) + 0.5 + 5.0)

    pool2, recs2, _ = build_inputs()
    off_color = StaplesConfig.model_validate(
        {"preferred": [{"name": "Synergy 119", "colors_any": ["U"], "boost": 5.0}]}
    )
    result2 = build_with(pool2, recs2, staples=off_color)
    assert "Synergy 119" not in {e.name for e in result2.mainboard}


def test_score_weights_clamp_negative_synergy() -> None:
    # Informe B4: Sol Ring synergy -0.15, inclusion 0.60 -> antes 0.45, ahora 0.60.
    assert ScoreWeights().score(-0.15, 0.60) == pytest.approx(0.60)
    assert ScoreWeights(clamp_negative_synergy=False).score(-0.15, 0.60) == pytest.approx(0.45)
    assert ScoreWeights().score(0.40, 0.50) == pytest.approx(0.90)


def test_negative_synergy_no_longer_lowers_deck_score() -> None:
    pool, recs, _ = build_inputs()
    staple = make_card("Generic Staple")
    pool.by_name[staple["name"]] = staple
    recs = recs + [Rec(name="Generic Staple", synergy=-0.15, inclusion=1.5)]
    result = build(pool, recs)
    entry = next(e for e in result.mainboard if e.name == "Generic Staple")
    assert entry.score == pytest.approx(1.5)

    legacy = build_with(
        pool, recs, weights=ScoreWeights(clamp_negative_synergy=False)
    )
    legacy_entry = next(e for e in legacy.mainboard if e.name == "Generic Staple")
    assert legacy_entry.score == pytest.approx(1.35)


def test_filler_tiebreak_prefers_cheaper_cmc() -> None:
    pool, recs, _ = build_inputs()
    pricey = make_card("Aaa Pricey Tie", cmc=5.0)
    budget = make_card("Zzz Budget Tie", cmc=1.0)
    pool.by_name[pricey["name"]] = pricey
    pool.by_name[budget["name"]] = budget
    recs = recs + [
        Rec(name="Aaa Pricey Tie", synergy=3.0, inclusion=0.5),
        Rec(name="Zzz Budget Tie", synergy=3.0, inclusion=0.5),
    ]
    bands = bands_fixture()
    bands["synergy"] = QuotaBand(min=0, max=1)  # room for exactly one of the pair
    result = build(pool, recs, bands=bands)
    names = {e.name for e in result.mainboard}
    # Equal score: CMC decides (cheaper first), beating the alphabetical order.
    assert "Zzz Budget Tie" in names
    assert "Aaa Pricey Tie" not in names


def test_weak_nonbasic_land_never_displaces_basics() -> None:
    pool, recs, _ = build_inputs()
    weak = make_card("Weak Tapland", mana_cost="", cmc=0.0, type_line="Land")
    pool.by_name[weak["name"]] = weak
    TAGS["Weak Tapland"] = {"lands"}
    recs = recs + [Rec(name="Weak Tapland", synergy=-1.0, inclusion=0.04)]
    result = build(pool, recs)
    names = {e.name for e in result.mainboard}
    assert "Weak Tapland" not in names  # score 0.04 < floor 0.05: a basic enters
    assert "Utility Land" in names  # good non-basics still enter

    permissive = build_with(
        pool, recs, weights=ScoreWeights(land_score_floor=0.0)
    )
    assert "Weak Tapland" in {e.name for e in permissive.mainboard}


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
