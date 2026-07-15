"""Latency budget of the pure swap path (Fase 4's hard requirement).

**Why 20 ms and not the roadmap's 100 ms.** The 100 ms in the roadmap is
end-to-end: FastAPI routing, validation, serialisation and this function. The
pure part must therefore sit an order of magnitude below it — a 100 ms assert
on the pure function would happily pass a 10x regression that blows the real
budget, which makes it a test that cannot fail for the reason we care about.

**Why the median of 50 and not the maximum.** The maximum on a shared runner
measures the runner: one GC pause or one scheduler preemption and the test goes
red for reasons that have nothing to do with the code. The median measures the
code. A regression that matters (an O(99) recount slipped inside the candidate
loop) moves the median, not just the tail.
"""

from __future__ import annotations

import logging
import statistics
import time

import pytest

from quotas.config import QuotaBand
from selector.constraints import CardFacts
from selector.greedy import DECK_SIZE
from selector.swap import swap_candidates, swap_is_feasible

logger = logging.getLogger(__name__)

N_CANDIDATES = 500
N_RUNS = 50
BUDGET_MS = 20.0

CATEGORIES = ("ramp", "card_draw", "removal", "board_wipe", "wincons")


def bands() -> dict[str, QuotaBand]:
    return {
        "lands": QuotaBand(min=34, max=40),
        "ramp": QuotaBand(min=8, max=14),
        "card_draw": QuotaBand(min=8, max=14),
        "removal": QuotaBand(min=6, max=12),
        "board_wipe": QuotaBand(min=2, max=5),
        "wincons": QuotaBand(min=2, max=5),
        "synergy": QuotaBand(min=0, max=60),
    }


def card(name: str, *, categories: set[str], cmc: float = 3.0) -> CardFacts:
    return CardFacts(
        name=name,
        oracle_id=f"oid-{name}",
        categories=frozenset(categories),
        cmc=cmc,
        mana_cost="{2}{R}",
        color_identity=frozenset("R"),
    )


def synthetic_deck() -> list[tuple[CardFacts, int]]:
    """A realistic 99: 36 basics, quota spells across the curve, synergy filler."""
    rows: list[tuple[CardFacts, int]] = []
    for category in CATEGORIES:
        rows += [
            (card(f"{category} {i}", categories={category}, cmc=float(i % 6)), 1)
            for i in range(10)
        ]
    filler = DECK_SIZE - 36 - sum(count for _, count in rows)
    rows += [
        (card(f"Synergy {i}", categories={"synergy"}, cmc=float(i % 7)), 1)
        for i in range(filler)
    ]
    rows.append(
        (
            CardFacts(
                name="Mountain",
                oracle_id="oid-Mountain",
                categories=frozenset({"lands"}),
                cmc=0.0,
                mana_cost="",
                color_identity=frozenset("R"),
                is_basic=True,
            ),
            36,
        )
    )
    assert sum(count for _, count in rows) == DECK_SIZE
    return rows


def synthetic_candidates() -> list[tuple[CardFacts, float]]:
    """500 recommendations spread over every category, none in the deck."""
    return [
        (
            card(
                f"Cand {i}",
                categories={CATEGORIES[i % len(CATEGORIES)]},
                cmc=float(i % 8),
            ),
            1.0 - i / N_CANDIDATES,
        )
        for i in range(N_CANDIDATES)
    ]


def median_ms(call) -> float:
    call()  # warm up: the first call pays for imports and dict resizing
    timings = []
    for _ in range(N_RUNS):
        start = time.perf_counter()
        call()
        timings.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(timings)


@pytest.mark.perf
def test_single_swap_validation_is_well_under_the_budget() -> None:
    rows = synthetic_deck()
    out_card, _ = rows[0]
    in_card = card("Fresh Blood", categories={"removal"}, cmc=2.0)
    median = median_ms(
        lambda: swap_is_feasible(
            deck=rows,
            out_card=out_card,
            in_card=in_card,
            bands=bands(),
            commander=card("Boss Goblin", categories=set()),
            banned_names=frozenset(),
            never_names=frozenset(),
            watchlist_names=frozenset(),
        )
    )
    logger.info("swap_is_feasible: median %.3f ms over %d runs", median, N_RUNS)
    assert median < BUDGET_MS, f"median {median:.3f} ms >= {BUDGET_MS} ms"


@pytest.mark.perf
def test_ranking_500_candidates_is_well_under_the_budget() -> None:
    # The real load: one deck count, then 500 O(categories) verdicts. If a
    # recount of the 99 ever creeps into the candidate loop, this is what sees it.
    rows = synthetic_deck()
    out_card = next(c for c, _ in rows if "removal" in c.categories)
    pool = synthetic_candidates()
    median = median_ms(
        lambda: swap_candidates(
            deck=rows,
            out_card=out_card,
            pool_candidates=pool,
            bands=bands(),
            commander=card("Boss Goblin", categories=set()),
            banned_names=frozenset(),
            never_names=frozenset(),
            watchlist_names=frozenset(),
            limit=10,
        )
    )
    logger.info(
        "swap_candidates (%d candidates): median %.3f ms over %d runs",
        N_CANDIDATES,
        median,
        N_RUNS,
    )
    assert median < BUDGET_MS, f"median {median:.3f} ms >= {BUDGET_MS} ms"


@pytest.mark.perf
def test_the_ranking_actually_returns_candidates() -> None:
    # A timing test on an empty result set would measure nothing.
    rows = synthetic_deck()
    out_card = next(c for c, _ in rows if "removal" in c.categories)
    found, total = swap_candidates(
        deck=rows,
        out_card=out_card,
        pool_candidates=synthetic_candidates(),
        bands=bands(),
        commander=card("Boss Goblin", categories=set()),
        banned_names=frozenset(),
        never_names=frozenset(),
        watchlist_names=frozenset(),
        limit=10,
    )
    assert len(found) == 10
    assert total == N_CANDIDATES // len(CATEGORIES)
