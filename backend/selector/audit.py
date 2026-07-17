"""Curated deck-audit flags — layer 1 of the audit (see DECISIONS 2026-07-17).

A small, hand-maintained list of *conditional* cards: cards whose value depends
on a property of the deck, which no cheap automatic signal captures. EDHREC
synergy cannot tell a "weak here" generic (Fierce Guardianship under a 9-mana
commander) from a "good anywhere" generic (Swords to Plowshares) — both score
~0 synergy. So the honest move for the known cases is to curate them with a
predicate.

The one case that matters today is the "free if you control your commander"
cycle: those spells are cast for {0} only while the commander is on the
battlefield, which a high-CMC commander rarely is when you need the free mode.
Layers 2 (low-synergy filler, needs a staples allowlist) and 3 (LLM audit) are
deferred; see ROADMAP.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

# A commander at or above this converted mana cost is rarely on the battlefield
# in time to make the "free if you control your commander" cycle actually free,
# so those spells almost always cost full price. Tunable.
FREE_SPELL_COMMANDER_CMC = 5.0

# The Commander Legends "free if you control your commander" cycle (WUBR). Cast
# for {0} only while the commander is out; otherwise a full-price spell.
_FREE_WITH_COMMANDER: tuple[str, ...] = (
    "Flawless Maneuver",
    "Fierce Guardianship",
    "Deadly Rollick",
    "Deflecting Swat",
)


@dataclass(frozen=True)
class ConditionalFlag:
    """One curated doubtful card and why it is doubtful in this deck."""

    name: str
    reason: str


def flag_conditionals(
    deck_card_names: Collection[str], commander_cmc: float
) -> list[ConditionalFlag]:
    """Curated doubtful cards in ``deck_card_names`` for this commander.

    ``deck_card_names`` are canonical pool names (the deck's non-basics). Only
    the free-with-commander cycle is curated so far, flagged when the commander
    is expensive enough that it is rarely out to make them free. The order is
    stable (the cycle's declared order), so repeated audits agree.
    """
    names = set(deck_card_names)
    flags: list[ConditionalFlag] = []
    if commander_cmc >= FREE_SPELL_COMMANDER_CMC:
        reason = (
            f"Gratis solo si controlas tu comandante, y con CMC {commander_cmc:.0f} "
            f"rara vez lo tienes en mesa a tiempo: casi siempre lo pagas entero. "
            f"Plantéate algo que sea bueno siempre."
        )
        for name in _FREE_WITH_COMMANDER:
            if name in names:
                flags.append(ConditionalFlag(name=name, reason=reason))
    return flags
