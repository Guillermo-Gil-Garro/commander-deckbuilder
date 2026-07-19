"""Tests for the deck audit (POST /audit + selector.audit).

Two pure layers that always run — the curated conditional flags and the
thinnest-category helper — then one end-to-end audit of a real high-CMC deck
built from the EDHREC disk cache, like the other ``/build`` tests.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app import service
from app.state import AppState
from quotas.config import QuotaBand
from selector.audit import (
    FILLER_SYNERGY_MAX,
    FREE_SPELL_COMMANDER_CMC,
    STAPLE_INCLUSION_MIN,
    flag_conditionals,
    flag_low_synergy_filler,
)


# --- layer 1: curated conditional flags (pure) -------------------------------


def test_free_spell_flagged_under_a_high_cmc_commander() -> None:
    flags = flag_conditionals({"Fierce Guardianship", "Sol Ring"}, commander_cmc=9.0)
    assert [f.name for f in flags] == ["Fierce Guardianship"]
    assert "9" in flags[0].reason  # the reason names the commander's CMC


def test_free_spell_not_flagged_under_a_cheap_commander() -> None:
    # A CMC-3 commander is reliably out, so the free mode is real: not flagged.
    assert flag_conditionals({"Fierce Guardianship"}, commander_cmc=3.0) == []


def test_the_cmc_threshold_is_inclusive() -> None:
    assert flag_conditionals({"Deadly Rollick"}, FREE_SPELL_COMMANDER_CMC)
    assert flag_conditionals({"Deadly Rollick"}, FREE_SPELL_COMMANDER_CMC - 0.1) == []


def test_only_cards_in_the_deck_are_flagged() -> None:
    assert flag_conditionals({"Sol Ring", "Lightning Bolt"}, 9.0) == []


def test_multiple_cycle_cards_flagged_in_a_stable_order() -> None:
    names = {"Deflecting Swat", "Flawless Maneuver", "Fierce Guardianship"}
    flags = flag_conditionals(names, 9.0)
    # The cycle's declared order, not the set's iteration order.
    assert [f.name for f in flags] == [
        "Flawless Maneuver",
        "Fierce Guardianship",
        "Deflecting Swat",
    ]


# --- layer 2: low-synergy filler (pure) --------------------------------------


def _filler_kwargs(**over):
    base = {
        "synergy_by_name": {"Filler Card": -0.1, "Sol Ring": -0.05, "Tech Card": 0.4},
        "inclusion_by_name": {"Filler Card": 0.05, "Sol Ring": 0.8, "Tech Card": 0.1},
        "land_names": set(),
        "protected_names": (),
    }
    base.update(over)
    return base


def test_low_synergy_low_inclusion_is_flagged() -> None:
    flags = flag_low_synergy_filler({"Filler Card"}, **_filler_kwargs())
    assert [f.name for f in flags] == ["Filler Card"]
    assert "relleno" in flags[0].reason


def test_high_inclusion_staple_is_not_flagged() -> None:
    # Sol Ring: ~0 synergy everywhere, but the inclusion bar IS the allowlist.
    assert flag_low_synergy_filler({"Sol Ring"}, **_filler_kwargs()) == []


def test_high_synergy_tech_is_not_flagged() -> None:
    assert flag_low_synergy_filler({"Tech Card"}, **_filler_kwargs()) == []


def test_lands_are_exempt() -> None:
    kwargs = _filler_kwargs(land_names={"Filler Card"})
    assert flag_low_synergy_filler({"Filler Card"}, **kwargs) == []


def test_protected_names_are_exempt() -> None:
    kwargs = _filler_kwargs(protected_names={"Filler Card"})
    assert flag_low_synergy_filler({"Filler Card"}, **kwargs) == []


def test_no_edhrec_data_gives_no_verdict() -> None:
    assert flag_low_synergy_filler({"Unknown Card"}, **_filler_kwargs()) == []


def test_thresholds_are_inclusive_exclusive_as_documented() -> None:
    kwargs = _filler_kwargs(
        synergy_by_name={"A": FILLER_SYNERGY_MAX, "B": FILLER_SYNERGY_MAX + 0.01},
        inclusion_by_name={"A": STAPLE_INCLUSION_MIN - 0.01, "B": 0.0},
    )
    # A: synergy AT the max (inclusive) and inclusion under the bar -> flagged.
    # B: synergy above the max -> clean, however low its inclusion.
    flags = flag_low_synergy_filler({"A", "B"}, **kwargs)
    assert [f.name for f in flags] == ["A"]


# --- thinnest category (pure) ------------------------------------------------


def test_thinnest_category_is_the_smallest_headroom() -> None:
    bands = {
        "lands": QuotaBand(min=34, max=39),
        "ramp": QuotaBand(min=8, max=12),
        "removal": QuotaBand(min=7, max=12),
        "synergy": QuotaBand(min=0, max=99),
    }
    counts = {"lands": 39, "ramp": 8, "removal": 11}  # ramp headroom 0, removal 4
    assert service._thinnest_category(counts, bands) == "ramp"


def test_thinnest_category_ignores_lands_and_synergy() -> None:
    bands = {"lands": QuotaBand(min=34, max=39), "synergy": QuotaBand(min=0, max=99)}
    assert service._thinnest_category({"lands": 34}, bands) is None


# --- end-to-end audit --------------------------------------------------------

COMMANDER = "The Ur-Dragon"  # CMC 9; EDHREC recommends Fierce Guardianship for it


@pytest.fixture()
def client(real_app_state: AppState) -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = real_app_state
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


@pytest.fixture()
def degraded_client() -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = None
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


@pytest.fixture()
def ur_dragon_deck(client: TestClient) -> dict:
    response = client.post("/build", json={"commander": COMMANDER})
    if response.status_code != 200:
        pytest.skip(f"Ur-Dragon build unavailable offline ({response.status_code})")
    return response.json()


def _refs(deck: dict) -> list[dict]:
    return [
        {"name": card["name"], "count": card["count"]}
        for card in deck["nonbasic_cards"] + deck["basic_lands"]
    ]


def test_audit_flags_a_free_spell_and_offers_a_palette(
    client: TestClient, ur_dragon_deck: dict
) -> None:
    deck_nonbasics = {card["name"] for card in ur_dragon_deck["nonbasic_cards"]}
    if "Fierce Guardianship" not in deck_nonbasics:
        pytest.skip("this Ur-Dragon build did not include a free-with-commander card")

    response = client.post(
        "/audit", json={"commander": COMMANDER, "deck": _refs(ur_dragon_deck)}
    )
    assert response.status_code == 200, response.text
    audit = response.json()

    flag = next(
        (f for f in audit["doubtful"] if f["card"]["name"] == "Fierce Guardianship"),
        None,
    )
    assert flag is not None, "the free-with-commander card must be flagged"
    assert flag["reason"]

    replacements = flag["replacements"]
    assert replacements, "a flagged card offers at least one replacement"
    assert {r["kind"] for r in replacements} <= {
        "same_role",
        "best_overall",
        "reinforce",
    }
    offered = [r["card"]["name"] for r in replacements]
    assert len(offered) == len(set(offered)), "the palette is deduped by name"
    # A replacement is a card you do not already run.
    deck_names = deck_nonbasics | {c["name"] for c in ur_dragon_deck["basic_lands"]}
    assert all(name not in deck_names for name in offered)

    # `missing` surfaces good cards the deck does not have.
    assert audit["missing"]
    assert all(m["name"] not in deck_names for m in audit["missing"])


def test_swap_replacements_offers_the_audit_palette_for_any_card(
    client: TestClient, ur_dragon_deck: dict
) -> None:
    """A manually chosen out card gets the audit's role-aware palette, not a
    flat top-N ranking (Guille 2026-07-19)."""
    out = ur_dragon_deck["nonbasic_cards"][0]["name"]
    response = client.post(
        "/swap/replacements",
        json={"commander": COMMANDER, "deck": _refs(ur_dragon_deck), "out": out},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["current"]["name"] == out
    replacements = body["replacements"]
    assert replacements, "there should be at least one feasible replacement"
    assert len(replacements) <= 4  # the palette is at most four
    assert {r["kind"] for r in replacements} <= {
        "same_role",
        "best_overall",
        "reinforce",
    }
    offered = [r["card"]["name"] for r in replacements]
    assert len(offered) == len(set(offered)), "the palette is deduped by name"
    deck_names = {c["name"] for c in ur_dragon_deck["nonbasic_cards"]} | {
        c["name"] for c in ur_dragon_deck["basic_lands"]
    }
    assert all(name not in deck_names for name in offered)
    # feasible_count is the full legal-swap total, never below what is shown.
    assert body["feasible_count"] >= len(replacements)


def test_swap_replacements_rejects_a_card_not_in_the_deck(
    client: TestClient, ur_dragon_deck: dict
) -> None:
    response = client.post(
        "/swap/replacements",
        json={
            "commander": COMMANDER,
            "deck": _refs(ur_dragon_deck),
            "out": "Black Lotus",  # legal card, but not in this deck
        },
    )
    assert response.status_code == 422


def test_audit_rejects_a_deck_that_is_not_99(client: TestClient) -> None:
    """A short deck is a 422 before any EDHREC read — a coherence error."""
    response = client.post(
        "/audit",
        json={"commander": COMMANDER, "deck": [{"name": "Sol Ring", "count": 1}]},
    )
    assert response.status_code == 422


def test_audit_is_degraded_without_a_pool(degraded_client: TestClient) -> None:
    response = degraded_client.post(
        "/audit",
        json={"commander": COMMANDER, "deck": [{"name": "Sol Ring", "count": 1}]},
    )
    assert response.status_code == 503
