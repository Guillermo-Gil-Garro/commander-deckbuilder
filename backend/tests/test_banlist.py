import json
from pathlib import Path

import pytest

from rules.banlist import (
    Banlist,
    BanlistError,
    load_banlist,
    resolve_banlist,
)
from rules.resolve import DEFAULT_POOL_PATH, NameIndex, build_name_index

META = {
    "version": "test",
    "status": "active",
    "updated": "2026-07-12",
    "review_cycle": "quarterly",
    "notes": "test",
}
NOMINATION = {
    "who": "anyone",
    "cooldown": "1 day",
    "threshold": "majority",
    "effect": "ban",
    "logging": "yes",
}


def _banlist(**sections) -> Banlist:
    payload = {
        "meta": META,
        "rules": [],
        "cards": [],
        "commanders": [],
        "watchlist": [],
        "explicitly_legal": [],
        "nomination_rule": NOMINATION,
        **sections,
    }
    return Banlist.model_validate(payload)


def _index(tmp_path: Path, names: dict[str, str]) -> NameIndex:
    pool = tmp_path / "pool.jsonl"
    pool.write_text(
        "".join(
            json.dumps(
                {"name": name, "oracle_id": oid, "is_commander_eligible": True}
            )
            + "\n"
            for name, oid in names.items()
        ),
        encoding="utf-8",
    )
    return build_name_index(pool)


def test_rule_exceptions_subtract(tmp_path: Path) -> None:
    index = _index(tmp_path, {"Card A": "oid-a", "Card B": "oid-b"})
    banlist = _banlist(
        rules=[
            {
                "id": "test_rule",
                "status": "banned",
                "predicate": "whatever",
                "reason": "test",
                "resolved_cards": ["Card A", "Card B"],
                "exceptions": [{"name": "Card B", "reason": "spared"}],
            }
        ]
    )
    resolved = resolve_banlist(banlist, index)
    assert resolved.banned == frozenset({"oid-a"})


def test_manual_cards_and_pending_review_are_banned(tmp_path: Path) -> None:
    index = _index(tmp_path, {"Card A": "oid-a", "Card B": "oid-b"})
    banlist = _banlist(
        cards=[
            {"name": "Card A", "status": "banned", "reason": "test"},
            {"name": "Card B", "status": "banned_pending_review", "reason": "test"},
        ]
    )
    resolved = resolve_banlist(banlist, index)
    assert resolved.banned == frozenset({"oid-a", "oid-b"})


def test_commanders_and_watchlist_scopes(tmp_path: Path) -> None:
    index = _index(tmp_path, {"Card A": "oid-a", "Card B": "oid-b"})
    banlist = _banlist(
        commanders=[
            {"name": "Card A", "status": "banned_as_commander", "reason": "test"}
        ],
        watchlist=[
            {"name": "Card A", "reason": "test", "scope": "in_the_99"},
            {"name": "Card B", "reason": "test"},
        ],
    )
    resolved = resolve_banlist(banlist, index)
    assert resolved.banned == frozenset()
    assert resolved.banned_as_commander == frozenset({"oid-a"})
    assert resolved.watchlist == {"oid-a": "in_the_99", "oid-b": None}


def test_unresolvable_name_fails(tmp_path: Path) -> None:
    index = _index(tmp_path, {"Card A": "oid-a"})
    banlist = _banlist(
        cards=[{"name": "Missing Card", "status": "banned", "reason": "test"}]
    )
    with pytest.raises(BanlistError, match="Missing Card"):
        resolve_banlist(banlist, index)


def test_load_banlist_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "banlist.yaml"
    path.write_text("meta: {version: '1'}\nbogus_section: []\n", encoding="utf-8")
    with pytest.raises(BanlistError, match="invalid banlist"):
        load_banlist(path)


def test_load_banlist_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BanlistError, match="not found"):
        load_banlist(tmp_path / "missing.yaml")


# --- Integration: the REAL banlist resolves fully against the REAL pool ----


@pytest.fixture(scope="module")
def real_index() -> NameIndex:
    return build_name_index(DEFAULT_POOL_PATH)


def test_real_banlist_loads_and_resolves(real_index: NameIndex) -> None:
    banlist = load_banlist()
    resolved = resolve_banlist(banlist, real_index)

    # Rules snapshot (11) minus no overlapping exceptions + 15 manual cards.
    assert len(resolved.banned) == 26
    assert len(resolved.banned_as_commander) == 3
    assert len(resolved.watchlist) == 6
    assert len(resolved.explicitly_legal) == 15

    demonic_tutor = real_index.resolve("Demonic Tutor")
    assert demonic_tutor.canonical_name == "Demonic Tutor"
    assert demonic_tutor.oracle_id in resolved.banned

    # Exceptions are legal even though the tutor rule exists.
    natural_order = real_index.resolve("Natural Order")
    assert natural_order.oracle_id not in resolved.banned
    assert natural_order.oracle_id in resolved.explicitly_legal

    # "The Mind Stone" (Infinity Stone) must not drag down "Mind Stone".
    mind_stone = real_index.resolve("Mind Stone")
    assert mind_stone.oracle_id not in resolved.banned
    assert real_index.resolve("The Mind Stone").oracle_id in resolved.banned

    # Tergrid: banned as commander, watched in the 99, resolved via face name.
    tergrid = real_index.resolve("Tergrid, God of Fright")
    assert tergrid.oracle_id in resolved.banned_as_commander
    assert resolved.watchlist[tergrid.oracle_id] == "in_the_99"
    assert tergrid.oracle_id not in resolved.banned


def test_real_banlist_alt_win_reason_group(real_index: NameIndex) -> None:
    banlist = load_banlist()
    group = {
        card.name
        for card in banlist.cards
        if card.reason_group == "alt_win_empty_library"
    }
    assert group == {
        "Thassa's Oracle",
        "Laboratory Maniac",
        "Jace, Wielder of Mysteries",
        "Doctor Doom, Unrivaled",
        "Demonic Consultation",
        "Tainted Pact",
    }

    resolved = resolve_banlist(banlist, real_index)
    for name in group:
        assert real_index.resolve(name).oracle_id in resolved.banned
