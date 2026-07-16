"""Tests for POST /export: the decklist file endpoint.

``tests/test_export.py`` owns the Archidekt format itself. What is tested here
is the frontier: that the response is a file (text/plain + a filename), that
the client's slots are honoured, and that a name we cannot resolve is refused
instead of silently breaking the import on the other side.
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


def payload(**overrides: object) -> dict:
    body: dict = {
        "commander": COMMANDER,
        "deck": [
            {"name": "Sol Ring", "count": 1, "slot": "ramp"},
            {"name": "Goblin Bombardment", "count": 1, "slot": "removal"},
            {"name": "Mountain", "count": 34, "slot": "lands"},
        ],
        "maybeboard": [{"name": "Goblin Matron"}],
        "new_cards": [{"name": "Goblin Chieftain"}],
    }
    body.update(overrides)
    return body


def test_the_export_is_a_downloadable_text_file(client: TestClient) -> None:
    response = client.post("/export", json=payload())

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="krenko-mob-boss.txt"'
    )


def test_the_decklist_carries_the_commander_and_the_client_slots(
    client: TestClient,
) -> None:
    body = client.post("/export", json=payload()).text

    assert "1x Krenko, Mob Boss [Commander]" in body
    # The slot the client sent, rendered through the single CATEGORY_LABELS.
    assert "1x Sol Ring [Ramp]" in body
    assert "1x Goblin Bombardment [Removal]" in body
    assert "34x Mountain [Lands]" in body


def test_the_sideboard_sections_are_rendered(client: TestClient) -> None:
    body = client.post("/export", json=payload()).text

    assert "# Sideboard" in body
    assert "1x Goblin Matron" in body
    assert "# --- cartas nuevas ---" in body
    assert "1x Goblin Chieftain" in body


def test_an_unknown_slot_falls_back_to_its_raw_label(client: TestClient) -> None:
    """The slot is the client's grouping: never validated, never rejected."""
    body = client.post(
        "/export",
        json=payload(deck=[{"name": "Sol Ring", "count": 1, "slot": "chaos"}]),
    ).text

    assert "1x Sol Ring [chaos]" in body


def test_an_unknown_card_is_a_422(client: TestClient) -> None:
    """A name we cannot resolve would break the import silently on Archidekt."""
    response = client.post(
        "/export",
        json=payload(deck=[{"name": "Fake McFakeface", "count": 1, "slot": "ramp"}]),
    )

    assert response.status_code == 422
    assert "Fake McFakeface" in response.json()["detail"]


def test_an_unknown_commander_is_a_404(client: TestClient) -> None:
    response = client.post(
        "/export", json=payload(commander="Fake McFakeface")
    )

    assert response.status_code == 404


def test_a_face_name_is_exported_canonically(client: TestClient) -> None:
    """Archidekt wants the exact name; the pool's canonical one is that name."""
    body = client.post(
        "/export",
        json=payload(deck=[{"name": "Bala Ged Recovery", "count": 1, "slot": "ramp"}]),
    ).text

    assert "1x Bala Ged Recovery // Bala Ged Sanctuary [Ramp]" in body


def test_an_unknown_format_is_a_422(client: TestClient) -> None:
    response = client.post("/export", json=payload(format="moxfield"))
    assert response.status_code == 422


def test_the_export_is_degraded_without_a_pool(degraded_client: TestClient) -> None:
    assert degraded_client.post("/export", json=payload()).status_code == 503


def test_a_real_krenko_deck_round_trips_through_the_export(
    client: TestClient,
) -> None:
    """The actual Fase 5 flow: build, then export what came back."""
    deck = client.post("/build", json={"commander": COMMANDER}).json()
    response = client.post(
        "/export",
        json={
            "commander": COMMANDER,
            "deck": [
                {"name": c["name"], "count": c["count"], "slot": c["slot"]}
                for c in deck["nonbasic_cards"] + deck["basic_lands"]
            ],
            "maybeboard": [{"name": c["name"]} for c in deck["maybeboard"]],
            "new_cards": [{"name": c["name"]} for c in deck["new_cards"]],
        },
    )

    assert response.status_code == 200
    lines = [line for line in response.text.splitlines() if line.startswith(("1x", "2x", "3x"))]
    # The commander line plus one line per mainboard row, plus the sideboard.
    assert lines[0] == f"1x {COMMANDER} [Commander]"
    exported = sum(
        int(line.split("x ", 1)[0])
        for line in response.text.splitlines()
        if line and line[0].isdigit() and "[" in line and "[Commander]" not in line
    )
    assert exported == 99
