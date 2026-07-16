import json
from pathlib import Path

import pytest

from quotas.config import load_quotas
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
        "featured:\n"
        + "".join(
            f"  - name: {json.dumps(name)}\n    description: {json.dumps('Hace cosas.')}\n"
            for name in names
        ),
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
    assert all(c.description == "Hace cosas." for c in featured)


def test_description_is_kept_verbatim(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    path = tmp_path / "featured.yaml"
    path.write_text(
        'featured:\n  - name: "Krenko, Mob Boss"\n'
        '    description: "Fabrica hordas de goblins."\n',
        encoding="utf-8",
    )
    featured = load_featured(path, resolved_banlist=EMPTY_BANLIST, name_index=index)
    assert featured[0].description == "Fabrica hordas de goblins."


def test_missing_description_fails(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    path = tmp_path / "featured.yaml"
    path.write_text('featured:\n  - name: "Krenko, Mob Boss"\n', encoding="utf-8")
    with pytest.raises(FeaturedError, match="invalid featured"):
        load_featured(path, resolved_banlist=EMPTY_BANLIST, name_index=index)


def test_empty_description_fails(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    path = tmp_path / "featured.yaml"
    path.write_text(
        'featured:\n  - name: "Krenko, Mob Boss"\n    description: ""\n',
        encoding="utf-8",
    )
    with pytest.raises(FeaturedError, match="invalid featured"):
        load_featured(path, resolved_banlist=EMPTY_BANLIST, name_index=index)


def test_bare_string_entry_fails(tmp_path: Path) -> None:
    """The old flat-list schema must be rejected, not silently accepted."""
    index = _index(tmp_path, MINI_POOL)
    path = tmp_path / "featured.yaml"
    path.write_text('featured:\n  - "Krenko, Mob Boss"\n', encoding="utf-8")
    with pytest.raises(FeaturedError, match="invalid featured"):
        load_featured(path, resolved_banlist=EMPTY_BANLIST, name_index=index)


def test_unknown_entry_key_fails(tmp_path: Path) -> None:
    index = _index(tmp_path, MINI_POOL)
    path = tmp_path / "featured.yaml"
    path.write_text(
        'featured:\n  - name: "Krenko, Mob Boss"\n    description: "Goblins."\n'
        "    archetype: aggro\n",
        encoding="utf-8",
    )
    with pytest.raises(FeaturedError, match="invalid featured"):
        load_featured(path, resolved_banlist=EMPTY_BANLIST, name_index=index)


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
    # Hover text: every one written, and short enough to read at a glance.
    assert all(c.description.strip() for c in featured)
    assert all(len(c.description) <= 90 for c in featured)


def test_every_featured_commander_has_an_explicit_archetype() -> None:
    """The picker's playstyle filter needs a real archetype for all 55.

    Guards the canonical-name trap: quotas.yaml is keyed by pool name, so a
    double-faced commander listed by its front face only (``Kefka, Court Mage``
    instead of ``Kefka, Court Mage // Kefka, Ruler of Ruin``) would silently
    fall back to the default archetype instead of failing.
    """
    index = build_name_index(DEFAULT_POOL_PATH)
    resolved = resolve_banlist(load_banlist(), index)
    featured = load_featured(resolved_banlist=resolved, name_index=index)
    config = load_quotas()

    unmapped = [c.name for c in featured if c.name not in config.commanders]
    assert unmapped == []
