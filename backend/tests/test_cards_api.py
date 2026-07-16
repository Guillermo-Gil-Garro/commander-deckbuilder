"""Tests for GET /cards/search and GET /why-not.

Both are the cheap explain endpoints: no solver, no EDHREC, no disk. What is
tested here is the frontier and the two things each one is easiest to
misread — that the card search is *not* filtered by the banlist, and that
``eligible`` is about the candidate set and not about the deck.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.state import AppState
from selector.deck_rules import RuleContext, resolve_never

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


# --- /cards/search ----------------------------------------------------------


def test_search_finds_a_card(client: TestClient) -> None:
    body = client.get("/cards/search", params={"q": "sol ring"}).json()

    assert body["names"] == ["Sol Ring"]
    assert body["count"] == 1


def test_search_covers_the_whole_pool_not_just_commanders(client: TestClient) -> None:
    """Sol Ring is not commander-eligible and must still be findable."""
    body = client.get("/cards/search", params={"q": "sol ring"}).json()
    commanders = client.get("/commanders/search", params={"q": "sol ring"}).json()

    assert body["names"] == ["Sol Ring"]
    assert commanders["commanders"] == []


def test_search_ranks_prefix_matches_first(client: TestClient) -> None:
    """"Battle Cry Goblin" must never outrank "Goblin Airbrusher"."""
    names = client.get("/cards/search", params={"q": "goblin", "limit": 50}).json()[
        "names"
    ]

    starts = [name.lower().startswith("goblin") for name in names]
    assert starts[0] and any(starts), "a prefix match must lead"
    # Every prefix match comes before every merely-containing one, and each
    # group stays alphabetical.
    assert starts == sorted(starts, reverse=True)
    prefixed = [name for name in names if name.lower().startswith("goblin")]
    assert prefixed == sorted(prefixed)


def test_search_is_case_insensitive(client: TestClient) -> None:
    lower = client.get("/cards/search", params={"q": "sol ring"}).json()
    upper = client.get("/cards/search", params={"q": "SOL RING"}).json()

    assert lower == upper


def test_search_does_not_fuzzy_match(client: TestClient) -> None:
    """A typo returns nothing rather than a guess."""
    assert client.get("/cards/search", params={"q": "sol rnig"}).json()["names"] == []


@pytest.mark.parametrize("q", ["", "s"])
def test_a_short_query_returns_nothing_rather_than_the_pool(
    client: TestClient, q: str
) -> None:
    body = client.get("/cards/search", params={"q": q})

    assert body.status_code == 200
    assert body.json() == {"count": 0, "names": []}


@pytest.mark.parametrize(("limit", "expected"), [(0, 1), (-5, 1), (999, 50)])
def test_the_search_limit_is_clamped_not_rejected(
    client: TestClient, limit: int, expected: int
) -> None:
    body = client.get("/cards/search", params={"q": "goblin", "limit": limit}).json()

    assert body["count"] == expected
    assert len(body["names"]) == expected


def test_the_search_is_not_filtered_by_the_banlist(
    client: TestClient, real_app_state: AppState
) -> None:
    """The box is a lookup, not a legality check. /why-not is the judge.

    Filtering banned cards out here would make a player who searches one think
    the card does not exist, instead of learning that their group threw it out.
    """
    banned = sorted(real_app_state.banned_names)[0]
    body = client.get("/cards/search", params={"q": banned, "limit": 50}).json()

    assert banned in body["names"]


def test_search_is_degraded_without_a_pool(degraded_client: TestClient) -> None:
    assert degraded_client.get("/cards/search", params={"q": "sol"}).status_code == 503


# --- /why-not ---------------------------------------------------------------


def _why_not(client: TestClient, card: str, commander: str = COMMANDER) -> dict:
    response = client.get("/why-not", params={"commander": commander, "card": card})
    assert response.status_code == 200, response.text
    return response.json()


def test_an_eligible_card_says_so_without_promising_a_slot(client: TestClient) -> None:
    """The whole point of the endpoint's wording: eligible != in the deck."""
    body = _why_not(client, "Goblin Chieftain")

    assert body["eligible"] is True
    assert body["reason_bucket"] == "not_selected"
    assert body["commander_name"] == COMMANDER
    assert body["card_name"] == "Goblin Chieftain"
    # The reason must say, in Spanish, that this is not a promise.
    assert "NO" in body["reason"] and "solver" in body["reason"]


