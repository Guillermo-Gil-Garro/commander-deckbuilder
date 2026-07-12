"""Tests for the hypergeometric color-source demand table (ported from the TFM).

Pins the hypergeometric contract, the minimality of ``K(pips, turn)``, the
monotonicity sanity checks, the on-the-play vs on-the-draw direction, and the
external validation against Karsten's published anchor (raw table, WITHOUT the
0.80 calibration factor). Deterministic and network-free (``math.comb``).
"""

from __future__ import annotations

import pytest

from pipeline.model import count_pips
from quotas.color_sources import (
    DEFAULT_DECK_SIZE,
    DEFAULT_RELIABILITY,
    DEMAND_CALIBRATION_FACTOR,
    DEMAND_TABLE,
    KARSTEN_ON_CURVE_ANCHOR,
    PIPS,
    TURNS,
    build_demand_table,
    card_color_pips,
    cards_seen,
    color_source_demand,
    color_source_targets,
    min_sources,
    pool_color_source_targets,
    prob_at_least,
    target_turn,
)


def test_key_parameters_are_preserved() -> None:
    assert DEFAULT_RELIABILITY == 0.90
    assert DEFAULT_DECK_SIZE == 99
    assert DEMAND_CALIBRATION_FACTOR == 0.80
    assert KARSTEN_ON_CURVE_ANCHOR == {1: 22, 2: 29, 3: 34}


def test_cards_seen_play_vs_draw() -> None:
    # On the play: opening hand, no turn-1 draw. On the draw: one extra per turn.
    assert cards_seen(1, on_play=True) == 7
    assert cards_seen(3, on_play=True) == 9
    assert cards_seen(1, on_play=False) == 8
    assert cards_seen(3, on_play=False) == 10


def test_cards_seen_rejects_turn_zero() -> None:
    with pytest.raises(ValueError):
        cards_seen(0, on_play=True)


def test_prob_at_least_boundaries() -> None:
    # Seeing the whole library guarantees you have seen every source.
    assert prob_at_least(1, sources=10, seen=DEFAULT_DECK_SIZE, deck_size=DEFAULT_DECK_SIZE) == 1.0
    # Zero sources can never satisfy a positive pip requirement.
    assert prob_at_least(1, sources=0, seen=7, deck_size=DEFAULT_DECK_SIZE) == 0.0
    # Fewer sources in the deck than pips required -> impossible.
    assert prob_at_least(3, sources=2, seen=7, deck_size=DEFAULT_DECK_SIZE) == 0.0
    # A probability is a probability.
    assert 0.0 <= prob_at_least(2, sources=30, seen=9, deck_size=DEFAULT_DECK_SIZE) <= 1.0


def test_prob_at_least_known_value() -> None:
    # Small exact case, computed by hand: N=10, K=4 sources, n=3 seen.
    # P(X >= 1) = 1 - C(6,3)/C(10,3) = 1 - 20/120 = 5/6.
    assert prob_at_least(1, sources=4, seen=3, deck_size=10) == pytest.approx(5 / 6)
    # P(X >= 2) = [C(4,2)*C(6,1) + C(4,3)] / C(10,3) = (36 + 4) / 120 = 1/3.
    assert prob_at_least(2, sources=4, seen=3, deck_size=10) == pytest.approx(1 / 3)


def test_prob_at_least_validates_domain() -> None:
    with pytest.raises(ValueError):
        prob_at_least(1, sources=DEFAULT_DECK_SIZE + 1, seen=7, deck_size=DEFAULT_DECK_SIZE)
    with pytest.raises(ValueError):
        prob_at_least(1, sources=10, seen=DEFAULT_DECK_SIZE + 1, deck_size=DEFAULT_DECK_SIZE)


@pytest.mark.parametrize("pips", PIPS)
@pytest.mark.parametrize("turn", TURNS)
def test_min_sources_is_minimal(pips: int, turn: int) -> None:
    """The returned K satisfies the threshold and K-1 does not (true minimum)."""
    k = min_sources(pips, turn)
    seen = cards_seen(turn, on_play=True)
    assert prob_at_least(pips, k, seen, DEFAULT_DECK_SIZE) >= DEFAULT_RELIABILITY
    if k > pips:
        assert prob_at_least(pips, k - 1, seen, DEFAULT_DECK_SIZE) < DEFAULT_RELIABILITY


@pytest.mark.parametrize("pips", PIPS)
@pytest.mark.parametrize("turn", TURNS)
def test_demand_within_bounds(pips: int, turn: int) -> None:
    k = DEMAND_TABLE[pips][turn]
    assert pips <= k <= DEFAULT_DECK_SIZE


def test_monotonic_in_pips_for_fixed_turn() -> None:
    # More colored pips never require fewer sources.
    for turn in TURNS:
        for earlier, later in zip(PIPS, PIPS[1:]):
            assert DEMAND_TABLE[later][turn] >= DEMAND_TABLE[earlier][turn]


def test_monotonic_in_turn_for_castable_cells() -> None:
    # A later target turn (more cards seen) never requires more sources.
    # Restricted to the castable region turn >= pips.
    for pips in PIPS:
        castable = [turn for turn in TURNS if turn >= pips]
        for earlier, later in zip(castable, castable[1:]):
            assert DEMAND_TABLE[pips][later] <= DEMAND_TABLE[pips][earlier]


def test_on_draw_never_exceeds_on_play() -> None:
    # Seeing one more card per turn can only lower (or keep) the requirement.
    on_play = build_demand_table(on_play=True)
    on_draw = build_demand_table(on_play=False)
    for pips in PIPS:
        for turn in TURNS:
            assert on_draw[pips][turn] <= on_play[pips][turn]


