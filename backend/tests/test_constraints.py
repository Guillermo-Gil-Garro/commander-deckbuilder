"""Tests for selector.constraints (the <100 ms swap feasibility checker)."""

from __future__ import annotations

import pytest

from quotas.config import QuotaBand
from selector.constraints import (
    CardFacts,
    Severity,
    deck_counts,
    hard_violations,
)
from selector.greedy import DECK_SIZE, SelectorError, karsten_floor


def facts(
    name: str,
    *,
    categories: set[str] = frozenset(),
    cmc: float = 2.0,
    mana_cost: str = "{1}{R}",
    is_basic: bool = False,
) -> CardFacts:
    return CardFacts(
        name=name,
        oracle_id=f"oid-{name}",
        categories=frozenset(categories),
        cmc=cmc,
        mana_cost=mana_cost,
        color_identity=frozenset("R"),
        is_basic=is_basic,
    )


def land(name: str, *, categories: set[str] = frozenset()) -> CardFacts:
    return facts(name, categories={"lands"} | set(categories), cmc=0.0, mana_cost="")


def basic(name: str = "Mountain") -> CardFacts:
    return facts(
        name, categories={"lands"}, cmc=0.0, mana_cost="", is_basic=True
    )


def bands_fixture() -> dict[str, QuotaBand]:
    return {
        "lands": QuotaBand(min=34, max=40),
        "ramp": QuotaBand(min=2, max=4),
        "card_draw": QuotaBand(min=2, max=4),
        "removal": QuotaBand(min=2, max=4),
        "board_wipe": QuotaBand(min=1, max=2),
        "wincons": QuotaBand(min=1, max=2),
        "synergy": QuotaBand(min=0, max=90),
    }


def deck(
    *,
    ramp: int = 3,
    card_draw: int = 3,
    removal: int = 3,
    board_wipe: int = 1,
    wincons: int = 1,
    lands: int = 36,
    extra: list[tuple[CardFacts, int]] | None = None,
) -> list[tuple[CardFacts, int]]:
    """A feasible 99 under ``bands_fixture``; the rest is synergy filler."""
    rows: list[tuple[CardFacts, int]] = []
    for category, n in (
        ("ramp", ramp),
        ("card_draw", card_draw),
        ("removal", removal),
        ("board_wipe", board_wipe),
        ("wincons", wincons),
    ):
        rows += [(facts(f"{category} {i}", categories={category}), 1) for i in range(n)]
    rows += extra or []
    placed = sum(count for _, count in rows)
    filler = DECK_SIZE - lands - placed
    rows += [
        (facts(f"Synergy {i}", categories={"synergy"}, cmc=2.0), 1)
        for i in range(filler)
    ]
    rows.append((basic(), lands))
    return rows


# ── deck_counts ──────────────────────────────────────────────────────────────


def test_multicategory_card_counts_in_every_category() -> None:
    counts = deck_counts([(facts("Swiss Army", categories={"ramp", "card_draw"}), 1)])
    assert counts.total == 1
    assert counts.by_category == {"ramp": 1, "card_draw": 1}
    assert counts.nonland_by_category == {"ramp": 1, "card_draw": 1}


def test_basics_with_count_above_one() -> None:
    counts = deck_counts([(basic(), 36), (facts("Spell", categories={"ramp"}), 1)])
    assert counts.total == 37
    assert counts.by_category["lands"] == 36
    # Basics are lands: they never feed a floor counter.
    assert "lands" not in counts.nonland_by_category


def test_land_counts_in_by_category_but_not_in_nonland_by_category() -> None:
    counts = deck_counts([(land("Grim Backwoods", categories={"card_draw"}), 1)])
    assert counts.by_category == {"lands": 1, "card_draw": 1}
    assert counts.nonland_by_category == {}


def test_curve_and_karsten_floor_measured_over_nonlands_only() -> None:
    rows = deck()
    counts = deck_counts(rows)
    # 36 basics + 63 non-lands: the curve fractions must sum to 1 over the 63.
    assert sum(counts.curve.values()) == pytest.approx(1.0)
    assert counts.karsten_floor > 0


