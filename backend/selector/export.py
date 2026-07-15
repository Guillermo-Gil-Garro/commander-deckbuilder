"""Decklist export formats. Lives in ``selector/`` and not in ``app/``: it
operates on ``DeckEntry``/``slot`` and the ``experiments/`` runners must import
it without pulling in FastAPI.

Archidekt import syntax: one ``<count>x <name> [<Category>]`` line per card,
the commander under ``[Commander]``, the maybeboard as a ``# Sideboard``
section. Pure formatting — it reads a finished build result and returns text.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from selector.greedy import DeckEntry

# Archidekt category shown per card (the slot the selector assigned, not every
# tag). Parity with ``quotas.config.CATEGORIES`` is asserted in test_export.
CATEGORY_LABELS: dict[str, str] = {
    "lands": "Lands",
    "ramp": "Ramp",
    "card_draw": "Card Draw",
    "removal": "Removal",
    "board_wipe": "Board Wipe",
    "wincons": "Wincons",
    "protection": "Protection",
    "synergy": "Synergy",
}


class DeckResultLike(Protocol):
    """The surface the exporter needs: ``GreedyResult`` and ``CpSatResult`` both fit."""

    commander_name: str
    mainboard: Sequence[DeckEntry]
    maybeboard: Sequence[DeckEntry]
    new_cards: Sequence[DeckEntry]


def format_archidekt(result: DeckResultLike) -> str:
    """Render a build result as an Archidekt import list. See module doc."""
    lines = [f"1x {result.commander_name} [Commander]"]
    for entry in result.mainboard:
        label = CATEGORY_LABELS.get(entry.slot, entry.slot)
        lines.append(f"{entry.count}x {entry.name} [{label}]")
    lines.append("")
    lines.append("# Sideboard")
    for entry in result.maybeboard:
        lines.append(f"1x {entry.name}")
    # Cold-start section at the end of the sideboard: the import format has no
    # sideboard categories, so a comment line (Archidekt ignores lines starting
    # with "#") separates them. Cards already in the maybeboard are not
    # repeated (a duplicated sideboard line would break the import).
    maybe_names = {entry.name for entry in result.maybeboard}
    fresh = [e for e in result.new_cards if e.name not in maybe_names]
    if fresh:
        lines.append("# --- cartas nuevas ---")
        for entry in fresh:
            lines.append(f"1x {entry.name}")
    lines.append("")
    return "\n".join(lines)
