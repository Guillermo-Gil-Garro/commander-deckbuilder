"""Tests for POST /api/deck: the build endpoint's HTTP contract.

The solver is not exercised here — ``tests/test_cpsat.py`` owns that. What is
tested is the frontier: which failure gets which status code, that the bands
cannot be tampered with, and that the response carries what Fase 5 needs.

Every EDHREC call is monkeypatched: these tests never touch the network.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app import service
from app.state import AppState
from pipeline.edhrec import EdhrecCommanderData, EdhrecError, EdhrecNotFound

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


@pytest.fixture()
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test would hit EDHREC instead of the disk cache."""

    def boom(name: str, variant: str | None = None) -> EdhrecCommanderData:
        raise AssertionError(f"unexpected EDHREC fetch for {name!r} ({variant})")

    monkeypatch.setattr(service, "fetch_commander", boom)


# --- errors -----------------------------------------------------------------


def test_unknown_commander_is_a_404(client: TestClient, no_network: None) -> None:
    response = client.post("/api/deck", json={"commander": "Fake McFakeface"})

    assert response.status_code == 404
    assert "Fake McFakeface" in response.json()["detail"]


def test_a_card_that_is_not_a_commander_is_a_404(
    client: TestClient, no_network: None
) -> None:
    """Sol Ring is in the pool and is not commander-eligible."""
    response = client.post("/api/deck", json={"commander": "Sol Ring"})

    assert response.status_code == 404


def test_a_banned_commander_is_a_422(
    client: TestClient, real_app_state: AppState, no_network: None
) -> None:
    """"That commander is banned" is about the input, not a missing resource."""
    banned_ids = real_app_state.resolved_banlist.banned_as_commander
    assert banned_ids, "the real banlist should ban some commander"
    name = next(
        card["name"]
        for card in real_app_state.pool.cards()
        if card.get("oracle_id") in banned_ids
    )

    response = client.post("/api/deck", json={"commander": name})

    assert response.status_code == 422
    assert "banlist" in response.json()["detail"]


def test_a_commander_without_an_edhrec_page_is_a_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EDHREC's data gap: their problem to fix, the player's to work around."""

    def not_found(name: str, variant: str | None = None) -> EdhrecCommanderData:
        raise EdhrecNotFound("no page")

    monkeypatch.setattr(service, "fetch_commander", not_found)

    response = client.post("/api/deck", json={"commander": "Yargle, Glutton of Urborg"})

    assert response.status_code == 404
    assert "EDHREC" in response.json()["detail"]


def test_edhrec_being_down_is_a_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Our outage, not the player's input: never a 4xx."""

    def down(name: str, variant: str | None = None) -> EdhrecCommanderData:
        raise EdhrecError("connection reset")

    monkeypatch.setattr(service, "fetch_commander", down)

    response = client.post("/api/deck", json={"commander": "Yargle, Glutton of Urborg"})

    assert response.status_code == 502


def test_an_invalid_dial_is_a_422(client: TestClient, no_network: None) -> None:
    response = client.post(
        "/api/deck", json={"commander": COMMANDER, "dials": {"lands": "yolo"}}
    )

    assert response.status_code == 422
    assert "low" in response.json()["detail"]


def test_a_dial_on_a_category_without_one_is_a_422(
    client: TestClient, no_network: None
) -> None:
    """quotas.yaml defines no dial for wincons: the config is the judge."""
    response = client.post(
        "/api/deck", json={"commander": COMMANDER, "dials": {"wincons": "high"}}
    )

    assert response.status_code == 422


def test_the_deck_endpoint_is_degraded_without_a_pool(
    degraded_client: TestClient,
) -> None:
    response = degraded_client.post("/api/deck", json={"commander": COMMANDER})
    assert response.status_code == 503


# --- the anti-tampering point -----------------------------------------------


def test_bands_in_the_request_are_rejected(client: TestClient, no_network: None) -> None:
    """The point of the whole stateless design.

    A client that could send its own bands could relax any quota and validate
    any deck. `extra="forbid"` is what makes that a 422 instead of a silently
    ignored key that someone later "helpfully" starts reading.
    """
    response = client.post(
        "/api/deck",
        json={
            "commander": COMMANDER,
            "bands": {"lands": {"min": 0, "max": 99}},
        },
    )

    assert response.status_code == 422


def test_an_unknown_request_field_is_rejected(
    client: TestClient, no_network: None
) -> None:
    response = client.post(
        "/api/deck", json={"commander": COMMANDER, "solver_time_limit_s": 600}
    )

    assert response.status_code == 422


