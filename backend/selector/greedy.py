"""Greedy deck selector by functional category (Fase 3, first candidate).

Given a commander, the card pool, EDHREC recommendations, resolved quota
bands and a pluggable tagger (``Callable[[str], set[str]]``), builds a
99-card mainboard plus maybeboard:

1. filter candidates (color identity, banlist, watchlist, in-pool);
2. score = ``synergy_weight * max(synergy, 0) + inclusion_weight *
   inclusion`` plus the flat boost of a matching preferred staple
   (negative EDHREC synergy is a staple signature, not a quality signal —
   see ``COMPARATIVA_EDHREC_B4``; the clamp is a ``ScoreWeights`` dial);
3. rules: ``always`` cards from ``rules.yaml`` (if given) are placed
   first — they count toward their quota categories and are never
   displaced by quota or filler picks; the banlist always wins over them
   (ban > never > always > prefer) and ``never`` cards are excluded from
   both mainboard and maybeboard, like the watchlist;
4. quota phase: fill each spell category to its ``min`` with the
   highest-scored candidates of that category, in ``FILL_ORDER``
   (scarcest categories first so plentiful ones cannot crowd them out);
   a multi-category card counts toward every category it is tagged with;
5. filler phase: fill the remaining spell slots by score without pushing
   any category over its ``max`` (synergy ceiling included); untagged
   non-land cards belong to the ``synergy`` bucket;
6. lands: the effective minimum is ``max(band.min, Karsten floor)`` of the
   deck under construction (recomputed to a fixpoint as filler shrinks);
   land slots take the best-scored recommended lands first — skipping
   non-basics below ``ScoreWeights.land_score_floor``, which lose to
   basics — and basics complete the rest, distributed proportionally to
   the deck's pure colored pips. If spell candidates run out before 99,
   the leftover slots also become basic lands (the deck is always 99).

Determinism: every ordering uses ``(-score, cmc, name)``; ties break by
ascending mana value (the cheaper card wins) and then alphabetically.

Cold start (Guille decision 2026-07-14): recommendations coming from the
EDHREC "New Cards" cardlist that did NOT make the mainboard are surfaced in
a dedicated ``new_cards`` section (cap ``NEW_CARDS_CAP``, ordered by score
desc) so the player sees recently printed cards whose EDHREC inclusion/
synergy has not caught up yet. It is independent from the maybeboard: the
maybeboard stays the best-N leftovers regardless of novelty, so a new card
may appear in both sections (or only in ``new_cards`` if its score is too
low for the maybeboard). Exclusions are the same as the maybeboard's:
banned, watchlist and ``never`` cards can never appear.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from quotas.config import QuotaBand
from quotas.color_sources import card_color_pips
from quotas.lands import curve_bucket, expected_curve_mv, land_count
from quotas.validator import CategoryStatus, validate_deck
from selector.deck_rules import (
    RuleContext,
    RulesConfig,
    boost_for,
    preferred_boosts,
    resolve_always,
    resolve_never,
    validate_forced_slot_budget,
)

logger = logging.getLogger(__name__)

DECK_SIZE = 99
MAYBEBOARD_SIZE = 15

# Cold-start section: EDHREC cardlist header that flags recently printed
# cards, cap of the section and the (fixed) display reason.
NEW_CARDS_HEADER = "New Cards"
NEW_CARDS_CAP = 10
NEW_CARDS_REASON = "carta nueva (EDHREC New Cards)"

LANDS_CATEGORY = "lands"
SYNERGY_CATEGORY = "synergy"

# Quota-phase order: scarcest candidate supply first (wincons/board wipes are
# rare in EDHREC pages, draw/ramp are plentiful), so that multi-category cards
# and shared slots never starve the narrow categories.
FILL_ORDER: tuple[str, ...] = ("wincons", "board_wipe", "removal", "ramp", "card_draw")

BASIC_BY_COLOR: dict[str, str] = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
}
COLORLESS_BASIC = "Wastes"
_WUBRG = ("W", "U", "B", "R", "G")


class SelectorError(Exception):
    """The deck cannot be built from the given inputs."""


class RecommendationLike(Protocol):
    """Minimum surface the selector needs from a recommendation.

    ``categories`` (the EDHREC cardlist headers, as in
    ``EdhrecRecommendation``) is read via ``getattr`` when present — it only
    feeds the cold-start ``new_cards`` section, so plain recommendations
    without it keep working.
    """

    name: str
    synergy: float
    inclusion: float


@dataclass(frozen=True)
class ScoreWeights:
    """Score dials: ``score = w_s * max(synergy, 0) + w_i * inclusion``.

    ``clamp_negative_synergy`` fixes the anti-staple bias found in
    ``COMPARATIVA_EDHREC_B4``: EDHREC synergy is negative for staples by
    construction, so it must not subtract (set to ``False`` to restore the
    raw linear score). ``land_score_floor`` is the filler land quality gate:
    a non-basic land scoring below it never beats a basic land.
    """

    synergy: float = 1.0
    inclusion: float = 1.0
    clamp_negative_synergy: bool = True
    land_score_floor: float = 0.05

    def score(self, synergy: float, inclusion: float) -> float:
        """Combined card score under these weights."""
        effective = max(synergy, 0.0) if self.clamp_negative_synergy else synergy
        return self.synergy * effective + self.inclusion * inclusion


@dataclass(frozen=True)
class DeckEntry:
    """One mainboard/maybeboard line. ``count`` > 1 only for basic lands."""

    name: str
    categories: tuple[str, ...]
    score: float | None
    reason: str
    slot: str  # grouping category for display
    count: int = 1


@dataclass
class GreedyResult:
    commander_name: str
    mainboard: list[DeckEntry]
    counts: dict[str, int]
    statuses: dict[str, CategoryStatus]
    maybeboard: list[DeckEntry]
    karsten_floor: int
    lands_target: int
    unresolved: list[str] = field(default_factory=list)
    # Cold-start section, independent from the maybeboard (see module doc).
    new_cards: list[DeckEntry] = field(default_factory=list)

    @property
    def total_cards(self) -> int:
        return sum(entry.count for entry in self.mainboard)


class PoolIndex:
    """Pool lookup by full name with face-name fallback ("A // B" vs "A")."""

    def __init__(self, cards: Iterable[Mapping[str, Any]]):
        self.by_name: dict[str, Mapping[str, Any]] = {}
        self._face_to_full: dict[str, str] = {}
        for card in cards:
            name = card["name"]
            self.by_name[name] = card
            if " // " in name:
                for face in name.split(" // "):
                    self._face_to_full.setdefault(face, name)

    def resolve(self, name: str) -> Mapping[str, Any] | None:
        card = self.by_name.get(name)
        if card is not None:
            return card
        full = self._face_to_full.get(name)
        return self.by_name.get(full) if full is not None else None

    def cards(self) -> Iterable[Mapping[str, Any]]:
        return self.by_name.values()


def load_pool(path: Any) -> PoolIndex:
    """Load ``cards.jsonl`` into a ``PoolIndex`` (fields read by name)."""
    import json
    from pathlib import Path

    path = Path(path)
    if not path.is_file():
        raise SelectorError(f"pool file not found: {path}")
    cards: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                cards.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SelectorError(f"invalid JSONL at {path}:{line_no}: {exc}") from exc
    return PoolIndex(cards)


@dataclass(frozen=True)
class _Candidate:
    name: str
    score: float
    categories: frozenset[str]
    cmc: float
    mana_cost: str
    is_basic: bool = False
    is_new: bool = False  # appeared in the EDHREC "New Cards" cardlist

    @property
    def is_land(self) -> bool:
        return LANDS_CATEGORY in self.categories


def _is_new_rec(rec: RecommendationLike) -> bool:
    """Whether the recommendation came from the EDHREC "New Cards" cardlist."""
    return NEW_CARDS_HEADER in (getattr(rec, "categories", None) or ())


def _new_cards_section(
    ordered: Sequence[_Candidate], picked: Mapping[str, DeckEntry]
) -> list[DeckEntry]:
    """Cold-start section: "New Cards" recommendations left out of the 99.

    ``ordered`` is already score-desc sorted and pre-filtered (banned,
    watchlist, ``never`` and off-identity cards never reach it), so this is
    just the top-``NEW_CARDS_CAP`` new cards not already in the mainboard.
    Independent from the maybeboard: overlap is possible and intended.
    """
    section: list[DeckEntry] = []
    for candidate in ordered:
        if len(section) >= NEW_CARDS_CAP:
            break
        if not candidate.is_new or candidate.name in picked or candidate.is_basic:
            continue
        section.append(
            DeckEntry(
                name=candidate.name,
                categories=tuple(sorted(candidate.categories)),
                score=candidate.score,
                reason=NEW_CARDS_REASON,
                slot=next(
                    (cat for cat in (LANDS_CATEGORY, *FILL_ORDER) if cat in candidate.categories),
                    SYNERGY_CATEGORY,
                ),
            )
        )
    return section


def _name_variants(name: str) -> set[str]:
    variants = {name}
    if " // " in name:
        variants.update(name.split(" // "))
    return variants


def _sorted_candidates(candidates: Iterable[_Candidate]) -> list[_Candidate]:
    # CMC before name: at equal score the cheaper card enters first (curve
    # bias fix, COMPARATIVA_EDHREC_B4), then alphabetical for determinism.
    return sorted(candidates, key=lambda c: (-c.score, c.cmc, c.name))


def _fits(
    candidate: _Candidate,
    counts: Mapping[str, int],
    bands: Mapping[str, QuotaBand],
    *,
    ignore: frozenset[str] = frozenset(),
) -> bool:
    """Adding the candidate keeps every one of its banded categories <= max.

    ``ignore`` skips categories whose limit is managed elsewhere (the lands
    phase fills to a target that may legitimately exceed the band max when
    the Karsten floor demands it).
    """
    for category in candidate.categories:
        if category in ignore:
            continue
        band = bands.get(category)
        if band is not None and counts.get(category, 0) + 1 > band.max:
            return False
    return True


def _add_counts(counts: dict[str, int], categories: Iterable[str], amount: int = 1) -> None:
    for category in categories:
        counts[category] = counts.get(category, 0) + amount


def _curve(cards: Sequence[_Candidate]) -> dict[str, float]:
    if not cards:
        return {}
    buckets: dict[str, int] = {}
    for card in cards:
        bucket = curve_bucket(card.cmc)
        buckets[bucket] = buckets.get(bucket, 0) + 1
    return {bucket: n / len(cards) for bucket, n in buckets.items()}


def _karsten_floor(nonland: Sequence[_Candidate], counts: Mapping[str, int]) -> int:
    ramp_plus_draw = counts.get("ramp", 0) + counts.get("card_draw", 0)
    return land_count(expected_curve_mv(_curve(nonland)), ramp_plus_draw)


def _basic_distribution(
    nonland: Sequence[_Candidate],
    commander_identity: Sequence[str],
    n_basics: int,
) -> list[tuple[str, int]]:
    """Basics per color, proportional to the deck's pure colored pips.

    Largest-remainder apportionment; ties break in WUBRG order. A deck with no
    colored pips splits evenly over the commander identity; an empty identity
    gets Wastes.
    """
    if n_basics <= 0:
        return []
    identity = [color for color in _WUBRG if color in commander_identity]
    if not identity:
        return [(COLORLESS_BASIC, n_basics)]
    demand: dict[str, int] = {color: 0 for color in identity}
    for card in nonland:
        for color, pips in card_color_pips(card.mana_cost).items():
            if color in demand:
                demand[color] += pips
    total = sum(demand.values())
    if total == 0:
        demand = {color: 1 for color in identity}
        total = len(identity)
    exact = {color: n_basics * demand[color] / total for color in identity}
    floors = {color: int(exact[color]) for color in identity}
    remainder = n_basics - sum(floors.values())
    by_fraction = sorted(
        identity, key=lambda color: (-(exact[color] - floors[color]), _WUBRG.index(color))
    )
    for color in by_fraction[:remainder]:
        floors[color] += 1
    return [
        (BASIC_BY_COLOR[color], floors[color]) for color in identity if floors[color] > 0
    ]


def build_deck_greedy(
    commander_name: str,
    *,
    pool: PoolIndex,
    recommendations: Sequence[RecommendationLike],
    bands: Mapping[str, QuotaBand],
    tagger: Callable[[str], set[str]],
    banned_names: frozenset[str] | set[str],
    watchlist_names: frozenset[str] | set[str],
    weights: ScoreWeights = ScoreWeights(),
    rules: RulesConfig | None = None,
    archetype: str | None = None,
) -> GreedyResult:
    """Build a 99-card mainboard + maybeboard for a commander. See module doc."""
    commander_card = pool.resolve(commander_name)
    if commander_card is None:
        raise SelectorError(f"commander not found in pool: {commander_name!r}")
    commander_full_name = commander_card["name"]
    commander_identity = set(commander_card.get("color_identity", []))
    lands_band = bands.get(LANDS_CATEGORY)
    if lands_band is None:
        raise SelectorError("bands must include a 'lands' band")

    # ── deck rules context: never-exclusions, boosts, budget check ───────
    rule_ctx: RuleContext | None = None
    never_excluded: set[str] = set()
    boosts: Mapping[str, float] = {}
    if rules is not None:
        if archetype is None:
            raise SelectorError(
                "archetype is required when rules are given (needed to "
                "evaluate archetype_in / archetype_not_in predicates)"
            )
        rule_ctx = RuleContext(
            commander_name=commander_full_name,
            color_identity=frozenset(commander_identity),
            archetype=archetype,
        )
        # Raises DeckRulesError if the always rules exceed the forced budget.
        validate_forced_slot_budget(rules, rule_ctx, banned_names)
        for never_name in resolve_never(rules, rule_ctx):
            card = pool.resolve(never_name)
            if card is None:
                raise SelectorError(
                    f"never rule card not found in pool: {never_name!r}"
                )
            never_excluded |= _name_variants(card["name"]) | {never_name}
        boosts = preferred_boosts(rules, commander_identity)

    # ── candidate filtering ──────────────────────────────────────────────
    candidates: dict[str, _Candidate] = {}
    unresolved: list[str] = []
    for rec in recommendations:
        card = pool.resolve(rec.name)
        if card is None:
            unresolved.append(rec.name)
            continue
        full_name = card["name"]
        if full_name == commander_full_name or full_name in candidates:
            continue
        variants = _name_variants(full_name) | {rec.name}
        if (
            variants & set(banned_names)
            or variants & set(watchlist_names)
            or variants & never_excluded
        ):
            continue
        if not set(card.get("color_identity", [])) <= commander_identity:
            continue
        categories = tagger(full_name) & set(bands) - {SYNERGY_CATEGORY}
        if not categories:
            categories = {SYNERGY_CATEGORY}
        candidates[full_name] = _Candidate(
            name=full_name,
            score=weights.score(rec.synergy, rec.inclusion) + boost_for(boosts, full_name),
            categories=frozenset(categories),
            cmc=float(card.get("cmc") or 0.0),
            mana_cost=card.get("mana_cost") or "",
            is_basic="Basic" in card.get("type_line", ""),
            is_new=_is_new_rec(rec),
        )
    if unresolved:
        logger.info(
            "%s: %d recommendations not in pool (skipped)",
            commander_name,
            len(unresolved),
        )

    # ── phase 0: always cards from rules.yaml (forced, never displaced) ──
    picked: dict[str, DeckEntry] = {}
    counts: dict[str, int] = {}
    auto_land_count = 0
    if rules is not None:
        assert rule_ctx is not None  # built above whenever rules is given
        for rule in resolve_always(rules, rule_ctx, banned_names):
            card = pool.resolve(rule.name)
            if card is None:
                raise SelectorError(
                    f"always rule card not found in pool: {rule.name!r}"
                )
            full_name = card["name"]
            if full_name == commander_full_name or full_name in picked:
                continue
            if _name_variants(full_name) & set(watchlist_names):
                # Watchlist contract: never auto-recommended, always included.
                logger.info(
                    "%s: always card %s is on the watchlist, skipped",
                    commander_name, full_name,
                )
                continue
            if not set(card.get("color_identity", [])) <= commander_identity:
                logger.info(
                    "%s: always card %s outside commander color identity, skipped",
                    commander_name, full_name,
                )
                continue
            candidate = candidates.get(full_name)
            if candidate is None:
                categories = tagger(full_name) & set(bands) - {SYNERGY_CATEGORY}
                if not categories:
                    categories = {SYNERGY_CATEGORY}
                candidate = _Candidate(
                    name=full_name,
                    # No EDHREC recommendation: only a preferred boost, if any.
                    score=boost_for(boosts, full_name),
                    categories=frozenset(categories),
                    cmc=float(card.get("cmc") or 0.0),
                    mana_cost=card.get("mana_cost") or "",
                    is_basic="Basic" in card.get("type_line", ""),
                )
                candidates[full_name] = candidate
            if (
                rule.quota_category is not None
                and rule.quota_category not in candidate.categories
            ):
                # The forced card consumes its declared quota slot: it counts
                # there on top of (or instead of, for pure filler) its tags.
                categories = (
                    set(candidate.categories) - {SYNERGY_CATEGORY}
                ) | {rule.quota_category}
                candidate = replace(candidate, categories=frozenset(categories))
                candidates[full_name] = candidate
            slot = (
                LANDS_CATEGORY
                if candidate.is_land
                else rule.quota_category
                or next(
                    (cat for cat in FILL_ORDER if cat in candidate.categories),
                    SYNERGY_CATEGORY,
                )
            )
            picked[full_name] = DeckEntry(
                name=full_name,
                categories=tuple(sorted(candidate.categories)),
                score=candidate.score,
                reason="always (rules.yaml)",
                slot=slot,
            )
            _add_counts(counts, candidate.categories)
            if candidate.is_land:
                auto_land_count += 1

    ordered = _sorted_candidates(candidates.values())
    spell_candidates = [c for c in ordered if not c.is_land]
    # Basics never compete as recommended lands: they only enter via the
    # color-demand distribution (and are the only allowed duplicates).
    land_candidates = [c for c in ordered if c.is_land and not c.is_basic]

    # ── phase 1: fill spell category minimums ────────────────────────────
    for category in FILL_ORDER:
        band = bands.get(category)
        if band is None:
            continue
        for candidate in spell_candidates:
            if counts.get(category, 0) >= band.min:
                break
            if candidate.name in picked or category not in candidate.categories:
                continue
            if not _fits(candidate, counts, bands):
                continue
            picked[candidate.name] = DeckEntry(
                name=candidate.name,
                categories=tuple(sorted(candidate.categories)),
                score=candidate.score,
                reason=f"{category} (cuota), score {candidate.score:.2f}",
                slot=category,
            )
            _add_counts(counts, candidate.categories)
        if counts.get(category, 0) < band.min:
            logger.info(
                "%s: category %s below min after quota phase (%d < %d)",
                commander_name,
                category,
                counts.get(category, 0),
                band.min,
            )
    # Auto-included lands live in ``picked`` too: keep them out of the spell
    # accounting (they belong to the lands target, not to spell slots).
    core_spells = [candidates[name] for name in picked if not candidates[name].is_land]
    core_counts = dict(counts)

    # ── phase 2 + Karsten fixpoint: filler spells vs. lands target ───────
    def select_filler(n_slots: int) -> list[_Candidate]:
        filler: list[_Candidate] = []
        trial_counts = dict(core_counts)
        for candidate in spell_candidates:
            if len(filler) >= n_slots:
                break
            if candidate.name in picked or not _fits(candidate, trial_counts, bands):
                continue
            filler.append(candidate)
            _add_counts(trial_counts, candidate.categories)
        return filler

    lands_target = min(lands_band.min, DECK_SIZE - len(core_spells))
    filler: list[_Candidate] = []
    for _ in range(16):
        slots = max(0, DECK_SIZE - lands_target - len(core_spells))
        filler = select_filler(slots)
        trial_counts = dict(core_counts)
        for candidate in filler:
            _add_counts(trial_counts, candidate.categories)
        floor = _karsten_floor(core_spells + filler, trial_counts)
        effective_min = max(lands_band.min, floor)
        # Spell candidates ran out: leftover slots become lands too.
        exhausted_target = DECK_SIZE - len(core_spells) - len(filler)
        new_target = max(effective_min, exhausted_target if len(filler) < slots else 0)
        new_target = min(new_target, DECK_SIZE - len(core_spells))
        if new_target <= lands_target:
            break
        lands_target = new_target
    else:
        raise SelectorError(
            f"lands target did not converge for {commander_name!r} "
            f"(last target {lands_target})"
        )

    for candidate in filler:
        primary = next(
            (cat for cat in FILL_ORDER if cat in candidate.categories),
            SYNERGY_CATEGORY,
        )
        picked[candidate.name] = DeckEntry(
            name=candidate.name,
            categories=tuple(sorted(candidate.categories)),
            score=candidate.score,
            reason=f"relleno por score {candidate.score:.2f}",
            slot=primary,
        )
        _add_counts(counts, candidate.categories)

    nonland_final = core_spells + filler
    karsten_floor = _karsten_floor(nonland_final, counts)

    # ── lands: recommended lands first, basics complete the rest ─────────
    lands_placed = auto_land_count
    for candidate in land_candidates:
        if lands_placed >= lands_target:
            break
        if candidate.name in picked:
            continue
        if candidate.score < weights.land_score_floor:
            # Land quality gate (COMPARATIVA_EDHREC_B4): a weak tapland never
            # beats a basic, and basics always remain available below.
            continue
        # A land tagged with other categories (e.g. cycling lands tagged as
        # draw) must not blow those maxes; the lands quota itself is governed
        # by the target, not by the band max (the Karsten floor may exceed it).
        if not _fits(candidate, counts, bands, ignore=frozenset({LANDS_CATEGORY})):
            continue
        picked[candidate.name] = DeckEntry(
            name=candidate.name,
            categories=tuple(sorted(candidate.categories)),
            score=candidate.score,
            reason=f"tierra recomendada, score {candidate.score:.2f}",
            slot=LANDS_CATEGORY,
        )
        _add_counts(counts, candidate.categories)
        lands_placed += 1

    basics = _basic_distribution(
        nonland_final, sorted(commander_identity), lands_target - lands_placed
    )
    basic_entries: list[DeckEntry] = []
    for basic_name, n in basics:
        if pool.resolve(basic_name) is None:
            raise SelectorError(f"basic land {basic_name!r} not found in pool")
        basic_entries.append(
            DeckEntry(
                name=basic_name,
                categories=(LANDS_CATEGORY,),
                score=None,
                reason=f"básica x{n} (proporcional a pips del mazo)",
                slot=LANDS_CATEGORY,
                count=n,
            )
        )
        _add_counts(counts, (LANDS_CATEGORY,), amount=n)

    mainboard = list(picked.values()) + basic_entries
    total = sum(entry.count for entry in mainboard)
    if total != DECK_SIZE:
        raise SelectorError(
            f"internal error: built {total} cards instead of {DECK_SIZE} "
            f"for {commander_name!r}"
        )

    statuses = validate_deck(
        counts,
        bands,
        curve=_curve(nonland_final),
        ramp_plus_draw=counts.get("ramp", 0) + counts.get("card_draw", 0),
    )

    # ── maybeboard: next best candidates that did not make the cut ───────
    maybeboard: list[DeckEntry] = []
    for candidate in ordered:
        if len(maybeboard) >= MAYBEBOARD_SIZE:
            break
        if candidate.name in picked or candidate.is_basic:
            continue
        full = all(
            bands[cat].max <= counts.get(cat, 0)
            for cat in candidate.categories
            if cat in bands
        ) and any(cat in bands for cat in candidate.categories)
        reason = (
            "fuera: cuota llena en " + "/".join(sorted(candidate.categories))
            if full
            else f"fuera por score {candidate.score:.2f}"
        )
        maybeboard.append(
            DeckEntry(
                name=candidate.name,
                categories=tuple(sorted(candidate.categories)),
                score=candidate.score,
                reason=reason,
                slot=next(
                    (cat for cat in (LANDS_CATEGORY, *FILL_ORDER) if cat in candidate.categories),
                    SYNERGY_CATEGORY,
                ),
            )
        )

    return GreedyResult(
        commander_name=commander_full_name,
        mainboard=mainboard,
        counts=counts,
        statuses=statuses,
        maybeboard=maybeboard,
        karsten_floor=karsten_floor,
        lands_target=lands_target,
        unresolved=unresolved,
        new_cards=_new_cards_section(ordered, picked),
    )
