from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as app_main


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app_main.app)


def test_health_counts_cards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    cards = tmp_path / "cards.jsonl"
    cards.write_text('{"name": "A"}\n{"name": "B"}\n{"name": "C"}\n', encoding="utf-8")
    monkeypatch.setattr(app_main, "CARDS_FILE", cards)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "cards_loaded": 3}


def test_health_without_cards_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setattr(app_main, "CARDS_FILE", tmp_path / "missing.jsonl")

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "cards_loaded": 0}
