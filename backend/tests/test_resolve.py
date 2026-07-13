import json
from pathlib import Path

import pytest

from rules.resolve import NameIndex, ResolutionError, build_name_index


def _write_pool(path: Path, cards: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(card) + "\n" for card in cards), encoding="utf-8"
    )
    return path


def _entry(name: str, oracle_id: str, eligible: bool = False) -> dict:
    return {"name": name, "oracle_id": oracle_id, "is_commander_eligible": eligible}


MINI_POOL = [
    _entry("Demonic Tutor", "oid-demonic-tutor"),
    _entry("Emeritus of Woe // Demonic Tutor", "oid-emeritus"),
    _entry("Tergrid, God of Fright // Tergrid's Lantern", "oid-tergrid", True),
    _entry("Everything Twice", "oid-dup-1"),
    _entry("Everything Twice", "oid-dup-2"),
    _entry("Shared Face // Alpha Back", "oid-shared-alpha"),
    _entry("Shared Face // Beta Back", "oid-shared-beta"),
]


@pytest.fixture
def index(tmp_path: Path) -> NameIndex:
    return build_name_index(_write_pool(tmp_path / "pool.jsonl", MINI_POOL))


def test_full_name_preferred_over_face_name(index: NameIndex) -> None:
    # "Demonic Tutor" is also a face of "Emeritus of Woe // Demonic Tutor",
    # but step 1 (full name) must win.
    resolved = index.resolve("Demonic Tutor")
    assert resolved.oracle_id == "oid-demonic-tutor"
    assert resolved.canonical_name == "Demonic Tutor"


def test_full_dfc_name_resolves(index: NameIndex) -> None:
    assert index.resolve("Emeritus of Woe // Demonic Tutor").oracle_id == (
        "oid-emeritus"
    )


def test_face_name_resolves_when_full_name_misses(index: NameIndex) -> None:
    resolved = index.resolve("Tergrid, God of Fright")
    assert resolved.oracle_id == "oid-tergrid"
    assert resolved.canonical_name == "Tergrid, God of Fright // Tergrid's Lantern"
    assert resolved.is_commander_eligible
    assert index.resolve("Tergrid's Lantern").oracle_id == "oid-tergrid"


def test_zero_matches_fails(index: NameIndex) -> None:
    with pytest.raises(ResolutionError, match="unresolvable name"):
        index.resolve("Nonexistent Card")


def test_substring_never_matches(index: NameIndex) -> None:
    with pytest.raises(ResolutionError):
        index.resolve("Demonic Tut")


def test_ambiguous_full_name_fails(index: NameIndex) -> None:
    with pytest.raises(ResolutionError, match="ambiguous name"):
        index.resolve("Everything Twice")


def test_ambiguous_face_name_fails(index: NameIndex) -> None:
    with pytest.raises(ResolutionError, match="ambiguous name"):
        index.resolve("Shared Face")


def test_missing_pool_fails(tmp_path: Path) -> None:
    with pytest.raises(ResolutionError, match="card pool not found"):
        build_name_index(tmp_path / "missing.jsonl")


def test_invalid_pool_entry_fails(tmp_path: Path) -> None:
    pool = tmp_path / "pool.jsonl"
    pool.write_text('{"name": "No Oracle Id"}\n', encoding="utf-8")
    with pytest.raises(ResolutionError, match="invalid pool entry"):
        build_name_index(pool)