# --- the happy path ---------------------------------------------------------


@pytest.fixture(scope="session")
def krenko_deck(real_app_state: AppState) -> dict:
    """One real build, reused: the solver is the slow part of this file."""
    app_main.app.state.deckbuilder = real_app_state
    try:
        response = TestClient(app_main.app).post(
            "/api/deck", json={"commander": COMMANDER}
        )
    finally:
        del app_main.app.state.deckbuilder
    assert response.status_code == 200, response.text
    return response.json()


def test_the_deck_has_exactly_99_cards(krenko_deck: dict) -> None:
    assert sum(card["count"] for card in krenko_deck["mainboard"]) == 99


def test_the_commander_is_not_in_its_own_99(krenko_deck: dict) -> None:
    assert COMMANDER not in [card["name"] for card in krenko_deck["mainboard"]]
    assert krenko_deck["commander"]["name"] == COMMANDER


def test_the_dials_are_echoed_and_the_bands_are_derived(krenko_deck: dict) -> None:
    assert krenko_deck["dials"] == {}
    # The bands are the server's answer, not the client's question.
    assert set(krenko_deck["bands"]) == set(krenko_deck["statuses"])
    assert krenko_deck["bands"]["lands"]["min"] <= krenko_deck["bands"]["lands"]["max"]


def test_the_dials_actually_move_the_bands(
    client: TestClient, krenko_deck: dict
) -> None:
    """A dial the client sends is honoured server-side, via quotas.yaml."""
    response = client.post(
        "/api/deck", json={"commander": COMMANDER, "dials": {"lands": "low"}}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dials"] == {"lands": "low"}
    assert body["bands"]["lands"]["max"] < krenko_deck["bands"]["lands"]["max"]


def test_every_deck_card_carries_the_full_card_shape(krenko_deck: dict) -> None:
    fields = {
        "name",
        "oracle_id",
        "scryfall_id",
        "color_identity",
        "count",
        "slot",
        "reason",
        "score",
    }
    for section in ("mainboard", "maybeboard", "new_cards"):
        assert krenko_deck[section], f"{section} should not be empty for Krenko"
        for card in krenko_deck[section]:
            assert set(card) == fields
            # Fase 5 builds the card images from this.
            assert card["scryfall_id"]


def test_the_solver_block_reports_what_it_did(krenko_deck: dict) -> None:
    solver = krenko_deck["solver"]
    assert solver["status"] in ("OPTIMAL", "FEASIBLE")
    assert solver["solve_time_s"] > 0
    assert set(solver) == {"status", "stage", "solve_time_s", "objective"}


def test_a_relaxed_stage_warns_but_still_answers_200(krenko_deck: dict) -> None:
    """INFEASIBLE is not an HTTP error: the warning and the stage carry it."""
    codes = {w["code"] for w in krenko_deck["warnings"]}
    if krenko_deck["solver"]["stage"] == "none":
        assert "relaxed_stage" not in codes
    else:
        assert "relaxed_stage" in codes
        assert all(w["severity"] == "amber" for w in krenko_deck["warnings"])


def test_a_forced_relaxed_stage_answers_200_with_an_amber_warning(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Krenko always solves at stage ``none``, so the relaxed branch above is
    never exercised by a real build. Force it: this is the path that shipped a
    NameError because no test ever ran it.
    """
    real_build = service.build_deck_cpsat

    def relaxed(*args: object, **kwargs: object) -> object:
        result = real_build(*args, **kwargs)
        object.__setattr__(result, "relaxation_stage", "soft_category_floors")
        return result

    monkeypatch.setattr(service, "build_deck_cpsat", relaxed)
    response = client.post("/api/deck", json={"commander": COMMANDER})

    assert response.status_code == 200
    body = response.json()
    assert body["solver"]["stage"] == "soft_category_floors"
    assert [w["code"] for w in body["warnings"]] == ["relaxed_stage"]
    assert body["warnings"][0]["severity"] == "amber"


def test_the_lands_never_go_below_the_karsten_floor(krenko_deck: dict) -> None:
    assert krenko_deck["counts"]["lands"] >= krenko_deck["karsten_floor"]
    assert krenko_deck["lands_target"] >= 0


def test_a_second_build_reuses_the_edhrec_memo(
    client: TestClient, krenko_deck: dict, no_network: None
) -> None:
    """The memo is why the swap path never re-parses 200 KB of JSON."""
    response = client.post("/api/deck", json={"commander": COMMANDER})

    assert response.status_code == 200
    assert response.json()["mainboard"] == krenko_deck["mainboard"]
