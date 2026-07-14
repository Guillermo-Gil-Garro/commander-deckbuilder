"""Tests for selector.deck_rules: loader, predicates, precedence and budget."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from quotas.config import QuotaBand, load_quotas
from selector.deck_rules import (
    DEFAULT_RULES_PATH,
    DeckRulesError,
    RuleContext,
    RulesConfig,
    When,
    archetype_for,
    boost_for,
    iter_card_names,
    load_rules,
    matches,
    preferred_boosts,
    resolve_always,
    resolve_never,
    validate_forced_slot_budget,
    validate_rules_names,
)
from selector.greedy import PoolIndex, build_deck_greedy, load_pool

REPO_ROOT = Path(__file__).resolve().parents[2]

ARCHETYPES = (
    "midrange",
    "aggro",
    "control",
    "spellslinger",
    "voltron",
    "graveyard",
    "enchantress",
    "lands_matter",
)


def write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def ctx(
    commander: str = "Boss Goblin",
    identity: tuple[str, ...] = ("R",),
    archetype: str = "midrange",
) -> RuleContext:
    return RuleContext(
        commander_name=commander,
        color_identity=frozenset(identity),
        archetype=archetype,
    )


@pytest.fixture(scope="module")
def real_pool() -> PoolIndex:
    return load_pool(REPO_ROOT / "data" / "processed" / "cards.jsonl")


@pytest.fixture(scope="module")
def real_rules() -> RulesConfig:
    return load_rules(DEFAULT_RULES_PATH)


# ── loader: real rules.yaml ──────────────────────────────────────────────────


def test_real_rules_yaml_loads_with_the_agreed_decision(real_rules) -> None:
    by_name = {r.name: r for r in real_rules.always}
    sol_ring = by_name["Sol Ring"]
    assert sol_ring.when is None  # unconditional
    assert sol_ring.quota_category == "ramp"
    signet = by_name["Arcane Signet"]
    assert signet.when is not None and signet.when.any_of is not None
    # Signet/Talisman cycles as prefer (Guille 2026-07-14): 20 entries, 0.2.
    assert len(real_rules.preferred) == 20
    assert all(p.boost == 0.2 and len(p.colors_any) == 2 for p in real_rules.preferred)
    assert {"Cyclonic Rift", "Toxic Deluge"} <= set(by_name)  # true auto-includes
    assert real_rules.meta.status == "draft"
    assert real_rules.semantics is not None
    assert real_rules.semantics.precedence == ("ban", "never", "always", "prefer")
    assert real_rules.nomination_rule is not None  # governance mirror
    never_names = {r.name for r in real_rules.never}
    assert "Arcane Signet" in never_names


def test_real_rules_names_resolve_in_pool(real_rules, real_pool) -> None:
    validate_rules_names(real_rules, real_pool.resolve)


def test_real_seeds_are_color_coherent(real_rules, real_pool) -> None:
    # An always gated on color_identity_contains [C] must be playable in a
    # deck whose identity is exactly {C} (e.g. Erode must really be white).
    for rule in real_rules.always:
        if rule.when is None or rule.when.color_identity_contains is None:
            continue
        card = real_pool.resolve(rule.name)
        assert card is not None, rule.name
        gate = set(rule.when.color_identity_contains)
        assert set(card.get("color_identity", [])) <= gate, (
            f"{rule.name}: identity {card.get('color_identity')} not playable "
            f"under gate {sorted(gate)}"
        )


# ── loader: schema errors ────────────────────────────────────────────────────


def test_missing_file_raises() -> None:
    with pytest.raises(DeckRulesError, match="not found"):
        load_rules("no/such/rules.yaml", valid_archetypes=ARCHETYPES)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "always: [unclosed")
    with pytest.raises(DeckRulesError, match="invalid YAML"):
        load_rules(path, valid_archetypes=ARCHETYPES)


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(DeckRulesError, match="mapping"):
        load_rules(path, valid_archetypes=ARCHETYPES)


def test_unknown_archetype_rejected_with_clear_message(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "always:\n"
        "  - name: Blasphemous Act\n"
        "    quota_category: board_wipe\n"
        "    when:\n"
        "      archetype_not_in: [go_wide]\n",
    )
    with pytest.raises(DeckRulesError, match=r"unknown archetype.*go_wide"):
        load_rules(path, valid_archetypes=ARCHETYPES)


def test_built_deck_predicate_rejected_with_clear_message(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "always:\n"
        "  - name: Sol Ring\n"
        "    quota_category: ramp\n"
        "    when:\n"
        "      creature_count: \">=30\"\n",
    )
    with pytest.raises(DeckRulesError, match="mazo construido"):
        load_rules(path, valid_archetypes=ARCHETYPES)


def test_unknown_predicate_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "never:\n"
        "  - name: Sol Ring\n"
        "    when:\n"
        "      moon_phase: full\n",
    )
    with pytest.raises(DeckRulesError, match="moon_phase"):
        load_rules(path, valid_archetypes=ARCHETYPES)


def test_lands_quota_category_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "always:\n  - name: Command Tower\n    quota_category: lands\n",
    )
    with pytest.raises(DeckRulesError, match="lands"):
        load_rules(path, valid_archetypes=ARCHETYPES)


def test_duplicated_names_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "always:\n"
        "  - name: Sol Ring\n    quota_category: ramp\n"
        "  - name: Sol Ring\n    quota_category: ramp\n",
    )
    with pytest.raises(DeckRulesError, match="duplicated names"):
        load_rules(path, valid_archetypes=ARCHETYPES)


def test_invalid_size_comparator_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "never:\n"
        "  - name: Sol Ring\n"
        "    when:\n"
        "      color_identity_size: \"~2\"\n",
    )
    with pytest.raises(DeckRulesError, match="color_identity_size"):
        load_rules(path, valid_archetypes=ARCHETYPES)


def test_unresolvable_name_raises(real_rules) -> None:
    def resolve_nothing(name: str) -> None:
        return None

    with pytest.raises(DeckRulesError, match="Sol Ring"):
        validate_rules_names(real_rules, resolve_nothing)


def test_iter_card_names_includes_commander_lists(real_rules) -> None:
    names = set(iter_card_names(real_rules))
    assert "Urza, Lord High Artificer" in names  # from commander_in
    assert "Sol Ring" in names


# ── predicates ───────────────────────────────────────────────────────────────


def test_color_identity_contains_requires_all_colors() -> None:
    when = When.model_validate({"color_identity_contains": ["W", "U"]})
    assert matches(when, ctx(identity=("W", "U", "B")))
    assert not matches(when, ctx(identity=("W",)))
    assert not matches(when, ctx(identity=()))


def test_color_identity_size_int_and_comparators() -> None:
    exact = When.model_validate({"color_identity_size": 2})
    assert matches(exact, ctx(identity=("W", "U")))
    assert not matches(exact, ctx(identity=("W",)))
    at_least = When.model_validate({"color_identity_size": ">=2"})
    assert matches(at_least, ctx(identity=("W", "U", "B")))
    assert not matches(at_least, ctx(identity=("W",)))
    at_most = When.model_validate({"color_identity_size": "<=1"})
    assert matches(at_most, ctx(identity=()))
    assert matches(at_most, ctx(identity=("R",)))
    assert not matches(at_most, ctx(identity=("R", "G")))


def test_archetype_in_and_not_in() -> None:
    wipe = When.model_validate({"archetype_not_in": ["aggro"]})
    assert matches(wipe, ctx(archetype="midrange"))
    assert not matches(wipe, ctx(archetype="aggro"))
    only = When.model_validate({"archetype_in": ["graveyard", "control"]})
    assert matches(only, ctx(archetype="control"))
    assert not matches(only, ctx(archetype="midrange"))


def test_commander_in_and_not_in() -> None:
    when = When.model_validate({"commander_in": ["Urza, Lord High Artificer"]})
    assert matches(when, ctx(commander="Urza, Lord High Artificer", identity=("U",)))
    assert not matches(when, ctx(commander="Krenko, Mob Boss"))
    negated = When.model_validate({"commander_not_in": ["Krenko, Mob Boss"]})
    assert not matches(negated, ctx(commander="Krenko, Mob Boss"))
    assert matches(negated, ctx(commander="Whoever"))


def test_any_of_is_an_or_and_direct_keys_are_anded() -> None:
    when = When.model_validate(
        {
            "any_of": [
                {"color_identity_size": ">=2"},
                {"commander_in": ["Urza, Lord High Artificer"]},
            ]
        }
    )
    assert matches(when, ctx(identity=("U", "R")))
    assert matches(when, ctx(commander="Urza, Lord High Artificer", identity=("U",)))
    assert not matches(when, ctx(identity=("R",)))
    combined = When.model_validate(
        {
            "color_identity_contains": ["R"],
            "any_of": [{"archetype_in": ["aggro"]}, {"archetype_in": ["voltron"]}],
        }
    )
    assert matches(combined, ctx(identity=("R",), archetype="aggro"))
    assert not matches(combined, ctx(identity=("U",), archetype="aggro"))
    assert not matches(combined, ctx(identity=("R",), archetype="control"))


# ── precedence: ban > never > always > prefer ────────────────────────────────


def precedence_config() -> RulesConfig:
    return RulesConfig.model_validate(
        {
            "always": [
                {"name": "Sol Ring", "quota_category": "ramp"},
                {"name": "Arcane Signet", "quota_category": "ramp"},
            ],
            "never": [{"name": "Arcane Signet"}],
            "preferred": [{"name": "Sol Ring", "colors_any": [], "boost": 0.4}],
        }
    )


def test_never_beats_always() -> None:
    config = precedence_config()
    always_names = {r.name for r in resolve_always(config, ctx())}
    assert "Sol Ring" in always_names
    assert "Arcane Signet" not in always_names  # never wins
    assert "Arcane Signet" in resolve_never(config, ctx())


def test_ban_beats_never_and_always() -> None:
    config = precedence_config()
    always_names = {r.name for r in resolve_always(config, ctx(), {"Sol Ring"})}
    assert "Sol Ring" not in always_names  # banlist wins over its always
    # prefer never rescues a banned/never card: it is only a boost lookup.
    boosts = preferred_boosts(config, ("R",))
    assert boosts == {"Sol Ring": 0.4}
    assert boost_for(boosts, "Sol Ring") == 0.4
    assert boost_for(boosts, "Arcane Signet") == 0.0


# ── selector behavior: always consumes quota, never blocks maybeboard ────────


@dataclass
class Rec:
    name: str
    synergy: float
    inclusion: float


def mini_card(name: str, **kwargs) -> dict:
    card = {
        "name": name,
        "mana_cost": "{1}{R}",
        "cmc": 2.0,
        "type_line": "Creature — Goblin",
        "oracle_text": "",
        "colors": ["R"],
        "color_identity": ["R"],
    }
    card.update(kwargs)
    return card


MINI_TAGS: dict[str, set[str]] = {}


def mini_tagger(name: str) -> set[str]:
    return set(MINI_TAGS.get(name, set()))


def mini_inputs() -> tuple[PoolIndex, list[Rec]]:
    MINI_TAGS.clear()
    MINI_TAGS.update(
        {
            "Mountain": {"lands"},
            "Sol Ring": {"ramp"},
            "Ramp A": {"ramp"},
            "Ramp B": {"ramp"},
        }
    )
    cards = [
        mini_card("Boss Goblin", type_line="Legendary Creature — Goblin"),
        mini_card("Mountain", mana_cost="", cmc=0.0, type_line="Basic Land — Mountain"),
        mini_card("Sol Ring", mana_cost="{1}", cmc=1.0, type_line="Artifact",
                  color_identity=[]),
        mini_card("Ramp A"),
        mini_card("Ramp B"),
    ]
    recs = [Rec("Ramp A", 0.9, 0.5), Rec("Ramp B", 0.8, 0.5)]
    for i in range(30):
        cards.append(mini_card(f"Synergy {i:02d}"))
        recs.append(Rec(f"Synergy {i:02d}", 0.5 - i * 0.001, 0.5))
    return PoolIndex(cards), recs


def mini_bands() -> dict[str, QuotaBand]:
    return {
        "lands": QuotaBand(min=10, max=40),
        "ramp": QuotaBand(min=0, max=2),
        "card_draw": QuotaBand(min=0, max=4),
        "removal": QuotaBand(min=0, max=4),
        "board_wipe": QuotaBand(min=0, max=2),
        "wincons": QuotaBand(min=0, max=2),
        "synergy": QuotaBand(min=0, max=90),
    }


def test_always_consumes_its_quota_category_slot() -> None:
    pool, recs = mini_inputs()
    config = RulesConfig.model_validate(
        {"always": [{"name": "Sol Ring", "quota_category": "ramp"}]}
    )
    result = build_deck_greedy(
        "Boss Goblin",
        pool=pool,
        recommendations=recs,
        bands=mini_bands(),
        tagger=mini_tagger,
        banned_names=set(),
        watchlist_names=set(),
        rules=config,
        archetype="midrange",
    )
    sol_ring = next(e for e in result.mainboard if e.name == "Sol Ring")
    assert sol_ring.reason == "always (rules.yaml)"
    assert sol_ring.slot == "ramp"
    # ramp max is 2 and Sol Ring consumes one slot: only ONE of Ramp A/B fits.
    assert result.counts["ramp"] == 2
    names = {e.name for e in result.mainboard}
    assert not {"Ramp A", "Ramp B"} <= names
    assert result.total_cards == 99


def test_always_with_null_quota_category_takes_a_filler_slot() -> None:
    pool, recs = mini_inputs()
    MINI_TAGS.pop("Sol Ring")  # untagged: pure filler card
    config = RulesConfig.model_validate(
        {"always": [{"name": "Sol Ring", "quota_category": None}]}
    )
    result = build_deck_greedy(
        "Boss Goblin",
        pool=pool,
        recommendations=recs,
        bands=mini_bands(),
        tagger=mini_tagger,
        banned_names=set(),
        watchlist_names=set(),
        rules=config,
        archetype="midrange",
    )
    sol_ring = next(e for e in result.mainboard if e.name == "Sol Ring")
    assert sol_ring.slot == "synergy"  # general filler bucket
    assert result.total_cards == 99


def test_never_blocks_mainboard_and_maybeboard() -> None:
    pool, recs = mini_inputs()
    # Top-scored recommendation, but never-ruled for mono decks.
    recs = [Rec("Ramp A", 9.9, 1.0)] + [r for r in recs if r.name != "Ramp A"]
    config = RulesConfig.model_validate(
        {"never": [{"name": "Ramp A", "when": {"color_identity_size": "<=1"}}]}
    )
    result = build_deck_greedy(
        "Boss Goblin",
        pool=pool,
        recommendations=recs,
        bands=mini_bands(),
        tagger=mini_tagger,
        banned_names=set(),
        watchlist_names=set(),
        rules=config,
        archetype="midrange",
    )
    all_names = {e.name for e in result.mainboard} | {e.name for e in result.maybeboard}
    assert "Ramp A" not in all_names
    assert "Ramp B" in {e.name for e in result.mainboard}


# ── forced slot budget ───────────────────────────────────────────────────────


def test_budget_validator_raises_over_budget() -> None:
    config = RulesConfig.model_validate(
        {
            "meta": {"forced_slot_budget": 1},
            "always": [
                {"name": "Sol Ring", "quota_category": "ramp"},
                {"name": "Arcane Signet", "quota_category": "ramp"},
            ],
        }
    )
    with pytest.raises(DeckRulesError, match="forced_slot_budget"):
        validate_forced_slot_budget(config, ctx())
    # The banlist frees a slot: back under budget.
    assert validate_forced_slot_budget(config, ctx(), {"Sol Ring"}) == 1


def test_budget_holds_for_all_55_featured_commanders(real_rules, real_pool) -> None:
    quotas = load_quotas()
    raw = yaml.safe_load(
        (REPO_ROOT / "featured_commanders.yaml").read_text(encoding="utf-8")
    )
    featured = raw["featured"]
    assert len(featured) == 55
    max_count = 0
    for name in featured:
        card = real_pool.resolve(name)
        assert card is not None, name
        context = RuleContext(
            commander_name=card["name"],
            color_identity=frozenset(card.get("color_identity", [])),
            archetype=archetype_for(quotas, card["name"]),
        )
        count = validate_forced_slot_budget(real_rules, context)
        max_count = max(max_count, count)
    # The 5-color midrange commanders saturate the budget exactly (16, with
    # Cyclonic Rift and Toxic Deluge added 2026-07-14).
    assert max_count == real_rules.meta.forced_slot_budget
