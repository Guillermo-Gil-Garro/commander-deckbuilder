"""Tests for the commander endpoints: the full picker list and name search.

``/commanders`` ships every selectable commander because the frontend pages,
filters and searches in the client; ``/commanders/search`` is the server-side
typeahead over the same index. The two answer deliberately different shapes —
the list is slim (thousands of rows), the search is the full card shape.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.state import AppState
from selector.deck_rules import archetype_for

COMMANDER_FIELDS = {
    "name",
    "oracle_id",
    "scryfall_id",
    "color_identity",
    "type_line",
    "mana_cost",
    "cmc",
    "image_uri_normal",
    "image_uri_art_crop",
    "archetype",
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


def test_the_commander_list_is_compressed(client: TestClient) -> None:
    """The list is ~1.3 MB of JSON: the picker fetches all 3.288 at once so it
    can filter and paginate client-side. Uncompressed that is the single
    heaviest thing we serve; gzip takes it to ~0.25 MB because the two image
    URLs of a row differ by one path segment.
    """
    response = client.get("/commanders", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"


# --- the full list ----------------------------------------------------------

LIST_FIELDS = {
    "name",
    "oracle_id",
    "color_identity",
    "image_uri_normal",
    "image_uri_art_crop",
    "archetype",
    "featured",
    "description",
}


def test_the_list_carries_every_selectable_commander(
    client: TestClient, real_app_state: AppState
) -> None:
    """Not the 55 featured: the frontend filters and pages the whole pool."""
    response = client.get("/commanders")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(real_app_state.commanders)
    assert body["count"] > 3000, "the whole pool, not a shortlist"
    assert {c["name"] for c in body["commanders"]} == {
        row.name for row in real_app_state.commanders
    }


def test_the_list_carries_the_slim_picker_shape(client: TestClient) -> None:
    """Slim by design, but both images: the picker renders the whole card."""
    body = client.get("/commanders").json()["commanders"]

    assert all(set(card) == LIST_FIELDS for card in body)
    assert all(card["image_uri_art_crop"] for card in body)
    assert all(card["image_uri_normal"] for card in body)


def test_the_list_publishes_the_full_card_image_from_the_pool(
    client: TestClient, real_app_state: AppState
) -> None:
    """Straight from the pool, never derived from the art crop's URL.

    The frontend used to rewrite `/art_crop/` to `/normal/`; that guessed at
    Scryfall's URL layout. This is the field the pool actually stores.
    """
    body = client.get("/commanders").json()["commanders"]

    assert {c["name"]: c["image_uri_normal"] for c in body} == {
        row.name: real_app_state.pool.by_name[row.name]["image_uri_normal"]
        for row in real_app_state.commanders
    }


def test_the_list_describes_the_featured_and_only_them(
    client: TestClient, real_app_state: AppState
) -> None:
    """A description is the shortlist's pitch; null everywhere else."""
    body = client.get("/commanders").json()["commanders"]

    described = {c["name"]: c["description"] for c in body if c["description"]}
    assert described == {c.name: c.description for c in real_app_state.featured}
    assert all(c["description"] is None for c in body if not c["featured"])
    assert all(c["description"] for c in body if c["featured"])


def test_the_list_describes_double_faced_featured_commanders(
    client: TestClient, real_app_state: AppState
) -> None:
    """The trap: the YAML names a front face, the API a canonical "a // b".

    `featured_commanders.yaml` lists Sephiroth, Etali and Kefka by their front
    face alone. An exact match against the raw YAML name would drop their
    descriptions **silently** — every other commander would still be fine, so
    nothing would fail. `load_featured` resolves through the pool's NameIndex,
    which is what makes the lookup land.
    """
    body = {c["name"]: c for c in client.get("/commanders").json()["commanders"]}

    double_faced = [c.name for c in real_app_state.featured if " // " in c.name]
    assert len(double_faced) >= 3, "the double-faced featured commanders are the point"
    for name in double_faced:
        assert body[name]["featured"] is True
        assert body[name]["description"], f"{name} lost its description"


