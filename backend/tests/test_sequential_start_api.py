"""Tests for POST /sequential/start: the guided build's entry point.

Two things are tested here. The elbow itself (``compute_decisions``) is pure
arithmetic and is tested directly on synthetic scores, where the conservative
choices — lower half only, deepest tie — can actually be pinned down. The
endpoint is then tested against a real Krenko build for the contract: the deck
is a /build deck, the decisions point into it, and the solver ran once.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app import service
from app.schemas import DeckCardView
from app.state import AppState

COMMANDER = "Krenko, Mob Boss"


@pytest.fixture()
def client(real_app_state: AppState) -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = real_app_state
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


@pytest.fixture()
def degraded_client() -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = None
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


# --- the elbow --------------------------------------------------------------


def _card(name: str, score: float, slot: str = "removal") -> DeckCardView:
    """A deck card with only the fields the elbow reads."""
    return DeckCardView(
        name=name,
        oracle_id=f"id-{name}",
        scryfall_id=f"sf-{name}",
        color_identity=["R"],
        type_line="Instant",
        mana_cost="{R}",
        cmc=1.0,
        image_uri_normal=None,
        image_uri_art_crop=None,
        categories=[slot],
        count=1,
        slot=slot,
        reason="",
        score=score,
    )


def test_the_elbow_cuts_at_the_largest_gap_in_the_lower_half() -> None:
    """Six cards: lower half is the last three, and the cliff is inside it."""
    cards = [
        _card("a", 10.0),
        _card("b", 9.0),
        _card("c", 8.0),
        _card("d", 7.0),
        _card("e", 1.0),  # the cliff: 7.0 -> 1.0
        _card("f", 0.9),
    ]

    decisions = service.compute_decisions(cards)

    assert [d.name for d in decisions] == ["f", "e"]


def test_the_elbow_never_reaches_above_the_lower_half() -> None:
    """The biggest gap of all is between the 1st and 2nd card; it is ignored.

    Nobody wants to be asked about their best card, so only the lower half is
    ever eligible however dramatic the gap above it.
    """
    cards = [
        _card("a", 100.0),  # the largest gap in the list is right below here
        _card("b", 5.0),
        _card("c", 4.9),
        _card("d", 4.8),
        _card("e", 4.7),
        _card("f", 4.6),
    ]

    decisions = service.compute_decisions(cards)

    assert "a" not in [d.name for d in decisions]
    assert "b" not in [d.name for d in decisions]


def test_a_gap_tie_picks_the_deepest_and_flags_fewer_cards() -> None:
    """Evenly spaced scores: every gap ties, so the cut is the last one."""
    cards = [_card(name, score) for name, score in zip("abcdef", [6.0, 5.0, 4.0, 3.0, 2.0, 1.0])]

    decisions = service.compute_decisions(cards)

    # Lower half is d, e, f; all gaps are 1.0, so the deepest tie cuts last.
    assert [d.name for d in decisions] == ["f"]


def test_a_category_below_the_minimum_size_is_never_decided() -> None:
    """Three cards is not enough of a curve to call anything an outlier."""
    cards = [_card("a", 10.0), _card("b", 9.0), _card("c", 0.1)]

    assert service.compute_decisions(cards) == []


def test_each_category_is_decided_on_its_own_scale() -> None:
    """A cheap removal is not doubtful just because ramp scores higher."""
    ramp = [_card(f"r{i}", score, "ramp") for i, score in enumerate([90.0, 89.0, 88.0, 87.0])]
    removal = [_card(f"x{i}", score, "removal") for i, score in enumerate([9.0, 8.0, 7.0, 6.0])]

    decisions = service.compute_decisions(ramp + removal)

    assert {d.category for d in decisions} == {"ramp", "removal"}


def test_decisions_are_worst_first_and_capped() -> None:
    many = [
        _card(f"c{i:02d}", float(100 - i), f"cat{i // 8}")
        for i in range(80)
    ]

    decisions = service.compute_decisions(many)

    assert len(decisions) <= service.MAX_DECISIONS
    scores = [d.score for d in decisions]
    assert scores == sorted(scores), "worst first"


def test_the_cap_is_honoured(krenko_start: dict) -> None:
    assert len(krenko_start["decisions"]) <= service.MAX_DECISIONS


def test_a_scoreless_card_is_skipped_rather_than_ranked_as_zero() -> None:
    """Only basics are scoreless, and a scoreless card cannot sit on an elbow."""
    cards = [_card(n, s) for n, s in zip("abcde", [5.0, 4.0, 3.0, 2.0, 1.0])]
    scoreless = _card("z", 0.0)
    cards.append(DeckCardView(**{**scoreless.model_dump(), "score": None}))

    decisions = service.compute_decisions(cards)

    assert "z" not in [d.name for d in decisions]


# --- the endpoint -----------------------------------------------------------


@pytest.fixture(scope="session")
def krenko_start(real_app_state: AppState) -> dict:
    """One real guided build, reused: the solver is the slow part of this file."""
    app_main.app.state.deckbuilder = real_app_state
    try:
        response = TestClient(app_main.app).post(
            "/sequential/start", json={"commander": COMMANDER}
        )
    finally:
        del app_main.app.state.deckbuilder
    assert response.status_code == 200, response.text
    return response.json()


def test_the_deck_is_exactly_a_build_deck(
    krenko_start: dict, real_app_state: AppState
) -> None:
    """`deck` is /build's response, not a variant of it: same shape, same deck."""
    app_main.app.state.deckbuilder = real_app_state
    try:
        built = TestClient(app_main.app).post(
            "/build", json={"commander": COMMANDER}
        ).json()
    finally:
        del app_main.app.state.deckbuilder

    deck = krenko_start["deck"]
    assert set(deck) == set(built)
    assert deck["nonbasic_cards"] == built["nonbasic_cards"]
    assert deck["basic_lands"] == built["basic_lands"]


