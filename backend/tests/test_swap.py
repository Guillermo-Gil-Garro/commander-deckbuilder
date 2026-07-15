"""Tests for selector.swap (validated swap + candidate ranking, no re-solve)."""

from __future__ import annotations

import pytest

from selector.constraints import (
    CardFacts,
    Severity,
    counts_after_swap,
    deck_counts,
)
from selector.greedy import SelectorError
from selector.swap import primary_category, swap_candidates, swap_is_feasible

# The deck fixtures are the checker's: the swap layer must agree with the rules
# module on what a feasible 99 looks like, so it is tested against the same one.
from tests.test_constraints import bands_fixture, basic, deck, facts

COMMANDER = facts("Boss Goblin")


def blue(name: str, *, categories: set[str] = frozenset({"synergy"})) -> CardFacts:
    """A card outside the mono-red commander's identity."""
    return CardFacts(
        name=name,
        oracle_id=f"oid-{name}",
        categories=frozenset(categories),
        cmc=2.0,
        mana_cost="{1}{U}",
        color_identity=frozenset("U"),
    )


def verdict(
    *,
    rows=None,
    out_card: CardFacts,
    in_card: CardFacts,
    banned: set[str] = frozenset(),
    never: set[str] = frozenset(),
    watchlist: set[str] = frozenset(),
    always: set[str] = frozenset(),
):
    return swap_is_feasible(
        deck=deck() if rows is None else rows,
        out_card=out_card,
        in_card=in_card,
        bands=bands_fixture(),
        commander=COMMANDER,
        banned_names=banned,
        never_names=never,
        watchlist_names=watchlist,
        always_names=always,
    )


def codes(violations) -> set[str]:
    return {v.code for v in violations}


# ── swap_is_feasible: the happy path ─────────────────────────────────────────


def test_swapping_synergy_for_synergy_is_feasible() -> None:
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=facts("Fresh Goblin", categories={"synergy"}),
    )
    assert result.feasible
    assert result.blockers == ()
    assert result.warnings == ()
    assert result.counts_after.total == 99


def test_statuses_after_cover_every_band() -> None:
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=facts("Fresh Goblin", categories={"synergy"}),
    )
    assert set(result.statuses_after) == set(bands_fixture())


# ── swap_is_feasible: RED ────────────────────────────────────────────────────


def test_banned_in_card_is_red() -> None:
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=facts("Black Lotus", categories={"synergy"}),
        banned={"Black Lotus"},
    )
    assert not result.feasible
    assert codes(result.blockers) == {"banned"}
    assert all(v.severity is Severity.RED for v in result.blockers)


def test_off_identity_in_card_is_red() -> None:
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=blue("Counterspell"),
    )
    assert not result.feasible
    assert codes(result.blockers) == {"color_identity"}


def test_duplicate_nonbasic_is_red() -> None:
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=facts("Synergy 1", categories={"synergy"}),
    )
    assert not result.feasible
    blocker = next(v for v in result.blockers if v.code == "duplicate_card")
    assert (blocker.actual, blocker.limit) == (2, 1)


def test_duplicate_basic_is_feasible() -> None:
    # Basics are the only legal duplicates: a 37th Mountain is a real swap.
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=basic(),
    )
    assert result.feasible
    assert result.counts_after.by_category["lands"] == 37


def test_in_card_equal_to_commander_is_red() -> None:
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=facts("Boss Goblin", categories={"synergy"}),
    )
    assert not result.feasible
    assert codes(result.blockers) == {"commander_duplicate"}


def test_dropping_removal_below_its_minimum_is_red() -> None:
    # removal sits at its floor of 2: trading one away for filler breaks it.
    result = verdict(
        rows=deck(removal=2),
        out_card=facts("removal 0", categories={"removal"}),
        in_card=facts("Fresh Goblin", categories={"synergy"}),
    )
    assert not result.feasible
    blocker = next(v for v in result.blockers if v.code == "category_floor")
    assert (blocker.category, blocker.actual, blocker.limit) == ("removal", 1, 2)


def test_swapping_a_card_for_itself_is_not_a_duplicate() -> None:
    same = facts("Synergy 0", categories={"synergy"})
    assert verdict(out_card=same, in_card=same).feasible


def test_swapping_out_a_card_not_in_the_deck_is_an_explicit_error() -> None:
    with pytest.raises(SelectorError, match="not in the deck"):
        verdict(
            out_card=facts("Not Here", categories={"synergy"}),
            in_card=facts("Fresh Goblin", categories={"synergy"}),
        )


def test_blockers_accumulate_without_short_circuiting() -> None:
    # The panel lists every reason at once, so all rules are evaluated.
    result = verdict(
        rows=deck(removal=2),
        out_card=facts("removal 0", categories={"removal"}),
        in_card=blue("Synergy 1"),  # off-identity + banned + already in the deck
        banned={"Synergy 1"},
    )
    assert not result.feasible
    assert codes(result.blockers) == {
        "color_identity",
        "banned",
        "duplicate_card",
        "category_floor",
    }


