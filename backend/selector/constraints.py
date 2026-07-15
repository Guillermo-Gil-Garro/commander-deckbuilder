"""Pure feasibility checker for the deck's hard rules (Fase 4, swap path).

The CP-SAT selector takes 0.05–10 s per solve, so a card swap cannot re-solve
and still answer in <100 ms. This module is the fast path: plain integer
counting over ``CardFacts``, no solver, no I/O — a swap is
``deck_counts`` + ``hard_violations`` over 99 rows, and ranking hundreds of
alternatives is ``counts_after_swap`` + ``hard_violations`` per candidate,
which never touches the 99 again. ``selector.swap`` owns the policy rules
(banlist, singleton, color identity) on top of these numeric ones.

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
from quotas.lands import curve_bucket, expected_curve_mv, land_count
from selector.greedy import DECK_SIZE, LANDS_CATEGORY, SelectorError


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
    floors). ``nonland_curve`` is the raw bucket histogram of the non-lands.

    Only integer counters are state; ``curve`` and ``karsten_floor`` derive
    from them. Storing the histogram rather than the finished curve fractions
    is what lets ``counts_after_swap`` rebuild a deck in O(categories): the
    ranking path scores hundreds of candidates against the same 99 rows, and
    recounting them per candidate is what the <100 ms budget cannot afford.

    A counter never holds a zero: a category drops out of the mapping when its
    last member leaves, so counts built by either route compare equal.
    """

    total: int
    by_category: Mapping[str, int]
    nonland_by_category: Mapping[str, int]
    nonland_curve: Mapping[str, int]

    @property
    def nonland_total(self) -> int:
        return sum(self.nonland_curve.values())

    @property
    def curve(self) -> Mapping[str, float]:
        """Fraction of non-lands per curve bucket (``selector.greedy.curve``)."""
        total = self.nonland_total
        if total == 0:
            return {}
        return {bucket: n / total for bucket, n in self.nonland_curve.items()}

    @property
    def karsten_floor(self) -> int:
        """The deck's own Karsten land floor.

        The aggregated form of ``selector.greedy.karsten_floor``: identical
        math over the histogram instead of a list of cards, because the swap
        path has counts and not cards. ``tests/test_constraints.py`` pins the
        two together — that test is the only thing keeping them from drifting.
        """
        ramp_plus_draw = self.by_category.get("ramp", 0) + self.by_category.get(
            "card_draw", 0
        )
        return land_count(expected_curve_mv(self.curve), ramp_plus_draw)


def deck_counts(cards: Sequence[tuple[CardFacts, int]]) -> DeckCounts:
    """Count a deck (``(card, copies)`` rows; ``copies`` > 1 only for basics)."""
    total = 0
    by_category: dict[str, int] = {}
    nonland_by_category: dict[str, int] = {}
    nonland_curve: dict[str, int] = {}
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
            bucket = curve_bucket(facts.cmc)
            nonland_curve[bucket] = nonland_curve.get(bucket, 0) + count
    return DeckCounts(
        total=total,
        by_category=by_category,
        nonland_by_category=nonland_by_category,
        nonland_curve=nonland_curve,
    )


def counts_after_swap(
    before: DeckCounts, out_card: CardFacts, in_card: CardFacts
) -> DeckCounts:
    """``before`` with one copy of ``out_card`` replaced by one copy of ``in_card``.

    O(categories), not O(99): the shortcut that makes candidate ranking fit the
    latency budget. ``tests/test_swap.py`` pins it to ``deck_counts`` over the
    resulting rows — the guarantee that the shortcut does not lie.

    ``total`` is carried over unchanged (one out, one in). Removing a card the
    deck does not hold is a usage error and raises rather than counting past
    zero; the swap layer rejects it up front by name.
    """
    by_category = dict(before.by_category)
    nonland_by_category = dict(before.nonland_by_category)
    nonland_curve = dict(before.nonland_curve)
    for card, delta in ((out_card, -1), (in_card, 1)):
        for category in card.categories:
            _bump(by_category, category, delta, card)
            if not card.is_land:
                _bump(nonland_by_category, category, delta, card)
        if not card.is_land:
            _bump(nonland_curve, curve_bucket(card.cmc), delta, card)
    return DeckCounts(
        total=before.total,
        by_category=by_category,
        nonland_by_category=nonland_by_category,
        nonland_curve=nonland_curve,
    )


def _bump(counter: dict[str, int], key: str, delta: int, card: CardFacts) -> None:
    value = counter.get(key, 0) + delta
    if value < 0:
        raise SelectorError(
            f"cannot remove {card.name!r}: the deck holds no {key!r} to remove"
        )
    if value == 0:
        counter.pop(key, None)
    else:
        counter[key] = value


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
