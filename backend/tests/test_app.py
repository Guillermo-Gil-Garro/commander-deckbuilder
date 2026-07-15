"""Tests for the HTTP frontier: health, the degraded path and the SPA mount."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.state import AppState


@pytest.fixture()
def client(real_app_state: AppState) -> Iterator[TestClient]:
    """A client over an app whose state is already loaded.

    The lifespan is bypassed on purpose: it would rebuild the whole state for
    every test. Injecting `app.state.deckbuilder` is exactly what the lifespan
    does, and `test_lifespan_populates_state` covers the lifespan itself.
    """
    app_main.app.state.deckbuilder = real_app_state
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


@pytest.fixture()
def degraded_client() -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = None
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


def test_health_reports_the_loaded_state(
    client: TestClient, real_app_state: AppState
) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "cards_loaded": len(real_app_state.pool.by_name),
        "commanders": len(real_app_state.commanders),
        "banned": len(real_app_state.banned_names),
        "tags": real_app_state.tags_count,
    }


def test_health_without_pool_is_degraded(degraded_client: TestClient) -> None:
    """Degraded still answers 200: health is the diagnosis, not the failure."""
    response = degraded_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "cards_loaded": 0,
        "commanders": 0,
        "banned": 0,
        "tags": 0,
    }


def test_lifespan_populates_state(
    monkeypatch: pytest.MonkeyPatch, real_app_state: AppState
) -> None:
    monkeypatch.setattr(app_main, "build_app_state", lambda: real_app_state)

    with TestClient(app_main.app) as client:
        assert client.app.state.deckbuilder is real_app_state  # type: ignore[attr-defined]
        assert client.get("/health").json()["status"] == "ok"


def test_lifespan_starts_the_app_when_the_pool_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Space must come up and show the diagnosis, not crash-loop."""
    monkeypatch.setattr(app_main, "build_app_state", lambda: None)

    with TestClient(app_main.app) as client:
        assert client.get("/health").json()["status"] == "degraded"


# --- SPA mount (non-regression) ---------------------------------------------


def _spa_client(tmp_path: Path) -> TestClient:
    """An app with the SPA mounted at a temp build dir, wired like main.py."""
    (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (tmp_path / "asset.js").write_text("console.log(1)", encoding="utf-8")

    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/health")
    def _health() -> dict[str, str]:
        return {"status": "ok"}

    app.mount(
        "/", app_main.SPAStaticFiles(directory=tmp_path, html=True), name="frontend"
    )
    return TestClient(app)


def test_spa_serves_real_assets(tmp_path: Path) -> None:
    response = _spa_client(tmp_path).get("/asset.js")
    assert response.status_code == 200
    assert "console.log" in response.text


def test_spa_falls_back_to_index_for_client_routes(tmp_path: Path) -> None:
    response = _spa_client(tmp_path).get("/commander/krenko")
    assert response.status_code == 200
    assert "spa" in response.text


def test_spa_serves_the_shell_for_an_unknown_path(tmp_path: Path) -> None:
    """An unmatched path is the SPA shell, not a 404 — including API typos.

    The documented consequence of serving the API at the root with no /api
    prefix: there is no namespace left to tell a client-side route from a
    misspelled endpoint. `test_spa_still_serves_real_api_routes` is the guard
    that matters — real routes must never reach this fallback.
    """
    response = _spa_client(tmp_path).get("/buildd")

    assert response.status_code == 200
    assert "spa" in response.text


def test_spa_still_serves_real_api_routes(tmp_path: Path) -> None:
    """Routes registered before the mount win: the SPA never shadows the API."""
    response = _spa_client(tmp_path).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