def test_zero_or_negative_count_is_an_explicit_error() -> None:
    with pytest.raises(SelectorError, match="invalid card count"):
        deck_counts([(basic(), 0)])


def test_karsten_floor_property_matches_the_selector_helper() -> None:
    # DeckCounts.karsten_floor is the aggregated form of greedy.karsten_floor
    # (histogram vs list of cards). Nothing but this test keeps them in lockstep.
    rows = deck()
    counts = deck_counts(rows)
    nonland = [card for card, n in rows for _ in range(n) if not card.is_land]
    assert counts.karsten_floor == karsten_floor(nonland, counts.by_category)


def test_counters_never_hold_a_zero() -> None:
    # The invariant that lets counts_after_swap results compare equal to
    # deck_counts ones: an empty category is absent, not present as 0.
    counts = deck_counts([(facts("Spell", categories={"ramp"}), 1)])
    assert 0 not in counts.by_category.values()
    assert "removal" not in counts.by_category


# ── hard_violations: the happy path ──────────────────────────────────────────


def test_feasible_deck_has_no_violations() -> None:
    counts = deck_counts(deck())
    assert counts.karsten_floor <= 36  # otherwise the fixture is not feasible
    assert hard_violations(counts, bands_fixture()) == ()


def test_bands_without_lands_is_an_explicit_error() -> None:
    counts = deck_counts(deck())
    with pytest.raises(SelectorError, match="must include a 'lands' band"):
        hard_violations(counts, {"ramp": QuotaBand(min=1, max=2)})


def test_deck_size_violation_is_red_and_reports_the_numbers() -> None:
    counts = deck_counts([(basic(), 40)])
    breach = next(v for v in hard_violations(counts, bands_fixture()) if v.code == "deck_size")
    assert (breach.category, breach.actual, breach.limit) == (None, 40, DECK_SIZE)
    assert breach.severity is Severity.RED


# ── the Grim Backwoods case (AUDITORIA_SELECTORES §5.D.1) ────────────────────


def test_multicategory_land_does_not_cover_a_spell_floor() -> None:
    # One card_draw spell short, "covered" by a land tagged card_draw.
    rows = deck(card_draw=1, extra=[(land("Grim Backwoods", categories={"card_draw"}), 1)])
    counts = deck_counts(rows)
    assert counts.by_category["card_draw"] == 2  # informative count says 2...
    breaches = hard_violations(counts, bands_fixture())
    floor = next(v for v in breaches if v.code == "category_floor")
    # ...but the floor only sees the single spell.
    assert (floor.category, floor.actual, floor.limit) == ("card_draw", 1, 2)


def test_multicategory_land_does_consume_the_category_ceiling() -> None:
    # 2 board_wipe spells (max 2) + one land tagged board_wipe = 3 > 2.
    rows = deck(
        board_wipe=2,
        extra=[(land("Wipe Land", categories={"board_wipe"}), 1)],
    )
    counts = deck_counts(rows)
    breaches = hard_violations(counts, bands_fixture())
    ceiling = next(v for v in breaches if v.code == "category_ceiling")
    assert (ceiling.category, ceiling.actual, ceiling.limit) == ("board_wipe", 3, 2)


# ── lands: Karsten floor and the max(band.max, lands_min) ceiling ────────────


def test_karsten_floor_raises_the_lands_floor_above_the_band_min() -> None:
    bands = bands_fixture()
    bands["lands"] = QuotaBand(min=0, max=40)
    counts = deck_counts(deck(lands=20))
    breach = next(
        v for v in hard_violations(counts, bands) if v.code == "lands_floor"
    )
    # The band asks for nothing; the deck's own curve does.
    assert breach.limit == counts.karsten_floor > 20
    assert breach.actual == 20


