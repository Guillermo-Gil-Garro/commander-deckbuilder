"""Tests for the art/language picker: printings fetcher, default policy,
gallery filtering, batch defaults and the PDF art override.

**No network.** ``pipeline.prints.fetch_prints`` is stubbed at the service
layer; the fetcher itself is exercised only through its disk cache (a
pre-written cache file short-circuits the download path).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app import service
from app.schemas import CardPrintView, PrintDefaultsRequest
from app.service import _default_print, _face_urls
from app.state import AppState
from pipeline import prints as prints_module

COMMANDER = "Krenko, Mob Boss"

_ONE_BY_ONE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAAAP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AfwD/2Q=="
)


def _row(
    scryfall_id: str,
    lang: str = "en",
    highres: bool = True,
    released_at: str = "2024-01-01",
    back: str = "",
) -> dict:
    return {
        "scryfall_id": scryfall_id,
        "set_code": "tst",
        "set_name": "Test Set",
        "collector_number": "1",
        "lang": lang,
        "released_at": released_at,
        "image_status": "highres_scan" if highres else "lowres",
        "highres": highres,
        "image_uri_normal": f"https://cards.scryfall.io/normal/front/{scryfall_id}.jpg",
        "image_uri_back_normal": back,
    }


def _views(*rows: dict) -> list[CardPrintView]:
    return [CardPrintView(**row) for row in rows]


# --- the default-art policy ---------------------------------------------------


def test_newest_spanish_highres_wins() -> None:
    prints = _views(
        _row("es-new", lang="es", released_at="2025-01-01"),
        _row("es-old", lang="es", released_at="2020-01-01"),
        _row("en-any"),
    )
    chosen = _default_print(prints, pool_scryfall_id="en-any")
    assert chosen is not None and chosen.scryfall_id == "es-new"


def test_spanish_lowres_beats_english_highres() -> None:
    # The high-res-only cut left almost no Spanish cards (Guille 2026-07-18):
    # a real-but-soft Spanish scan now wins over keeping the English art.
    prints = _views(_row("en-pool"), _row("es-lo", lang="es", highres=False))
    chosen = _default_print(prints, pool_scryfall_id="en-pool")
    assert chosen is not None and chosen.scryfall_id == "es-lo"


def test_spanish_highres_still_beats_spanish_lowres() -> None:
    prints = _views(
        _row("es-lo", lang="es", highres=False, released_at="2025-06-01"),
        _row("es-hi", lang="es", released_at="2020-01-01"),
    )
    chosen = _default_print(prints, pool_scryfall_id="whatever")
    assert chosen is not None and chosen.scryfall_id == "es-hi"


def test_no_spanish_and_highres_pool_art_stays() -> None:
    prints = _views(_row("en-pool"), _row("en-other", released_at="2025-01-01"))
    assert _default_print(prints, pool_scryfall_id="en-pool") is None


def test_no_spanish_and_lowres_pool_art_falls_back_to_english_highres() -> None:
    prints = _views(_row("en-hi"), _row("en-pool", highres=False))
    chosen = _default_print(prints, pool_scryfall_id="en-pool")
    assert chosen is not None and chosen.scryfall_id == "en-hi"


def test_nothing_highres_and_no_spanish_keeps_pool_art() -> None:
    prints = _views(_row("en-lo", highres=False))
    assert _default_print(prints, pool_scryfall_id="en-lo") is None


# --- the fetcher's disk cache -------------------------------------------------


def test_cached_prints_are_served_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prints_module, "CACHE_DIR", tmp_path)
    rows = [_row("cached-id", lang="es")]
    (tmp_path / "some-oracle-id.json").write_text(
        json.dumps({"schema": prints_module._CACHE_SCHEMA, "rows": rows}),
        encoding="utf-8",
    )

    def boom(*args, **kwargs):  # any network call is a test failure
        raise AssertionError("network hit despite cache")

    monkeypatch.setattr(prints_module.httpx, "get", boom)
    assert prints_module.fetch_prints("some-oracle-id") == rows


def test_query_does_not_exclude_digital_printings() -> None:
    """Digital MTGO/Arena scans (e.g. Lion's Eye Diamond's Vintage Masters) are
    legitimate proxy art and must stay in the picker (Guille 2026-07-20)."""
    assert "is:digital" not in prints_module._QUERY_TEMPLATE


def test_plain_list_cache_is_treated_as_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-schema cache (a bare list) refetches, so the -is:digital rollout
    reaches every already-cached card."""
    monkeypatch.setattr(prints_module, "CACHE_DIR", tmp_path)
    (tmp_path / "oid.json").write_text(json.dumps([_row("old")]), encoding="utf-8")
    monkeypatch.setattr(prints_module, "_download_prints", lambda o: [_row("fresh")])
    assert prints_module.fetch_prints("oid")[0]["scryfall_id"] == "fresh"