# ── swap_is_feasible: AMBER never blocks ─────────────────────────────────────


def test_adding_a_never_card_by_hand_is_amber_and_feasible() -> None:
    # "never" = never auto-recommended, not illegal (rules.yaml semantics).
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=facts("Rhystic Study", categories={"synergy"}),
        never={"Rhystic Study"},
    )
    assert result.feasible
    assert result.blockers == ()
    assert codes(result.warnings) == {"add_never_manually"}
    assert all(v.severity is Severity.AMBER for v in result.warnings)


def test_adding_a_watchlist_card_by_hand_is_amber_and_feasible() -> None:
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=facts("Thoracle", categories={"synergy"}),
        watchlist={"Thoracle"},
    )
    assert result.feasible
    assert codes(result.warnings) == {"watchlist"}


def test_removing_an_always_card_is_amber_and_feasible() -> None:
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=facts("Fresh Goblin", categories={"synergy"}),
        always={"Synergy 0"},
    )
    assert result.feasible
    assert codes(result.warnings) == {"remove_always"}


def test_a_banned_card_is_red_even_when_it_is_also_a_never_card() -> None:
    # Precedence ban > never: AMBER never softens a RED.
    result = verdict(
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=facts("Black Lotus", categories={"synergy"}),
        banned={"Black Lotus"},
        never={"Black Lotus"},
    )
    assert not result.feasible
    assert codes(result.blockers) == {"banned"}
    assert codes(result.warnings) == {"add_never_manually"}


# ── the non-worsening rule comes from hard_violations ────────────────────────


def test_a_swap_that_does_not_worsen_an_already_broken_floor_is_feasible() -> None:
    # A deck delivered at a relaxed CP-SAT stage is already below a floor; the
    # swap layer must not freeze it (baseline reading, owned by constraints).
    rows = deck(card_draw=1)
    result = verdict(
        rows=rows,
        out_card=facts("Synergy 0", categories={"synergy"}),
        in_card=facts("Fresh Goblin", categories={"synergy"}),
    )
    assert result.feasible


def test_a_swap_that_worsens_an_already_broken_floor_is_red() -> None:
    rows = deck(card_draw=1)
    result = verdict(
        rows=rows,
        out_card=facts("card_draw 0", categories={"card_draw"}),
        in_card=facts("Fresh Goblin", categories={"synergy"}),
    )
    assert not result.feasible
    assert "category_floor" in codes(result.blockers)


# ── counts_after_swap: the shortcut must not lie ─────────────────────────────


def rows_after(rows, out_card: CardFacts, in_card: CardFacts):
    """``rows`` with one copy of out_card replaced by one of in_card."""
    result = []
    removed = False
    for card, count in rows:
        if not removed and card.name == out_card.name:
            removed = True
            if count > 1:
                result.append((card, count - 1))
            continue
        result.append((card, count))
    assert removed, f"{out_card.name!r} not in rows"
    added = False
    for i, (card, count) in enumerate(result):
        if card.name == in_card.name:
            result[i] = (card, count + 1)
            added = True
            break
    if not added:
        result.append((in_card, 1))
    return result


@pytest.mark.parametrize(
    "out_card, in_card",
    [
        # plain spell for spell
        (facts("Synergy 0", categories={"synergy"}), facts("Fresh", categories={"synergy"})),
        # spell for a basic already in the deck (count goes 36 -> 37)
        (facts("Synergy 0", categories={"synergy"}), basic()),
        # basic out (count goes 36 -> 35) for a spell
        (basic(), facts("Fresh", categories={"synergy"})),
        # multi-category card, and one whose category empties out
        (
            facts("board_wipe 0", categories={"board_wipe"}),
            facts("Swiss", categories={"ramp", "card_draw"}),
        ),
        # different curve buckets on both sides
        (
            facts("Synergy 0", categories={"synergy"}, cmc=2.0),
            facts("Big", categories={"synergy"}, cmc=8.0),
        ),
    ],
)
def test_counts_after_swap_matches_a_full_recount(out_card, in_card) -> None:
    rows = deck()
    before = deck_counts(rows)
    assert counts_after_swap(before, out_card, in_card) == deck_counts(
        rows_after(rows, out_card, in_card)
    )


def test_counts_after_swap_refuses_to_count_past_zero() -> None:
    before = deck_counts(deck())
    with pytest.raises(SelectorError, match="holds no"):
        counts_after_swap(
            before,
            facts("Ghost", categories={"protection"}),  # no such card in the deck
            facts("Fresh", categories={"synergy"}),
        )


# ── primary_category ─────────────────────────────────────────────────────────


def test_primary_category_follows_fill_order_and_lands_win() -> None:
    from tests.test_constraints import land

    assert primary_category(land("Boseiju", categories={"removal"})) == "lands"
    # FILL_ORDER puts wincons before ramp.
    assert primary_category(facts("X", categories={"ramp", "wincons"})) == "wincons"
    assert primary_category(facts("X", categories={"synergy"})) == "synergy"
    assert primary_category(facts("X", categories=set())) == "synergy"


