"""Tests for the swap endpoints: candidates and live validation.

``tests/test_swap.py`` owns the swap rules themselves. What is tested here is
the frontier: that "infeasible" is a 200 and only an incoherent request is a
422, that the deck is rebuilt server-side from names alone, and that the
Spanish messages come out of the errors table.

The deck fixture is a real Krenko build, so these run against the same 99 the
player would be editing. No test here touches the network.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
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


@pytest.fixture(scope="session")
def krenko_mainboard(real_app_state: AppState) -> list[dict]:
    """A real 99, as the client would send it back: names and counts only."""
    app_main.app.state.deckbuilder = real_app_state
    try:
        response = TestClient(app_main.app).post(
            "/api/deck", json={"commander": COMMANDER}
        )
    finally:
        del app_main.app.state.deckbuilder
    assert response.status_code == 200, response.text
    return [
        {"name": card["name"], "count": card["count"]}
        for card in response.json()["mainboard"]
    ]


@pytest.fixture(scope="session")
def replacement(real_app_state: AppState, krenko_mainboard: list[dict]) -> str:
    """A card the API itself says is a feasible swap for Goblin Bombardment.

    Taken from /candidates rather than hardcoded: any card picked by hand is
    one deck regeneration away from being in the 99 already, which would make
    every "feasible" test fail on `duplicate_card` for no good reason.
    """
    app_main.app.state.deckbuilder = real_app_state
    try:
        response = TestClient(app_main.app).post(
            "/api/deck/swap/candidates",
            json={
                "commander": COMMANDER,
                "deck": krenko_mainboard,
                "out": "Goblin Bombardment",
                "limit": 1,
            },
        )
    finally:
        del app_main.app.state.deckbuilder
    assert response.status_code == 200, response.text
    candidates = response.json()["candidates"]
    assert candidates, "Krenko should have at least one removal alternative"
    return candidates[0]["name"]


# --- candidates -------------------------------------------------------------


def test_candidates_are_ranked_and_counted(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    response = client.post(
        "/api/deck/swap/candidates",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "limit": 5,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["out"]["name"] == "Goblin Bombardment"
    assert body["limit"] == 5
    assert len(body["candidates"]) <= 5
    assert body["feasible_count"] >= len(body["candidates"])
    scores = [c["score"] for c in body["candidates"]]
    assert scores == sorted(scores, reverse=True)


def test_candidates_carry_the_card_shape_and_a_reason(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    body = client.post(
        "/api/deck/swap/candidates",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
        },
    ).json()

    assert body["candidates"], "Krenko should have removal alternatives"
    for card in body["candidates"]:
        assert set(card) == {
            "name",
            "oracle_id",
            "scryfall_id",
            "color_identity",
            "score",
            "reason",
        }
        assert card["scryfall_id"]
        assert "score" in card["reason"]


def test_candidates_are_never_already_in_the_deck(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    body = client.post(
        "/api/deck/swap/candidates",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "limit": 50,
        },
    ).json()

    in_deck = {row["name"] for row in krenko_mainboard}
    assert not [c for c in body["candidates"] if c["name"] in in_deck]


def test_candidates_stay_inside_the_commander_identity(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    body = client.post(
        "/api/deck/swap/candidates",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "limit": 50,
        },
    ).json()

    assert all(set(c["color_identity"]) <= {"R"} for c in body["candidates"])


def test_the_candidates_limit_is_clamped_not_rejected(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    response = client.post(
        "/api/deck/swap/candidates",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "limit": 9999,
        },
    )

    assert response.status_code == 200
    assert response.json()["limit"] == 50


# --- validate: the domain results (always 200) ------------------------------


def test_a_feasible_swap_is_200_and_carries_the_full_traffic_light(
    client: TestClient, krenko_mainboard: list[dict], replacement: str
) -> None:
    """statuses come back even when feasible: it is the live quota panel."""
    body = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "in": replacement,
        },
    ).json()

    assert body["feasible"] is True, body["blockers"]
    assert body["blockers"] == []
    assert body["deck_size"] == 99
    assert set(body["statuses"]) == {
        "lands",
        "ramp",
        "card_draw",
        "removal",
        "board_wipe",
        "wincons",
        "protection",
        "synergy",
    }
    assert body["karsten_floor"] > 0


def test_an_infeasible_swap_is_a_200_with_blockers(
    client: TestClient, krenko_mainboard: list[dict], replacement: str
) -> None:
    """"Not feasible" is a verdict about the deck, not an error about the request."""
    response = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Mountain",
            "in": replacement,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["feasible"] is False
    assert body["blockers"], "removing a land must break the Karsten floor"
    assert all(b["severity"] == "red" for b in body["blockers"])
    # The message the player reads, from the errors table.
    assert any("tierras" in b["message"] for b in body["blockers"])


def test_an_off_identity_card_is_blocked_in_spanish(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    response = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "in": "Counterspell",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["feasible"] is False
    codes = {b["code"] for b in body["blockers"]}
    assert "color_identity" in codes
    assert any("identidad" in b["message"] for b in body["blockers"])


def test_a_duplicate_is_blocked(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    """Singleton: only basics repeat."""
    in_deck = next(
        row["name"]
        for row in krenko_mainboard
        if row["count"] == 1 and row["name"] != "Goblin Bombardment"
    )
    body = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "in": in_deck,
        },
    ).json()

    assert body["feasible"] is False
    assert "duplicate_card" in {b["code"] for b in body["blockers"]}


def test_a_never_card_is_amber_and_still_feasible(
    client: TestClient, real_app_state: AppState, krenko_mainboard: list[dict]
) -> None:
    """rules.yaml: never means "I don't recommend it", not "it's illegal"."""
    from selector.deck_rules import RuleContext, resolve_never

    ctx = RuleContext(
        commander_name=COMMANDER,
        color_identity=frozenset("R"),
        archetype="aggro",
    )
    in_deck = {row["name"] for row in krenko_mainboard}
    never = next(
        (
            name
            for name in resolve_never(real_app_state.rules, ctx)
            if name not in in_deck
            and (card := real_app_state.pool.resolve(name)) is not None
            and set(card.get("color_identity") or ()) <= {"R"}
        ),
        None,
    )
    if never is None:
        pytest.skip("no 'never' rule matches Krenko outside its own deck")

    body = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "in": never,
        },
    ).json()

    assert body["feasible"] is True, body["blockers"]
    warnings = {w["code"]: w for w in body["warnings"]}
    assert "add_never_manually" in warnings
    assert warnings["add_never_manually"]["severity"] == "amber"


def test_removing_an_always_card_is_amber_and_still_feasible(
    client: TestClient, real_app_state: AppState, krenko_mainboard: list[dict], replacement: str
) -> None:
    """rules.yaml: "el mazo sigue siendo válido y exportable"."""
    from selector.deck_rules import RuleContext, resolve_always

    ctx = RuleContext(
        commander_name=COMMANDER,
        color_identity=frozenset("R"),
        archetype="aggro",
    )
    in_deck = {row["name"] for row in krenko_mainboard}
    always = next(
        (
            rule.name
            for rule in resolve_always(
                real_app_state.rules, ctx, real_app_state.banned_names
            )
            if rule.name in in_deck
        ),
        None,
    )
    if always is None:
        pytest.skip("no 'always' rule card ended up in Krenko's deck")

    body = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": always,
            "in": replacement,
        },
    ).json()

    warnings = {w["code"]: w for w in body["warnings"]}
    assert "remove_always" in warnings
    assert warnings["remove_always"]["severity"] == "amber"


# --- validate: the incoherent requests (422) --------------------------------


def test_an_out_card_not_in_the_deck_is_a_422(
    client: TestClient, krenko_mainboard: list[dict], replacement: str
) -> None:
    """Island is in the pool and cannot be in a mono-red deck."""
    response = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Island",
            "in": replacement,
        },
    )

    assert response.status_code == 422
    assert "no está en el mazo" in response.json()["detail"]


def test_a_card_outside_the_pool_is_a_422(
    client: TestClient, krenko_mainboard: list[dict]
) -> None:
    response = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "in": "Fake McFakeface",
        },
    )

    assert response.status_code == 422
    assert "Fake McFakeface" in response.json()["detail"]


def test_a_deck_that_is_not_99_is_a_422(
    client: TestClient, krenko_mainboard: list[dict], replacement: str
) -> None:
    response = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard[:-1],
            "out": "Goblin Bombardment",
            "in": replacement,
        },
    )

    assert response.status_code == 422
    assert "99" in response.json()["detail"]


def test_bands_in_a_swap_request_are_rejected(
    client: TestClient, krenko_mainboard: list[dict], replacement: str
) -> None:
    """Same anti-tampering point as /api/deck: bands are never received."""
    response = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "in": replacement,
            "bands": {"lands": {"min": 0, "max": 99}},
        },
    )

    assert response.status_code == 422


def test_deck_rows_take_names_and_counts_only(
    client: TestClient, krenko_mainboard: list[dict], replacement: str
) -> None:
    """A client-declared category is refused, not trusted and not ignored."""
    tampered = [dict(row) for row in krenko_mainboard]
    tampered[0]["slot"] = "lands"

    response = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": COMMANDER,
            "deck": tampered,
            "out": "Goblin Bombardment",
            "in": replacement,
        },
    )

    assert response.status_code == 422


def test_an_unknown_commander_is_a_404(
    client: TestClient, krenko_mainboard: list[dict], replacement: str
) -> None:
    response = client.post(
        "/api/deck/swap/validate",
        json={
            "commander": "Fake McFakeface",
            "deck": krenko_mainboard,
            "out": "Goblin Bombardment",
            "in": replacement,
        },
    )

    assert response.status_code == 404


def test_the_swap_endpoints_are_degraded_without_a_pool(
    degraded_client: TestClient,
) -> None:
    for path, extra in (
        ("/api/deck/swap/validate", {"in": "Chaos Warp"}),
        ("/api/deck/swap/candidates", {}),
    ):
        response = degraded_client.post(
            path,
            json={
                "commander": COMMANDER,
                "deck": [{"name": "Sol Ring", "count": 1}],
                "out": "Sol Ring",
                **extra,
            },
        )
        assert response.status_code == 503, path
