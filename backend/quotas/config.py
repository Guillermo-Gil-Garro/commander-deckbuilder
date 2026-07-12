"""Quota configuration: pydantic models and the ``quotas.yaml`` loader.

``quotas.yaml`` (repo root) is the single source of truth for archetype
quota bands, user dials (delta + UI labels) and per-commander overrides.
See ``docs/CUOTAS_PROPUESTA.md`` for the approved design.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)

# Same anchoring pattern as pipeline.scryfall / app.main, replicated so the
# quotas package stays free of pipeline imports.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUOTAS_PATH = REPO_ROOT / "quotas.yaml"

CATEGORIES: tuple[str, ...] = (
    "lands",
    "ramp",
    "card_draw",
    "removal",
    "board_wipe",
    "wincons",
    "synergy",
)
# Ceiling-only categories: min is fixed at 0 and the YAML uses a bare integer.
CEILING_ONLY_CATEGORIES: tuple[str, ...] = ("synergy",)


class QuotasError(Exception):
    """Invalid or unreadable quota configuration."""


class QuotaBand(BaseModel):
    """Inclusive ``[min, max]`` quota band for one category."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min: int = Field(ge=0)
    max: int = Field(ge=0)

    @model_validator(mode="after")
    def _min_not_above_max(self) -> "QuotaBand":
        if self.min > self.max:
            raise ValueError(f"inverted band: min {self.min} > max {self.max}")
        return self


def _band_payload(category: str, value: Any) -> Any:
    """Normalize a YAML band value to a ``QuotaBand`` payload.

    Regular categories take a two-item ``[min, max]`` sequence. Ceiling-only
    categories (``synergy``) take a bare integer ceiling (min implicitly 0),
    or an explicit band whose min MUST be 0.
    """
    if isinstance(value, QuotaBand):
        value = {"min": value.min, "max": value.max}
    if category in CEILING_ONLY_CATEGORIES:
        if isinstance(value, bool):
            raise ValueError(f"{category}: expected an integer ceiling, got a bool")
        if isinstance(value, int):
            return {"min": 0, "max": value}
        if isinstance(value, (list, tuple)) and len(value) == 2 and value[0] != 0:
            raise ValueError(
                f"{category} is ceiling-only: min must be 0, got {value[0]}"
            )
        if isinstance(value, dict) and value.get("min", 0) != 0:
            raise ValueError(
                f"{category} is ceiling-only: min must be 0, got {value.get('min')}"
            )
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ValueError(
                f"{category}: a band must be a [min, max] pair, got {value!r}"
            )
        return {"min": value[0], "max": value[1]}
    if isinstance(value, dict):
        return value
    raise ValueError(f"{category}: cannot interpret {value!r} as a quota band")


class ArchetypeQuotas(BaseModel):
    """Full quota block for one archetype (all categories required)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lands: QuotaBand
    ramp: QuotaBand
    card_draw: QuotaBand
    removal: QuotaBand
    board_wipe: QuotaBand
    wincons: QuotaBand
    synergy: QuotaBand

    @model_validator(mode="before")
    @classmethod
    def _coerce_bands(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {
            key: _band_payload(key, value) if key in CATEGORIES else value
            for key, value in data.items()
        }

    def band(self, category: str) -> QuotaBand:
        """The band for a category name (must be one of ``CATEGORIES``)."""
        if category not in CATEGORIES:
            raise QuotasError(f"unknown quota category {category!r}")
        return getattr(self, category)


class DialSpec(BaseModel):
    """One UI dial: band shift delta plus the low/high meme labels."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    delta: int = Field(gt=0)
    low: str = Field(min_length=1)
    high: str = Field(min_length=1)


class CommanderQuotas(BaseModel):
    """Per-commander customization: archetype and/or single-category overrides."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    archetype: str | None = None
    overrides: dict[str, QuotaBand] = Field(default_factory=dict)

    @field_validator("overrides", mode="before")
    @classmethod
    def _coerce_overrides(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        coerced: dict[str, Any] = {}
        for category, band in value.items():
            if category not in CATEGORIES:
                raise ValueError(
                    f"override for unknown category {category!r} "
                    f"(expected one of {CATEGORIES})"
                )
            coerced[category] = _band_payload(category, band)
        return coerced


class QuotasDefaults(BaseModel):
    """Global fallbacks (currently just the default archetype)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    archetype: str


class QuotasConfig(BaseModel):
    """Parsed ``quotas.yaml``: defaults, archetypes, dials and commanders."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    defaults: QuotasDefaults
    archetypes: dict[str, ArchetypeQuotas]
    dials: dict[str, DialSpec] = Field(default_factory=dict)
    commanders: dict[str, CommanderQuotas] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _cross_references(self) -> "QuotasConfig":
        if self.defaults.archetype not in self.archetypes:
            raise ValueError(
                f"defaults.archetype {self.defaults.archetype!r} is not a defined "
                f"archetype (have: {sorted(self.archetypes)})"
            )
        for category in self.dials:
            if category not in CATEGORIES:
                raise ValueError(
                    f"dial for unknown category {category!r} "
                    f"(expected one of {CATEGORIES})"
                )
        for name, commander in self.commanders.items():
            if (
                commander.archetype is not None
                and commander.archetype not in self.archetypes
            ):
                raise ValueError(
                    f"commander {name!r} references unknown archetype "
                    f"{commander.archetype!r} (have: {sorted(self.archetypes)})"
                )
        return self


def load_quotas(path: Path | str = DEFAULT_QUOTAS_PATH) -> QuotasConfig:
    """Load and validate a quotas YAML file.

    Raises ``QuotasError`` for a missing file, malformed YAML, or any schema /
    cross-reference violation (inverted band, unknown archetype, unknown
    category, ...).
    """
    path = Path(path)
    if not path.is_file():
        raise QuotasError(f"quotas file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise QuotasError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise QuotasError(
            f"quotas file must be a mapping at the top level, "
            f"got {type(raw).__name__}: {path}"
        )
    try:
        config = QuotasConfig.model_validate(raw)
    except ValidationError as exc:
        raise QuotasError(f"invalid quota config in {path}: {exc}") from exc
    logger.debug(
        "Loaded quotas config from %s: %d archetypes, %d dials, %d commanders",
        path,
        len(config.archetypes),
        len(config.dials),
        len(config.commanders),
    )
    return config
