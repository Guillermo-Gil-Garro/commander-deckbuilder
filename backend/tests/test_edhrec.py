import json
from pathlib import Path

import pytest

from pipeline.edhrec import (
    EdhrecError,
    fetch_commander,
    parse_commander_page,
    slugify_commander,
)

FIXTURE = Path(__file__).parent / "fixtures" / "edhrec_sample.json"


@pytest.fixture()
def sample_raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_slugify_commander() -> None:
    assert slugify_commander("Atraxa, Praetors' Voice") == "atraxa-praetors-voice"
    assert slugify_commander("K'rrik, Son of Yawgmoth") == "krrik-son-of-yawgmoth"
    assert slugify_commander("Krenko, Mob Boss") == "krenko-mob-boss"
    assert slugify_commander("Double  Spaced   Name") == "double-spaced-name"
    assert slugify_commander("Curly’s Quote") == "curlys-quote"
    assert slugify_commander("Séance Ünstable") == "seance-unstable"
    assert slugify_commander("  Edges. And, Dots.  ") == "edges-and-dots"


def test_parse_fixture(sample_raw: dict) -> None:
    data = parse_commander_page(sample_raw, "atraxa-praetors-voice")

    assert data.name == "Atraxa, Praetors' Voice"
    assert data.slug == "atraxa-praetors-voice"
    assert data.num_decks == 42495
    assert len(data.recommendations) == 65

    by_name = {rec.name: rec for rec in data.recommendations}
    tekuthal = by_name["Tekuthal, Inquiry Dominus"]
    assert tekuthal.synergy == pytest.approx(0.2717298248462816)
    assert tekuthal.num_decks == 28009
    assert tekuthal.potential_decks == 42495
    assert tekuthal.inclusion == pytest.approx(28009 / 42495)
    assert tekuthal.categories == ["High Synergy Cards"]

    for rec in data.recommendations:
        assert 0.0 <= rec.inclusion <= 1.0
        assert rec.categories


def test_parse_merges_categories_for_duplicates() -> None:
    view = {
        "name": "Astral Cornucopia",
        "synergy": 0.2,
        "num_decks": 10,
        "potential_decks": 40,
    }
    raw = {
        "container": {
            "json_dict": {
                "card": {"name": "Test Commander", "num_decks": 40},
                "cardlists": [
                    {"header": "High Synergy Cards", "cardviews": [view]},
                    {"header": "Mana Artifacts", "cardviews": [dict(view)]},
                ],
            }
        }
    }

    data = parse_commander_page(raw, "test-commander")

    assert len(data.recommendations) == 1
    rec = data.recommendations[0]
    assert rec.categories == ["High Synergy Cards", "Mana Artifacts"]
    assert rec.inclusion == pytest.approx(0.25)


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"container": {}},
        {"container": {"json_dict": {"card": {}, "cardlists": []}}},
        {
            "container": {
                "json_dict": {
                    "card": {"name": "X", "num_decks": 1},
                    "cardlists": [{"header": "Creatures"}],
                }
            }
        },
        {
            "container": {
                "json_dict": {
                    "card": {"name": "X", "num_decks": 1},
                    "cardlists": [
                        {"header": "Creatures", "cardviews": [{"name": "No Signals"}]}
                    ],
                }
            }
        },
    ],
)
def test_parse_unexpected_structure_raises(raw: dict) -> None:
    with pytest.raises(EdhrecError):
        parse_commander_page(raw, "broken")


def test_fetch_commander_uses_cache_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipeline.edhrec as edhrec

    monkeypatch.setattr(edhrec, "CACHE_DIR", tmp_path)
    cache_file = tmp_path / "atraxa-praetors-voice.json"
    cache_file.write_bytes(FIXTURE.read_bytes())

    def _no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted despite existing cache")

    monkeypatch.setattr(edhrec.httpx, "get", _no_network)

    data = fetch_commander("Atraxa, Praetors' Voice")

    assert data.slug == "atraxa-praetors-voice"
    assert data.num_decks == 42495
    assert len(data.recommendations) == 65


def test_fetch_commander_downloads_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipeline.edhrec as edhrec

    monkeypatch.setattr(edhrec, "CACHE_DIR", tmp_path)
    payload = FIXTURE.read_bytes()
    calls: list[str] = []

    class FakeResponse:
        content = payload

        def raise_for_status(self) -> None:
            return None

    def _fake_get(url: str, **kwargs: object) -> FakeResponse:
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(edhrec.httpx, "get", _fake_get)

    data = fetch_commander("Atraxa, Praetors' Voice")

    assert calls == [
        "https://json.edhrec.com/pages/commanders/atraxa-praetors-voice.json"
    ]
    assert (tmp_path / "atraxa-praetors-voice.json").read_bytes() == payload
    assert len(data.recommendations) == 65

    fetch_commander("Atraxa, Praetors' Voice")
    assert len(calls) == 1


def test_fetch_commander_variant_uses_separate_url_and_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipeline.edhrec as edhrec

    monkeypatch.setattr(edhrec, "CACHE_DIR", tmp_path)
    payload = FIXTURE.read_bytes()
    calls: list[str] = []

    class FakeResponse:
        content = payload

        def raise_for_status(self) -> None:
            return None

    def _fake_get(url: str, **kwargs: object) -> FakeResponse:
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(edhrec.httpx, "get", _fake_get)

    data = fetch_commander("Atraxa, Praetors' Voice", variant="optimized")

    assert calls == [
        "https://json.edhrec.com/pages/commanders/atraxa-praetors-voice/optimized.json"
    ]
    variant_cache = tmp_path / "atraxa-praetors-voice--optimized.json"
    assert variant_cache.read_bytes() == payload
    assert not (tmp_path / "atraxa-praetors-voice.json").exists()
    assert len(data.recommendations) == 65

    # Variant cache hit: no second download.
    fetch_commander("Atraxa, Praetors' Voice", variant="optimized")
    assert len(calls) == 1

    # Global page is a separate cache entry and URL.
    fetch_commander("Atraxa, Praetors' Voice")
    assert calls[1] == (
        "https://json.edhrec.com/pages/commanders/atraxa-praetors-voice.json"
    )
    assert (tmp_path / "atraxa-praetors-voice.json").read_bytes() == payload


def test_fetch_commander_variant_cache_does_not_shadow_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipeline.edhrec as edhrec

    monkeypatch.setattr(edhrec, "CACHE_DIR", tmp_path)
    (tmp_path / "atraxa-praetors-voice.json").write_bytes(FIXTURE.read_bytes())

    def _no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted for uncached variant")

    monkeypatch.setattr(edhrec.httpx, "get", _no_network)

    fetch_commander("Atraxa, Praetors' Voice")

    with pytest.raises(AssertionError, match="network access attempted"):
        fetch_commander("Atraxa, Praetors' Voice", variant="optimized")
