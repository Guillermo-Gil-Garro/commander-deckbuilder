"""Build the initial LLM tagging surface and split it into labeling batches.

Surface = union of every card recommended on the 55 cached EDHREC pages of
``featured_commanders.yaml`` (global pages by default; pass
``--variant optimized`` to use the Bracket 4 subpages instead), resolved
against the processed pool
(two-step exact rule); unresolvable names are reported and discarded.
Cards already present in the tag store (human ground truth or previously
merged LLM batches) are subtracted, so re-running after merges only emits
what is still untagged.

Output: ``batches/batch_NNN.jsonl``, ~250 cards per batch, sorted by card
name (deterministic). Each line carries everything a labeling session needs
without pool access: oracle_id, name, mana_cost, type_line, oracle_text.
Batches are regenerable and gitignored.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import yaml  # noqa: E402

from pipeline.edhrec import fetch_commander  # noqa: E402
from rules.resolve import ResolutionError, build_name_index  # noqa: E402
from tags.store import DEFAULT_POOL_PATH, load_tags  # noqa: E402

logger = logging.getLogger("make_batches")

FEATURED_PATH = REPO_ROOT / "featured_commanders.yaml"
BATCHES_DIR = Path(__file__).resolve().parent / "batches"
BATCH_SIZE = 250

BATCH_FIELDS = ("oracle_id", "name", "mana_cost", "type_line", "oracle_text")


def load_featured_names() -> list[str]:
    raw = yaml.safe_load(FEATURED_PATH.read_text(encoding="utf-8"))
    entries = raw["featured"]
    if not isinstance(entries, list) or not entries:
        raise SystemExit(f"no featured commanders in {FEATURED_PATH}")
    # Entries are {name, description} mappings (rules.featured schema); older
    # revisions of this file were plain strings.
    return [e["name"] if isinstance(e, dict) else e for e in entries]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        default=None,
        help="EDHREC page variant, e.g. 'optimized' (Bracket 4). Default: global pages.",
    )
    args = parser.parse_args()

    commanders = load_featured_names()
    rec_names: set[str] = set()
    for name in commanders:
        data = fetch_commander(name, variant=args.variant)
        rec_names.update(rec.name for rec in data.recommendations)
    logger.info(
        "EDHREC union (%s): %d distinct recommended names across %d commanders",
        args.variant or "global", len(rec_names), len(commanders),
    )

    name_index = build_name_index()
    resolved: dict[str, str] = {}  # oracle_id -> canonical name
    unresolvable: list[str] = []
    for name in sorted(rec_names):
        try:
            match = name_index.resolve(name)
        except ResolutionError:
            unresolvable.append(name)
            continue
        resolved[match.oracle_id] = match.canonical_name
    if unresolvable:
        logger.info(
            "Discarded %d unresolvable names: %s", len(unresolvable), unresolvable
        )
    logger.info("Surface resolved against pool: %d distinct cards", len(resolved))

    store = load_tags()
    already_tagged = set(resolved) & set(store)
    todo_ids = set(resolved) - set(store)
    logger.info(
        "Already tagged in store: %d -> to label: %d", len(already_tagged), len(todo_ids)
    )

    cards: list[dict] = []
    with DEFAULT_POOL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            card = json.loads(line)
            if card["oracle_id"] in todo_ids:
                cards.append({field: card.get(field) or "" for field in BATCH_FIELDS})
    missing = todo_ids - {c["oracle_id"] for c in cards}
    if missing:
        raise SystemExit(f"resolved oracle_ids missing from pool (bug): {sorted(missing)}")
    cards.sort(key=lambda c: (c["name"], c["oracle_id"]))

    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    for stale in sorted(BATCHES_DIR.glob("batch_*.jsonl")):
        stale.unlink()
        logger.info("Removed stale batch %s", stale.name)

    n_batches = 0
    for start in range(0, len(cards), BATCH_SIZE):
        n_batches += 1
        batch_path = BATCHES_DIR / f"batch_{n_batches:03d}.jsonl"
        with batch_path.open("w", encoding="utf-8", newline="\n") as fh:
            for card in cards[start : start + BATCH_SIZE]:
                fh.write(json.dumps(card, ensure_ascii=False) + "\n")
    logger.info(
        "Wrote %d batches of <=%d cards (%d cards total) to %s",
        n_batches, BATCH_SIZE, len(cards), BATCHES_DIR,
    )


if __name__ == "__main__":
    main()
