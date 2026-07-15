"""Tests for selector.export (Archidekt import format)."""

from __future__ import annotations

from dataclasses import dataclass, field

from quotas.config import CATEGORIES
from selector.export import CATEGORY_LABELS, format_archidekt
from selector.greedy import DeckEntry


@dataclass
class FakeResult:
    """Minimal ``DeckResultLike``: what both selectors hand the exporter."""

    commander_name: str = "Krenko, Mob Boss"
    mainboard: list[DeckEntry] = field(default_factory=list)
    maybeboard: list[DeckEntry] = field(default_factory=list)
    new_cards: list[DeckEntry] = field(default_factory=list)


def entry(name: str, slot: str = "synergy", count: int = 1) -> DeckEntry:
    return DeckEntry(
        name=name, categories=(slot,), score=0.5, reason="test", slot=slot, count=count
    )


def test_commander_line_is_first_and_tagged_commander() -> None:
    out = format_archidekt(FakeResult(mainboard=[entry("Goblin Bushwhacker")]))
    assert out.splitlines()[0] == "1x Krenko, Mob Boss [Commander]"


def test_mainboard_uses_the_label_of_the_slot() -> None:
    out = format_archidekt(
        FakeResult(
            mainboard=[entry("Cultivate", slot="ramp"), entry("Wrath", slot="board_wipe")]
        )
    )
    assert "1x Cultivate [Ramp]" in out
    assert "1x Wrath [Board Wipe]" in out


def test_basics_keep_their_count() -> None:
    out = format_archidekt(FakeResult(mainboard=[entry("Mountain", "lands", count=34)]))
    assert "34x Mountain [Lands]" in out


def test_unknown_slot_falls_back_to_the_raw_slot_name() -> None:
    out = format_archidekt(FakeResult(mainboard=[entry("Weird", slot="not_a_category")]))
    assert "1x Weird [not_a_category]" in out


def test_maybeboard_goes_in_a_sideboard_section() -> None:
    out = format_archidekt(
        FakeResult(mainboard=[entry("A")], maybeboard=[entry("B"), entry("C")])
    )
    lines = out.splitlines()
    idx = lines.index("# Sideboard")
    assert lines[idx + 1 : idx + 3] == ["1x B", "1x C"]


def test_new_cards_section_dedupes_against_the_maybeboard() -> None:
    out = format_archidekt(
        FakeResult(
            mainboard=[entry("A")],
            maybeboard=[entry("Dup")],
            new_cards=[entry("Dup"), entry("Fresh")],
        )
    )
    # A duplicated sideboard line would break the Archidekt import.
    assert out.count("1x Dup") == 1
    lines = out.splitlines()
    assert lines[lines.index("# --- cartas nuevas ---") + 1] == "1x Fresh"


def test_no_new_cards_section_when_all_are_already_in_the_maybeboard() -> None:
    out = format_archidekt(
        FakeResult(mainboard=[entry("A")], maybeboard=[entry("Dup")], new_cards=[entry("Dup")])
    )
    assert "# --- cartas nuevas ---" not in out


def test_protection_has_a_label() -> None:
    out = format_archidekt(FakeResult(mainboard=[entry("Swiftfoot Boots", "protection")]))
    assert "1x Swiftfoot Boots [Protection]" in out


def test_labels_cover_exactly_the_quota_categories() -> None:
    # Guard against the next category being added to quotas.yaml and forgotten
    # here (protection was, and exported as the raw slug).
    assert set(CATEGORY_LABELS) == set(CATEGORIES)