def test_the_response_is_only_the_deck_and_the_decisions(krenko_start: dict) -> None:
    assert set(krenko_start) == {"deck", "decisions"}


def test_every_decision_points_at_a_card_in_the_deck(krenko_start: dict) -> None:
    """Decisions are pointers: the art comes from the deck, keyed by oracle_id."""
    by_id = {card["oracle_id"]: card for card in krenko_start["deck"]["nonbasic_cards"]}

    assert krenko_start["decisions"], "Krenko's deck should have doubtful cards"
    for decision in krenko_start["decisions"]:
        assert set(decision) == {"oracle_id", "name", "category", "score"}
        card = by_id[decision["oracle_id"]]
        assert card["name"] == decision["name"]
        assert card["slot"] == decision["category"]
        assert card["score"] == decision["score"]


def test_no_basic_land_is_ever_decided(krenko_start: dict) -> None:
    """Basics are interchangeable copies, not cards to weigh."""
    basics = {card["oracle_id"] for card in krenko_start["deck"]["basic_lands"]}

    assert not (basics & {d["oracle_id"] for d in krenko_start["decisions"]})


def test_decisions_are_worst_first(krenko_start: dict) -> None:
    scores = [d["score"] for d in krenko_start["decisions"]]
    assert scores == sorted(scores)


def test_a_decided_card_is_below_its_categorys_median(krenko_start: dict) -> None:
    """The lower-half rule, checked on the real deck."""
    by_slot: dict[str, list[float]] = {}
    for card in krenko_start["deck"]["nonbasic_cards"]:
        if card["score"] is not None:
            by_slot.setdefault(card["slot"], []).append(card["score"])

    for decision in krenko_start["decisions"]:
        scores = sorted(by_slot[decision["category"]], reverse=True)
        lower = scores[len(scores) // 2 :]
        assert decision["score"] <= lower[0]


def test_the_dials_reach_the_guided_build(client: TestClient) -> None:
    response = client.post(
        "/sequential/start", json={"commander": COMMANDER, "dials": {"lands": "low"}}
    )

    assert response.status_code == 200
    assert response.json()["deck"]["dials"] == {"lands": "low"}


def test_bands_in_the_request_are_rejected(client: TestClient) -> None:
    """Same anti-tampering as /build: it takes the same request model."""
    response = client.post(
        "/sequential/start",
        json={"commander": COMMANDER, "bands": {"lands": {"lo": 0, "hi": 99}}},
    )

    assert response.status_code == 422


def test_an_unknown_commander_is_a_404(client: TestClient) -> None:
    response = client.post("/sequential/start", json={"commander": "Fake McFakeface"})
    assert response.status_code == 404


def test_sequential_start_is_degraded_without_a_pool(
    degraded_client: TestClient,
) -> None:
    response = degraded_client.post("/sequential/start", json={"commander": COMMANDER})
    assert response.status_code == 503
