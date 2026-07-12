"""Layered quota-band resolution: defaults -> archetype -> overrides -> dials.

Produces the effective ``[min, max]`` band per category for a deck. The
Karsten land floor is deliberately NOT applied here — it depends on the
current deck (curve, ramp+draw), so it lives in ``quotas.validator``.
"""

from __future__ import annotations

from typing import Mapping

from quotas.config import (
    CATEGORIES,
    CEILING_ONLY_CATEGORIES,
    QuotaBand,
    QuotasConfig,
    QuotasError,
)

DIAL_LOW = "low"
DIAL_CENTER = "center"
DIAL_HIGH = "high"


def _shifted(band: QuotaBand, delta: int, *, ceiling_only: bool) -> QuotaBand:
    """Shift a band by ``delta`` (whole band, or ceiling only), clamped to >= 0."""
    if ceiling_only:
        return QuotaBand(min=0, max=max(0, band.max + delta))
    return QuotaBand(min=max(0, band.min + delta), max=max(0, band.max + delta))


def resolve_bands(
    config: QuotasConfig,
    commander_name: str | None = None,
    dials: Mapping[str, str | None] | None = None,
) -> dict[str, QuotaBand]:
    """Effective quota bands for a commander plus user dial positions.

    Layers, in precedence order:

    1. default archetype (``defaults.archetype``);
    2. the commander's archetype, if the commander is listed in ``commanders``;
    3. the commander's per-category ``overrides``;
    4. user dials: ``{category: "low" | "center" | "high" | None}``. ``low`` /
       ``high`` shift the band by the category's dial delta (``synergy`` only
       moves the ceiling; its min stays 0); ``None`` / ``"center"`` leave it
       untouched. A dial on a category with no ``DialSpec`` in the config, or
       an unknown position, raises ``QuotasError``.
    """
    commander = (
        config.commanders.get(commander_name) if commander_name is not None else None
    )
    archetype_name = config.defaults.archetype
    if commander is not None and commander.archetype is not None:
        archetype_name = commander.archetype
    # Guaranteed to exist: QuotasConfig cross-validates every archetype reference.
    archetype = config.archetypes[archetype_name]

    bands: dict[str, QuotaBand] = {
        category: archetype.band(category) for category in CATEGORIES
    }
    if commander is not None:
        bands.update(commander.overrides)

    for category, position in (dials or {}).items():
        if position is None or position == DIAL_CENTER:
            continue
        if position not in (DIAL_LOW, DIAL_HIGH):
            raise QuotasError(
                f"invalid dial position {position!r} for {category!r} "
                f"(expected {DIAL_LOW!r}, {DIAL_CENTER!r}, {DIAL_HIGH!r} or None)"
            )
        spec = config.dials.get(category)
        if spec is None:
            raise QuotasError(f"no dial defined for category {category!r}")
        delta = spec.delta if position == DIAL_HIGH else -spec.delta
        bands[category] = _shifted(
            bands[category], delta, ceiling_only=category in CEILING_ONLY_CATEGORIES
        )
    return bands
