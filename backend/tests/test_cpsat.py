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
    # EDHREC cardlist headers; only "New Cards" matters to the selector.
    categories: tuple[str, ...] = ()


def make_card(
    name: str,
    *,
    mana_cost: str = "{1}{R}",
    cmc: float = 2.0,
    type_line: str = "Creature — Goblin",
    color_identity: list[str] | None = None,
    oracle_text: str = "",
    price_usd: float | None = None,
) -> dict:
    return {
        "name": name,
        "mana_cost": mana_cost,
        "cmc": cmc,
        "type_line": type_line,
        "oracle_text": oracle_text,
        "colors": ["R"],
        "color_identity": ["R"] if color_identity is None else color_identity,
        "price_usd": price_usd,
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


def test_preferred_land_injected_when_edhrec_omits_it() -> None:
    # The core fix: a preferred dual/fetch is depressed by price and NOT in the
    # EDHREC recommendations, so it must be injected into the candidate pool
    # with its boost as base score. A 0.4-score land beats a basic (score 0) in
    # the objective, so the solver takes it.
    pool, recs = build_inputs()
    dual = make_card("Red Dual", mana_cost="", cmc=0.0, type_line="Land")
    pool.by_name[dual["name"]] = dual
    TAGS["Red Dual"] = {"lands"}  # tagged land, but no Rec for it
    rules = RulesConfig.model_validate(
        {"preferred": [{"name": "Red Dual", "colors_any": ["R"], "boost": 0.4}]}
    )
    assert "Red Dual" not in {r.name for r in recs}  # EDHREC never lists it
    result = build_with(pool, recs, rules=rules)
    entry = next(e for e in result.mainboard if e.name == "Red Dual")
    assert entry.score == pytest.approx(0.4)
    assert entry.reason.startswith("tierra recomendada")


def test_preferred_not_injected_when_color_predicate_misses() -> None:
    # A dual whose two colors are not both in the identity must NOT be injected
    # (color_identity_contains all-of): an off-color dual is not fixing.
    pool, recs = build_inputs()  # mono-red commander
    dual = make_card("Azorius Dual", mana_cost="", cmc=0.0, type_line="Land")
    pool.by_name[dual["name"]] = dual
    TAGS["Azorius Dual"] = {"lands"}
    rules = RulesConfig.model_validate(
        {
            "preferred": [
                {"name": "Azorius Dual", "color_identity_contains": ["W", "U"],
                 "boost": 0.4}
            ]
        }
    )
    result = build_with(pool, recs, rules=rules)
    all_names = {e.name for e in result.mainboard} | {
        e.name for e in result.maybeboard
    }
    assert "Azorius Dual" not in all_names


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


# ── semántica de mínimos: solo no-tierras los cubren (AUDITORIA §5.D.1) ──


def add_multi_land(
    pool: PoolIndex, name: str = "Grim Backwoods Clone", *, categories: set[str],
    synergy: float = 0.9,
) -> Rec:
    """Una tierra multicategoría de score alto (el 'truco' de Meren)."""
    card = make_card(
        name, mana_cost="", cmc=0.0, type_line="Land",
        oracle_text="{T}: Add {R}.",
    )
    pool.by_name[card["name"]] = card
    TAGS[name] = {"lands"} | categories
    return Rec(name=name, synergy=synergy, inclusion=0.5)


def test_multicategory_land_cannot_satisfy_a_spell_minimum() -> None:
    # Sin hechizos de card_draw suficientes, una tierra [lands/card_draw] de
    # score altísimo NO puede cubrir el mínimo: el suelo duro es infactible y
    # el selector relaja y lo reporta, en vez de dar la cuota por cumplida.
    pool, recs = build_inputs()
    recs = [r for r in recs if not r.name.startswith("Draw ")]
    for i in range(4):
        TAGS.pop(f"Draw {i}", None)
    recs = recs + [add_multi_land(pool, categories={"card_draw"})]
    result = build(pool, recs)

    assert "Grim Backwoods Clone" in {e.name for e in result.mainboard}
    # La tierra entra (score 0.9) y cuenta en el conteo informativo...
    assert result.counts["card_draw"] == 1
    # ...pero no cubre el mínimo: cuota relajada y déficit reportado.
    assert result.relaxation_stage == "soft_category_floors"
    assert result.penalties["soft_floors"]["card_draw"]["deficit"] == 2


def test_spell_minimum_ignores_land_and_is_met_by_nonlands() -> None:
    # Con hechizos disponibles, el mínimo se cumple con ellos y la tierra
    # multicategoría es un extra: no hay relajación ninguna.
    pool, recs = build_inputs()
    recs = recs + [add_multi_land(pool, categories={"card_draw"})]
    result = build(pool, recs)
    assert result.relaxation_stage == "none"
    draws = [
        e for e in result.mainboard if "card_draw" in e.categories
    ]
    nonland_draws = [e for e in draws if "lands" not in e.categories]
    assert len(nonland_draws) >= bands_fixture()["card_draw"].min


def test_multicategory_land_still_consumes_the_category_maximum() -> None:
    # La otra cara: la tierra sigue contando para el MÁXIMO (no se puede
    # reventar el techo por la puerta de atrás).
    pool, recs = build_inputs()
    lands = [
        add_multi_land(pool, f"Wipe Land {i}", categories={"board_wipe"}, synergy=0.95)
        for i in range(3)
    ]
    result = build(pool, recs + lands)
    band = bands_fixture()["board_wipe"]
    assert result.counts["board_wipe"] <= band.max
    # Y el mínimo lo sigue cubriendo un hechizo, no las tierras.
    nonland_wipes = [
        e
        for e in result.mainboard
        if "board_wipe" in e.categories and "lands" not in e.categories
    ]
    assert len(nonland_wipes) >= band.min


# ── razones por carta (AUDITORIA §5.D.3) ──


def test_every_mainboard_entry_has_a_nonempty_reason() -> None:
    pool, recs = build_inputs()
    add_rule_cards(pool)
    result = build_with(pool, recs, rules=rules_fixture())
    for entry in result.mainboard:
        assert entry.reason and entry.reason.strip(), entry.name
    assert all(e.reason.strip() for e in result.maybeboard)
    # Nadie se queda con el "cp-sat, score X" genérico de antes.
    assert not any("cp-sat," in e.reason for e in result.mainboard)


def test_reasons_use_the_greedy_vocabulary() -> None:
    pool, recs = build_inputs()
    result = build(pool, recs)
    by_name = {e.name: e for e in result.mainboard}

    # Cuota: el mejor ramp cubre el mínimo de ramp.
    assert by_name["Ramp 0"].reason.startswith("ramp (cuota), score")
    # Relleno: la synergy de más score no cubre ningún mínimo.
    assert by_name["Synergy 000"].reason.startswith("relleno por score")
    # Tierra recomendada (sin otras categorías: sin coletilla).
    assert by_name["Utility Land"].reason == "tierra recomendada, score 1.40"
    # Básicas: razón honesta (las coloca el solver, no un reparto por pips).
    assert by_name["Mountain"].reason.startswith("básica x")
    assert "solver" in by_name["Mountain"].reason


def test_multicategory_land_reason_states_it_does_not_cover_the_minimum() -> None:
    pool, recs = build_inputs()
    recs = recs + [add_multi_land(pool, "Draw Land", categories={"card_draw"})]
    result = build(pool, recs)
    entry = next(e for e in result.mainboard if e.name == "Draw Land")
    assert entry.reason == (
        "tierra recomendada, score 1.40 (cuenta en card_draw, "
        "pero no cubre su mínimo)"
    )


def test_quota_reason_covers_categories_the_greedy_never_fills() -> None:
    # protection no está en FILL_ORDER (el greedy no la rellena): aun así la
    # carta que cubre su mínimo debe explicarse como cuota, no como relleno.
    pool, recs = build_inputs()
    guard = make_card("Lightning Boots", type_line="Artifact — Equipment")
    pool.by_name[guard["name"]] = guard
    TAGS["Lightning Boots"] = {"protection"}
    bands = bands_fixture()
    bands["protection"] = QuotaBand(min=1, max=3)
    result = build(
        pool, recs + [Rec(name="Lightning Boots", synergy=0.2, inclusion=0.5)],
        bands=bands,
    )
    entry = next(e for e in result.mainboard if e.name == "Lightning Boots")
    assert entry.reason.startswith("protection (cuota), score")


# ── cartas nuevas (arranque en frío, lista "New Cards" de EDHREC) ──


def new_rec(name: str, synergy: float = 0.01) -> Rec:
    return Rec(
        name=name, synergy=synergy, inclusion=0.5,
        categories=("New Cards", "Creatures"),
    )


def add_fresh(pool: PoolIndex, n: int = 1, prefix: str = "Fresh") -> list[Rec]:
    recs = []
    for i in range(n):
        name = f"{prefix} {i:02d}" if n > 1 else prefix
        card = make_card(name)
        pool.by_name[card["name"]] = card
        recs.append(new_rec(name, synergy=0.05 - i * 0.001))
    return recs


def test_new_card_outside_mainboard_appears_in_new_cards() -> None:
    pool, recs = build_inputs()
    result = build(pool, recs + add_fresh(pool))
    assert "Fresh" not in {e.name for e in result.mainboard}
    entry = next(e for e in result.new_cards if e.name == "Fresh")
    assert entry.reason == "carta nueva (EDHREC New Cards)"
    assert entry.score == pytest.approx(0.55)
    # Sección independiente: score demasiado bajo para el maybeboard normal.
    assert "Fresh" not in {e.name for e in result.maybeboard}


def test_banned_and_watchlist_new_cards_excluded_from_new_cards() -> None:
    pool, recs = build_inputs()
    recs = recs + add_fresh(pool, n=3)
    result = build(pool, recs, banned={"Fresh 00"}, watchlist={"Fresh 01"})
    names = {e.name for e in result.new_cards}
    assert not {"Fresh 00", "Fresh 01"} & names
    assert "Fresh 02" in names


def test_never_rule_new_card_excluded_from_new_cards() -> None:
    pool, recs = build_inputs()
    recs = recs + add_fresh(pool, n=2)
    never = RulesConfig.model_validate({"never": [{"name": "Fresh 00"}]})
    result = build_with(pool, recs, rules=never)
    names = {e.name for e in result.new_cards}
    assert "Fresh 00" not in names
    assert "Fresh 01" in names


def test_new_card_in_mainboard_not_duplicated_in_new_cards() -> None:
    pool, recs = build_inputs()
    hot = make_card("Hot New Staple")
    pool.by_name[hot["name"]] = hot
    result = build(pool, recs + [new_rec("Hot New Staple", synergy=9.9)])
    assert "Hot New Staple" in {e.name for e in result.mainboard}
    assert "Hot New Staple" not in {e.name for e in result.new_cards}


def test_new_cards_cap_ten_score_order_and_determinism() -> None:
    pool, recs = build_inputs()
    recs = recs + add_fresh(pool, n=12)
    result = build(pool, recs)
    # Cap 10, orden por score desc: entran los 10 mejores Fresh.
    assert [e.name for e in result.new_cards] == [f"Fresh {i:02d}" for i in range(10)]
    scores = [e.score for e in result.new_cards]
    assert scores == sorted(scores, reverse=True)
    again = build(pool, recs)
    assert [e.name for e in again.new_cards] == [e.name for e in result.new_cards]


# ── método C: boost al score por precio suprimido (capa 3 antisesgo precio) ──


def test_price_factor_floor_saturation_and_null() -> None:
    from selector.cp_sat import _price_factor, C_PRICE_CAP_USD, C_PRICE_FLOOR_USD

    # Suelo: por debajo (y en) el suelo, 0 (las baratas no necesitan ayuda).
    assert _price_factor(None) == 0.0  # precio nulo -> 0
    assert _price_factor(0.5) == 0.0
    assert _price_factor(C_PRICE_FLOOR_USD) == 0.0
    # Saturación: en (y por encima de) el tope, 1 (sin empujón infinito).
    assert _price_factor(C_PRICE_CAP_USD) == pytest.approx(1.0)
    assert _price_factor(1500.0) == pytest.approx(1.0)
    # Creciente y estrictamente entre 0 y 1 en la zona log.
    mid = _price_factor(30.0)
    assert 0.0 < mid < 1.0
    assert _price_factor(60.0) > mid  # monótona


def test_inclusion_factor_zero_and_saturation() -> None:
    from selector.cp_sat import _inclusion_factor, C_INCLUSION_FULL

    assert _inclusion_factor(0.0) == 0.0  # inclusión 0 -> 0 (nunca sube morralla)
    assert _inclusion_factor(-0.1) == 0.0
    assert _inclusion_factor(C_INCLUSION_FULL) == pytest.approx(1.0)
    assert _inclusion_factor(1.0) == pytest.approx(1.0)  # clamp por encima del tope
    half = _inclusion_factor(C_INCLUSION_FULL / 2)
    assert half == pytest.approx(0.5)


def test_price_boost_ordering_expensive_played_beats_the_rest() -> None:
    from selector.cp_sat import price_boost

    expensive_played = price_boost(100.0, 0.5)  # cara Y jugada: boost máximo
    expensive_unplayed = price_boost(100.0, 0.0)  # cara pero morralla: 0
    cheap_played = price_boost(1.0, 0.5)  # barata jugada: 0 (bajo el suelo)
    assert expensive_played > 0.0
    assert expensive_unplayed == 0.0
    assert cheap_played == 0.0
    assert expensive_played > expensive_unplayed
    assert expensive_played > cheap_played


def test_c_weight_zero_disables_boost(monkeypatch) -> None:
    import selector.cp_sat as cp_sat

    monkeypatch.setattr(cp_sat, "C_WEIGHT", 0.0)
    # Con C apagado el boost es 0 sea cual sea el precio/inclusión.
    assert cp_sat.price_boost(100.0, 0.9) == 0.0
    assert cp_sat.price_boost(500.0, 1.0) == 0.0


def test_c_weight_zero_reproduces_pre_c_score(monkeypatch) -> None:
    # Regresión: con C_WEIGHT=0 el score de un candidato caro+jugado es
    # exactamente weights.score(synergy, inclusion), sin término C.
    import selector.cp_sat as cp_sat

    monkeypatch.setattr(cp_sat, "C_WEIGHT", 0.0)
    pool, recs = build_inputs()
    pricey = make_card("Pricey Staple", price_usd=120.0)
    pool.by_name[pricey["name"]] = pricey
    recs = recs + [Rec(name="Pricey Staple", synergy=3.0, inclusion=0.5)]
    result = build(pool, recs)
    entry = next(e for e in result.mainboard if e.name == "Pricey Staple")
    assert entry.score == pytest.approx(ScoreWeights().score(3.0, 0.5))


def test_method_c_flips_expensive_played_over_equal_cheap_card(monkeypatch) -> None:
    # Dos candidatos de synergy compiten por un único slot (techo synergy=1).
    # La barata tiene un pelín MÁS de score base (0.05), así que con C apagado
    # gana ella; con C encendido, el boost de la cara+jugada (0.15) supera ese
    # margen y entra en su lugar.
    import selector.cp_sat as cp_sat

    def make_pair() -> tuple[PoolIndex, list[Rec]]:
        pool, recs = build_inputs()
        cheap = make_card("Cheap Twin", cmc=3.0, price_usd=1.0)
        pricey = make_card("Pricey Twin", cmc=3.0, price_usd=120.0)
        pool.by_name[cheap["name"]] = cheap
        pool.by_name[pricey["name"]] = pricey
        return pool, recs + [
            Rec(name="Cheap Twin", synergy=3.05, inclusion=0.5),
            Rec(name="Pricey Twin", synergy=3.0, inclusion=0.5),
        ]

    bands = bands_fixture()
    bands["synergy"] = QuotaBand(min=0, max=1)  # sitio para exactamente uno
    bands["lands"] = QuotaBand(min=10, max=90)  # básicas mantienen 99 factible

    pool_on, recs_on = make_pair()
    on = build(pool_on, recs_on, bands=bands)  # C al valor por defecto (>0)
    on_names = {e.name for e in on.mainboard}
    assert "Pricey Twin" in on_names
    assert "Cheap Twin" not in on_names
    assert on.relaxation_stage == "none"  # C no causa relajación

    monkeypatch.setattr(cp_sat, "C_WEIGHT", 0.0)
    pool_off, recs_off = make_pair()
    off = build(pool_off, recs_off, bands=bands)
    off_names = {e.name for e in off.mainboard}
    assert "Cheap Twin" in off_names
    assert "Pricey Twin" not in off_names
