"""CP-SAT deck selector (Fase 3, second candidate) — TFM port, simplified.

Ported from the author's TFM ``optimizer/cp_sat_builder.py`` and adapted to
this project's data structures (same inputs as ``selector.greedy`` for direct
comparability). One BoolVar per non-basic candidate, one IntVar per basic
land type, ``Σ == 99``, objective ``Maximize(Σ score·x − penalties)`` in
integer score space (``SCORE_SCALE``).

Constraints and penalties:

- **Bands** from ``quotas.resolver.resolve_bands``. At the strictest stage all
  category minimums and maximums are hard. The ``lands`` minimum is
  ``max(band.min, Karsten floor)`` — the floor depends on the selected deck
  (curve, ramp+draw), so it is resolved by an outer fixpoint: solve, compute
  the floor of the solution, raise the hard lands lower bound if breached,
  re-solve. Unlike the TFM, the Karsten floor is NEVER relaxed here.
- **Color fixing** (soft, never relaxed within composition stages): per
  commander-identity color, a convex penalty on the deficit of sources versus
  the fixed ``quotas.color_sources.pool_color_source_targets`` demand
  (computed over the non-land candidates — a simplification vs. the TFM's
  per-selection ``K_c`` variable). The convex shape enters as epigraph
  supporting lines (no per-source booleans), exactly the TFM trick. Supply is
  heuristic: mana production parsed from ``type_line`` basic subtypes and
  ``oracle_text`` "Add ..." lines (the pool has no ``produced_mana`` field).
- **Curve penalty**: NOT ported — this project has no target-curve source
  (the TFM took it from EDHREC average decks); left out by design.
- **Staged relaxation** (simplified to the project's rules): ``none`` →
  ``soft_category_floors`` (category minimums become penalised soft floors)
  → ``drop_ceilings`` (category/lands maximums dropped too) →
  ``base_size_and_lands`` (only 99 + lands ≥ Karsten/band floor). Banlist,
  color identity, the Karsten floor and forced always cards are never
  relaxed.
- **Rules** (``rules.yaml``, optional): ``always`` cards are forced with a
  hard ``x == 1`` at every stage (precedence ban > never > always > prefer:
  banned or never-matching cards never reach the model); ``never`` cards are
  excluded from candidates entirely (mainboard and maybeboard, like the
  watchlist); ``preferred`` cards add their flat boost to the score.
- **Score corrections** (COMPARATIVA_EDHREC_B4): negative EDHREC synergy is
  clamped to 0 by default (``ScoreWeights``); non-basic lands scoring below
  ``ScoreWeights.land_score_floor`` are excluded (``x == 0`` — basics are
  strictly better); and the objective embeds a lexicographic CMC tiebreak
  (score coefficients stretched by ``TIEBREAK_SCALE``, each card paying a
  tiny CMC term) so among equal-score optima the cheaper deck wins.
- **Determinism**: 1 worker, fixed seed, stable variable order
  ``(-score, cmc, name)``.

The result mirrors ``GreedyResult`` (mainboard ``DeckEntry`` rows with score
and reason, counts, validator statuses, maybeboard) plus solver metadata
(status, solve time, relaxation stage, objective, penalty breakdown).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

from ortools.sat.python import cp_model

from quotas.config import QuotaBand
from quotas.color_sources import (
    DEFAULT_DECK_SIZE,
    DEFAULT_ON_PLAY,
    DEFAULT_RELIABILITY,
    cards_seen,
    min_sources,
    pool_color_source_targets,
    prob_at_least,
)
from quotas.validator import CategoryStatus, validate_deck
from selector.greedy import (
    BASIC_BY_COLOR,
    COLORLESS_BASIC,
    DECK_SIZE,
    FILL_ORDER,
    LANDS_CATEGORY,
    MAYBEBOARD_SIZE,
    SYNERGY_CATEGORY,
    _WUBRG,
    DeckEntry,
    PoolIndex,
    RecommendationLike,
    ScoreWeights,
    SelectorError,
    _Candidate,
    _curve,
    _karsten_floor,
    _name_variants,
    _sorted_candidates,
)
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

# Wide scale so close-but-distinct float scores keep distinct integer
# coefficients (TFM: a low scale would collapse them into artificial ties).
SCORE_SCALE = 1_000_000
# Lexicographic CMC tiebreak: score coefficients are stretched by
# TIEBREAK_SCALE and each selected card pays round(cmc * CMC_TIEBREAK_UNIT)
# (clamped so the whole deck's CMC term stays below ONE stretched score unit,
# i.e. 1e-6 score). Among equal-score optima the cheaper deck wins; no real
# score difference can ever be overturned.
TIEBREAK_SCALE = 8192
CMC_TIEBREAK_UNIT = 4
_MAX_CMC_TIEBREAK = TIEBREAK_SCALE // DECK_SIZE - 1
DEFAULT_RANDOM_SEED = 0
DEFAULT_TIME_LIMIT_S = 10.0

# Soft-floor weight (TFM tuning decision, kept): a missing category slot costs
# ~0.50 score points — playability over shape, but below a typical quality gap.
FLOOR_PENALTY_PER_CARD = round(0.50 * SCORE_SCALE)
# Global λ for the convex color-source deficit penalty (TFM calibrated value):
# a fully unsupported color costs ≈ λ (the shape saturates near 1 reliability
# unit); realistic 2-4 source shortfalls cost a few hundredths.
COLOR_SOURCE_PENALTY_SCALE = round(0.50 * SCORE_SCALE)

_KARSTEN_FIXPOINT_MAX_ITER = 16


@dataclass(frozen=True)
class _Stage:
    """One relaxation stage: which derived hard constraints stay active.

    The lands lower bound (band min raised to the Karsten floor) is absent on
    purpose: it is active at EVERY stage (never relaxed, per project rule).
    """

    name: str
    hard_category_floors: bool
    hard_ceilings: bool
    composition: bool  # False = base model: size + lands floor only.


_STAGES: tuple[_Stage, ...] = (
    _Stage("none", True, True, True),
    _Stage("soft_category_floors", False, True, True),
    _Stage("drop_ceilings", False, False, True),
    _Stage("base_size_and_lands", False, False, False),
)


@dataclass
class CpSatResult:
    """Explainable CP-SAT build result (mirrors ``GreedyResult`` + solver data)."""

    commander_name: str
    mainboard: list[DeckEntry]
    counts: dict[str, int]
    statuses: dict[str, CategoryStatus]
    maybeboard: list[DeckEntry]
    karsten_floor: int
    lands_target: int  # effective hard lands minimum applied in the final solve
    solver_status: str  # "OPTIMAL" | "FEASIBLE"
    relaxation_stage: str
    solve_time_s: float  # wall time accumulated over every solve attempt
    objective_value: float  # penalised objective, unscaled score units
    raw_score_sum: float  # Σ score of the selected non-basics (no penalties)
    penalties: dict[str, Any] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)

    @property
    def total_cards(self) -> int:
        return sum(entry.count for entry in self.mainboard)


# ── Convex color-deficit penalty (ported from TFM optimizer/color_sources) ──

# Two-pip on-curve spell as the fixed representative requirement (Karsten's
# canonical fixing benchmark) — a declared modeling choice, not a knob.
_REPRESENTATIVE_PIPS = 2
_REPRESENTATIVE_TURN = 2

# Deficit values where the piecewise-linear approximation breaks (denser where
# the convex curve bends most, i.e. at low deficit).
_PENALTY_BREAKPOINTS: tuple[int, ...] = (0, 1, 2, 4, 6, 9, 13, 18, 24, 31, 40)


def _build_penalty_shape() -> tuple[float, ...]:
    """Marginal reliability loss per missing source (non-decreasing, convex).

    ``shape[d-1]`` is the extra reliability lost by the ``d``-th missing
    source, clamped to the running maximum (convex envelope). Values are in
    reliability units [0, 1]; the cumulative penalty saturates near the base
    reliability, never growing like ``λ·deficit``.
    """
    seen = cards_seen(_REPRESENTATIVE_TURN, on_play=DEFAULT_ON_PLAY)
    k = min_sources(_REPRESENTATIVE_PIPS, _REPRESENTATIVE_TURN)
    base = prob_at_least(_REPRESENTATIVE_PIPS, k, seen, DEFAULT_DECK_SIZE)
    cumulative = [
        max(0.0, base - prob_at_least(_REPRESENTATIVE_PIPS, k - d, seen, DEFAULT_DECK_SIZE))
        for d in range(k + 1)
    ]
    marginals: list[float] = []
    running_max = 0.0
    for d in range(1, k + 1):
        running_max = max(running_max, cumulative[d] - cumulative[d - 1])
        marginals.append(running_max)
    return tuple(marginals)


_PENALTY_SHAPE: tuple[float, ...] = _build_penalty_shape()


def _deficit_penalty(deficit: int) -> float:
    """Cumulative convex penalty (reliability units) for ``deficit`` missing sources."""
    if deficit <= 0:
        return 0.0
    if deficit <= len(_PENALTY_SHAPE):
        return sum(_PENALTY_SHAPE[:deficit])
    return sum(_PENALTY_SHAPE) + (deficit - len(_PENALTY_SHAPE)) * _PENALTY_SHAPE[-1]


def _penalty_lines(scale: int) -> tuple[tuple[int, int], ...]:
    """Integer supporting lines ``(slope, intercept)`` of the convex penalty.

    Epigraph form: the solver adds ``pen ≥ slope·deficit + intercept`` per
    line — no per-source booleans, so the LP yields the convex value directly
    (TFM trick for a fast optimality proof).
    """
    points = [(d, _deficit_penalty(d)) for d in _PENALTY_BREAKPOINTS]
    lines: list[tuple[int, int]] = []
    for (d0, p0), (d1, p1) in zip(points, points[1:]):
        slope = (p1 - p0) / (d1 - d0)
        lines.append((round(slope * scale), round((p0 - slope * d0) * scale)))
    (d_pen, p_pen), (d_last, p_last) = points[-2], points[-1]
    sat_slope = (p_last - p_pen) / (d_last - d_pen)
    lines.append((round(sat_slope * scale), round((p_last - sat_slope * d_last) * scale)))
    return tuple(lines)


def _color_penalty_scaled(deficit: int, scale: int) -> int:
    """Evaluate the same epigraph the solver uses (for reporting)."""
    if deficit <= 0 or scale <= 0:
        return 0
    return max(0, max(s * deficit + i for s, i in _penalty_lines(scale)))


# ── Mana-production heuristic (the pool has no ``produced_mana`` field) ─────

_BASIC_TYPE_TO_COLOR: dict[str, str] = {
    "Plains": "W",
    "Island": "U",
    "Swamp": "B",
    "Mountain": "R",
    "Forest": "G",
}
_ADD_COLORED_RE = re.compile(r"\{([WUBRG])\}")
_ANY_COLOR_HINTS = ("any color", "any combination of colors", "any one color")


def _produced_colors(card: Mapping[str, Any], identity: frozenset[str]) -> frozenset[str]:
    """Colors of ``identity`` this card is a mana source of (heuristic).

    Basic land subtypes in the ``type_line`` imply their color (duals,
    shocklands); ``oracle_text`` lines containing "Add" contribute their pure
    colored symbols, and "any color"-style wordings contribute the whole
    identity. Declared approximation: one-shot rituals count as sources (the
    TFM's Scryfall ``produced_mana`` does the same), fetches do not.
    """
    colors: set[str] = set()
    type_line = card.get("type_line") or ""
    for subtype, color in _BASIC_TYPE_TO_COLOR.items():
        if subtype in type_line:
            colors.add(color)
    for line in (card.get("oracle_text") or "").split("\n"):
        if "Add" not in line:
            continue
        lowered = line.lower()
        if any(hint in lowered for hint in _ANY_COLOR_HINTS):
            colors |= set(identity)
        colors.update(_ADD_COLORED_RE.findall(line))
    return frozenset(colors) & identity


# ── Model assembly and solve ─────────────────────────────────────────────────


def _make_solver(random_seed: int, time_limit_s: float) -> cp_model.CpSolver:
    """Deterministic CP-SAT solver: single worker + fixed seed (TFM port)."""
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = random_seed
    solver.parameters.max_time_in_seconds = time_limit_s
    return solver


def _assemble_model(
    ordered: Sequence[_Candidate],
    basic_types: Sequence[tuple[str, str | None]],  # (basic name, color | None)
    bands: Mapping[str, QuotaBand],
    stage: _Stage,
    lands_min: int,
    color_targets: Mapping[str, int],
    producers: Mapping[str, frozenset[str]],  # candidate name -> colors produced
    forced: frozenset[str] = frozenset(),  # always cards (rules.yaml): x == 1
    excluded: frozenset[str] = frozenset(),  # weak non-basic lands: x == 0
) -> tuple[cp_model.CpModel, dict[str, cp_model.IntVar], dict[str, cp_model.IntVar]]:
    """One CP-SAT model for a relaxation stage and a fixed lands lower bound."""
    model = cp_model.CpModel()

    x: dict[str, cp_model.IntVar] = {}
    score_terms = []
    for cand in ordered:
        var = model.new_bool_var(f"x_{cand.name}")
        x[cand.name] = var
        cmc_term = min(round(cand.cmc * CMC_TIEBREAK_UNIT), _MAX_CMC_TIEBREAK)
        coefficient = round(cand.score * SCORE_SCALE) * TIEBREAK_SCALE - cmc_term
        if coefficient:
            score_terms.append(coefficient * var)

    # Forced always cards and the land quality gate are as hard as the
    # banlist: active at EVERY relaxation stage, never dropped.
    for name in sorted(forced):
        model.add(x[name] == 1)
    for name in sorted(excluded):
        model.add(x[name] == 0)

    b: dict[str, cp_model.IntVar] = {}
    for basic_name, _color in basic_types:
        b[basic_name] = model.new_int_var(0, DECK_SIZE, f"b_{basic_name}")

    model.add(sum(x.values()) + sum(b.values()) == DECK_SIZE)

    basics_sum = sum(b.values())
    lands_count = sum(x[c.name] for c in ordered if c.is_land) + basics_sum
    # The lands floor (band min raised to the Karsten floor by the outer
    # fixpoint) is hard at EVERY stage — never relaxed by design.
    model.add(lands_count >= lands_min)

    penalty_terms = []
    if stage.composition:
        lands_band = bands[LANDS_CATEGORY]
        if stage.hard_ceilings:
            # The Karsten floor may legitimately exceed the band max.
            model.add(lands_count <= max(lands_band.max, lands_min))

        for category, band in bands.items():
            if category == LANDS_CATEGORY:
                continue
            members = [x[c.name] for c in ordered if category in c.categories]
            count_expr = sum(members)
            if stage.hard_ceilings:
                model.add(count_expr <= band.max)
            if band.min > 0:
                if stage.hard_category_floors:
                    model.add(count_expr >= band.min)
                else:
                    deficit = model.new_int_var(0, band.min, f"deficit_{category}")
                    model.add(deficit >= band.min - count_expr)
                    # TIEBREAK_SCALE keeps the penalty/score ratio intact.
                    penalty_terms.append(FLOOR_PENALTY_PER_CARD * TIEBREAK_SCALE * deficit)

        # Color fixing (soft, epigraph form): deficit of sources vs the fixed
        # pool demand target per identity color.
        if color_targets and COLOR_SOURCE_PENALTY_SCALE > 0:
            lines = _penalty_lines(COLOR_SOURCE_PENALTY_SCALE)
            for color in _WUBRG:
                target = color_targets.get(color, 0)
                if target <= 0:
                    continue
                source_vars = [
                    x[c.name] for c in ordered if color in producers.get(c.name, frozenset())
                ]
                source_vars += [b[name] for name, bc in basic_types if bc == color]
                deficit = model.new_int_var(0, target, f"colordef_{color}")
                model.add(deficit >= target - sum(source_vars))
                max_pen = _color_penalty_scaled(target, COLOR_SOURCE_PENALTY_SCALE)
                pen = model.new_int_var(0, max(max_pen, 0), f"colorpen_{color}")
                for slope, intercept in lines:
                    model.add(pen >= slope * deficit + intercept)
                # TIEBREAK_SCALE keeps the penalty/score ratio intact.
                penalty_terms.append(TIEBREAK_SCALE * pen)

    objective = sum(score_terms)
    if penalty_terms:
        objective = objective - sum(penalty_terms)
    model.maximize(objective)
    return model, x, b


def build_deck_cpsat(
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
    time_limit_s: float = DEFAULT_TIME_LIMIT_S,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> CpSatResult:
    """Build a 99-card mainboard + maybeboard with CP-SAT. See module doc."""
    commander_card = pool.resolve(commander_name)
    if commander_card is None:
        raise SelectorError(f"commander not found in pool: {commander_name!r}")
    commander_full_name = commander_card["name"]
    commander_identity = frozenset(commander_card.get("color_identity", []))
    lands_band = bands.get(LANDS_CATEGORY)
    if lands_band is None:
        raise SelectorError("bands must include a 'lands' band")

    # ── deck rules context (same rules as the greedy, kept in lockstep) ──
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
            color_identity=commander_identity,
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

    # ── candidate filtering (same rules as the greedy, kept in lockstep) ──
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
        if "Basic" in card.get("type_line", ""):
            # Basics never compete as recommended lands: they only enter via
            # the per-color IntVars (the only allowed duplicates).
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
        )
    if unresolved:
        logger.info(
            "%s: %d recommendations not in pool (skipped)",
            commander_name,
            len(unresolved),
        )

    # ── always cards from rules.yaml (forced with x == 1; as in the greedy) ──
    forced_names: set[str] = set()
    forced_slots: dict[str, str] = {}  # full name -> declared quota_category
    if rules is not None:
        assert rule_ctx is not None  # built above whenever rules is given
        for rule in resolve_always(rules, rule_ctx, banned_names):
            card = pool.resolve(rule.name)
            if card is None:
                raise SelectorError(
                    f"always rule card not found in pool: {rule.name!r}"
                )
            full_name = card["name"]
            if full_name == commander_full_name or "Basic" in card.get("type_line", ""):
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
            if full_name not in candidates:
                categories = tagger(full_name) & set(bands) - {SYNERGY_CATEGORY}
                if not categories:
                    categories = {SYNERGY_CATEGORY}
                candidates[full_name] = _Candidate(
                    name=full_name,
                    # No EDHREC recommendation: only a preferred boost, if any.
                    score=boost_for(boosts, full_name),
                    categories=frozenset(categories),
                    cmc=float(card.get("cmc") or 0.0),
                    mana_cost=card.get("mana_cost") or "",
                )
            if (
                rule.quota_category is not None
                and rule.quota_category not in candidates[full_name].categories
            ):
                # The forced card consumes its declared quota slot: it counts
                # there on top of (or instead of, for pure filler) its tags.
                categories = (
                    set(candidates[full_name].categories) - {SYNERGY_CATEGORY}
                ) | {rule.quota_category}
                candidates[full_name] = replace(
                    candidates[full_name], categories=frozenset(categories)
                )
            if rule.quota_category is not None:
                forced_slots[full_name] = rule.quota_category
            forced_names.add(full_name)

    ordered = _sorted_candidates(candidates.values())
    forced = frozenset(forced_names)
    # Land quality gate (COMPARATIVA_EDHREC_B4): weak non-basic lands are
    # excluded outright — the basics IntVars are always a better filler.
    excluded_lands = frozenset(
        c.name
        for c in ordered
        if c.is_land and c.score < weights.land_score_floor and c.name not in forced
    )

    # ── basics available to the solver (one IntVar per type) ─────────────
    identity_colors = [c for c in _WUBRG if c in commander_identity]
    if identity_colors:
        basic_types = [(BASIC_BY_COLOR[color], color) for color in identity_colors]
    else:
        basic_types = [(COLORLESS_BASIC, None)]
    for basic_name, _color in basic_types:
        if pool.resolve(basic_name) is None:
            raise SelectorError(f"basic land {basic_name!r} not found in pool")

    # ── color fixing data: fixed pool demand + heuristic supply ──────────
    commander_cmc = float(commander_card.get("cmc") or 0.0)
    nonland_candidates = [c for c in ordered if not c.is_land]
    color_targets = pool_color_source_targets(
        ((c.mana_cost, c.cmc) for c in nonland_candidates), commander_cmc
    )
    color_targets = {c: k for c, k in color_targets.items() if c in commander_identity}
    producers: dict[str, frozenset[str]] = {}
    for cand in ordered:
        card = pool.resolve(cand.name)
        produced = _produced_colors(card or {}, commander_identity)
        if produced:
            producers[cand.name] = produced

    # ── Karsten fixpoint over the hard lands lower bound ─────────────────
    lands_min = lands_band.min
    total_solve_time = 0.0
    selected: list[_Candidate] = []
    basic_counts: dict[str, int] = {}
    stage_used: _Stage | None = None
    solver_status = ""
    objective_scaled = 0

    for _ in range(_KARSTEN_FIXPOINT_MAX_ITER):
        solved = False
        for stage in _STAGES:
            model, x, bvars = _assemble_model(
                ordered,
                basic_types,
                bands,
                stage,
                lands_min,
                color_targets,
                producers,
                forced=forced,
                excluded=excluded_lands,
            )
            solver = _make_solver(random_seed, time_limit_s)
            status = solver.solve(model)
            total_solve_time += solver.wall_time
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                selected = [c for c in ordered if solver.value(x[c.name]) == 1]
                basic_counts = {
                    name: solver.value(var)
                    for name, var in bvars.items()
                    if solver.value(var) > 0
                }
                stage_used = stage
                solver_status = solver.status_name(status)
                objective_scaled = round(solver.objective_value)
                solved = True
                break
        if not solved:
            # Even the base model (99 + lands floor, basics unbounded) failed:
            # structurally impossible input, not a relaxation matter.
            raise SelectorError(
                f"CP-SAT infeasible at every relaxation stage for {commander_name!r} "
                f"(lands_min={lands_min})"
            )

        nonland_selected = [c for c in selected if not c.is_land]
        counts_trial: dict[str, int] = {}
        for cand in selected:
            for category in cand.categories:
                counts_trial[category] = counts_trial.get(category, 0) + 1
        floor = _karsten_floor(nonland_selected, counts_trial)
        lands_total = counts_trial.get(LANDS_CATEGORY, 0) + sum(basic_counts.values())
        effective_min = max(lands_band.min, floor)
        if lands_total >= effective_min:
            break
        lands_min = max(lands_min + 1, effective_min)
    else:
        raise SelectorError(
            f"Karsten lands fixpoint did not converge for {commander_name!r} "
            f"(last lands_min {lands_min})"
        )
    assert stage_used is not None  # loop either solved or raised

    # ── assemble result (same shapes as the greedy for comparability) ────
    counts: dict[str, int] = {}
    picked: dict[str, DeckEntry] = {}
    for cand in selected:
        for category in cand.categories:
            counts[category] = counts.get(category, 0) + 1
        slot = (
            LANDS_CATEGORY
            if cand.is_land
            else forced_slots.get(cand.name)
            or next((c for c in FILL_ORDER if c in cand.categories), SYNERGY_CATEGORY)
        )
        picked[cand.name] = DeckEntry(
            name=cand.name,
            categories=tuple(sorted(cand.categories)),
            score=cand.score,
            reason=(
                "always (rules.yaml)"
                if cand.name in forced
                else f"cp-sat, score {cand.score:.2f}"
            ),
            slot=slot,
        )

    basic_entries: list[DeckEntry] = []
    for basic_name, n in sorted(basic_counts.items()):
        basic_entries.append(
            DeckEntry(
                name=basic_name,
                categories=(LANDS_CATEGORY,),
                score=None,
                reason=f"básica x{n} (asignada por el solver)",
                slot=LANDS_CATEGORY,
                count=n,
            )
        )
        counts[LANDS_CATEGORY] = counts.get(LANDS_CATEGORY, 0) + n

    mainboard = list(picked.values()) + basic_entries
    total = sum(entry.count for entry in mainboard)
    if total != DECK_SIZE:
        raise SelectorError(
            f"internal error: built {total} cards instead of {DECK_SIZE} "
            f"for {commander_name!r}"
        )

    nonland_final = [c for c in selected if not c.is_land]
    karsten_floor = _karsten_floor(nonland_final, counts)
    statuses = validate_deck(
        counts,
        bands,
        curve=_curve(nonland_final),
        ramp_plus_draw=counts.get("ramp", 0) + counts.get("card_draw", 0),
    )

    # ── penalty breakdown (what the objective actually paid) ─────────────
    raw_score_sum = sum(c.score for c in selected)
    soft_floors: dict[str, Any] = {}
    if stage_used.composition and not stage_used.hard_category_floors:
        for category, band in bands.items():
            if category == LANDS_CATEGORY or band.min <= 0:
                continue
            deficit = max(0, band.min - counts.get(category, 0))
            if deficit:
                soft_floors[category] = {
                    "deficit": deficit,
                    "penalty_scaled": deficit * FLOOR_PENALTY_PER_CARD,
                }
    color_rows: dict[str, Any] = {}
    if stage_used.composition:
        for color, target in sorted(color_targets.items()):
            sources = sum(
                1 for c in selected if color in producers.get(c.name, frozenset())
            )
            sources += basic_counts.get(BASIC_BY_COLOR[color], 0)
            deficit = max(0, target - sources)
            color_rows[color] = {
                "sources": sources,
                "target": target,
                "deficit": deficit,
                "penalty_scaled": _color_penalty_scaled(
                    deficit, COLOR_SOURCE_PENALTY_SCALE
                ),
            }
    penalties = {
        "soft_floors": soft_floors,
        "color_sources": color_rows,
        "total_scaled": sum(r["penalty_scaled"] for r in soft_floors.values())
        + sum(r["penalty_scaled"] for r in color_rows.values()),
    }

    # ── maybeboard: best-scored candidates left out ───────────────────────
    maybeboard: list[DeckEntry] = []
    for cand in ordered:
        if len(maybeboard) >= MAYBEBOARD_SIZE:
            break
        if cand.name in picked:
            continue
        full = all(
            bands[cat].max <= counts.get(cat, 0)
            for cat in cand.categories
            if cat in bands
        ) and any(cat in bands for cat in cand.categories)
        reason = (
            "fuera: cuota llena en " + "/".join(sorted(cand.categories))
            if full
            else f"fuera por score {cand.score:.2f}"
        )
        maybeboard.append(
            DeckEntry(
                name=cand.name,
                categories=tuple(sorted(cand.categories)),
                score=cand.score,
                reason=reason,
                slot=next(
                    (c for c in (LANDS_CATEGORY, *FILL_ORDER) if c in cand.categories),
                    SYNERGY_CATEGORY,
                ),
            )
        )

    return CpSatResult(
        commander_name=commander_full_name,
        mainboard=mainboard,
        counts=counts,
        statuses=statuses,
        maybeboard=maybeboard,
        karsten_floor=karsten_floor,
        lands_target=lands_min,
        solver_status=solver_status,
        relaxation_stage=stage_used.name,
        solve_time_s=total_solve_time,
        objective_value=objective_scaled / (SCORE_SCALE * TIEBREAK_SCALE),
        raw_score_sum=raw_score_sum,
        penalties=penalties,
        unresolved=unresolved,
    )
