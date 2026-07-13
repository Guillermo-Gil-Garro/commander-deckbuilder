import json
from pathlib import Path

import pytest

from rules.banlist import ResolvedBanlist, load_banlist, resolve_banlist
from rules.featured import FeaturedError, load_featured
from rules.resolve import DEFAULT_POOL_PATH, NameIndex, build_name_index

EMPTY_BANLIST = ResolvedBanlist(
    banned=frozenset(),
    banned_as_commander=frozenset(),
    watchlist={},
    explicitly_legal=frozenset(),
)


def _index(tmp_path: Path, cards: list[dict]) -> NameIndex:
    pool = tmp_path / "pool.jsonl"
    pool.write_text(
        "".join(json.dumps(card) + "\n" for card in cards), encoding="utf-8"
    )
    return build_name_index(pool)


def _featured_file(tmp_path: Path, names: list[str]) -> Path:
    path = tmp_path / "featured.yaml"
    path.write_text(
        "featured:\n" + "".join(f"  - {json.dumps(name)}\n" for name in names),
        encoding="utf-8",
    )
    return path


MINI_POOL = [
    {
        "name": "Tergrid, God of Fright // Tergrid's Lantern",
        "oracle_id": "oid-tergrid",
        "is_commander_eligible": True,
    },
    {
        "name": "Krenko, Mob Boss",
        "oracle_id": "oid-krenko",
        "is_commander_eligible": True,
    },
    {
        "name": "Grizzly Bears",
        "oracle_id": "oid-bears",
        "is_commander_eligible": False,
    },
]


def test_loads_and_resolves_to_canonical_names(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    path = _featured_file(tmp_path, ["Krenko, Mob Boss", "Tergrid, God of Fright"])
    featured = load_featured(path, resolved_banlist=EMPTY_BANLIST, name_index=index)
    assert [(c.name, c.oracle_id) for c in featured] == [
        ("Krenko, Mob Boss", "oid-krenko"),
        ("Tergrid, God of Fright // Tergrid's Lantern", "oid-tergrid"),
    ]


def test_banned_as_commander_fails(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    banlist = ResolvedBanlist(
        banned=frozenset(),
        banned_as_commander=frozenset({"oid-tergrid"}),
        watchlist={},
        explicitly_legal=frozenset(),
    )
    path = _featured_file(tmp_path, ["Krenko, Mob Boss", "Tergrid, God of Fright"])
    with pytest.raises(FeaturedError, match="banned_as_commander"):
        load_featured(path, resolved_banlist=banlist, name_index=index)


def test_duplicate_fails_even_across_name_forms(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    path = _featured_file(
        tmp_path,
        ["Tergrid, God of Fright", "Tergrid, God of Fright // Tergrid's Lantern"],
    )
    with pytest.raises(FeaturedError, match="duplicate"):
        load_featured(path, resolved_banlist=EMPTY_BANLIST, name_index=index)


def test_not_commander_eligible_fails(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    path = _featured_file(tmp_path, ["Grizzly Bears"])
    with pytest.raises(FeaturedError, match="not commander-eligible"):
        load_featured(path, resolved_banlist=EMPTY_BANLIST, name_index=index)


def test_unresolvable_name_fails(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    path = _featured_file(tmp_path, ["Nonexistent Commander"])
    with pytest.raises(FeaturedError, match="Nonexistent Commander"):
        load_featured(path, resolved_banlist=EMPTY_BANLIST, name_index=index)


def test_missing_file_fails(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    with pytest.raises(FeaturedError, match="not found"):
        load_featured(
            tmp_path / "missing.yaml",
            resolved_banlist=EMPTY_BANLIST,
            name_index=index,
        )


def test_unknown_top_level_key_fails(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    path = tmp_path / "featured.yaml"
    path.write_text("featured: []\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(FeaturedError, match="invalid featured"):
        load_featured(path, resolved_banlist=EMPTY_BANLIST, name_index=index)


# --- Integration: the REAL featured list against the REAL pool + banlist ---


def test_real_featured_commanders_load() -> None:
    index = build_name_index(DEFAULT_POOL_PATH)
    resolved = resolve_banlist(load_banlist(), index)
    featured = load_featured(resolved_banlist=resolved, name_index=index)

    assert len(featured) == 55
    assert len({c.oracle_id for c in featured}) == 55
    names = {c.name for c in featured}
    assert "Krenko, Mob Boss" in names
    assert all(c.oracle_id not in resolved.banned_as_commander for c in featured)
