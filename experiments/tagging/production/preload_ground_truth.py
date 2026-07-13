"""Preload the 200-card human ground truth into the production tag store.

Reads ``experiments/tagging/test_set_filled.csv`` (column ``final_labels``,
Guille's hand labels), resolves each name against the pool, applies the
rubric v2 unified MDFC criterion (playable spell//land faces ARE ``lands``)
and merges the entries into ``data/tags/llm_tags.jsonl`` with
``source="human"`` — maximum confidence, never re-labeled by LLM batches.

Idempotent: re-running against an already-preloaded store is a no-op.
"""

from __future__ import annotations

import csv
import json
import logging
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from rules.resolve import build_name_index  # noqa: E402
from tags.store import (  # noqa: E402
    CATEGORIES,
    DEFAULT_POOL_PATH,
    DEFAULT_STORE_PATH,
    RUBRIC_VERSION,
    TagStoreError,
    is_land_card,
    merge_batch,
)

logger = logging.getLogger("preload_ground_truth")

CSV_PATH = REPO_ROOT / "experiments" / "tagging" / "test_set_filled.csv"


def parse_final_labels(raw: str, *, name: str) -> list[str]:
    """CSV ``final_labels`` ("a|b" or "none") -> canonical label list."""
    raw = raw.strip()
    if raw == "none":
        return []
    labels = raw.split("|")
    unknown = set(labels) - set(CATEGORIES)
    if unknown:
        raise TagStoreError(f"{name}: unknown labels in ground truth: {sorted(unknown)}")
    return [c for c in CATEGORIES if c in labels]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cards_by_id: dict[str, dict] = {}
    with DEFAULT_POOL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            card = json.loads(line)
            cards_by_id[card["oracle_id"]] = card

    name_index = build_name_index()
    entries: list[dict] = []
    mdfc_fixed: list[str] = []
    with CSV_PATH.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            resolved = name_index.resolve(row["name"])
            labels = parse_final_labels(row["final_labels"], name=row["name"])
            # Rubric v2 unified criterion: playable MDFC land faces count as
            # lands; the ~5 ground-truth disagreements resolve in favor of v2.
            if is_land_card(cards_by_id[resolved.oracle_id]) and "lands" not in labels:
                labels = [c for c in CATEGORIES if c == "lands" or c in labels]
                mdfc_fixed.append(resolved.canonical_name)
            entries.append(
                {
                    "oracle_id": resolved.oracle_id,
                    "name": resolved.canonical_name,
                    "labels": labels,
                    "rubric_version": RUBRIC_VERSION,
                    "source": "human",
                }
            )

    logger.info("Ground truth rows: %d", len(entries))
    logger.info("MDFC lands criterion applied to %d cards: %s", len(mdfc_fixed), mdfc_fixed)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8", newline="\n"
    ) as tmp:
        for entry in entries:
            tmp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        tmp_path = Path(tmp.name)
    try:
        added, skipped = merge_batch(tmp_path, DEFAULT_STORE_PATH)
    finally:
        tmp_path.unlink()
    logger.info("Store %s: %d added, %d already present", DEFAULT_STORE_PATH, added, skipped)


if __name__ == "__main__":
    main()