def test_stale_cache_without_image_status_is_refetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows cached before `image_status` existed may hide placeholders."""
    monkeypatch.setattr(prints_module, "CACHE_DIR", tmp_path)
    old_row = {k: v for k, v in _row("old-id").items() if k != "image_status"}
    (tmp_path / "oid.json").write_text(json.dumps([old_row]), encoding="utf-8")

    fetched: list[str] = []

    def fake_download(oracle_id: str) -> list[dict]:
        fetched.append(oracle_id)
        return [_row("fresh-id")]

    monkeypatch.setattr(prints_module, "_download_prints", fake_download)
    rows = prints_module.fetch_prints("oid")
    assert fetched == ["oid"]
    assert rows[0]["scryfall_id"] == "fresh-id"


def test_concurrent_fetch_of_same_id_is_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two threads fetching the same oracle_id must not race on the cache file.

    Regression: concurrent writers collided on the shared cache path (WinError
    32/5) -> 500 on the art default endpoint. A per-oracle_id lock serializes
    them; the loser reads the freshly written cache instead of re-downloading.
    """
    import threading

    monkeypatch.setattr(prints_module, "CACHE_DIR", tmp_path)
    prints_module._locks.clear()
    downloads: list[str] = []
    downloads_lock = threading.Lock()
    ready = threading.Barrier(2)

    def counting_download(oracle_id: str) -> list[dict]:
        with downloads_lock:
            downloads.append(oracle_id)
        return [_row("fresh-id", lang="es")]

    monkeypatch.setattr(prints_module, "_download_prints", counting_download)

    results: list[list[dict]] = []
    results_lock = threading.Lock()
    errors: list[BaseException] = []

    def run() -> None:
        ready.wait()  # release both threads at once to force the race
        try:
            rows = prints_module.fetch_prints("shared-oid")
            with results_lock:
                results.append(rows)
        except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(downloads) == 1  # the loser reused the cache, no second fetch
    assert all(r[0]["scryfall_id"] == "fresh-id" for r in results)
    assert not list(tmp_path.glob("*.tmp"))  # no stray temp files
    assert json.loads((tmp_path / "shared-oid.json").read_text(encoding="utf-8"))


def test_normalize_drops_placeholder_scans() -> None:
    card = {
        "id": "x",
        "image_status": "placeholder",
        "image_uris": {"normal": "stock.jpg"},
    }
    assert prints_module._normalize(card) is None


def test_face_images_prefers_top_level_then_card_faces() -> None:
    single = {"image_uris": {"normal": "front.jpg"}}
    assert prints_module._face_images(single) == ("front.jpg", "")
    dfc = {
        "card_faces": [
            {"image_uris": {"normal": "a.jpg"}},
            {"image_uris": {"normal": "b.jpg"}},
        ]
    }
    assert prints_module._face_images(dfc) == ("a.jpg", "b.jpg")


# --- service: gallery filter + batch defaults ---------------------------------


@pytest.fixture()
def client(real_app_state: AppState) -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = real_app_state
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


def _krenko_oracle_id(state: AppState) -> str:
    card = state.pool.resolve(COMMANDER)
    assert card is not None
    return card["oracle_id"]