def test_build_demand_table_is_deterministic() -> None:
    assert build_demand_table() == build_demand_table()
    assert build_demand_table() == DEMAND_TABLE


# --- Karsten anchor (external validation, raw table WITHOUT the 0.80 factor) --


def test_on_curve_diagonal_exact_values() -> None:
    # Regression pin of the raw hypergeometric on-curve diagonal (turn == pips).
    assert {pips: DEMAND_TABLE[pips][pips] for pips in PIPS} == {1: 27, 2: 40, 3: 48}


def test_on_curve_diagonal_vs_karsten_anchor() -> None:
    # The --compare logic: delta >= 0 expected, since no mulligan + on the play is
    # more conservative than Karsten's published numbers (which fold in the free
    # Commander mulligan). The raw table is compared, never the calibrated one.
    diagonal = [DEMAND_TABLE[pips][pips] for pips in PIPS]
    assert diagonal == sorted(diagonal)
    for pips in PIPS:
        assert DEMAND_TABLE[pips][pips] >= KARSTEN_ON_CURVE_ANCHOR[pips]
    # Sanity band, not a calibration target: catches absurd values without
    # rejecting the legitimately high triple-pip-on-curve count (~48/99).
    for k in diagonal:
        assert 15 <= k <= 55


# --- Calibrated demand ---------------------------------------------------------


def test_color_source_demand_lookup_and_domain() -> None:
    # The entry point returns the empirically calibrated demand (round(factor * K)),
    # not the raw theoretical table (which stays pure for the Karsten comparison).
    assert color_source_demand(2, 4) == round(DEMAND_CALIBRATION_FACTOR * DEMAND_TABLE[2][4])
    with pytest.raises(ValueError):
        color_source_demand(4, 1)
    with pytest.raises(ValueError):
        color_source_demand(1, 8)


def test_color_source_targets_on_curve_calibrated() -> None:
    # round(0.80 * {27, 40, 48}) == {22, 32, 38}.
    assert color_source_targets({"R": 2, "G": 1}) == {"R": 32, "G": 22}
    assert color_source_targets({"B": 3}) == {"B": 38}


def test_color_source_targets_omits_zero_and_clamps_high_pips() -> None:
    assert color_source_targets({"W": 0}) == {}
    assert color_source_targets({}) == {}
    # 4+ same-color pips is outside the table domain; clamp to the 3-pip demand.
    assert color_source_targets({"U": 5}) == color_source_targets({"U": 3})


def test_color_source_targets_validates_input() -> None:
    with pytest.raises(ValueError):
        color_source_targets({"Z": 1})
    with pytest.raises(ValueError):
        color_source_targets({"C": 1})  # colorless is not a fixable color
    with pytest.raises(ValueError):
        color_source_targets({"W": -1})


# --- Per-card colored pips (pure pips vs the pipeline's inclusive count) ------


def test_card_color_pips_counts_pure_pips() -> None:
    assert card_color_pips("{2}{R}{R}") == {"R": 2}
    assert card_color_pips("{G}{W}{U}") == {"G": 1, "W": 1, "U": 1}


def test_card_color_pips_excludes_hybrid_and_phyrexian() -> None:
    # Hybrid {W/U} and phyrexian {R/P} are not a committed demand for one color.
    assert card_color_pips("{W/U}{R/P}{R}") == {"R": 1}
    assert card_color_pips("{G/U}{G/U}") == {}


def test_card_color_pips_ignores_generic_colorless_variable() -> None:
    assert card_color_pips("{3}") == {}
    assert card_color_pips("{X}{C}{1}") == {}
    assert card_color_pips("") == {}


def test_target_turn_shifts_at_or_above_commander_cost() -> None:
    # Below the commander's cost: cast on curve. At or above: one turn later.
    assert target_turn(2.0, commander_mana_value=4.0) == 2
    assert target_turn(4.0, commander_mana_value=4.0) == 5
    assert target_turn(6.0, commander_mana_value=4.0) == 7


def test_target_turn_clamps_to_table_domain() -> None:
    assert target_turn(0.0, commander_mana_value=4.0) == 1
    assert target_turn(9.0, commander_mana_value=4.0) == 7
    assert target_turn(7.0, commander_mana_value=8.0) == 7


def test_pool_targets_relax_vs_on_curve_for_late_cards() -> None:
    # A double-pip card at the commander's cost demands turn 5 sources, strictly
    # fewer than the on-curve turn-2 benchmark for the same two pips.
    pool = pool_color_source_targets([("{2}{U}{U}", 4.0)], commander_mana_value=4.0)
    on_curve = color_source_targets({"U": 2})
    assert pool["U"] == color_source_demand(2, 5)
    assert pool["U"] < on_curve["U"]


def test_pool_targets_take_max_demand_per_color() -> None:
    # The early single-pip card, not the late double-pip one, can dominate.
    pool = pool_color_source_targets(
        [("{R}", 1.0), ("{5}{R}{R}", 7.0)], commander_mana_value=5.0
    )
    assert pool["R"] == max(color_source_demand(1, 1), color_source_demand(2, 7))


def test_pool_targets_ignore_colorless_and_hybrid_only_cards() -> None:
    pool = pool_color_source_targets(
        [("{3}", 3.0), ("{W/U}{W/U}", 2.0)], commander_mana_value=4.0
    )
    assert pool == {}


def test_pure_pips_differ_from_pipeline_count_pips_on_hybrids() -> None:
    # pipeline.model.count_pips (the Card.pips field) counts hybrid/phyrexian once
    # per color; the Karsten fixing axis must not, or it overstates the demand.
    cost = "{W/U}{W/U}{U}"
    inclusive = count_pips(cost)
    assert inclusive["W"] == 2 and inclusive["U"] == 3
    assert card_color_pips(cost) == {"U": 1}
