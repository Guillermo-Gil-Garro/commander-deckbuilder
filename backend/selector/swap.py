"""Validated card swap without re-solving (Fase 4, the <100 ms path).

The player swaps a card and the deck must be re-validated live. Re-running the
selector is not an option: CP-SAT takes 0.05–10 s. So a swap never re-solves —
it re-counts. ``selector.constraints`` owns the numeric rules (quota floors and
ceilings, deck size, the Karsten land floor); this module owns the *policy*
rules that are about the swap itself rather than the deck's shape, and joins
both into one verdict.

**Nothing numeric is re-implemented here.** Every quota rule comes out of
``hard_violations``, including the non-worsening reading via its ``baseline``
parameter — CP-SAT may deliver a deck at a relaxed stage, and that deck already
breaches a floor. Without the baseline the checker would block every swap on
it, the swap that repairs it included.

Severity follows ``rules.yaml`` (``semantics.user_override``), not intuition:

- **RED** blocks. The banlist is the *only* policy RED ("no overrideable por el
  usuario"); the rest are RED because the result would not be a legal 99:
  off-identity card, a second copy of a non-basic, the commander in its own 99.
- **AMBER** informs and never blocks. ``never`` means "I never auto-recommend
  this", not "illegal": it is kept out of the candidate list, but a player who
  hunts it down by hand gets it with a warning. Same for removing an ``always``
  card — ``rules.yaml`` calls it "el mazo sigue siendo válido y exportable".

Every rule is evaluated; nothing short-circuits. The panel shows all the
reasons a swap is refused at once, so finding one blocker is not a reason to
stop looking for the others.

``Violation.actual``/``limit`` on policy codes read as "offending copies vs
copies allowed" (``banned`` 1 vs 0, ``duplicate_card`` 2 vs 1). Which card each
one is about is implicit in the code — ``remove_always`` is about ``out_card``,
every other about ``in_card`` — and the caller already knows both.

Candidate ranking is deliberately narrow: only cards sharing ``out_card``'s
primary category. Swapping a removal for a removal is the question the UI is
asking; "anything that fits" would bury the answer in noise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Collection, Mapping, Sequence

from quotas.config import QuotaBand
from quotas.validator import CategoryStatus, validate_deck
from selector.constraints import (
    CardFacts,
    DeckCounts,
    Severity,
    Violation,
    counts_after_swap,
    deck_counts,
    hard_violations,
)
from selector.greedy import (
    FILL_ORDER,
    LANDS_CATEGORY,
    SYNERGY_CATEGORY,
    SelectorError,
    _name_variants,
    _sorted_candidates,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SwapVerdict:
    """The answer to "can I play this swap?". ``feasible`` iff no blockers."""

    feasible: bool
    blockers: tuple[Violation, ...]
    warnings: tuple[Violation, ...]
    counts_after: DeckCounts
    statuses_after: Mapping[str, CategoryStatus]


@dataclass(frozen=True)
class Candidate:
    """One feasible replacement, with the ranking key's fields flat.

    Flat rather than wrapping ``CardFacts`` so ``greedy._sorted_candidates``
    orders it: the ordering key is defined once, in the selector.
    """

    name: str
    score: float
    cmc: float
    categories: tuple[str, ...]
    primary_category: str


def swap_is_feasible(
    *,
    deck: Sequence[tuple[CardFacts, int]],
    out_card: CardFacts,
    in_card: CardFacts,
    bands: Mapping[str, QuotaBand],
    commander: CardFacts,
    banned_names: Collection[str],
    never_names: Collection[str],
    watchlist_names: Collection[str],
    always_names: Collection[str] = (),
) -> SwapVerdict:
    """Validate replacing ``out_card`` with ``in_card`` in ``deck`` (the 99).

    ``deck`` rows and both cards must be server-side ``CardFacts`` rederived
    from the pool: a client that could declare a card's categories or color
    identity could declare any deck legal.
    """
    counts_before = deck_counts(deck)
    deck_names = _deck_name_index(deck)
    counts_after, blockers, warnings = _evaluate(
        counts_before=counts_before,
        deck_names=deck_names,
        out_card=out_card,
        in_card=in_card,
        bands=bands,
        commander=commander,
        banned=set(banned_names),
        never=set(never_names),
        watchlist=set(watchlist_names),
        always=set(always_names),
    )
    return SwapVerdict(
        feasible=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        counts_after=counts_after,
        statuses_after=_statuses(counts_after, bands),
    )


def swap_candidates(
    *,
    deck: Sequence[tuple[CardFacts, int]],
    out_card: CardFacts,
    pool_candidates: Sequence[tuple[CardFacts, float]],
    bands: Mapping[str, QuotaBand],
    commander: CardFacts,
    banned_names: Collection[str],
    never_names: Collection[str],
    watchlist_names: Collection[str],
    always_names: Collection[str] = (),
    limit: int,
) -> tuple[tuple[Candidate, ...], int]:
    """Feasible replacements for ``out_card``, best first, plus the total.

    ``pool_candidates`` are the commander's EDHREC recommendations already
    resolved against the pool, as ``(facts, score)``. Dropped from the universe:
    cards already in the deck, banned / ``never`` / watchlist cards, cards
    outside the commander's identity, and anything whose primary category is
    not ``out_card``'s. What survives is then filtered by ``swap_is_feasible``.

    The second element is the number of feasible candidates *before* ``limit``
    trims the list, so the UI can say "37 options" while showing ten.
    """
    if limit < 0:
        raise SelectorError(f"limit must be >= 0, got {limit}")
    # Counted once per request, never per candidate: this is the whole budget.
    counts_before = deck_counts(deck)
    deck_names = _deck_name_index(deck)
    banned = set(banned_names)
    never = set(never_names)
    watchlist = set(watchlist_names)
    always = set(always_names)
    excluded = banned | never | watchlist
    target = primary_category(out_card)

    feasible: list[Candidate] = []
    for facts, score in pool_candidates:
        if primary_category(facts) != target:
            continue
        variants = _name_variants(facts.name)
        if variants & excluded or variants & deck_names:
            continue
        if not facts.color_identity <= commander.color_identity:
            continue
        _, blockers, _ = _evaluate(
            counts_before=counts_before,
            deck_names=deck_names,
            out_card=out_card,
            in_card=facts,
            bands=bands,
            commander=commander,
            banned=banned,
            never=never,
            watchlist=watchlist,
            always=always,
        )
        if blockers:
            continue
        feasible.append(
            Candidate(
                name=facts.name,
                score=score,
                cmc=facts.cmc,
                categories=tuple(sorted(facts.categories)),
                primary_category=target,
            )
        )
    ordered = _sorted_candidates(feasible)
    return tuple(ordered[:limit]), len(ordered)


def primary_category(facts: CardFacts) -> str:
    """The category a card is displayed and swapped under (as in the selectors)."""
    if facts.is_land:
        return LANDS_CATEGORY
    return next(
        (category for category in FILL_ORDER if category in facts.categories),
        SYNERGY_CATEGORY,
    )


def _deck_name_index(deck: Sequence[tuple[CardFacts, int]]) -> set[str]:
    """Every name the deck answers to, face names included ("A // B" vs "A")."""
    names: set[str] = set()
    for facts, _ in deck:
        names |= _name_variants(facts.name)
    return names


def _evaluate(
    *,
    counts_before: DeckCounts,
    deck_names: set[str],
    out_card: CardFacts,
    in_card: CardFacts,
    bands: Mapping[str, QuotaBand],
    commander: CardFacts,
    banned: set[str],
    never: set[str],
    watchlist: set[str],
    always: set[str],
) -> tuple[DeckCounts, list[Violation], list[Violation]]:
    """The shared verdict core. Statuses are NOT computed here: the ranking
    path calls this hundreds of times and only reads ``blockers``."""
    if not _name_variants(out_card.name) & deck_names:
        raise SelectorError(f"cannot swap out {out_card.name!r}: not in the deck")
    counts_after = counts_after_swap(counts_before, out_card, in_card)
    in_variants = _name_variants(in_card.name)

    blockers: list[Violation] = []
    off_identity = in_card.color_identity - commander.color_identity
    if off_identity:
        blockers.append(Violation("color_identity", None, len(off_identity), 0))
    if in_variants & banned:
        blockers.append(Violation("banned", None, 1, 0))
    if _is_duplicate(in_card, out_card, deck_names):
        blockers.append(Violation("duplicate_card", None, 2, 1))
    if in_card.name == commander.name:
        blockers.append(Violation("commander_duplicate", None, 1, 0))
    # deck_size included: hard_violations already raises it when the swap does
    # not conserve the 99 (the basics-with-count safety net).
    blockers.extend(hard_violations(counts_after, bands, baseline=counts_before))

    warnings: list[Violation] = []
    if in_variants & never:
        warnings.append(Violation("add_never_manually", None, 1, 0, Severity.AMBER))
    if in_variants & watchlist:
        warnings.append(Violation("watchlist", None, 1, 0, Severity.AMBER))
    if _name_variants(out_card.name) & always:
        warnings.append(Violation("remove_always", None, 1, 0, Severity.AMBER))
    return counts_after, blockers, warnings


def _is_duplicate(in_card: CardFacts, out_card: CardFacts, deck_names: set[str]) -> bool:
    """Singleton rule: basics are the only card the deck may hold twice.

    Swapping a card for itself is a no-op, not a duplicate: ``out_card`` leaves
    as ``in_card`` arrives.
    """
    if in_card.is_basic or in_card.name == out_card.name:
        return False
    return bool(_name_variants(in_card.name) & deck_names)


def _statuses(
    counts: DeckCounts, bands: Mapping[str, QuotaBand]
) -> dict[str, CategoryStatus]:
    return validate_deck(
        counts.by_category,
        bands,
        curve=counts.curve,
        ramp_plus_draw=counts.by_category.get("ramp", 0)
        + counts.by_category.get("card_draw", 0),
    )