def test_the_list_puts_the_featured_first_then_sorts_alphabetically(
    client: TestClient, real_app_state: AppState
) -> None:
    body = client.get("/commanders").json()["commanders"]

    featured = [c for c in body if c["featured"]]
    rest = [c for c in body if not c["featured"]]
    assert body[: len(featured)] == featured, "featured must lead the list"
    assert {c["name"] for c in featured} == {c.name for c in real_app_state.featured}
    assert [c["name"] for c in featured] == sorted(c["name"] for c in featured)
    assert [c["name"] for c in rest] == sorted(c["name"] for c in rest)


def test_the_list_never_offers_a_banned_commander(
    client: TestClient, real_app_state: AppState
) -> None:
    """Both ban sets hide a card here, so the list is exactly what /build takes."""
    banlist = real_app_state.resolved_banlist
    banned = banlist.banned | banlist.banned_as_commander
    body = client.get("/commanders").json()["commanders"]

    assert not ({c["oracle_id"] for c in body} & banned)


def test_the_list_reports_each_commander_archetype(
    client: TestClient, real_app_state: AppState
) -> None:
    """The archetype must be the one the build would resolve, not a guess."""
    body = client.get("/commanders").json()["commanders"]

    known = set(real_app_state.quotas.archetypes)
    assert all(card["archetype"] in known for card in body)
    assert {card["name"]: card["archetype"] for card in body} == {
        row.name: archetype_for(real_app_state.quotas, row.name)
        for row in real_app_state.commanders
    }


def test_the_list_is_degraded_without_a_pool(degraded_client: TestClient) -> None:
    assert degraded_client.get("/commanders").status_code == 503


# --- search -----------------------------------------------------------------


def test_search_finds_a_commander(client: TestClient) -> None:
    body = client.get("/commanders/search", params={"q": "krenko"}).json()
    assert "Krenko, Mob Boss" in [c["name"] for c in body["commanders"]]


def test_search_is_case_insensitive(client: TestClient) -> None:
    lower = client.get("/commanders/search", params={"q": "krenko"}).json()
    upper = client.get("/commanders/search", params={"q": "KRENKO"}).json()
    assert lower == upper


def test_search_handles_commas_and_apostrophes(client: TestClient) -> None:
    """The reason q is a query param and not a path segment."""
    body = client.get(
        "/commanders/search", params={"q": "Atraxa, Praetors' Voice"}
    ).json()
    assert [c["name"] for c in body["commanders"]] == ["Atraxa, Praetors' Voice"]


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
    response = client.get("/commanders/search", params={"q": q})
    assert response.status_code == 200
    assert response.json() == {"count": 0, "commanders": []}


def test_search_without_q_is_a_422(client: TestClient) -> None:
    assert client.get("/commanders/search").status_code == 422


def test_search_defaults_to_20_results(client: TestClient) -> None:
    body = client.get("/commanders/search", params={"q": "the"}).json()
    assert body["count"] == len(body["commanders"]) == 20


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(0, 1), (-5, 1), (1, 1), (50, 50), (999, 50)],
)
def test_search_clamps_the_limit(
    client: TestClient, limit: int, expected: int
) -> None:
    body = client.get("/commanders/search", params={"q": "the", "limit": limit}).json()
    assert body["count"] == len(body["commanders"]) == expected


def test_search_never_returns_a_banned_commander(
    real_app_state: AppState, client: TestClient
) -> None:
    banlist = real_app_state.resolved_banlist
    hidden = banlist.banned | banlist.banned_as_commander
    body = client.get("/commanders/search", params={"q": "the", "limit": 50}).json()
    assert all(card["oracle_id"] not in hidden for card in body["commanders"])


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
    body = client.get("/commanders/search", params={"q": "krenko"}).json()["commanders"]
    assert body and all(set(card) == COMMANDER_FIELDS for card in body)


def test_search_is_degraded_without_a_pool(degraded_client: TestClient) -> None:
    response = degraded_client.get("/commanders/search", params={"q": "krenko"})
    assert response.status_code == 503
