"""Tests for POST /maybeboard: the live bench, grouped by category.

The point of the endpoint is that the bench tracks the deck, so the tests that
matter here are the exclusions: a card in the deck is not on the bench, and a
card swapped out reappears on it. ``tests/test_swap.py`` owns the ranking
rules; this file owns the frontier and the grouping.

The deck fixture is a real Krenko build, so these run against the same 99 the
player would be editing.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.state import AppState
from selector.deck_rules import RuleContext, resolve_never

COMMANDER = "Krenko, Mob Boss"
# The one card shape the whole API publishes (app.schemas.DeckCardView).
CARD_FIELDS = {
    "name",
    "oracle_id",
    "scryfall_id",
    "color_identity",
    "type_line",
    "mana_cost",
    "cmc",
    "image_uri_normal",
    "image_uri_art_crop",
    "categories",
    "count",
    "slot",
    "reason",
    "score",
}


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


@pytest.fixture(scope="session")
def krenko_mainboard(real_app_state: AppState) -> list[dict]:
    """A real 99, as the client would send it back: names and counts only."""
    app_main.app.state.deckbuilder = real_app_state
    try:
        response = TestClient(app_main.app).post("/build", json={"commander": COMMANDER})
    finally:
        del app_main.app.state.deckbuilder
    assert response.status_code == 200, response.text
    deck = response.json()
    # The 99 is the two lists together: /build returns basics apart from the
    # rest, and the maybeboard wants the whole mainboard back.
    return [
        {"name": card["name"], "count": card["count"]}
        for card in deck["nonbasic_cards"] + deck["basic_lands"]
    ]


def _maybeboard(client: TestClient, deck: list[dict], **extra) -> dict:
    response = client.post(
        "/maybeboard", json={"commander": COMMANDER, "deck": deck, **extra}
    )
    assert response.status_code == 200, response.text
    return response.json()["maybeboard"]


def _names(maybeboard: dict) -> set[str]:
    return {card["name"] for cards in maybeboard.values() for card in cards}


def test_maybeboard_groups_cards_by_category(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    maybeboard = _maybeboard(client, krenko_mainboard)

    assert maybeboard, "Krenko should have a bench"
    assert all(cards for cards in maybeboard.values()), "no empty groups"
    for cards in maybeboard.values():
        assert all(set(card) == CARD_FIELDS for card in cards)


def test_maybeboard_is_ranked_by_score_within_each_category(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    for category, cards in _maybeboard(client, krenko_mainboard).items():
        scores = [card["score"] for card in cards]
        assert scores == sorted(scores, reverse=True), category


def test_maybeboard_excludes_cards_already_in_the_deck(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    """The whole reason the endpoint takes a deck instead of a commander."""
    maybeboard = _maybeboard(client, krenko_mainboard)

    in_deck = {card["name"] for card in krenko_mainboard}
    assert not (_names(maybeboard) & in_deck)


def test_maybeboard_tracks_the_deck_after_a_swap(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    """The live property: swap a bench card in and it leaves the bench.

    This is what /build's frozen maybeboard cannot do, and the reason this
    endpoint derives from the deck rather than re-running the solver.
    """
    before = _maybeboard(client, krenko_mainboard)
    promoted = next(iter(before.values()))[0]["name"]
    assert promoted not in {c["name"] for c in krenko_mainboard}

    # Swap it in for a card the deck holds, conserving the 99.
    demoted = krenko_mainboard[0]["name"]
    swapped = [c for c in krenko_mainboard if c["name"] != demoted]
    swapped.append({"name": promoted, "count": 1})

    after = _maybeboard(client, swapped)

    assert promoted not in _names(after), "a card now in the deck is still benched"


def test_maybeboard_never_offers_a_banned_card(
    client: TestClient, krenko_mainboard: list[dict], real_app_state: AppState
) -> None:
    maybeboard = _maybeboard(client, krenko_mainboard)

    assert not (_names(maybeboard) & set(real_app_state.banned_names))
    assert not (_names(maybeboard) & set(real_app_state.watchlist_names))


def test_maybeboard_never_offers_a_never_card(
    client: TestClient, krenko_mainboard: list[dict], real_app_state: AppState
) -> None:
    """`never` means "do not auto-recommend", and the bench is a recommendation."""
    ctx = RuleContext(
        commander_name=COMMANDER,
        color_identity=frozenset(
            real_app_state.commander_by_name(COMMANDER).color_identity  # type: ignore[union-attr]
        ),
        archetype="aggro",
    )
    never = resolve_never(real_app_state.rules, ctx)
    assert never, "this test needs Krenko to match some never rule"

    assert not (_names(_maybeboard(client, krenko_mainboard)) & set(never))


def test_maybeboard_stays_inside_the_commander_identity(
    client: TestClient, krenko_mainboard: list[dict], real_app_state: AppState
) -> None:
    identity = set(
        real_app_state.commander_by_name(COMMANDER).color_identity  # type: ignore[union-attr]
    )

    for cards in _maybeboard(client, krenko_mainboard).values():
        for card in cards:
            assert set(card["color_identity"]) <= identity, card["name"]


def test_maybeboard_limit_is_per_category(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    maybeboard = _maybeboard(client, krenko_mainboard, limit=2)

    assert all(len(cards) <= 2 for cards in maybeboard.values())
    # Per category and not overall: more than one category may fill up.
    assert sum(len(cards) for cards in maybeboard.values()) > 2


@pytest.mark.parametrize(("limit", "expected"), [(0, 1), (-5, 1), (999, 50)])
def test_maybeboard_clamps_the_limit(
    client: TestClient, krenko_mainboard: list[dict], limit: int, expected: int
) -> None:
    response = client.post(
        "/maybeboard",
        json={"commander": COMMANDER, "deck": krenko_mainboard, "limit": limit},
    )

    assert response.status_code == 200, response.text
    assert response.json()["limit"] == expected


def test_maybeboard_defaults_to_ten_per_category(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    body = client.post(
        "/maybeboard", json={"commander": COMMANDER, "deck": krenko_mainboard}
    ).json()

    assert body["limit"] == 10
    assert all(len(cards) <= 10 for cards in body["maybeboard"].values())


def test_maybeboard_agrees_with_the_swap_candidates(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    """Both read the same scored universe, so the bench must not contradict it.

    Candidates are the stricter list (feasibility-checked and limited to one
    category), so every candidate for a card being swapped out should be
    somewhere on the bench for that card's category.
    """
    out = "Goblin Bombardment"
    candidates = client.post(
        "/sequential/candidates",
        json={"commander": COMMANDER, "deck": krenko_mainboard, "out": out, "limit": 5},
    ).json()

    assert candidates["candidates"], "need candidates to compare against"

    benched = _names(_maybeboard(client, krenko_mainboard, limit=50))
    for candidate in candidates["candidates"]:
        assert candidate["name"] in benched, candidate["name"]


def test_maybeboard_unknown_commander_is_a_404(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    response = client.post(
        "/maybeboard", json={"commander": "Not A Commander", "deck": krenko_mainboard}
    )

    assert response.status_code == 404


def test_maybeboard_unknown_card_is_a_422(client: TestClient) -> None:
    response = client.post(
        "/maybeboard",
        json={"commander": COMMANDER, "deck": [{"name": "Not A Card", "count": 1}]},
    )

    assert response.status_code == 422


def test_maybeboard_empty_deck_is_a_422(client: TestClient) -> None:
    response = client.post("/maybeboard", json={"commander": COMMANDER, "deck": []})

    assert response.status_code == 422


def test_maybeboard_rejects_bands_from_the_client(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    """Same anti-tampering point as /build: bands are never received."""
    response = client.post(
        "/maybeboard",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "bands": {"lands": {"lo": 0, "hi": 99}},
        },
    )

    assert response.status_code == 422


def test_maybeboard_is_degraded_without_a_pool(
    degraded_client: TestClient, krenko_mainboard: list[dict]
) -> None:
    response = degraded_client.post(
        "/maybeboard", json={"commander": COMMANDER, "deck": krenko_mainboard}
    )

    assert response.status_code == 503
