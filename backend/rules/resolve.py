"""Exact card-name resolution against the processed card pool.

Implements the two-step rule documented in ``banlist.yaml``:

1. exact match on the full Scryfall name (``"A // B"`` for multi-faced cards);
2. only if step 1 has no match, exact match on a single face name.

Matching is always by string equality — never by substring ("The Mind Stone"
vs "Mind Stone"). Zero matches or ambiguity at the deciding step raises
``ResolutionError``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

logger = logging.getLogger(__name__)

# Same anchoring pattern as quotas.config: keep rules free of pipeline imports.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POOL_PATH = REPO_ROOT / "data" / "processed" / "cards.jsonl"

FACE_SEPARATOR = " // "


class ResolutionError(Exception):
    """A card name could not be resolved to exactly one pool card."""


@dataclass(frozen=True)
class ResolvedName:
    """One pool card a name resolved to."""

    oracle_id: str
    canonical_name: str
    is_commander_eligible: bool


class NameIndex:
    """Exact-name lookup over the card pool (full names, then face names)."""

    def __init__(
        self,
        full_names: dict[str, list[ResolvedName]],
        face_names: dict[str, list[ResolvedName]],
    ) -> None:
        self._full_names = full_names
        self._face_names = face_names

    def resolve(self, name: str) -> ResolvedName:
        """Resolve ``name`` to a single pool card, or raise ``ResolutionError``.

        Step 1 matches the full Scryfall name; step 2 (only on zero full-name
        matches) matches a single face name. Ambiguity at either deciding step
        is an error.
        """
        matches = self._full_names.get(name, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ResolutionError(
                f"ambiguous name {name!r}: matches {len(matches)} pool cards "
                f"by full name ({[m.oracle_id for m in matches]})"
            )
        matches = self._face_names.get(name, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ResolutionError(
                f"ambiguous name {name!r}: matches {len(matches)} pool cards "
                f"by face name ({[m.canonical_name for m in matches]})"
            )
        raise ResolutionError(
            f"unresolvable name {name!r}: no pool card matches it exactly "
            f"(full name or face name)"
        )


def name_index_from_cards(cards: Iterable[Mapping[str, Any]]) -> NameIndex:
    """Build a ``NameIndex`` from already-parsed pool cards.

    Same index as ``build_name_index``, for callers that already hold the pool
    in memory (the API startup loads it once via ``load_pool``) — re-reading
    ``cards.jsonl`` just to index it parses 16 MB a second time.

    Raises ``ResolutionError`` for an entry missing the fields the index needs;
    the entry is identified by its 1-based position and its ``name`` (when
    present), since the caller's iterable has no line numbers to report.
    """
    full_names: dict[str, list[ResolvedName]] = {}
    face_names: dict[str, list[ResolvedName]] = {}
    count = 0
    for position, card in enumerate(cards, start=1):
        try:
            entry = ResolvedName(
                oracle_id=card["oracle_id"],
                canonical_name=card["name"],
                is_commander_eligible=card["is_commander_eligible"],
            )
        except (KeyError, TypeError) as exc:
            name = card.get("name") if isinstance(card, Mapping) else None
            raise ResolutionError(
                f"invalid pool entry #{position} ({name!r}): {exc}"
            ) from exc
        count += 1
        full_names.setdefault(entry.canonical_name, []).append(entry)
        if FACE_SEPARATOR in entry.canonical_name:
            for face in entry.canonical_name.split(FACE_SEPARATOR):
                face_names.setdefault(face, []).append(entry)

    logger.debug(
        "Built name index: %d cards, %d full names, %d face names",
        count,
        len(full_names),
        len(face_names),
    )
    return NameIndex(full_names, face_names)


def _iter_pool(pool_path: Path) -> Iterator[Mapping[str, Any]]:
    with pool_path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ResolutionError(
                    f"invalid pool entry at {pool_path}:{line_no}: {exc}"
                ) from exc


def build_name_index(pool_path: Path | str = DEFAULT_POOL_PATH) -> NameIndex:
    """Build a ``NameIndex`` from a ``cards.jsonl`` pool file.

    Raises ``ResolutionError`` for a missing/unreadable pool or a pool entry
    without the fields the index needs.
    """
    pool_path = Path(pool_path)
    if not pool_path.is_file():
        raise ResolutionError(f"card pool not found: {pool_path}")
    index = name_index_from_cards(_iter_pool(pool_path))
    logger.debug("Built name index from %s", pool_path)
    return index
