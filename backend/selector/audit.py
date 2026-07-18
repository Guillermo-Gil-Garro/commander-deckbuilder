"""Deck-audit flags: curated conditionals (layer 1) and low-synergy filler
(layer 2). See DECISIONS 2026-07-17/18.

**Layer 1** is a small, hand-maintained list of *conditional* cards: cards
whose value depends on a property of the deck, which no cheap automatic signal
captures. EDHREC synergy cannot tell a "weak here" generic (Fierce
Guardianship under a 9-mana commander) from a "good anywhere" generic (Swords
to Plowshares) — both score ~0 synergy. So the known cases are curated with a
predicate. The one case today is the "free if you control your commander"
cycle: cast for {0} only while the commander is out, which a high-CMC
commander rarely is in time.

**Layer 2** flags probable filler with two EDHREC signals instead of a
hand-kept allowlist: a card is doubtful when its *synergy* with this commander
is at or below zero AND its global *inclusion* is low — the staples a curated
allowlist would have listed (Sol Ring, Swords) clear the inclusion bar by
themselves, so the list maintains itself. Lands are out of scope (the manabase
is Karsten's business), and cards with no EDHREC data give no verdict.

Layer 3 (LLM audit) is deferred; see ROADMAP.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
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


# Layer 2 thresholds, both tunable. A card is probable filler when its EDHREC
# synergy with THIS commander is at or below FILLER_SYNERGY_MAX and its global
# inclusion is under STAPLE_INCLUSION_MIN — high-inclusion cards are the
# staples a hand-kept allowlist would have listed (Sol Ring ~0.8, Swords ~0.25),
# so the allowlist maintains itself.
FILLER_SYNERGY_MAX = 0.0
STAPLE_INCLUSION_MIN = 0.25


def flag_low_synergy_filler(
    deck_card_names: Collection[str],
    *,
    synergy_by_name: Mapping[str, float],
    inclusion_by_name: Mapping[str, float],
    land_names: Collection[str],
    protected_names: Collection[str] = (),
) -> list[ConditionalFlag]:
    """Layer-2 flags: probable filler in ``deck_card_names``, stable order.

    A deck card is flagged when EDHREC knows it for this commander (both maps
    are keyed by canonical pool name) and it is low-synergy AND low-inclusion.
    ``land_names`` are exempt (the manabase is the Karsten module's business,
    not filler), and so are ``protected_names`` (forced ``always`` cards: the
    player's own rules outrank a statistical hunch). Cards absent from the
    maps yield no verdict — no signal, no flag.
    """
    protected = set(protected_names)
    lands = set(land_names)
    flags: list[ConditionalFlag] = []
    for name in sorted(deck_card_names):
        if name in lands or name in protected:
            continue
        synergy = synergy_by_name.get(name)
        inclusion = inclusion_by_name.get(name)
        if synergy is None or inclusion is None:
            continue
        if synergy <= FILLER_SYNERGY_MAX and inclusion < STAPLE_INCLUSION_MIN:
            flags.append(
                ConditionalFlag(
                    name=name,
                    reason=(
                        f"Sinergia {synergy:+.2f} con tu comandante y solo un "
                        f"{inclusion:.0%} de los mazos que podrían jugarla la "
                        f"juegan: probablemente es relleno. Mira el banquillo "
                        f"por algo con más razón de estar."
                    ),
                )
            )
    return flags
