"""Tests for selector.cp_sat with a synthetic mini-pool (mirrors test_greedy)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from quotas.config import QuotaBand
from selector.cp_sat import CpSatResult, build_deck_cpsat
from selector.greedy import DECK_SIZE, PoolIndex, ScoreWeights
from selector.deck_rules import RulesConfig


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
    oracle_text: str = "",
) -> dict:
    return {
        "name": name,
        "mana_cost": mana_cost,
        "cmc": cmc,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "colors": ["R"],
        "color_identity": ["R"] if color_identity is None else color_identity,
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


def build_inputs(n_synergy: int = 120) -> tuple[PoolIndex, list[Rec]]:
    """Mono-red commander, tagged specialists plus a sea of synergy filler."""
    tags: dict[str, set[str]] = {"Mountain": {"lands"}}
    cards = [
        make_card("Boss Goblin", type_line="Legendary Creature — Goblin"),
        make_card(
            "Mountain", mana_cost="", cmc=0.0, type_line="Basic Land — Mountain"
        ),
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
    add(
        "Utility Land",
        {"lands"},
        0.9,
        mana_cost="",
        cmc=0.0,
        type_line="Land",
        oracle_text="{T}: Add {R}.",
    )
    for i in range(n_synergy):
        add(f"Synergy {i:03d}", set(), 0.8 - i * 0.001)

    TAGS.clear()
    TAGS.update(tags)
    return PoolIndex(cards), recs


def build(
    pool: PoolIndex,
    recs: list[Rec],
    *,
    bands: dict[str, QuotaBand] | None = None,
    banned: set[str] = frozenset(),
    watchlist: set[str] = frozenset(),
) -> CpSatResult:
    return build_deck_cpsat(
        "Boss Goblin",
        pool=pool,
        recommendations=recs,
        bands=bands if bands is not None else bands_fixture(),
        tagger=tagger,
        banned_names=banned,
        watchlist_names=watchlist,
        time_limit_s=10.0,
    )


def test_exactly_99_cards_and_bands_respected() -> None:
    pool, recs = build_inputs()
    result = build(pool, recs)
    bands = bands_fixture()
    assert result.total_cards == DECK_SIZE
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.relaxation_stage == "none"
    for category, band in bands.items():
        n = result.counts.get(category, 0)
        if category == "lands":
            # The Karsten floor may legitimately exceed the band max.
            assert n >= band.min
            continue
        assert band.min <= n <= band.max, category


def test_banned_and_watchlist_excluded_everywhere() -> None:
    pool, recs = build_inputs()
    banned = {"Synergy 000"}
    watchlist = {"Synergy 001"}
    result = build(pool, recs, banned=banned, watchlist=watchlist)
    all_names = {e.name for e in result.mainboard} | {
        e.name for e in result.maybeboard
    }
    assert not (banned | watchlist) & all_names


def test_karsten_floor_respected() -> None:
    pool, recs = build_inputs()
    result = build(pool, recs)
    lands_total = sum(
        e.count for e in result.mainboard if "lands" in e.categories
    )
    assert lands_total >= result.karsten_floor
    assert lands_total >= bands_fixture()["lands"].min


def test_karsten_floor_beats_low_lands_band() -> None:
    # A lands band far below the floor: the fixpoint must raise the minimum.
    pool, recs = build_inputs()
    bands = bands_fixture()
    bands["lands"] = QuotaBand(min=0, max=40)
    result = build(pool, recs, bands=bands)
    lands_total = sum(
        e.count for e in result.mainboard if "lands" in e.categories
    )
    assert result.karsten_floor > 0
    assert lands_total >= result.karsten_floor


def test_infeasible_floors_relax_in_order_and_report() -> None:
    # ramp min 6 but only 4 ramp candidates exist -> the hard floor stage is
    # infeasible; the next stage (soft floors) must solve and be reported.
    pool, recs = build_inputs()
    bands = bands_fixture()
    bands["ramp"] = QuotaBand(min=6, max=8)
    result = build(pool, recs, bands=bands)
    assert result.relaxation_stage == "soft_category_floors"
    assert result.total_cards == DECK_SIZE
    # The unmet floor shows up as an explicit penalty.
    assert result.penalties["soft_floors"]["ramp"]["deficit"] == 2


def test_infeasible_ceilings_relax_further() -> None:
    # Tiny synergy ceiling + tight lands ceiling: even with soft floors the
    # ceilings cannot host 99 cards -> drop_ceilings stage.
    pool, recs = build_inputs(n_synergy=120)
    bands = bands_fixture()
    bands["synergy"] = QuotaBand(min=0, max=5)
    bands["lands"] = QuotaBand(min=10, max=12)
    for cat in ("ramp", "card_draw", "removal", "board_wipe", "wincons"):
        bands[cat] = QuotaBand(min=0, max=2)
    result = build(pool, recs, bands=bands)
    assert result.relaxation_stage == "drop_ceilings"
    assert result.total_cards == DECK_SIZE
    # Karsten/lands floor still holds even under relaxation.
    lands_total = sum(
        e.count for e in result.mainboard if "lands" in e.categories
    )
    assert lands_total >= result.karsten_floor


def test_determinism_two_runs_identical() -> None:
    pool, recs = build_inputs()
    first = build(pool, recs)
    second = build(pool, recs)
    assert [(e.name, e.count) for e in first.mainboard] == [
        (e.name, e.count) for e in second.mainboard
    ]
    assert [e.name for e in first.maybeboard] == [
        e.name for e in second.maybeboard
    ]
    assert first.objective_value == second.objective_value
    assert first.relaxation_stage == second.relaxation_stage


def test_commander_and_off_identity_excluded() -> None:
    pool, recs = build_inputs()
    blue = make_card("Blue Intruder", color_identity=["U"])
    pool.by_name[blue["name"]] = blue
    recs = recs + [
        Rec(name="Blue Intruder", synergy=9.9, inclusion=1.0),
        Rec(name="Boss Goblin", synergy=9.9, inclusion=1.0),
    ]
    result = build(pool, recs)
    names = {e.name for e in result.mainboard}
    assert "Blue Intruder" not in names
    assert "Boss Goblin" not in names


def test_missing_commander_raises() -> None:
    from selector.greedy import SelectorError

    pool, recs = build_inputs()
    with pytest.raises(SelectorError, match="commander not found"):
        build_deck_cpsat(
            "Nobody",
            pool=pool,
            recommendations=recs,
            bands=bands_fixture(),
            tagger=tagger,
            banned_names=set(),
            watchlist_names=set(),
        )


# ── rules.yaml (always/never/prefer) y sesgo del score (COMPARATIVA_EDHREC_B4) ──


def rules_fixture() -> RulesConfig:
    return RulesConfig.model_validate(
        {
            "always": [
                {"name": "Sol Ring", "quota_category": "ramp"},
                {
                    "name": "Arcane Signet",
                    "quota_category": "ramp",
                    "when": {
                        "any_of": [
                            {"color_identity_size": ">=2"},
                            {"commander_in": ["Urza, Lord High Artificer"]},
                        ]
                    },
                },
            ],
            "never": [
                {
                    "name": "Arcane Signet",
                    "when": {
                        "color_identity_size": "<=1",
                        "commander_not_in": ["Urza, Lord High Artificer"],
                    },
                },
            ],
        }
    )


def add_rule_cards(pool: PoolIndex) -> None:
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
    rules: RulesConfig | None = None,
    archetype: str | None = "midrange",
    banned: set[str] = frozenset(),
    weights: ScoreWeights = ScoreWeights(),
    bands: dict[str, QuotaBand] | None = None,
) -> CpSatResult:
    return build_deck_cpsat(
        commander,
        pool=pool,
        recommendations=recs,
        bands=bands if bands is not None else bands_fixture(),
        tagger=tagger,
        banned_names=banned,
        watchlist_names=set(),
        weights=weights,
        rules=rules,
        archetype=archetype,
        time_limit_s=10.0,
    )


def test_sol_ring_forced_signet_never_in_plain_mono() -> None:
    pool, recs = build_inputs()
    add_rule_cards(pool)
    result = build_with(pool, recs, rules=rules_fixture())
    entry = next(e for e in result.mainboard if e.name == "Sol Ring")
    assert entry.reason == "always (rules.yaml)"
    # Never rule: out of the mainboard AND the maybeboard in plain mono.
    all_names = {e.name for e in result.mainboard} | {e.name for e in result.maybeboard}
    assert "Arcane Signet" not in all_names
    # The forced card counts in its category and the band max still holds.
    assert result.counts["ramp"] <= bands_fixture()["ramp"].max


def test_signet_forced_for_listed_mono_exception_commander() -> None:
    pool, recs = build_inputs()
    add_rule_cards(pool)
    urza = make_card(
        "Urza, Lord High Artificer",
        type_line="Legendary Creature — Human Artificer",
    )
    pool.by_name[urza["name"]] = urza
    result = build_with(
        pool, recs, commander="Urza, Lord High Artificer", rules=rules_fixture()
    )
    entry = next(e for e in result.mainboard if e.name == "Arcane Signet")
    assert entry.reason == "always (rules.yaml)"
    assert "Sol Ring" in {e.name for e in result.mainboard}


def test_banlist_beats_always_rule() -> None:
    pool, recs = build_inputs()
    add_rule_cards(pool)
    result = build_with(pool, recs, rules=rules_fixture(), banned={"Sol Ring"})
    all_names = {e.name for e in result.mainboard} | {e.name for e in result.maybeboard}
    assert "Sol Ring" not in all_names


def test_rules_none_and_empty_config_are_identical() -> None:
    pool, recs = build_inputs()
    base = build(pool, recs)  # rules omitted: legacy call signature
    empty = build_with(pool, recs, rules=RulesConfig())
    assert [(e.name, e.count) for e in base.mainboard] == [
        (e.name, e.count) for e in empty.mainboard
    ]
    assert base.objective_value == empty.objective_value


def test_negative_synergy_no_longer_lowers_score() -> None:
    pool, recs = build_inputs()
    staple = make_card("Generic Staple")
    pool.by_name[staple["name"]] = staple
    recs = recs + [Rec(name="Generic Staple", synergy=-0.15, inclusion=1.5)]
    result = build(pool, recs)
    entry = next(e for e in result.mainboard if e.name == "Generic Staple")
    assert entry.score == pytest.approx(1.5)  # max(-0.15, 0) + 1.5


def test_objective_tiebreak_prefers_cheaper_cmc() -> None:
    pool, recs = build_inputs()
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
    # Wide lands ceiling: basics keep the 99 feasible with every ceiling hard.
    bands["lands"] = QuotaBand(min=10, max=90)
    result = build(pool, recs, bands=bands)
    assert result.relaxation_stage == "none"  # ceilings stayed hard
    names = {e.name for e in result.mainboard}
    assert "Zzz Budget Tie" in names
    assert "Aaa Pricey Tie" not in names


def test_weak_nonbasic_land_excluded_in_favor_of_basics() -> None:
    pool, recs = build_inputs()
    weak = make_card("Weak Tapland", mana_cost="", cmc=0.0, type_line="Land")
    pool.by_name[weak["name"]] = weak
    TAGS["Weak Tapland"] = {"lands"}
    recs = recs + [Rec(name="Weak Tapland", synergy=-1.0, inclusion=0.04)]
    result = build(pool, recs)
    names = {e.name for e in result.mainboard}
    assert "Weak Tapland" not in names  # score 0.04 < floor 0.05: x == 0
    assert "Utility Land" in names  # good non-basics still enter
