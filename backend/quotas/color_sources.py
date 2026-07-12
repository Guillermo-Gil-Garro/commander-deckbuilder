"""Color-source demand for the mana-base fixing axis (hypergeometric).

Ported from the TFM ``optimizer/color_sources.py``. The dual of the land
floor: ``quotas.lands`` answers *how many lands* a deck needs (Karsten's
closed-form regression); this module answers the other Karsten axis — *how
many sources of a given color* are needed to cast a spell with one/two/three
colored pips on time — by applying the **hypergeometric distribution
directly** (sampling without replacement), which is the original form of
Karsten's source analysis. We compute it ourselves (``math.comb``, stdlib,
no scipy) rather than copying Karsten's published table, with Karsten as
**external validation**.

The output is the table ``K(pips, turn)`` = the minimum number of sources of a
color such that ``P(having seen >= pips sources by the target turn) >=
reliability``.

Model (parameters declared, conservative by design):

- **Deck size N = 99.** Total deck is 100 (99 + commander), but the commander
  lives in the command zone and is never drawn, so the drawable population of the
  hypergeometric is the 99-card library.
- **Reliability = 0.90** (Karsten's standard threshold).
- **On the play**: one fewer card seen per turn than on the draw — conservative.
  ``cards_seen(T) = 7 + (T - 1)`` on the play; ``7 + T`` on the draw.
- **No mulligan**: pure hypergeometric. Declared conservative — the real London
  mulligan would lower the requirement; not modelled.
- **Deployment assumption**: at most one source played per turn. For the cells
  actually consumed (``turn >= pips``, since a card with ``N`` colored pips has
  CMC ``>= N``), having *seen* ``>= pips`` sources by turn ``T`` suffices.
  Cells with ``turn < pips`` are still computed for completeness.

Hypergeometric pmf, with ``N`` = deck, ``K`` = sources, ``n`` = cards seen,
``x`` = sources among them::

    h(x; N, n, K) = C(K, x) * C(N - K, n - x) / C(N, n)

``K(pips, turn) = min K in [pips, N] s.t. sum_{x>=pips} h(x) >= reliability``.
"""

from __future__ import annotations

import re
from math import comb
from typing import Mapping

PIPS: tuple[int, ...] = (1, 2, 3)
TURNS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)

COLORS: tuple[str, ...] = ("W", "U", "B", "R", "G")

DEFAULT_RELIABILITY = 0.90
DEFAULT_DECK_SIZE = 99
DEFAULT_ON_PLAY = True

OPENING_HAND = 7

# Karsten's orientative Commander source counts for 1/2/3 pips on-curve, used as
# an external sanity anchor (his published numbers fold in the free Commander
# mulligan, so our pure no-mulligan / on-the-play table is expected to run higher).
KARSTEN_ON_CURVE_ANCHOR: dict[int, int] = {1: 22, 2: 29, 3: 34}


def cards_seen(turn: int, *, on_play: bool = DEFAULT_ON_PLAY) -> int:
    """Cards seen by the main phase of ``turn``: opening hand plus draw steps.

    On the play there is no turn-1 draw, so ``7 + (turn - 1)``; on the draw,
    ``7 + turn``.
    """
    if turn < 1:
        raise ValueError(f"turn must be >= 1, got {turn}")
    draws = (turn - 1) if on_play else turn
    return OPENING_HAND + draws


def prob_at_least(pips: int, sources: int, seen: int, deck_size: int) -> float:
    """``P(X >= pips)`` for ``X ~ Hypergeometric(deck_size, sources, seen)``.

    ``math.comb(a, b)`` returns 0 when ``b > a``, so the boundary cases (more
    sources requested than drawn, or fewer sources in the deck than ``pips``)
    fall out without special-casing.
    """
    if pips < 0:
        raise ValueError(f"pips must be >= 0, got {pips}")
    if not 0 <= sources <= deck_size:
        raise ValueError(f"sources must be in [0, {deck_size}], got {sources}")
    if not 0 <= seen <= deck_size:
        raise ValueError(f"seen must be in [0, {deck_size}], got {seen}")
    total = comb(deck_size, seen)
    if total == 0:
        return 0.0
    favorable = 0
    upper = min(sources, seen)
    for x in range(pips, upper + 1):
        favorable += comb(sources, x) * comb(deck_size - sources, seen - x)
    return favorable / total


def min_sources(
    pips: int,
    turn: int,
    *,
    reliability: float = DEFAULT_RELIABILITY,
    deck_size: int = DEFAULT_DECK_SIZE,
    on_play: bool = DEFAULT_ON_PLAY,
) -> int:
    """Smallest ``K`` in ``[pips, deck_size]`` with ``P(X >= pips) >= reliability``.

    Raises ``ValueError`` if no ``K`` reaches the threshold (cannot happen for
    ``pips <= 3`` and a full 99-card deck, but guarded explicitly).
    """
    seen = cards_seen(turn, on_play=on_play)
    for sources in range(pips, deck_size + 1):
        if prob_at_least(pips, sources, seen, deck_size) >= reliability:
            return sources
    raise ValueError(
        f"No source count in [0, {deck_size}] reaches reliability {reliability} "
        f"for pips={pips}, turn={turn}."
    )