def test_an_explicit_lands_min_overrides_the_recomputed_karsten_floor() -> None:
    """The solver reaches its lands floor through a fixpoint, and that floor can
    land above the Karsten floor of the deck it finally produces. Recomputing
    the floor from the result then reads a legal deck as over the ceiling —
    which is how a Giada-in-aggro build died accusing constraints.py of having
    diverged. Callers that know the floor the solver used must pass it.
    """
    bands = bands_fixture()
    bands["lands"] = QuotaBand(min=33, max=36)
    counts = deck_counts(deck(lands=37))
    assert counts.karsten_floor <= 36, "otherwise the case is not reproduced"

    # Recomputed: 37 lands over a ceiling of max(36, karsten) == 36.
    assert [v for v in hard_violations(counts, bands) if v.code == "lands_ceiling"]
    # The floor the solver actually enforced lifts the ceiling with it.
    assert not [
        v
        for v in hard_violations(counts, bands, lands_min=37)
        if v.code == "lands_ceiling"
    ]


def test_lands_ceiling_is_max_of_band_max_and_the_karsten_floor() -> None:
    # A band max below the Karsten floor must not turn the floor into a breach.
    bands = bands_fixture()
    counts = deck_counts(deck(lands=36))
    bands["lands"] = QuotaBand(min=34, max=35)
    assert counts.karsten_floor >= 36
    assert not [
        v for v in hard_violations(counts, bands) if v.code == "lands_ceiling"
    ]
    # Above the floor, though, the ceiling bites again.
    over = deck_counts(deck(lands=counts.karsten_floor + 1))
    breach = next(
        v for v in hard_violations(over, bands) if v.code == "lands_ceiling"
    )
    assert breach.limit == over.karsten_floor
    assert breach.actual == over.karsten_floor + 1


def test_ceiling_only_category_has_no_floor() -> None:
    bands = bands_fixture()
    counts = deck_counts(deck())
    # synergy min is 0: it can never produce a category_floor breach.
    assert not [
        v
        for v in hard_violations(counts, bands)
        if v.code == "category_floor" and v.category == "synergy"
    ]


# ── non-worsening rule (decks delivered at a relaxed stage) ──────────────────


def test_baseline_below_floor_accepts_a_neutral_swap() -> None:
    bands = bands_fixture()
    below = deck_counts(deck(card_draw=1))  # 1 < min 2: already broken
    assert "category_floor" in {v.code for v in hard_violations(below, bands)}
    # Same shape after the swap (a synergy card for another synergy card).
    after = deck_counts(deck(card_draw=1))
    assert hard_violations(after, bands, baseline=below) == ()


def test_baseline_below_floor_still_blocks_a_swap_that_lowers_it() -> None:
    bands = bands_fixture()
    below = deck_counts(deck(card_draw=1))
    worse = deck_counts(deck(card_draw=0))
    breach = next(
        v
        for v in hard_violations(worse, bands, baseline=below)
        if v.code == "category_floor"
    )
    assert (breach.category, breach.actual, breach.limit) == ("card_draw", 0, 2)


def test_baseline_below_floor_accepts_the_swap_that_fixes_it() -> None:
    bands = bands_fixture()
    below = deck_counts(deck(card_draw=1))
    fixed = deck_counts(deck(card_draw=2))
    assert hard_violations(fixed, bands, baseline=below) == ()


def test_non_worsening_applies_to_ceilings_too() -> None:
    bands = bands_fixture()
    over = deck_counts(deck(ramp=6))  # max 4: already over
    assert [v.code for v in hard_violations(over, bands)] == ["category_ceiling"]
    assert hard_violations(over, bands, baseline=over) == ()
    worse = deck_counts(deck(ramp=7))
    assert [
        v.code for v in hard_violations(worse, bands, baseline=over)
    ] == ["category_ceiling"]


def test_deck_size_is_never_excused_by_the_baseline() -> None:
    bands = bands_fixture()
    baseline = deck_counts([(basic(), 40)])
    counts = deck_counts([(basic(), 40)])
    assert any(
        v.code == "deck_size" for v in hard_violations(counts, bands, baseline=baseline)
    )
