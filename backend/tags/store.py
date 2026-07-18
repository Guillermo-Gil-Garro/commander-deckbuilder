"""Persistent store for production functional tags (Fase 2 decision).

The production tagging engine is LLM-cached-first: batches of cards are
labeled offline against ``experiments/tagging/production/RUBRIC.md`` and
merged here. The store is a JSONL file (``data/tags/llm_tags.jsonl``, one
line per card: oracle_id, name, labels, rubric_version, source) that is
versioned in git — tags are a valuable, reviewable artifact.

Sources: ``"llm"`` (batch executors) and ``"human"`` (the 200-card ground
truth, maximum confidence). Merging is idempotent for identical labels and
fails loudly on conflicting labels for the same card.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

# Same anchoring pattern as rules.resolve: no pipeline imports needed here.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE_PATH = REPO_ROOT / "data" / "tags" / "llm_tags.jsonl"
DEFAULT_POOL_PATH = REPO_ROOT / "data" / "processed" / "cards.jsonl"

RUBRIC_VERSION = "v3"

# Canonical label vocabulary and order (quotas.config.CATEGORIES parity).
# "protection" added by rubric v3 (2026-07-14). (A v4 `stax` label was trialled
# 2026-07-18 and reverted: it forced weak prison pieces the deck did not want.)
CATEGORIES = (
    "lands",
    "ramp",
    "card_draw",
    "removal",
    "board_wipe",
    "wincons",
    "protection",
    "synergy",
)
SOURCES = ("llm", "human")

FACE_SEPARATOR = " // "

_LAND_TYPE_RE = re.compile(r"\bLand\b")
_LAND_FACE_TEXT_RE = re.compile(r"\bthis land\b", re.I)


class TagStoreError(Exception):
    """Invalid store/batch data or a label conflict while merging."""


@dataclass(frozen=True)
class TagEntry:
    """One tagged card. ``labels`` is canonically ordered (CATEGORIES order)."""

    oracle_id: str
    name: str
    labels: tuple[str, ...]
    rubric_version: str
    source: str

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "oracle_id": self.oracle_id,
                "name": self.name,
                "labels": list(self.labels),
                "rubric_version": self.rubric_version,
                "source": self.source,
            },
            ensure_ascii=False,
        )


def _normalize_labels(labels: Any, *, context: str) -> tuple[str, ...]:
    """Validate labels against the vocabulary and return them in canonical order."""
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        raise TagStoreError(f"{context}: 'labels' must be a list of strings, got {labels!r}")
    unknown = set(labels) - set(CATEGORIES)
    if unknown:
        raise TagStoreError(
            f"{context}: unknown labels {sorted(unknown)} (vocabulary: {list(CATEGORIES)})"
        )
    if len(set(labels)) != len(labels):
        raise TagStoreError(f"{context}: duplicated labels in {labels!r}")
    return tuple(c for c in CATEGORIES if c in labels)


def _parse_entry(data: Any, *, context: str) -> TagEntry:
    if not isinstance(data, dict):
        raise TagStoreError(f"{context}: expected a JSON object, got {type(data).__name__}")
    try:
        oracle_id = data["oracle_id"]
        name = data["name"]
        raw_labels = data["labels"]
    except KeyError as exc:
        raise TagStoreError(f"{context}: missing required field {exc}") from exc
    if not isinstance(oracle_id, str) or not oracle_id:
        raise TagStoreError(f"{context}: 'oracle_id' must be a non-empty string")
    if not isinstance(name, str) or not name:
        raise TagStoreError(f"{context}: 'name' must be a non-empty string")
    source = data.get("source", "llm")
    if source not in SOURCES:
        raise TagStoreError(f"{context}: unknown source {source!r} (expected one of {list(SOURCES)})")
    rubric_version = data.get("rubric_version", RUBRIC_VERSION)
    if not isinstance(rubric_version, str) or not rubric_version:
        raise TagStoreError(f"{context}: 'rubric_version' must be a non-empty string")
    return TagEntry(
        oracle_id=oracle_id,
        name=name,
        labels=_normalize_labels(raw_labels, context=f"{context} ({name})"),
        rubric_version=rubric_version,
        source=source,
    )


def _read_entries(path: Path) -> list[TagEntry]:
    entries: list[TagEntry] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TagStoreError(f"invalid JSON at {path}:{line_no}: {exc}") from exc
            entries.append(_parse_entry(data, context=f"{path}:{line_no}"))
    return entries


def load_tags(store_path: Path | str = DEFAULT_STORE_PATH) -> dict[str, TagEntry]:
    """Load the tag store keyed by oracle_id. A missing file is an empty store."""
    store_path = Path(store_path)
    if not store_path.is_file():
        logger.info("Tag store not found at %s: starting empty", store_path)
        return {}
    store: dict[str, TagEntry] = {}
    for entry in _read_entries(store_path):
        previous = store.get(entry.oracle_id)
        if previous is not None:
            raise TagStoreError(
                f"corrupt store {store_path}: oracle_id {entry.oracle_id} appears "
                f"twice ({previous.name!r} / {entry.name!r})"
            )
        store[entry.oracle_id] = entry
    logger.debug("Loaded %d tag entries from %s", len(store), store_path)
    return store


def _write_store(store: Mapping[str, TagEntry], store_path: Path) -> None:
    """Rewrite the store atomically, sorted by (name, oracle_id) for stable diffs."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(store.values(), key=lambda e: (e.name, e.oracle_id))
    tmp_path = store_path.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
        for entry in ordered:
            fh.write(entry.to_json_line() + "\n")
    tmp_path.replace(store_path)


