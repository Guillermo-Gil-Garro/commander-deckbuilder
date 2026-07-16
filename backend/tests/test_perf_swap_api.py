"""The roadmap's hard requirement: a swap re-validates in under 100 ms.

``tests/test_perf_swap.py`` measures the pure function against a 20 ms budget.
This measures what the roadmap actually promised: the whole HTTP cycle —
routing, pydantic validation of a 99-card body, rebuilding every ``CardFacts``
from the pool + tagger, resolving the bands, the verdict, and serialising the
response — over the real pool and a real Krenko deck.

**Why the median of 30.** Same reason as the pure test: the maximum measures
the machine (one GC pause, one scheduler preemption), the median measures the
code.

**Why it skips instead of failing without a pool.** ``cards.jsonl`` is
gitignored, so in CI this test would measure nothing. It runs on Guille's
machine, which is the only place where the number means something.
"""

from __future__ import annotations

import logging
import statistics
import time
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.state import AppState
from rules.resolve import DEFAULT_POOL_PATH

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    not DEFAULT_POOL_PATH.is_file(),
    reason=f"no card pool at {DEFAULT_POOL_PATH}: the number would be meaningless",
)

COMMANDER = "Krenko, Mob Boss"
N_RUNS = 30
BUDGET_MS = 100.0


@pytest.fixture()
def client(real_app_state: AppState) -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = real_app_state
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


@pytest.mark.perf
def test_the_full_http_swap_validation_fits_in_100ms(client: TestClient) -> None:
    built = client.post("/build", json={"commander": COMMANDER}).json()
    deck = [
        {"name": card["name"], "count": card["count"]}
        for card in built["nonbasic_cards"] + built["basic_lands"]
    ]
    candidates = client.post(
        "/sequential/candidates",
        json={"commander": COMMANDER, "deck": deck, "out": "Goblin Bombardment", "limit": 1},
    ).json()["candidates"]
    assert candidates, "need a real replacement for a meaningful measurement"

    payload = {
        "commander": COMMANDER,
        "deck": deck,
        "out": "Goblin Bombardment",
        "in": candidates[0]["name"],
    }

    def call() -> None:
        response = client.post("/sequential/validate", json=payload)
        assert response.status_code == 200
        assert response.json()["feasible"] is True

    call()  # warm up: the first call pays for imports and dict resizing
    timings = []
    for _ in range(N_RUNS):
        start = time.perf_counter()
        call()
        timings.append((time.perf_counter() - start) * 1000.0)
    median = statistics.median(timings)

    logger.info(
        "POST /sequential/validate (99 cards): median %.2f ms, max %.2f ms "
        "over %d runs",
        median,
        max(timings),
        N_RUNS,
    )
    assert median < BUDGET_MS, f"median {median:.2f} ms >= {BUDGET_MS} ms"
