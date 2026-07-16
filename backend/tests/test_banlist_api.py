"""Tests for GET /banlist: the group's banlist and watchlist as the UI reads it.

Exercises the real artifacts (like the rest of the API tests): the endpoint is
a pure projection of ``banlist.yaml`` resolved against the pool, so the numbers
here track the real file.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.state import AppState

BANNED_FIELDS = {"name", "reason", "image_uri_normal", "oracle_id"}
WATCHLIST_FIELDS = BANNED_FIELDS | {"scope"}


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


def test_banlist_has_both_blocks_with_the_right_shape(client: TestClient) -> None:
    body = client.get("/banlist").json()

    assert set(body) == {"banned", "watchlist"}
    assert body["banned"] and body["watchlist"]
    assert all(set(entry) == BANNED_FIELDS for entry in body["banned"])
    assert all(set(entry) == WATCHLIST_FIELDS for entry in body["watchlist"])


def test_banlist_covers_exactly_the_resolved_banned_set(
    client: TestClient, real_app_state: AppState
) -> None:
    """Manual bans + rule-resolved bans, minus exceptions — the same set /build
    refuses, so no more and no less."""
    body = client.get("/banlist").json()

    assert {entry["oracle_id"] for entry in body["banned"]} == set(
        real_app_state.resolved_banlist.banned
    )


def test_every_banned_entry_has_a_reason_and_art(client: TestClient) -> None:
    banned = client.get("/banlist").json()["banned"]

    assert all(entry["reason"] for entry in banned)
    assert all(entry["oracle_id"] for entry in banned)
    # The real pool has art for every banned card.
    assert all(entry["image_uri_normal"] for entry in banned)


def test_a_rule_resolved_ban_carries_its_rule_reason(client: TestClient) -> None:
    """Demonic Tutor is banned by the generic-tutors rule, not listed by hand,
    yet it must appear with a reason — that is the whole point of including
    rule-resolved bans, not only the manual ones."""
    by_name = {entry["name"]: entry for entry in client.get("/banlist").json()["banned"]}

    assert "Demonic Tutor" in by_name
    assert by_name["Demonic Tutor"]["reason"]


def test_watchlist_reports_scope(client: TestClient) -> None:
    """A scoped entry keeps its scope; an unscoped one is null, not absent."""
    watchlist = client.get("/banlist").json()["watchlist"]

    scopes = {entry["name"]: entry["scope"] for entry in watchlist}
    assert "Tergrid, God of Fright // Tergrid's Lantern" in scopes
    assert scopes["Tergrid, God of Fright // Tergrid's Lantern"] == "in_the_99"
    assert any(scope is None for scope in scopes.values())


def test_both_lists_are_sorted_alphabetically(client: TestClient) -> None:
    body = client.get("/banlist").json()

    assert [e["name"] for e in body["banned"]] == sorted(
        e["name"] for e in body["banned"]
    )
    assert [e["name"] for e in body["watchlist"]] == sorted(
        e["name"] for e in body["watchlist"]
    )


def test_banlist_is_degraded_without_a_pool(degraded_client: TestClient) -> None:
    assert degraded_client.get("/banlist").status_code == 503
