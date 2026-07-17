"""CP-SAT deck selector (Fase 3, second candidate) — TFM port, simplified.

Ported from the author's TFM ``optimizer/cp_sat_builder.py`` and adapted to
this project's data structures (same inputs as ``selector.greedy`` for direct
comparability). One BoolVar per non-basic candidate, one IntVar per basic
land type, ``Σ == 99``, objective ``Maximize(Σ score·x − penalties)`` in
integer score space (``SCORE_SCALE``).

Constraints and penalties:

- **Bands** from ``quotas.resolver.resolve_bands``. At the strictest stage all
  category minimums and maximums are hard. **Minimum semantics (AUDITORIA_SELECTORES
  §5.D.1): the minimum of a non-``lands`` category is satisfied by NON-LAND cards
  only.** A multi-category land (Boseiju → ``removal``, Grim Backwoods →
  ``card_draw``, Westvale Abbey → ``wincons``) stays eligible, keeps counting
  toward that category's MAXIMUM (so it cannot blow the ceiling through the back
  door) and toward the informative counts reported to the validator — but it can
  never *fulfil* a minimum. Rationale: the audit found CP-SAT paying quotas "on
  paper" with marginal utility lands (Grim Backwoods 0.19 as card_draw, Westvale
  Abbey 0.13 as a wincon) in order to cut real engines. The ``lands`` minimum is
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
- **Explainability** (AUDITORIA_SELECTORES §5.D.3): the solver returns a set of
  cards, never a narrative, so per-card reasons are reconstructed post-hoc from
  the optimal solution (``_quota_coverage`` / ``_reason_for``) in the greedy's
  vocabulary: ``"<cat> (cuota), score X"`` when the card is attributed to a
  category minimum, ``"relleno por score X"`` when it entered on score alone,
  ``"tierra recomendada, score X"`` for non-basic lands (multi-category ones
  say which categories they count in and that they do NOT cover their minimum),
  ``"always (rules.yaml)"`` when forced by a rule. Basics keep
  ``"asignada por el solver"``: unlike the greedy they are not distributed
  proportionally to pips — the solver places them, mostly driven by the color
  penalty — and saying otherwise would be false.

``selector.constraints`` re-implements the ``none`` stage of the hard rules as
plain counting (the <100 ms swap path cannot re-solve). The two are kept in
lockstep by ``tests/test_constraints_contract.py`` plus the ``__debug__``
assertion at the end of ``build_deck_cpsat``. **Known limit**: both only catch
the checker being *stricter* than this model. A hard constraint added to
``_assemble_model`` and not to ``constraints.hard_violations`` leaves the
checker laxer and goes undetected — add it in both places.

The result mirrors ``GreedyResult`` (mainboard ``DeckEntry`` rows with score
and reason, counts, validator statuses, maybeboard, cold-start ``new_cards``
section — see ``selector.greedy`` module doc for its relation to the
maybeboard) plus solver metadata (status, solve time, relaxation stage,
objective, penalty breakdown).
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
    _is_new_rec,
    _name_variants,
    _new_cards_section,
    _sorted_candidates,
    curve as _curve,
    karsten_floor as _karsten_floor,
)
from selector.constraints import CardFacts, deck_counts, hard_violations
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
# Global λ for the convex color-source deficit penalty: score points paid per
# unit of lost reliability. Recalibrated 2026-07-15 from the TFM's inherited
# 0.50 (AUDITORIA_SELECTORES §5.D.2 measured that CP-SAT ended with FEWER color
# sources than the greedy despite being the only one with a fixing penalty).
# Diagnosis: at λ=0.50 one missing source costs 0.005–0.03 score, one to two
# orders of magnitude below the 0.1–0.9 score gaps it trades against — the
# penalty could never bite. At λ=4.0 a marginal source costs ≈0.04 score near
# zero deficit and ≈0.10 in the double-digit deficit range where these decks
# actually sit: the solver buys a source whenever it costs less than ~0.1
# score, but a genuine quality gap still wins. Empirically (sweep over the 5
# test commanders vs the greedy baseline) this is the widest setting that keeps
# every color at or above the greedy's source count while costing ≤1.3% of raw
# score (worst case Omnath); λ=5.0 already breaks the 2% score budget (Omnath
# −2.4%) and λ≥10 craters it (−5.6%) by overriding real card quality.
COLOR_SOURCE_PENALTY_SCALE = round(4.0 * SCORE_SCALE)

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
    # Cold-start section, independent from the maybeboard (see greedy module doc).
    new_cards: list[DeckEntry] = field(default_factory=list)

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


# ── Post-hoc explanations (reasons are the formatter's job, not the solver's) ─


def _quota_order(bands: Mapping[str, QuotaBand]) -> tuple[str, ...]:
    """Categories with a minimum to attribute, in greedy's scarcity order.

    ``FILL_ORDER`` first (so a multi-category card is explained by the same
    category the greedy would have picked it for, and by the same one used for
    its ``slot``), then any other quota category the greedy never fills —
    ``protection`` today — so that those cards get explained too instead of
    being passed off as score filler.
    """
    rest = sorted(
        cat
        for cat, band in bands.items()
        if band.min > 0
        and cat not in FILL_ORDER
        and cat not in (LANDS_CATEGORY, SYNERGY_CATEGORY)
    )
    return tuple(cat for cat in FILL_ORDER if cat in bands) + tuple(rest)


def _quota_coverage(
    selected: Sequence[_Candidate], bands: Mapping[str, QuotaBand]
) -> dict[str, list[str]]:
    """Which category minimums each selected card is covering (card -> categories).

    The solver returns a set of cards, not a narrative: it never "picks a card
    for a quota". This reconstructs the attribution the greedy would make from
    the optimal solution — for each category, its ``min`` slots go to the
    best-scored NON-LAND members present (``selected`` is already ordered by
    ``(-score, cmc, name)``). Lands are excluded on purpose: they cannot fulfil
    a spell minimum in the model either, so claiming they cover one would be a
    lie (see module doc). Cards not attributed to any minimum entered on score
    alone, and are explained as such.
    """
    coverage: dict[str, list[str]] = {}
    for category in _quota_order(bands):
        band = bands[category]
        members = [
            c for c in selected if not c.is_land and category in c.categories
        ]
        for cand in members[: band.min]:
            coverage.setdefault(cand.name, []).append(category)
    return coverage


def _reason_for(
    cand: _Candidate,
    *,
    forced: frozenset[str],
    coverage: Mapping[str, Sequence[str]],
    bands: Mapping[str, QuotaBand],
) -> str:
    """Greedy-style reason for one selected card, derived from the solution."""
    if cand.name in forced:
        return "always (rules.yaml)"
    if cand.is_land:
        # Multi-category lands: state plainly that they count toward those
        # categories (they do, for the ceilings) but never satisfy their min.
        extra = sorted(
            cat
            for cat in cand.categories
            if cat != LANDS_CATEGORY and cat in bands and bands[cat].min > 0
        )
        if extra:
            return (
                f"tierra recomendada, score {cand.score:.2f} "
                f"(cuenta en {'/'.join(extra)}, pero no cubre su mínimo)"
            )
        return f"tierra recomendada, score {cand.score:.2f}"
    covered = coverage.get(cand.name)
    if covered:
        return f"{covered[0]} (cuota), score {cand.score:.2f}"
    return f"relleno por score {cand.score:.2f}"


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
            # Ceilings count EVERY member (multi-category lands included): a
            # land tagged `removal` does consume removal's ceiling.
            ceiling_expr = cp_model.LinearExpr.sum(
                [x[c.name] for c in ordered if category in c.categories]
            )
            # Floors count NON-LAND members only: a utility land may not fulfil
            # a spell quota (see module doc, AUDITORIA_SELECTORES §5.D.1).
            floor_expr = cp_model.LinearExpr.sum(
                [
                    x[c.name]
                    for c in ordered
                    if category in c.categories and not c.is_land
                ]
            )
            if stage.hard_ceilings:
                model.add(ceiling_expr <= band.max)
            if band.min > 0:
                if stage.hard_category_floors:
                    model.add(floor_expr >= band.min)
                else:
                    deficit = model.new_int_var(0, band.min, f"deficit_{category}")
                    model.add(deficit >= band.min - floor_expr)
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


def _facts_for_check(
    pool: PoolIndex,
    selected: Sequence[_Candidate],
    basic_counts: Mapping[str, int],
) -> list[tuple[CardFacts, int]]:
    """The built deck as ``constraints.CardFacts`` rows (``__debug__`` assertion only)."""
    rows: list[tuple[CardFacts, int]] = []
    for cand in selected:
        card = pool.resolve(cand.name) or {}
        rows.append(
            (
                CardFacts(
                    name=cand.name,
                    oracle_id=str(card.get("oracle_id") or ""),
                    categories=cand.categories,
                    cmc=cand.cmc,
                    mana_cost=cand.mana_cost,
                    color_identity=frozenset(card.get("color_identity", [])),
                ),
                1,
            )
        )
    for basic_name, n in sorted(basic_counts.items()):
        card = pool.resolve(basic_name) or {}
        rows.append(
            (
                CardFacts(
                    name=basic_name,
                    oracle_id=str(card.get("oracle_id") or ""),
                    categories=frozenset({LANDS_CATEGORY}),
                    cmc=float(card.get("cmc") or 0.0),
                    mana_cost=card.get("mana_cost") or "",
                    color_identity=frozenset(card.get("color_identity", [])),
                    is_basic=True,
                ),
                n,
            )
        )
    return rows


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
            is_new=_is_new_rec(rec),
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

    # ── preferred cards injected into the candidate pool (rules.yaml) ─────
    # EDHREC's popularity score is depressed by price, so premium fixing (ABUR
    # duals, fetchlands) and pricey staples are often ABSENT from the
    # recommendations. A boost that only reweighted existing candidates would
    # never see them, so every preferred card whose color predicates match the
    # identity is injected here with its boost as base score — the same pool
    # entry an ``always`` gets, minus the ``x == 1`` forcing (prefer never
    # forces: ban > never > always > prefer). Cards already candidates (an
    # EDHREC rec or an always) keep their EDHREC-derived score, which already
    # includes this boost via ``boost_for``.
    for pref_name, boost in boosts.items():
        card = pool.resolve(pref_name)
        if card is None:
            # Names are cross-checked against the pool at load
            # (validate_rules_names); a miss here means a minimal test pool.
            logger.debug("preferred card %r not in pool, not injected", pref_name)
            continue
        full_name = card["name"]
        if full_name == commander_full_name or full_name in candidates:
            continue
        variants = _name_variants(full_name) | {pref_name}
        if (
            variants & set(banned_names)
            or variants & set(watchlist_names)
            or variants & never_excluded
        ):
            continue
        if not set(card.get("color_identity", [])) <= commander_identity:
            continue
        if "Basic" in card.get("type_line", ""):
            continue
        categories = tagger(full_name) & set(bands) - {SYNERGY_CATEGORY}
        if not categories:
            categories = {SYNERGY_CATEGORY}
        candidates[full_name] = _Candidate(
            name=full_name,
            score=boost,
            categories=frozenset(categories),
            cmc=float(card.get("cmc") or 0.0),
            mana_cost=card.get("mana_cost") or "",
        )

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
    # Same split the model enforces: floors are met by non-lands only, so the
    # deficits reported below must be measured on the same counter.
    nonland_counts: dict[str, int] = {}
    coverage = _quota_coverage(selected, bands)
    picked: dict[str, DeckEntry] = {}
    for cand in selected:
        for category in cand.categories:
            counts[category] = counts.get(category, 0) + 1
            if not cand.is_land:
                nonland_counts[category] = nonland_counts.get(category, 0) + 1
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
            reason=_reason_for(
                cand, forced=forced, coverage=coverage, bands=bands
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
            deficit = max(0, band.min - nonland_counts.get(category, 0))
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

    if __debug__ and stage_used.name == "none":
        # Closes the loop with the swap checker: at the strictest stage the
        # solution satisfies every hard rule by construction, so any violation
        # reported here means constraints.py and _assemble_model have diverged.
        breaches = hard_violations(
            deck_counts(_facts_for_check(pool, selected, basic_counts)),
            bands,
            # The floor the fixpoint settled on, not the one this deck's curve
            # implies: they differ when the search raised the floor above what
            # the winning deck ends up needing, and recomputing it here would
            # read a legal deck as over its ceiling.
            lands_min=lands_min,
        )
        if breaches:
            raise SelectorError(
                f"internal error: CP-SAT solution for {commander_name!r} breaks "
                f"hard rules at stage 'none' (selector/constraints.py has "
                f"diverged from _assemble_model): {breaches}"
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
        new_cards=_new_cards_section(ordered, picked),
    )
