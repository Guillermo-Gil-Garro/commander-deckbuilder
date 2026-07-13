"""Featured commanders: loader and validation for ``featured_commanders.yaml``.

Every entry must resolve against the pool (two-step exact rule), be unique,
be commander-eligible, and not be ``banned_as_commander`` in the group
banlist. Any violation raises ``FeaturedError``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from rules.banlist import ResolvedBanlist
from rules.resolve import REPO_ROOT, NameIndex, ResolutionError

logger = logging.getLogger(__name__)

DEFAULT_FEATURED_PATH = REPO_ROOT / "featured_commanders.yaml"


class FeaturedError(Exception):
    """Invalid, unreadable or unresolvable featured-commanders list."""


class _FeaturedFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    featured: list[str]


class FeaturedCommander(BaseModel):
    """One featured commander, resolved against the pool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    oracle_id: str


def load_featured(
    path: Path | str = DEFAULT_FEATURED_PATH,
    *,
    resolved_banlist: ResolvedBanlist,
    name_index: NameIndex,
) -> list[FeaturedCommander]:
    """Load, resolve and validate the featured commanders list.

    Returns the commanders in file order with their canonical pool name.
    Raises ``FeaturedError`` for a missing/malformed file, an unresolvable or
    duplicated name, a card that is not commander-eligible, or a commander in
    the banlist's ``banned_as_commander`` set.
    """
    path = Path(path)
    if not path.is_file():
        raise FeaturedError(f"featured commanders file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise FeaturedError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise FeaturedError(
            f"featured commanders file must be a mapping at the top level, "
            f"got {type(raw).__name__}: {path}"
        )
    try:
        parsed = _FeaturedFile.model_validate(raw)
    except ValidationError as exc:
        raise FeaturedError(f"invalid featured commanders file {path}: {exc}") from exc

    featured: list[FeaturedCommander] = []
    seen: dict[str, str] = {}
    for name in parsed.featured:
        try:
            resolved = name_index.resolve(name)
        except ResolutionError as exc:
            raise FeaturedError(f"featured commander {name!r}: {exc}") from exc
        if resolved.oracle_id in seen:
            raise FeaturedError(
                f"duplicate featured commander: {name!r} resolves to the same "
                f"card as {seen[resolved.oracle_id]!r}"
            )
        seen[resolved.oracle_id] = name
        if not resolved.is_commander_eligible:
            raise FeaturedError(
                f"featured commander {name!r} ({resolved.canonical_name}) "
                f"is not commander-eligible in the pool"
            )
        if resolved.oracle_id in resolved_banlist.banned_as_commander:
            raise FeaturedError(
                f"featured commander {name!r} ({resolved.canonical_name}) "
                f"is banned_as_commander in the group banlist"
            )
        featured.append(
            FeaturedCommander(
                name=resolved.canonical_name, oracle_id=resolved.oracle_id
            )
        )

    logger.debug("Loaded %d featured commanders from %s", len(featured), path)
    return featured
