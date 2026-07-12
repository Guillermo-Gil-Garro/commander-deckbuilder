"""Build the Commander-legal card pool.

Run from ``backend/`` as ``python -m pipeline.build``. Downloads (or reuses)
the Scryfall oracle_cards bulk file, filters to Commander-legal cards with
playable layouts, and writes one Card JSON per line to
``data/processed/cards.jsonl``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline.model import Card
from pipeline.scryfall import REPO_ROOT, ensure_oracle_cards

logger = logging.getLogger(__name__)

OUTPUT_FILE = REPO_ROOT / "data" / "processed" / "cards.jsonl"

EXCLUDED_LAYOUTS = frozenset(
    {"token", "emblem", "art_series", "scheme", "vanguard", "planar"}
)


def is_commander_legal(data: dict) -> bool:
    return data.get("legalities", {}).get("commander") == "legal"


def has_playable_layout(data: dict) -> bool:
    return data.get("layout") not in EXCLUDED_LAYOUTS


def build(bulk_path: Path, output_path: Path) -> tuple[int, int, int]:
    """Filter and parse the bulk file. Returns (total, legal, written)."""
    logger.info("Loading bulk file %s", bulk_path)
    with bulk_path.open(encoding="utf-8") as fh:
        cards = json.load(fh)

    total = len(cards)
    legal = 0
    written = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as out:
        for data in cards:
            if not is_commander_legal(data):
                continue
            legal += 1
            if not has_playable_layout(data):
                continue
            card = Card.from_scryfall(data)
            out.write(card.model_dump_json() + "\n")
            written += 1

    logger.info(
        "Cards in bulk: %d | commander-legal: %d | written: %d -> %s",
        total,
        legal,
        written,
        output_path,
    )
    return total, legal, written


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    bulk_path = ensure_oracle_cards()
    build(bulk_path, OUTPUT_FILE)


if __name__ == "__main__":
    main()
