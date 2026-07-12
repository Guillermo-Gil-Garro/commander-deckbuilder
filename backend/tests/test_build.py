import json
from pathlib import Path

from pipeline.build import (
    build,
    has_playable_layout,
    has_playable_type,
    is_commander_legal,
)


def _card(name: str, layout: str = "normal", commander: str = "legal") -> dict:
    return {
        "id": f"00000000-0000-0000-0000-{abs(hash(name)) % 10**12:012d}",
        "name": name,
        "mana_cost": "{G}",
        "cmc": 1.0,
        "type_line": "Creature — Elf",
        "oracle_text": "",
        "colors": ["G"],
        "color_identity": ["G"],
        "layout": layout,
        "legalities": {"commander": commander},
    }


def test_is_commander_legal() -> None:
    assert is_commander_legal(_card("Legal One"))
    assert not is_commander_legal(_card("Banned One", commander="banned"))
    assert not is_commander_legal(_card("Illegal One", commander="not_legal"))
    assert not is_commander_legal({"name": "No Legalities"})


def test_has_playable_layout() -> None:
    assert has_playable_layout(_card("Normal"))
    for layout in ("token", "emblem", "art_series", "scheme", "vanguard", "planar"):
        assert not has_playable_layout(_card("Bad", layout=layout))


def test_has_playable_type_excludes_stickers_and_attractions() -> None:
    assert has_playable_type(_card("Elf"))
    sticker = _card("Sticker Sheet")
    sticker["type_line"] = "Stickers"
    assert not has_playable_type(sticker)
    attraction = _card("Ferris Wheel")
    attraction["type_line"] = "Artifact — Attraction"
    assert not has_playable_type(attraction)
    assert has_playable_type({"name": "No Type Line"})


def test_build_filters_and_writes_jsonl(tmp_path: Path) -> None:
    bulk = [
        _card("Keep Me"),
        _card("Banned", commander="banned"),
        _card("A Token", layout="token"),
        _card("An Emblem", layout="emblem"),
    ]
    bulk_path = tmp_path / "bulk.json"
    bulk_path.write_text(json.dumps(bulk), encoding="utf-8")
    output_path = tmp_path / "cards.jsonl"

    total, legal, written = build(bulk_path, output_path)

    assert (total, legal, written) == (4, 3, 1)
    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["name"] == "Keep Me"