def test_gallery_lists_every_real_scan_with_quality_flags(
    client: TestClient, real_app_state: AppState, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [_row("hi-1"), _row("lo-1", highres=False), _row("hi-es", lang="es")]
    monkeypatch.setattr(service, "fetch_prints", lambda oid: rows)
    oid = _krenko_oracle_id(real_app_state)
    body = client.get(f"/cards/{oid}/prints").json()
    assert body["name"] == COMMANDER
    # Low-res scans are shown too (badged client-side), placeholders never
    # reach this layer (the fetcher drops them at normalization).
    assert [p["scryfall_id"] for p in body["prints"]] == ["hi-1", "lo-1", "hi-es"]
    assert [p["highres"] for p in body["prints"]] == [True, False, True]
    assert body["default_scryfall_id"] == "hi-es"


def test_unknown_oracle_id_is_404(client: TestClient) -> None:
    response = client.get("/cards/no-such-oracle-id/prints")
    assert response.status_code == 404


def test_fullart_basics_defaults_to_theros(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The basics picker offers full-art options and defaults to the Theros
    printing when it is among them (Guille 2026-07-20)."""
    theros_id = service._theros_basic_id("Forest")
    rows = [_row("other-fullart"), _row(theros_id)]
    monkeypatch.setattr(service, "fetch_fullart_basics", lambda name: rows)
    body = client.get("/cards/basics/Forest/fullart").json()
    assert body["name"] == "Forest"
    assert {p["scryfall_id"] for p in body["prints"]} == {"other-fullart", theros_id}
    assert body["default_scryfall_id"] == theros_id


def test_fullart_basics_dragon_theme_defaults_to_tdm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Dragon deck defaults its basics to the TDM 'dragon eye' printing."""
    dragon_id = service.DRAGON_BASIC_IDS["Forest"]
    theros_id = service._theros_basic_id("Forest")
    rows = [_row(theros_id), _row(dragon_id)]
    monkeypatch.setattr(service, "fetch_fullart_basics", lambda name: rows)
    body = client.get("/cards/basics/Forest/fullart?theme=dragon").json()
    assert body["default_scryfall_id"] == dragon_id
    # Without the theme it stays Theros.
    plain = client.get("/cards/basics/Forest/fullart").json()
    assert plain["default_scryfall_id"] == theros_id


def test_fullart_basics_rejects_a_nonbasic(client: TestClient) -> None:
    assert client.get("/cards/basics/Sol Ring/fullart").status_code == 404


def test_batch_defaults_resolve_per_card(
    client: TestClient, real_app_state: AppState, monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = _krenko_oracle_id(real_app_state)
    rows = [_row("es-hi", lang="es")]
    monkeypatch.setattr(service, "fetch_prints", lambda o: rows)
    body = client.post("/cards/prints/defaults", json={"oracle_ids": [oid]}).json()
    assert body["defaults"][oid]["scryfall_id"] == "es-hi"


def test_batch_defaults_reject_unknown_ids(client: TestClient) -> None:
    response = client.post(
        "/cards/prints/defaults", json={"oracle_ids": ["bogus-id"]}
    )
    assert response.status_code == 404


def test_batch_defaults_cap_is_enforced(client: TestClient) -> None:
    ids = [f"id-{i}" for i in range(26)]
    response = client.post("/cards/prints/defaults", json={"oracle_ids": ids})
    assert response.status_code == 422


def test_scryfall_down_is_502(
    client: TestClient, real_app_state: AppState, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(oid: str):
        raise prints_module.PrintsError("boom")

    monkeypatch.setattr(service, "fetch_prints", down)
    oid = _krenko_oracle_id(real_app_state)
    assert client.get(f"/cards/{oid}/prints").status_code == 502


# --- the PDF honours art overrides --------------------------------------------


def test_face_urls_override_wins_and_is_id_based(real_app_state: AppState) -> None:
    card = real_app_state.pool.resolve(COMMANDER)
    assert card is not None
    urls = _face_urls(card, override_id="chosen-print-id")
    assert urls == [
        "https://api.scryfall.com/cards/chosen-print-id?format=image&version=normal"
    ]


def test_face_urls_override_adds_back_face_for_dfc(real_app_state: AppState) -> None:
    card = real_app_state.pool.resolve("Etali, Primal Conqueror")
    assert card is not None and card.get("image_uri_back_normal")
    urls = _face_urls(card, override_id="p-id")
    assert len(urls) == 2 and urls[1].endswith("&face=back")


def test_face_urls_override_beats_theros_basics(real_app_state: AppState) -> None:
    card = real_app_state.pool.resolve("Mountain")
    assert card is not None
    urls = _face_urls(card, override_id="fancy-mountain")
    assert "api.scryfall.com/cards/fancy-mountain" in urls[0]


def test_pdf_export_fetches_the_override_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_fetch(url: str) -> bytes:
        calls.append(url)
        return _ONE_BY_ONE_JPEG

    monkeypatch.setattr(service, "fetch_card_image", fake_fetch)
    response = client.post(
        "/export/pdf",
        json={
            "commander": COMMANDER,
            "cards": [{"name": "Sol Ring", "count": 1}],
            "art_overrides": {"Sol Ring": "override-print-id"},
        },
    )
    assert response.status_code == 200, response.text
    assert any("cards/override-print-id?format=image" in u for u in calls)
    # The commander had no override: its pool art was fetched, not an id URL.
    commander_calls = [u for u in calls if "override-print-id" not in u]
    assert commander_calls
