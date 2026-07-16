"""Tests for POST /export/pdf: the print-and-cut proxy PDF endpoint.

The frontier only: that the response is a PDF file with the right filename,
that the page count follows ceil(faces / 9), that a double-faced card and a
``count > 1`` each add the faces they should, and that unknown names / a
degraded service are refused. The geometry of a real sheet (63x88 mm cells) is
measured in ``scripts``-style verification, not here.

**No network.** ``app.service.fetch_card_image`` is monkeypatched to a stub
that returns a fixed 1x1 JPEG, so no test touches Scryfall.
"""

from __future__ import annotations

import base64
import re
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app import service
from app.state import AppState

COMMANDER = "Krenko, Mob Boss"

# Eight single-faced red cards (verified: empty image_uri_back_normal), so
# commander + these eight is exactly nine faces = one full page.
SINGLE_FACED = [
    "Sol Ring",
    "Goblin Bombardment",
    "Goblin Chieftain",
    "Goblin Matron",
    "Goblin King",
    "Skirk Prospector",
    "Impact Tremors",
    "Krenko's Command",
]

# A modal double-faced card: image_uri_back_normal is set, so it prints two
# consecutive faces instead of one.
DFC = "Westvale Abbey // Ormendahl, Profane Prince"

# A valid 1x1 JPEG. fpdf2 parses it like any card image, so the render path is
# exercised for real without any card art or the network.
_ONE_BY_ONE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAAAP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AfwD/2Q=="
)


def _page_count(pdf_bytes: bytes) -> int:
    """Number of ``/Type /Page`` objects (the page tree node is ``/Pages``)."""
    return len(re.findall(rb"/Type\s*/Page\b", pdf_bytes))


@pytest.fixture()
def fetch_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the image fetcher with an offline stub; record the URLs it saw."""
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


@pytest.fixture()
def degraded_client() -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = None
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


def _cards(*names: str, count: int = 1) -> list[dict]:
    return [{"name": name, "count": count} for name in names]


def test_the_response_is_a_pdf_attachment(
    client: TestClient, fetch_calls: list[str]
) -> None:
    response = client.post(
        "/export/pdf", json={"commander": COMMANDER, "cards": _cards("Sol Ring")}
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="krenko-mob-boss_proxies.pdf"'
    )
    assert response.content.startswith(b"%PDF")
    # The stub answered; the real Scryfall fetcher was never reached.
    assert fetch_calls


def test_nine_faces_are_one_page(client: TestClient, fetch_calls: list[str]) -> None:
    """Commander + eight single-faced cards = nine faces = exactly one page."""
    response = client.post(
        "/export/pdf", json={"commander": COMMANDER, "cards": _cards(*SINGLE_FACED)}
    )

    assert response.status_code == 200, response.text
    assert _page_count(response.content) == 1


def test_a_tenth_face_spills_onto_a_second_page(
    client: TestClient, fetch_calls: list[str]
) -> None:
    response = client.post(
        "/export/pdf",
        json={"commander": COMMANDER, "cards": _cards(*SINGLE_FACED, "Goblin Warchief")},
    )

    assert response.status_code == 200, response.text
    # Ten faces -> ceil(10 / 9) == 2.
    assert _page_count(response.content) == 2


def test_count_greater_than_one_prints_each_copy(
    client: TestClient, fetch_calls: list[str]
) -> None:
    """A ``count`` of nine copies is nine faces, so with the commander it is two pages."""
    one_page = client.post(
        "/export/pdf",
        json={"commander": COMMANDER, "cards": _cards("Sol Ring", count=8)},
    )
    two_pages = client.post(
        "/export/pdf",
        json={"commander": COMMANDER, "cards": _cards("Sol Ring", count=9)},
    )

    assert _page_count(one_page.content) == 1  # commander + 8 copies = 9 faces
    assert _page_count(two_pages.content) == 2  # commander + 9 copies = 10 faces


def test_a_double_faced_card_prints_both_faces(
    client: TestClient, fetch_calls: list[str]
) -> None:
    """Swapping one single-faced card for a DFC adds a face and tips the page over.

    Both decks carry nine card entries (commander + eight), so the only
    difference is that the DFC contributes two cells (front + back) where a
    single-faced card contributes one: nine faces (one page) becomes ten (two).
    """
    all_singles = client.post(
        "/export/pdf", json={"commander": COMMANDER, "cards": _cards(*SINGLE_FACED)}
    )
    with_dfc = client.post(
        "/export/pdf",
        json={"commander": COMMANDER, "cards": _cards(*SINGLE_FACED[:7], DFC)},
    )

    assert _page_count(all_singles.content) == 1
    assert _page_count(with_dfc.content) == 2


def test_an_unknown_card_is_a_422(
    client: TestClient, fetch_calls: list[str]
) -> None:
    response = client.post(
        "/export/pdf",
        json={"commander": COMMANDER, "cards": _cards("Fake McFakeface")},
    )

    assert response.status_code == 422
    assert "Fake McFakeface" in response.json()["detail"]


def test_an_unknown_commander_is_a_422(
    client: TestClient, fetch_calls: list[str]
) -> None:
    response = client.post(
        "/export/pdf", json={"commander": "Fake McFakeface", "cards": []}
    )

    assert response.status_code == 422
    assert "Fake McFakeface" in response.json()["detail"]


def test_the_pdf_export_is_degraded_without_a_pool(
    degraded_client: TestClient, fetch_calls: list[str]
) -> None:
    response = degraded_client.post(
        "/export/pdf", json={"commander": COMMANDER, "cards": _cards("Sol Ring")}
    )
    assert response.status_code == 503


def test_an_extra_field_is_rejected(
    client: TestClient, fetch_calls: list[str]
) -> None:
    """``extra="forbid"``: a stray key is a 422, not a silently dropped field."""
    response = client.post(
        "/export/pdf",
        json={"commander": COMMANDER, "cards": _cards("Sol Ring"), "slot": "ramp"},
    )
    assert response.status_code == 422
