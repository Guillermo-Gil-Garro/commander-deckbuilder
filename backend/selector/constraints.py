"""Pure feasibility checker for the deck's hard rules (Fase 4, swap path).

The CP-SAT selector takes 0.05–10 s per solve, so a card swap cannot re-solve
and still answer in <100 ms. This module is the fast path: plain integer
counting over ``CardFacts``, no solver, no I/O — a swap is
``deck_counts`` + ``hard_violations`` over 99 rows.

It replicates the ``none`` (strictest) stage of ``selector.cp_sat._assemble_model``.
The two cannot share code literally (the solver builds ``LinearExpr`` over
BoolVars, this counts ``int``), so what is shared is the *semantics*, and the
constants come from ``selector.greedy`` / ``quotas.config`` — never rewritten.
The mapping, verified against ``cp_sat.py`` (lines as of this commit):

===================================================  ==================  ============
Rule                                                 ``cp_sat.py``       code
===================================================  ==================  ============
``Σ x + Σ basics == DECK_SIZE``                      ``:444``            ``deck_size``
``lands >= lands_min`` (hard at EVERY stage)         ``:450``            ``lands_floor``
``lands <= max(lands_band.max, lands_min)``          ``:457``            ``lands_ceiling``
category ceiling, counting ALL members              ``:462-466, :477``  ``category_ceiling``
category floor, counting NON-LAND members only      ``:467-475, :480``  ``category_floor``
===================================================  ==================  ============

Semantics worth restating, because they are the whole point (see
``AUDITORIA_SELECTORES §5.D.1``):

- **Ceilings count every member.** A land tagged ``removal`` (Boseiju) does
  consume removal's ceiling — the back door stays shut.
- **Floors count non-lands only.** A utility land can never *fulfil* a spell
  quota; ``Grim Backwoods`` tagged ``card_draw`` is not card draw.
- ``band.min > 0`` is the floor guard, exactly as in the model. Ceiling-only
  categories (``quotas.config.CEILING_ONLY_CATEGORIES``, i.e. ``synergy``) have
  ``min == 0`` by config validation, so the same guard covers them; testing the
  guard rather than the constant is what keeps this identical to the model, not
  merely equivalent under today's config.
- ``lands_min`` is the band floor raised to the deck's own Karsten floor,
  ``max(band.min, counts.karsten_floor)`` — which is precisely the fixpoint
  condition ``cp_sat`` iterates to. The Karsten floor is never relaxed.

**Non-worsening rule.** CP-SAT relaxes by stages, so a deck delivered at
``soft_category_floors`` or beyond *already* breaches a floor. A naive checker
would call every swap on it infeasible — including the swap that fixes it.
Hence every numeric rule is accepted when ``compliant(after) OR
not_worse(after, baseline)``. With ``baseline=None`` this is the pure hard
rule; on a ``none``-stage deck both readings coincide (see
``tests/test_constraints_contract.py``). ``deck_size`` is exempt: no stage ever
relaxes it, so a baseline can never breach it.

**Out of scope, on purpose.** ``forced`` (``always`` cards, ``x == 1``,
``cp_sat.py:435``) and ``excluded`` (land quality gate, ``x == 0``,
``cp_sat.py:437``) are generation-time rules, not deck validity: ``rules.yaml``
declares ``remove_always`` AMBER ("el mazo sigue siendo válido y exportable")
and a weak land in the deck is still a legal deck. Only the banlist is RED, and
it is filtered upstream. The swap layer owns those, as it owns the banlist.

**Known limit.** The contract test proves the checker does not reject what the
solver accepts. It cannot detect the reverse: a hard constraint added to
``_assemble_model`` and not here leaves the checker *laxer*, never stricter,
and the contract stays green. Adding a rule to the model means adding it here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from quotas.config import QuotaBand
from selector.greedy import (
    DECK_SIZE,
    LANDS_CATEGORY,
    SelectorError,
    curve,
    karsten_floor,
)


class Severity(str, Enum):
    """How a rule breach must be surfaced to the player."""

    RED = "red"  # blocks: the deck is not a valid 99
    AMBER = "amber"  # warns: the deck stays valid and exportable


@dataclass(frozen=True)
class Violation:
    """One breached hard rule. The Spanish message is the API layer's job.

    ``code`` is stable and machine-readable (``deck_size``, ``lands_floor``,
    ``lands_ceiling``, ``category_floor``, ``category_ceiling``); ``limit`` is
    the bound that ``actual`` failed against.
    """

    code: str
    category: str | None
    actual: int
    limit: int
    severity: Severity = Severity.RED


@dataclass(frozen=True)
class CardFacts:
    """Facts about one card, derived from the pool + tagger. NEVER from the client.

    The API must rebuild these server-side from the card name: a client that
    could declare its own ``categories`` could declare any deck legal.
    """

    name: str
    oracle_id: str
    categories: frozenset[str]
    cmc: float
    mana_cost: str
    color_identity: frozenset[str]
    is_basic: bool = False

    @property
    def is_land(self) -> bool:
        return LANDS_CATEGORY in self.categories


@dataclass(frozen=True)
class DeckCounts:
    """Everything the hard rules need, counted once.

    ``by_category`` counts every member (feeds ceilings and the counts reported
    to the player); ``nonland_by_category`` counts non-lands only (feeds
    floors). ``curve`` and ``karsten_floor`` are measured over the non-lands,
    as in both selectors.
    """

    total: int
    by_category: Mapping[str, int]
    nonland_by_category: Mapping[str, int]
    curve: Mapping[str, float]
    karsten_floor: int


def deck_counts(cards: Sequence[tuple[CardFacts, int]]) -> DeckCounts:
    """Count a deck (``(card, copies)`` rows; ``copies`` > 1 only for basics)."""
    total = 0
    by_category: dict[str, int] = {}
    nonland_by_category: dict[str, int] = {}
    nonland: list[CardFacts] = []
    for facts, count in cards:
        if count <= 0:
            raise SelectorError(f"invalid card count {count} for {facts.name!r}")
        total += count
        for category in facts.categories:
            by_category[category] = by_category.get(category, 0) + count
            if not facts.is_land:
                nonland_by_category[category] = (
                    nonland_by_category.get(category, 0) + count
                )
        if not facts.is_land:
            nonland.extend([facts] * count)
    return DeckCounts(
        total=total,
        by_category=by_category,
        nonland_by_category=nonland_by_category,
        curve=curve(nonland),
        karsten_floor=karsten_floor(nonland, by_category),
    )


def hard_violations(
    counts: DeckCounts,
    bands: Mapping[str, QuotaBand],
    *,
    baseline: DeckCounts | None = None,
) -> tuple[Violation, ...]:
    """Hard rules broken by ``counts``; empty means the deck is feasible.

    With ``baseline`` given, a numeric rule already broken by the baseline deck
    is only reported when the new counts are *worse* than it (see module doc).
    """
    lands_band = bands.get(LANDS_CATEGORY)
    if lands_band is None:
        raise SelectorError("bands must include a 'lands' band")

    violations: list[Violation] = []
    if counts.total != DECK_SIZE:
        # Never relaxed by any stage, so the baseline cannot excuse it.
        violations.append(
            Violation("deck_size", None, counts.total, DECK_SIZE)
        )

    lands = counts.by_category.get(LANDS_CATEGORY, 0)
    base_lands = (
        None if baseline is None else baseline.by_category.get(LANDS_CATEGORY, 0)
    )
    lands_min = max(lands_band.min, counts.karsten_floor)
    if _floor_broken(lands, lands_min, base_lands):
        violations.append(Violation("lands_floor", LANDS_CATEGORY, lands, lands_min))
    # The Karsten floor may legitimately exceed the band max (cp_sat.py:457).
    lands_max = max(lands_band.max, lands_min)
    if _ceiling_broken(lands, lands_max, base_lands):
        violations.append(Violation("lands_ceiling", LANDS_CATEGORY, lands, lands_max))

    for category in sorted(bands):
        if category == LANDS_CATEGORY:
            continue
        band = bands[category]
        members = counts.by_category.get(category, 0)
        base_members = (
            None if baseline is None else baseline.by_category.get(category, 0)
        )
        if _ceiling_broken(members, band.max, base_members):
            violations.append(
                Violation("category_ceiling", category, members, band.max)
            )
        if band.min > 0:
            nonlands = counts.nonland_by_category.get(category, 0)
            base_nonlands = (
                None
                if baseline is None
                else baseline.nonland_by_category.get(category, 0)
            )
            if _floor_broken(nonlands, band.min, base_nonlands):
                violations.append(
                    Violation("category_floor", category, nonlands, band.min)
                )
    return tuple(violations)


def _floor_broken(actual: int, limit: int, baseline: int | None) -> bool:
    return actual < limit and (baseline is None or actual < baseline)


def _ceiling_broken(actual: int, limit: int, baseline: int | None) -> bool:
    return actual > limit and (baseline is None or actual > baseline)