def test_an_off_identity_card_is_rejected(client: TestClient) -> None:
    """Krenko is mono-red; Counterspell is blue."""
    body = _why_not(client, "Counterspell")

    assert body["eligible"] is False
    assert body["reason_bucket"] == "color_identity"
    assert "identidad" in body["reason"]


def test_a_banned_card_is_rejected_as_the_groups_call(
    client: TestClient, real_app_state: AppState
) -> None:
    banned = next(
        name
        for name in sorted(real_app_state.banned_names)
        if (card := real_app_state.pool.resolve(name)) is not None
        and set(card.get("color_identity") or ()) <= {"R"}
    )
    body = _why_not(client, banned)

    assert body["eligible"] is False
    assert body["reason_bucket"] == "banned"
    assert "banlist" in body["reason"]


def test_why_not_honours_the_archetype_scoped_banlist_exception(
    client: TestClient,
) -> None:
    """Smothering Tithe is banned, except in enchantress decks. /why-not must
    agree with /build: telling a Sythis player it is 'banned' while the build
    includes it would be the endpoint lying about its own selector.
    """
    in_enchantress = _why_not(client, "Smothering Tithe", commander="Sythis, Harvest's Hand")
    assert in_enchantress["reason_bucket"] != "banned"

    # A white deck that is not enchantress still sees the ban.
    elsewhere = _why_not(client, "Smothering Tithe", commander="Giada, Font of Hope")
    assert elsewhere["eligible"] is False
    assert elsewhere["reason_bucket"] == "banned"


def test_a_never_card_is_rejected_by_its_rule(
    client: TestClient, real_app_state: AppState
) -> None:
    ctx = RuleContext(
        commander_name=COMMANDER,
        color_identity=frozenset("R"),
        archetype="aggro",
    )
    never = sorted(resolve_never(real_app_state.rules, ctx))
    assert never, "this test needs Krenko to match some never rule"

    body = _why_not(client, never[0])

    assert body["eligible"] is False
    assert body["reason_bucket"] == "never_rule"
    assert "never" in body["reason"]


def test_a_card_outside_the_pool_is_a_verdict_not_a_404(client: TestClient) -> None:
    """Our pool is exactly the Commander-legal set, so absent is the answer.

    A typo and an illegal card are indistinguishable here, and the Spanish
    reason says both rather than pretending we can tell them apart.
    """
    body = _why_not(client, "Fake McFakeface")

    assert body["eligible"] is False
    assert body["reason_bucket"] == "not_commander_legal"
    assert "legal" in body["reason"] and "nombre" in body["reason"]


def test_a_face_name_resolves_to_its_full_card(client: TestClient) -> None:
    """The pool's face-name fallback must apply here like everywhere else."""
    body = _why_not(client, "Fire", commander="Kroxa, Titan of Death's Hunger")

    assert body["card_name"] == "Fire // Ice"
    assert body["reason_bucket"] != "not_commander_legal"


def test_an_unknown_commander_is_a_404(client: TestClient) -> None:
    response = client.get(
        "/why-not", params={"commander": "Fake McFakeface", "card": "Sol Ring"}
    )

    assert response.status_code == 404


def test_why_not_is_degraded_without_a_pool(degraded_client: TestClient) -> None:
    response = degraded_client.get(
        "/why-not", params={"commander": COMMANDER, "card": "Sol Ring"}
    )
    assert response.status_code == 503