def merge_batch(
    batch_path: Path | str,
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> tuple[int, int]:
    """Merge a labeled batch (JSONL of tag entries) into the store.

    Each batch line needs ``oracle_id``, ``name`` and ``labels`` (validated
    against ``CATEGORIES``); ``source`` defaults to ``"llm"`` and
    ``rubric_version`` to the current ``RUBRIC_VERSION``.

    Idempotent: a card already stored with identical labels is skipped. A card
    already stored (or repeated inside the batch) with *different* labels
    raises ``TagStoreError`` — conflicts must be resolved by a human, never
    silently overwritten. Returns ``(added, skipped_identical)``.
    """
    batch_path = Path(batch_path)
    if not batch_path.is_file():
        raise TagStoreError(f"batch file not found: {batch_path}")
    store_path = Path(store_path)

    store = load_tags(store_path)
    added = 0
    skipped = 0
    for entry in _read_entries(batch_path):
        existing = store.get(entry.oracle_id)
        if existing is None:
            store[entry.oracle_id] = entry
            added += 1
        elif existing.labels == entry.labels:
            skipped += 1
        else:
            raise TagStoreError(
                f"label conflict for {entry.name!r} ({entry.oracle_id}): "
                f"store has {list(existing.labels)} (source={existing.source}), "
                f"batch {batch_path} has {list(entry.labels)} (source={entry.source})"
            )

    if added:
        _write_store(store, store_path)
    logger.info(
        "Merged %s into %s: %d added, %d already present (identical)",
        batch_path, store_path, added, skipped,
    )
    return added, skipped


def add_label(
    oracle_id: str,
    label: str,
    rubric_version: str,
    store_path: Path | str = DEFAULT_STORE_PATH,
) -> TagEntry:
    """Add one label to an EXISTING store entry, keeping its other labels.

    Explicit update path for rubric extensions (e.g. v3 ``protection``): the
    regular ``merge_batch`` rejects label conflicts on purpose, so re-labeling
    an already-stored card goes through here. The entry keeps its name and
    source; its ``rubric_version`` is stamped with the given one (the label
    only exists under that rubric).

    Raises ``TagStoreError`` if the oracle_id is not in the store or the
    label is not in ``CATEGORIES``. Idempotent: if the entry already has the
    label, the store is left untouched and the entry is returned as-is.
    Returns the (possibly updated) entry.
    """
    if label not in CATEGORIES:
        raise TagStoreError(
            f"unknown label {label!r} (vocabulary: {list(CATEGORIES)})"
        )
    if not isinstance(rubric_version, str) or not rubric_version:
        raise TagStoreError("'rubric_version' must be a non-empty string")
    store_path = Path(store_path)
    store = load_tags(store_path)
    entry = store.get(oracle_id)
    if entry is None:
        raise TagStoreError(
            f"oracle_id {oracle_id!r} not in store {store_path}: add_label only "
            f"updates existing entries (use merge_batch for new cards)"
        )
    if label in entry.labels:
        logger.debug("%s already has label %r: no-op", entry.name, label)
        return entry
    updated = TagEntry(
        oracle_id=entry.oracle_id,
        name=entry.name,
        labels=tuple(c for c in CATEGORIES if c in (*entry.labels, label)),
        rubric_version=rubric_version,
        source=entry.source,
    )
    store[oracle_id] = updated
    _write_store(store, store_path)
    logger.info(
        "Added label %r to %s (%s): labels now %s",
        label, updated.name, oracle_id, list(updated.labels),
    )
    return updated


def _iter_pool_cards(pool_path: Path) -> Iterable[dict[str, Any]]:
    if not pool_path.is_file():
        raise TagStoreError(f"card pool not found: {pool_path}")
    with pool_path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise TagStoreError(f"invalid pool entry at {pool_path}:{line_no}: {exc}") from exc


def is_land_card(card: Mapping[str, Any]) -> bool:
    """Land per rubric v2: land in the type_line (any face) or a playable
    MDFC land face. The pool's ``type_line`` is front-face only, so MDFC
    spell//land backs are detected by their "this land" oracle text (same
    heuristic as the audited regex tagger); transform backs never qualify."""
    if _LAND_TYPE_RE.search(card.get("type_line", "")):
        return True
    return card.get("layout") == "modal_dfc" and bool(
        _LAND_FACE_TEXT_RE.search(card.get("oracle_text") or "")
    )


def tagger_from_store(
    store: Mapping[str, TagEntry],
    pool_cards: Iterable[Mapping[str, Any]] | None = None,
    pool_path: Path | str = DEFAULT_POOL_PATH,
) -> Callable[[str], set[str]]:
    """Build the selector-compatible ``name -> set of labels`` callable.

    Store labels win. On top of them there is a *lands fallback layer*: any
    pool card without a store entry whose type_line (or playable MDFC land
    face, rubric v2) is a land gets ``{"lands"}``. Rationale: EDHREC pages
    rarely list basics/staple lands, so most lands will never go through an
    LLM batch, yet the selector must see them as lands. Names are matched by
    full Scryfall name or any single face name; unknown names return an
    empty set (the selector treats that as the synergy bucket).
    """
    if pool_cards is None:
        pool_cards = _iter_pool_cards(Path(pool_path))

    labels_by_name: dict[str, set[str]] = {}
    for entry in store.values():
        names = {entry.name}
        if FACE_SEPARATOR in entry.name:
            names.update(entry.name.split(FACE_SEPARATOR))
        for name in names:
            labels_by_name.setdefault(name, set()).update(entry.labels)

    tagged_ids = set(store)
    fallback_land_names: set[str] = set()
    for card in pool_cards:
        if card.get("oracle_id") in tagged_ids:
            continue
        if is_land_card(card):
            name = card["name"]
            fallback_land_names.add(name)
            if FACE_SEPARATOR in name:
                fallback_land_names.update(name.split(FACE_SEPARATOR))

    logger.debug(
        "Store tagger ready: %d tagged names, %d fallback land names",
        len(labels_by_name), len(fallback_land_names),
    )

    def tag(name: str) -> set[str]:
        labels = labels_by_name.get(name)
        if labels is not None:
            return set(labels)
        if name in fallback_land_names:
            return {"lands"}
        return set()

    return tag