def build_demand_table(
    *,
    reliability: float = DEFAULT_RELIABILITY,
    deck_size: int = DEFAULT_DECK_SIZE,
    on_play: bool = DEFAULT_ON_PLAY,
) -> dict[int, dict[int, int]]:
    """The full ``K(pips, turn)`` table, ``pips -> turn -> K`` (deterministic)."""
    return {
        pips: {
            turn: min_sources(
                pips,
                turn,
                reliability=reliability,
                deck_size=deck_size,
                on_play=on_play,
            )
            for turn in TURNS
        }
        for pips in PIPS
    }


DEMAND_TABLE: dict[int, dict[int, int]] = build_demand_table()

# Empirical demand-correction factor (calibrated in the TFM, 2026-06-22, against real
# tournament manabases). The theoretical table is conservative by construction (no
# mulligan, on the play, reliability 0.90): real functional decks supply ~0.7 of K
# (direct supply/demand ratio over multicolor commanders). ``K_cal = round(factor * K)``
# brings the demand down to the observed level — absorbing the aggregate effect of the
# London mulligan and deckbuilders' risk tolerance without attributing it to any single
# modelled assumption. 0.80 (build-calibrated, minimises sources-per-color MAE) sits just
# above the bare 0.7 ratio so a small activating deficit remains on genuinely
# under-sourced colors. The factor scales only the **demand level**; the raw theoretical
# table stays pure for the Karsten comparison.
DEMAND_CALIBRATION_FACTOR = 0.80


def color_source_demand(pips: int, turn: int) -> int:
    """Calibrated ``K(pips, turn)`` (``round(DEMAND_CALIBRATION_FACTOR * K)``).

    Domain is validated explicitly: ``pips in PIPS`` and ``turn in TURNS``.
    """
    if pips not in DEMAND_TABLE:
        raise ValueError(f"pips must be one of {PIPS}, got {pips}")
    row = DEMAND_TABLE[pips]
    if turn not in row:
        raise ValueError(f"turn must be one of {TURNS}, got {turn}")
    return round(DEMAND_CALIBRATION_FACTOR * row[turn])


def color_source_targets(max_pips_by_color: Mapping[str, int]) -> dict[str, int]:
    """Calibrated minimum color sources per color, at the on-curve benchmark.

    ``max_pips_by_color`` maps a color letter (WUBRG) to the maximum number of
    **pure** pips of that color demanded by any single card in the pool/deck
    (count them with ``card_color_pips``, NOT with ``pipeline.model.count_pips``
    — see the ``card_color_pips`` docstring for the difference). Colors with 0
    pips are omitted from the result. Pips above 3 clamp to 3: outside the
    domain of Karsten's analysis, and 3 already demands nearly half the library.

    Target turn == pips (Karsten's canonical on-curve benchmark). Per-card
    commander-CMC turn modulation was never implemented in the TFM and is out
    of scope here, so the signature deliberately takes no commander CMC.
    """
    targets: dict[str, int] = {}
    for color, pips in max_pips_by_color.items():
        if color not in COLORS:
            raise ValueError(f"color must be one of {COLORS}, got {color!r}")
        if pips < 0:
            raise ValueError(f"pips must be >= 0, got {pips} for color {color}")
        if pips == 0:
            continue
        capped = min(pips, max(PIPS))
        targets[color] = color_source_demand(capped, capped)
    return targets


# ── Per-card colored pips (the demand side, card level) ──────────────────────

_MANA_TOKEN = re.compile(r"\{([^}]+)\}")


def card_color_pips(mana_cost: str) -> dict[str, int]:
    """Count single-color pips per color in a Scryfall ``mana_cost`` string.

    Only **pure** colored pips count. Hybrid (``{W/U}``) and phyrexian (``{W/P}``,
    ``{U/P}``) symbols are deliberately excluded — a hybrid pip can be paid by either
    of two colors (or life), so it does not create a *committed* demand for one
    color's sources; counting it would overstate the fixing pressure. Generic
    (``{2}``), variable (``{X}``) and colorless (``{C}``) symbols are not colored.

    This deliberately differs from ``pipeline.model.count_pips`` (the ``Card.pips``
    field), which counts hybrid/phyrexian symbols once per color for pool-level
    color statistics. For the Karsten fixing axis only the pure count is valid.

    Returns a dict with only the colors that have a positive count (empty for lands /
    costless cards).
    """
    counts: dict[str, int] = {}
    for token in _MANA_TOKEN.findall(mana_cost or ""):
        if token in COLORS:  # a lone W/U/B/R/G; "/" tokens (hybrid/phyrexian) skip
            counts[token] = counts.get(token, 0) + 1
    return counts
