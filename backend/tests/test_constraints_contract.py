"""Anti-divergence contract: selector.constraints vs the CP-SAT model.

The checker (``constraints.hard_violations``) and the solver
(``cp_sat._assemble_model``) cannot share code — one counts ints, the other
builds LinearExpr over BoolVars. This is the only real guarantee they stay in
lockstep: every deck CP-SAT delivers at the ``none`` stage must be violation-
free for the checker, and a deck delivered at a relaxed stage may only break
the rules that stage relaxes.

Known limit (also in both module docs): this catches the checker being
*stricter* than the model, never laxer. A hard constraint added to the model
and not to the checker keeps this test green.
"""

from __future__ import annotations

from quotas.config import QuotaBand
from selector.constraints import CardFacts, deck_counts, hard_violations
from selector.cp_sat import _STAGES, CpSatResult
from selector.greedy import PoolIndex

from tests.test_cpsat import bands_fixture, build, build_inputs

# Which violation codes each relaxation stage is allowed to produce (i.e. the
# rules the stage itself drops in _assemble_model). The Karsten lands floor and
# the deck size are absent on purpose: no stage ever relaxes them.
ALLOWED_BY_STAGE: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "soft_category_floors": frozenset({"category_floor"}),
    "drop_ceilings": frozenset({"category_floor", "category_ceiling", "lands_ceiling"}),
    "base_size_and_lands": frozenset(
        {"category_floor", "category_ceiling", "lands_ceiling"}
    ),
}


def check(pool: PoolIndex, result: CpSatResult, bands: dict[str, QuotaBand]) -> None:
    """Assert the result only breaks what its relaxation stage allows."""
    counts = deck_counts(_deck_rows(pool, result))
    codes = {v.code for v in hard_violations(counts, bands)}
    allowed = ALLOWED_BY_STAGE[result.relaxation_stage]
    assert codes <= allowed, (
        f"stage {result.relaxation_stage}: unexpected violations "
        f"{sorted(codes - allowed)}"
    )


def _deck_rows(pool: PoolIndex, result: CpSatResult):
    """Rebuild the CardFacts rows of a finished deck from its mainboard.

    Deliberately NOT reusing the solver's internal ``_Candidate`` objects: this
    goes through the public result the API would see, so the contract covers
    the same path the swap endpoint will take.
    """
    rows = []
    for entry in result.mainboard:
        card = pool.resolve(entry.name) or {}
        rows.append(
            (
                CardFacts(
                    name=entry.name,
                    oracle_id=str(card.get("oracle_id") or ""),
                    categories=frozenset(entry.categories),
                    cmc=float(card.get("cmc") or 0.0),
                    mana_cost=card.get("mana_cost") or "",
                    color_identity=frozenset(card.get("color_identity", [])),
                    is_basic="Basic" in (card.get("type_line") or ""),
                ),
                entry.count,
            )
        )
    return rows


def test_stage_none_deck_has_zero_hard_violations() -> None:
    pool, recs = build_inputs()
    result = build(pool, recs)
    assert result.relaxation_stage == "none"
    check(pool, result, bands_fixture())


def test_stage_none_baseline_and_pure_hard_rule_coincide() -> None:
    # On a none-stage deck the non-worsening reading adds nothing: both are ().
    pool, recs = build_inputs()
    result = build(pool, recs)
    counts = deck_counts(_deck_rows(pool, result))
    bands = bands_fixture()
    assert hard_violations(counts, bands) == ()
    assert hard_violations(counts, bands, baseline=counts) == ()


def test_soft_floors_stage_only_breaks_category_floors() -> None:
    pool, recs = build_inputs()
    bands = bands_fixture()
    bands["ramp"] = QuotaBand(min=6, max=8)  # only 4 ramp candidates exist
    result = build(pool, recs, bands=bands)
    assert result.relaxation_stage == "soft_category_floors"
    counts = deck_counts(_deck_rows(pool, result))
    codes = {v.code for v in hard_violations(counts, bands)}
    assert codes == {"category_floor"}
    # And the checker's deficit matches the penalty the solver reported.
    breach = next(
        v
        for v in hard_violations(counts, bands)
        if v.code == "category_floor" and v.category == "ramp"
    )
    assert breach.limit - breach.actual == result.penalties["soft_floors"]["ramp"][
        "deficit"
    ]


def test_drop_ceilings_stage_only_breaks_floors_and_ceilings() -> None:
    pool, recs = build_inputs(n_synergy=120)
    bands = bands_fixture()
    bands["synergy"] = QuotaBand(min=0, max=5)
    bands["lands"] = QuotaBand(min=10, max=12)
    for cat in ("ramp", "card_draw", "removal", "board_wipe", "wincons"):
        bands[cat] = QuotaBand(min=0, max=2)
    result = build(pool, recs, bands=bands)
    assert result.relaxation_stage == "drop_ceilings"
    check(pool, result, bands)


def test_multicategory_land_deck_agrees_with_the_solver() -> None:
    # The Grim Backwoods case end to end: the solver relaxes because a land
    # cannot pay a spell floor, and the checker must see exactly that floor.
    from tests.test_cpsat import TAGS, add_multi_land

    pool, recs = build_inputs()
    recs = [r for r in recs if not r.name.startswith("Draw ")]
    for i in range(4):
        TAGS.pop(f"Draw {i}", None)
    recs = recs + [add_multi_land(pool, categories={"card_draw"})]
    result = build(pool, recs)
    assert result.relaxation_stage == "soft_category_floors"
    counts = deck_counts(_deck_rows(pool, result))
    codes = {
        (v.code, v.category) for v in hard_violations(counts, bands_fixture())
    }
    assert codes == {("category_floor", "card_draw")}


def test_facts_for_check_matches_the_public_mainboard_rows() -> None:
    # The __debug__ assertion inside build_deck_cpsat builds its rows from the
    # internal candidates; they must count the same as the public mainboard.
    pool, recs = build_inputs()
    result = build(pool, recs)
    public = deck_counts(_deck_rows(pool, result))
    assert public.total == result.total_cards
    assert public.by_category == result.counts


def test_every_relaxation_stage_is_covered_by_the_contract() -> None:
    # A new stage in _assemble_model must declare what it is allowed to break.
    assert set(ALLOWED_BY_STAGE) == {s.name for s in _STAGES}