# ── swap_candidates ──────────────────────────────────────────────────────────


def candidates(pool_candidates, *, out_card, limit=10, **kwargs):
    return swap_candidates(
        deck=kwargs.pop("rows", None) or deck(),
        out_card=out_card,
        pool_candidates=pool_candidates,
        bands=bands_fixture(),
        commander=COMMANDER,
        banned_names=kwargs.pop("banned", frozenset()),
        never_names=kwargs.pop("never", frozenset()),
        watchlist_names=kwargs.pop("watchlist", frozenset()),
        limit=limit,
        **kwargs,
    )


def test_candidates_are_only_of_the_out_cards_primary_category() -> None:
    pool = [
        (facts("Swords", categories={"removal"}), 0.9),
        (facts("Sol Ring", categories={"ramp"}), 0.95),
        (facts("Goblin", categories={"synergy"}), 0.8),
        (facts("Wrath", categories={"board_wipe"}), 0.7),
    ]
    found, total = candidates(pool, out_card=facts("removal 0", categories={"removal"}))
    assert [c.name for c in found] == ["Swords"]
    assert total == 1
    assert found[0].primary_category == "removal"


def test_candidates_exclude_banned_never_watchlist_and_off_identity() -> None:
    pool = [
        (facts("Good", categories={"removal"}), 0.9),
        (facts("Banned", categories={"removal"}), 0.99),
        (facts("Never", categories={"removal"}), 0.98),
        (facts("Watched", categories={"removal"}), 0.97),
        (blue("Blue Removal", categories={"removal"}), 0.96),
    ]
    found, total = candidates(
        pool,
        out_card=facts("removal 0", categories={"removal"}),
        banned={"Banned"},
        never={"Never"},
        watchlist={"Watched"},
    )
    assert [c.name for c in found] == ["Good"]
    assert total == 1


def test_candidates_exclude_cards_already_in_the_deck() -> None:
    pool = [
        (facts("removal 1", categories={"removal"}), 0.99),  # already in the 99
        (facts("Good", categories={"removal"}), 0.5),
    ]
    found, _ = candidates(pool, out_card=facts("removal 0", categories={"removal"}))
    assert [c.name for c in found] == ["Good"]


def test_candidates_are_ordered_by_score_then_cmc_then_name() -> None:
    pool = [
        (facts("Zebra", categories={"removal"}, cmc=1.0), 0.5),
        (facts("Alpha", categories={"removal"}, cmc=1.0), 0.5),
        (facts("Cheap", categories={"removal"}, cmc=0.0), 0.5),
        (facts("Best", categories={"removal"}, cmc=9.0), 0.9),
    ]
    found, _ = candidates(pool, out_card=facts("removal 0", categories={"removal"}))
    assert [c.name for c in found] == ["Best", "Cheap", "Alpha", "Zebra"]


def test_candidates_that_would_break_a_ceiling_are_filtered_out() -> None:
    # ramp sits at its max of 4. Both candidates are removal by primary
    # category, so both are considered; the multi-category one would push ramp
    # to 5 on its way in, and the ceiling counts every member.
    pool = [
        (facts("Pure Removal", categories={"removal"}), 0.5),
        (facts("Ramp Removal", categories={"removal", "ramp"}), 0.9),
    ]
    found, total = candidates(
        pool,
        rows=deck(ramp=4),
        out_card=facts("removal 0", categories={"removal"}),
    )
    assert [c.name for c in found] == ["Pure Removal"]
    assert total == 1


def test_candidates_of_another_primary_category_are_never_considered() -> None:
    found, total = candidates(
        [(facts("Extra Ramp", categories={"ramp"}), 0.5)],
        out_card=facts("Synergy 0", categories={"synergy"}),
    )
    assert (found, total) == ((), 0)


def test_feasible_count_is_the_total_before_the_limit_trims() -> None:
    pool = [(facts(f"Removal {i}", categories={"removal"}), 0.5 + i) for i in range(9)]
    found, total = candidates(
        pool, out_card=facts("removal 0", categories={"removal"}), limit=3
    )
    assert total == 9
    assert len(found) == 3
    assert total > len(found)
    # The trim keeps the best ones, in order.
    assert [c.name for c in found] == ["Removal 8", "Removal 7", "Removal 6"]


def test_limit_zero_returns_the_count_without_any_candidate() -> None:
    pool = [(facts("Swords", categories={"removal"}), 0.9)]
    found, total = candidates(
        pool, out_card=facts("removal 0", categories={"removal"}), limit=0
    )
    assert (found, total) == ((), 1)


def test_negative_limit_is_an_explicit_error() -> None:
    with pytest.raises(SelectorError, match="limit must be >= 0"):
        candidates([], out_card=facts("removal 0", categories={"removal"}), limit=-1)
