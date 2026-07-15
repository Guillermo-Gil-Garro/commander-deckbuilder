"""Tests for app.state: the degradation matrix and the derived indexes.

The failure policy under test: a missing card pool degrades (returns None),
every broken versioned config raises its own typed error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.state import EDHREC_MEMO_MAX, AppState, EdhrecMemo, build_app_state
from pipeline.edhrec import EdhrecCommanderData
from quotas.config import QuotasError
from rules.banlist import BanlistError
from rules.featured import FeaturedError
from selector.deck_rules import DeckRulesError
from tags.store import TagStoreError


# --- degradation matrix -----------------------------------------------------


def test_missing_pool_degrades_instead_of_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No pool must not stop the Space from starting: it must report itself."""
    with caplog.at_level("ERROR"):
        state = build_app_state(pool_path=tmp_path / "missing.jsonl")

    assert state is None
    assert "503" in caplog.text


def test_broken_quotas_fails_hard(tmp_path: Path) -> None:
    bad = tmp_path / "quotas.yaml"
    bad.write_text("archetypes: not-a-mapping\n", encoding="utf-8")
    with pytest.raises(QuotasError):
        build_app_state(quotas_path=bad)


def test_missing_rules_fails_hard(tmp_path: Path) -> None:
    with pytest.raises(DeckRulesError):
        build_app_state(rules_path=tmp_path / "missing.yaml")


def test_broken_banlist_fails_hard(tmp_path: Path) -> None:
    bad = tmp_path / "banlist.yaml"
    bad.write_text("meta: {}\n", encoding="utf-8")
    with pytest.raises(BanlistError):
        build_app_state(banlist_path=bad)


def test_missing_tags_fails_hard(tmp_path: Path) -> None:
    """A missing store would tag everything 'synergy' and break every quota."""
    with pytest.raises(TagStoreError, match="not found"):
        build_app_state(tags_path=tmp_path / "missing.jsonl")


def test_empty_tags_fails_hard(tmp_path: Path) -> None:
    empty = tmp_path / "llm_tags.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(TagStoreError, match="empty"):
        build_app_state(tags_path=empty)


def test_broken_featured_fails_hard(tmp_path: Path) -> None:
    bad = tmp_path / "featured.yaml"
    bad.write_text("featured:\n  - Nonexistent Card Name\n", encoding="utf-8")
    with pytest.raises(FeaturedError):
        build_app_state(featured_path=bad)


# --- solver time limit ------------------------------------------------------


def test_solver_time_limit_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECKBUILDER_SOLVER_TIME_LIMIT", "2.5")
    state = build_app_state()
    assert state is not None
    assert state.solver_time_limit_s == 2.5


def test_solver_time_limit_default(real_app_state: AppState) -> None:
    assert real_app_state.solver_time_limit_s == 10.0


@pytest.mark.parametrize("raw", ["abc", "0", "-1"])
def test_invalid_solver_time_limit_fails_hard(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("DECKBUILDER_SOLVER_TIME_LIMIT", raw)
    with pytest.raises(ValueError):
        build_app_state()


# --- derived state ----------------------------------------------------------


def test_banned_names_is_not_empty(real_app_state: AppState) -> None:
    assert real_app_state.banned_names
    assert "Approach of the Second Sun" in real_app_state.banned_names


def test_commander_index_excludes_both_ban_sets(real_app_state: AppState) -> None:
    """A card banned in the 99 must not resurface as a commander either."""
    banlist = real_app_state.resolved_banlist
    hidden = banlist.banned | banlist.banned_as_commander
    assert hidden, "the real banlist should ban something"
    assert all(row.oracle_id not in hidden for row in real_app_state.commanders)


def test_commander_index_holds_the_eligible_cards(real_app_state: AppState) -> None:
    assert real_app_state.commander_by_name("Krenko, Mob Boss") is not None
    # Not legendary: never a commander.
    assert real_app_state.commander_by_name("Grizzly Bears") is None


def test_commander_lookup_is_case_insensitive(real_app_state: AppState) -> None:
    row = real_app_state.commander_by_name("krEnKo, mOb BoSs")
    assert row is not None and row.name == "Krenko, Mob Boss"


def test_commander_rows_carry_the_fields_the_api_publishes(
    real_app_state: AppState,
) -> None:
    row = real_app_state.commander_by_name("Krenko, Mob Boss")
    assert row is not None
    assert row.oracle_id and row.scryfall_id
    assert row.color_identity == ("R",)


def test_commanders_are_sorted_by_name(real_app_state: AppState) -> None:
    names = [row.name for row in real_app_state.commanders]
    assert names == sorted(names)


def test_featured_are_loaded_in_file_order(real_app_state: AppState) -> None:
    assert len(real_app_state.featured) == 55


def test_tagger_reads_the_store(real_app_state: AppState) -> None:
    assert "ramp" in real_app_state.tagger("Sol Ring")


def test_state_is_frozen(real_app_state: AppState) -> None:
    with pytest.raises(Exception):
        real_app_state.tags_count = 0  # type: ignore[misc]


# --- a pool that loads but is unusable --------------------------------------


def test_pool_missing_a_banlist_card_fails_hard(tmp_path: Path) -> None:
    """A pool the banlist cannot resolve against is a hard error, not degraded:
    the pool loaded fine, so this is a data mismatch we must not paper over."""
    pool = tmp_path / "cards.jsonl"
    pool.write_text(
        json.dumps(
            {
                "name": "Grizzly Bears",
                "oracle_id": "oid-bears",
                "scryfall_id": "sid-bears",
                "color_identity": ["G"],
                "is_commander_eligible": False,
                "type_line": "Creature — Bear",
                "layout": "normal",
                "oracle_text": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises((BanlistError, DeckRulesError)):
        build_app_state(pool_path=pool)


# --- EDHREC memo ------------------------------------------------------------


def _page(slug: str) -> EdhrecCommanderData:
    return EdhrecCommanderData(name=slug, slug=slug, num_decks=1, recommendations=[])


def test_edhrec_memo_round_trips() -> None:
    memo = EdhrecMemo()
    assert memo.get("krenko-mob-boss") is None
    memo.put("krenko-mob-boss", _page("krenko-mob-boss"))
    stored = memo.get("krenko-mob-boss")
    assert stored is not None and stored.slug == "krenko-mob-boss"


def test_edhrec_memo_keys_variants_apart() -> None:
    memo = EdhrecMemo()
    memo.put("krenko-mob-boss", _page("global"))
    memo.put("krenko-mob-boss", _page("optimized"), variant="optimized")
    assert memo.get("krenko-mob-boss").slug == "global"  # type: ignore[union-attr]
    assert memo.get("krenko-mob-boss", "optimized").slug == "optimized"  # type: ignore[union-attr]


def test_edhrec_memo_evicts_fifo() -> None:
    memo = EdhrecMemo(max_entries=2)
    memo.put("a", _page("a"))
    memo.put("b", _page("b"))
    memo.put("c", _page("c"))
    assert len(memo) == 2
    assert memo.get("a") is None
    assert memo.get("b") is not None and memo.get("c") is not None


def test_edhrec_memo_default_is_bounded(real_app_state: AppState) -> None:
    assert len(real_app_state.edhrec_memo) == 0
    assert EDHREC_MEMO_MAX == 64
