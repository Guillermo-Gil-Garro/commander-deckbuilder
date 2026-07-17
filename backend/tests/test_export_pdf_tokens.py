"""Tests for the token-filling of the proxy PDF (``include_tokens``).

Two layers: the pure copy-count rule (``_token_copies`` / ``_deck_tokens_to_print``),
and the endpoint wiring — that ``include_tokens`` appends token faces after the
cards, that it is off by default, and that a token whose image fails to download
is dropped instead of sinking the sheet.

**No network.** ``app.service.fetch_card_image`` is stubbed, and token image
URLs are recognised by their ``api.scryfall.com`` host.
"""

from __future__ import annotations

import base64
import re
from typing import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app import service
from app.service import _deck_tokens_to_print, _token_copies
from app.state import AppState

COMMANDER = "Krenko, Mob Boss"  # makes a Goblin creature token ("number of ...")

_ONE_BY_ONE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAAAP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AfwD/2Q=="
)


def _page_count(pdf_bytes: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page\b", pdf_bytes))


def _token(name: str, type_line: str, scryfall_id: str = "tok-id") -> dict:
    return {"name": name, "scryfall_id": scryfall_id, "type_line": type_line}


def _producer(oracle_text: str, *tokens: dict) -> dict:
    return {"oracle_text": oracle_text, "tokens": list(tokens)}


# --- the pure copy-count rule ------------------------------------------------


def test_noncreature_token_is_one_copy() -> None:
    assert _token_copies("Token Artifact — Treasure", ["create a treasure"]) == 1


def test_creature_token_with_two_makers_is_two_copies() -> None:
    assert _token_copies("Token Creature — Goblin", ["make a goblin", "make a goblin"]) == 2


def test_lone_oneoff_creature_token_is_one_copy() -> None:
    # Beast Within: one maker, no "several/recurring" signal.
    text = "destroy target permanent. its controller creates a 3/3 green beast creature token."
    assert _token_copies("Token Creature — Beast", [text]) == 1


def test_lone_recurring_creature_token_is_two_copies() -> None:
    # Krenko-style: one maker, but "number of" signals it makes several.
    text = "create a number of 1/1 red goblin creature tokens equal to..."
    assert _token_copies("Token Creature — Goblin", [text]) == 2


def test_tokens_dedupe_by_name_and_type_and_sort_by_copies() -> None:
    producers = [
        _producer("create a number of 1/1 goblins", _token("Goblin", "Token Creature — Goblin")),
        _producer("create a goblin too", _token("Goblin", "Token Creature — Goblin")),
        _producer("create a treasure", _token("Treasure", "Token Artifact — Treasure")),
    ]
    tokens = _deck_tokens_to_print(producers)
    # One Goblin entry (two makers -> 2 copies) and one Treasure (1 copy),
    # Goblin first because it has more copies.
    assert [(t.name, t.copies) for t in tokens] == [("Goblin", 2), ("Treasure", 1)]


# --- endpoint wiring ---------------------------------------------------------


@pytest.fixture()
def fetch_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_fetch(url: str) -> bytes:
        calls.append(url)
        return _ONE_BY_ONE_JPEG

    monkeypatch.setattr(service, "fetch_card_image", fake_fetch)
    return calls


@pytest.fixture()
def client(real_app_state: AppState) -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = real_app_state
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


def _token_calls(calls: list[str]) -> list[str]:
    return [u for u in calls if "api.scryfall.com/cards/" in u]


def test_tokens_are_off_by_default(client: TestClient, fetch_calls: list[str]) -> None:
    response = client.post(
        "/export/pdf", json={"commander": COMMANDER, "cards": []}
    )
    assert response.status_code == 200, response.text
    # No include_tokens -> no token endpoint was ever hit.
    assert _token_calls(fetch_calls) == []


def test_include_tokens_fetches_the_commanders_token(
    client: TestClient, fetch_calls: list[str]
) -> None:
    response = client.post(
        "/export/pdf",
        json={"commander": COMMANDER, "cards": [], "include_tokens": True},
    )
    assert response.status_code == 200, response.text
    # Krenko's Goblin token was fetched from the token endpoint.
    assert _token_calls(fetch_calls)


def test_include_tokens_adds_cells_after_the_cards(
    client: TestClient, fetch_calls: list[str]
) -> None:
    """Commander alone is one face; adding its two Goblin copies makes three."""
    without = client.post(
        "/export/pdf", json={"commander": COMMANDER, "cards": []}
    )
    with_tokens = client.post(
        "/export/pdf",
        json={"commander": COMMANDER, "cards": [], "include_tokens": True},
    )
    # Both fit on one page, so page count can't show it; the token faces are the
    # extra cells. The commander makes exactly one token (Goblin), printed twice.
    assert _page_count(without.content) == 1
    assert _page_count(with_tokens.content) == 1
    assert len(_token_calls(fetch_calls)) == 1  # one distinct token URL, fetched once


def test_a_failing_token_image_is_dropped_not_fatal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token image that 404s leaves the token off the sheet; the PDF still renders."""

    def flaky_fetch(url: str) -> bytes:
        if "api.scryfall.com/cards/" in url:
            raise httpx.HTTPStatusError(
                "boom", request=httpx.Request("GET", url), response=httpx.Response(404)
            )
        return _ONE_BY_ONE_JPEG

    monkeypatch.setattr(service, "fetch_card_image", flaky_fetch)
    response = client.post(
        "/export/pdf",
        json={"commander": COMMANDER, "cards": [], "include_tokens": True},
    )
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF")
