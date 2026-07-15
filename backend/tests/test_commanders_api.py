"""Tests for the commander endpoints: featured list and name search."""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.state import AppState

CARD_FIELDS = {"name", "oracle_id", "scryfall_id", "color_identity"}


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


# --- featured ---------------------------------------------------------------


def test_featured_returns_the_whole_list_in_file_order(
    client: TestClient, real_app_state: AppState
) -> None:
    response = client.get("/api/commanders/featured")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 55
    assert [c["name"] for c in body] == [c.name for c in real_app_state.featured]


def test_featured_cards_carry_the_full_card_shape(client: TestClient) -> None:
    body = client.get("/api/commanders/featured").json()
    assert all(set(card) == CARD_FIELDS for card in body)
    # Fase 5 builds the images from scryfall_id: it must never be empty.
    assert all(card["scryfall_id"] for card in body)


def test_featured_is_degraded_without_a_pool(degraded_client: TestClient) -> None:
    assert degraded_client.get("/api/commanders/featured").status_code == 503


# --- search -----------------------------------------------------------------


def test_search_finds_a_commander(client: TestClient) -> None:
    body = client.get("/api/commanders", params={"q": "krenko"}).json()
    assert "Krenko, Mob Boss" in [c["name"] for c in body]


def test_search_is_case_insensitive(client: TestClient) -> None:
    lower = client.get("/api/commanders", params={"q": "krenko"}).json()
    upper = client.get("/api/commanders", params={"q": "KRENKO"}).json()
    assert lower == upper


def test_search_handles_commas_and_apostrophes(client: TestClient) -> None:
    """The reason q is a query param and not a path segment."""
    body = client.get(
        "/api/commanders", params={"q": "Atraxa, Praetors' Voice"}
    ).json()
    assert [c["name"] for c in body] == ["Atraxa, Praetors' Voice"]


def test_search_ranks_prefix_matches_before_substring_matches(
    real_app_state: AppState,
) -> None:
    # "god" matches both ways: "Goda, ..." by prefix, "Tergrid, God of
    # Fright" only by substring.
    rows = real_app_state.search_commanders("god", limit=50)
    names = [row.name for row in rows]
    prefixes = [n for n in names if n.lower().startswith("god")]
    assert len(names) > len(prefixes) > 0, "need both ranks to prove the order"
    assert names[: len(prefixes)] == prefixes, names


def test_search_is_alphabetical_within_each_rank(real_app_state: AppState) -> None:
    names = [row.name for row in real_app_state.search_commanders("the", limit=50)]
    prefixes = [n for n in names if n.lower().startswith("the")]
    rest = names[len(prefixes) :]
    assert prefixes == sorted(prefixes)
    assert rest == sorted(rest)


@pytest.mark.parametrize("q", ["", "k", " "])
def test_search_below_the_minimum_returns_empty(client: TestClient, q: str) -> None:
    """Short queries must not dump thousands of rows."""
    response = client.get("/api/commanders", params={"q": q})
    assert response.status_code == 200
    assert response.json() == []


def test_search_without_q_is_a_422(client: TestClient) -> None:
    assert client.get("/api/commanders").status_code == 422


def test_search_defaults_to_20_results(client: TestClient) -> None:
    body = client.get("/api/commanders", params={"q": "the"}).json()
    assert len(body) == 20


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(0, 1), (-5, 1), (1, 1), (50, 50), (999, 50)],
)
def test_search_clamps_the_limit(
    client: TestClient, limit: int, expected: int
) -> None:
    body = client.get("/api/commanders", params={"q": "the", "limit": limit}).json()
    assert len(body) == expected


def test_search_never_returns_a_banned_commander(
    real_app_state: AppState, client: TestClient
) -> None:
    banlist = real_app_state.resolved_banlist
    hidden = banlist.banned | banlist.banned_as_commander
    body = client.get("/api/commanders", params={"q": "the", "limit": 50}).json()
    assert all(card["oracle_id"] not in hidden for card in body)


def test_search_excludes_a_specific_banned_commander(
    real_app_state: AppState,
) -> None:
    """Pick a real banned_as_commander card and prove search hides it."""
    banned_ids = real_app_state.resolved_banlist.banned_as_commander
    assert banned_ids, "the real banlist should ban some commander"
    names = {
        card["name"]
        for card in (
            real_app_state.pool.by_name[name]
            for name in real_app_state.pool.by_name
        )
        if card["oracle_id"] in banned_ids
    }
    for name in names:
        assert real_app_state.commander_by_name(name) is None
        found = real_app_state.search_commanders(name, limit=50)
        assert name not in [row.name for row in found]


def test_search_results_carry_the_full_card_shape(client: TestClient) -> None:
    body = client.get("/api/commanders", params={"q": "krenko"}).json()
    assert body and all(set(card) == CARD_FIELDS for card in body)


def test_search_is_degraded_without_a_pool(degraded_client: TestClient) -> None:
    response = degraded_client.get("/api/commanders", params={"q": "krenko"})
    assert response.status_code == 503
