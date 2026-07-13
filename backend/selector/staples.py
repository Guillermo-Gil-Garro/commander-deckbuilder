"""Staples config: pydantic models and the ``staples.yaml`` loader.

``staples.yaml`` (repo root) is the single source of truth for the group's
staple policy (decision 2026-07-14), consumed by both selectors:

- ``auto_includes``: cards that ALWAYS enter the mainboard when their
  condition applies (``always``; ``multicolor_or_listed_mono`` = every 2+
  color deck plus the listed mono-color commanders). The banlist always wins
  over an auto-include, they must pass the commander's color identity, they
  count toward their quota categories like any card, and filler never
  displaces them.
- ``preferred``: allowed staples per color. They never force their way in:
  they add a flat score ``boost`` when the commander identity contains any
  of ``colors_any`` (an empty list applies to every deck).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Literal, Mapping

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

# Same anchoring pattern as quotas.config: the YAML lives at the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAPLES_PATH = REPO_ROOT / "staples.yaml"

DEFAULT_PREFERRED_BOOST = 0.3
FACE_SEPARATOR = " // "
_WUBRG = ("W", "U", "B", "R", "G")


class StaplesError(Exception):
    """Invalid or unreadable staples configuration."""


class AutoInclude(BaseModel):
    """One forced staple: enters every deck where its condition applies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    condition: Literal["always", "multicolor_or_listed_mono"]
    mono_exceptions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _exceptions_only_for_conditional(self) -> "AutoInclude":
        if self.condition == "always" and self.mono_exceptions:
            raise ValueError(
                f"{self.name!r}: mono_exceptions only applies to "
                f"condition 'multicolor_or_listed_mono'"
            )
        return self


class PreferredStaple(BaseModel):
    """One allowed staple: flat score boost when the deck matches its colors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    colors_any: tuple[str, ...] = ()  # empty = applies to every deck
    boost: float = Field(default=DEFAULT_PREFERRED_BOOST, gt=0)

    @field_validator("colors_any")
    @classmethod
    def _valid_colors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = set(value) - set(_WUBRG)
        if unknown:
            raise ValueError(
                f"unknown colors {sorted(unknown)} (expected one of {list(_WUBRG)})"
            )
        if len(set(value)) != len(value):
            raise ValueError(f"duplicated colors in {list(value)!r}")
        return value


class StaplesConfig(BaseModel):
    """Parsed ``staples.yaml``: forced auto-includes plus preferred staples."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auto_includes: tuple[AutoInclude, ...] = ()
    preferred: tuple[PreferredStaple, ...] = ()

    @model_validator(mode="after")
    def _no_duplicate_names(self) -> "StaplesConfig":
        for label, items in (
            ("auto_includes", self.auto_includes),
            ("preferred", self.preferred),
        ):
            names = [item.name for item in items]
            if len(set(names)) != len(names):
                dupes = sorted({n for n in names if names.count(n) > 1})
                raise ValueError(f"duplicated names in {label}: {dupes}")
        return self


def load_staples(path: Path | str = DEFAULT_STAPLES_PATH) -> StaplesConfig:
    """Load and validate a staples YAML file.

    Raises ``StaplesError`` for a missing file, malformed YAML, or any schema
    violation (unknown condition, unknown color, duplicated names, ...).
    """
    path = Path(path)
    if not path.is_file():
        raise StaplesError(f"staples file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StaplesError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise StaplesError(
            f"staples file must be a mapping at the top level, "
            f"got {type(raw).__name__}: {path}"
        )
    try:
        config = StaplesConfig.model_validate(raw)
    except ValidationError as exc:
        raise StaplesError(f"invalid staples config in {path}: {exc}") from exc
    logger.debug(
        "Loaded staples config from %s: %d auto-includes, %d preferred",
        path,
        len(config.auto_includes),
        len(config.preferred),
    )
    return config


def resolve_auto_includes(
    config: StaplesConfig,
    commander_color_identity: Iterable[str],
    commander_name: str,
    banned_names: Iterable[str],
) -> list[str]:
    """Auto-include card names that apply to this commander (banlist wins).

    ``always`` applies to every deck; ``multicolor_or_listed_mono`` applies to
    2+ color identities and to mono-color commanders listed in the staple's
    ``mono_exceptions`` (colorless never qualifies). Banned staples are
    dropped here — the banlist always beats a staple.
    """
    identity = set(commander_color_identity)
    banned = set(banned_names)
    resolved: list[str] = []
    for staple in config.auto_includes:
        if staple.name in banned:
            logger.info("auto-include %r is banned: banlist wins", staple.name)
            continue
        if staple.condition == "always":
            applies = True
        else:  # multicolor_or_listed_mono
            applies = len(identity) >= 2 or commander_name in staple.mono_exceptions
        if applies:
            resolved.append(staple.name)
    return resolved


def preferred_boosts(
    config: StaplesConfig, color_identity: Iterable[str]
) -> dict[str, float]:
    """``name -> boost`` for the preferred staples matching this identity.

    A staple matches when the identity contains any color of ``colors_any``
    (an empty ``colors_any`` matches every deck). The boost is a flat score
    bonus — it never forces the card in.
    """
    identity = set(color_identity)
    return {
        staple.name: staple.boost
        for staple in config.preferred
        if not staple.colors_any or set(staple.colors_any) & identity
    }


def boost_for(boosts: Mapping[str, float], full_name: str) -> float:
    """Boost for a card by full Scryfall name, with face-name fallback."""
    boost = boosts.get(full_name)
    if boost is not None:
        return boost
    if FACE_SEPARATOR in full_name:
        for face in full_name.split(FACE_SEPARATOR):
            if face in boosts:
                return boosts[face]
    return 0.0
