"""Scan the tag store's cards for `protection` candidates (rubric v3).

The v3 rubric (2026-07-14) added the ``protection`` category, but the 5,283
cards already in ``data/tags/llm_tags.jsonl`` were labeled under v2, which
had no such label. This script scans the oracle text of every stored card
(resolved against the processed pool by oracle_id) with recall-oriented
regexes for protection patterns — granting hexproof / shroud / indestructible
/ protection / ward / phasing, untargetability, target redirection, totem
armor — and writes the candidates to ``batches/batch_protection.jsonl`` in
the standard batch format (oracle_id, name, mana_cost, type_line,
oracle_text) for a labeling session to re-label via ``tags.store.add_label``.

Deliberately generous: innate-keyword noise (a creature's own hexproof) is
mostly avoided by requiring a granting verb, but precision is the labeler's
job, not this scanner's.

NOTE: ``make_batches.py`` deletes every ``batches/batch_*.jsonl`` on rerun,
so regenerate this file (or merge its labels first) after running it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from tags.store import DEFAULT_POOL_PATH, load_tags  # noqa: E402

logger = logging.getLogger("find_protection_candidates")

BATCHES_DIR = Path(__file__).resolve().parent / "batches"
OUTPUT_PATH = BATCHES_DIR / "batch_protection.jsonl"

BATCH_FIELDS = ("oracle_id", "name", "mana_cost", "type_line", "oracle_text")

# Requiring a granting verb before the keyword skips most innate keyword
# lines ("Trample, indestructible") while catching grants to your own
# permanents/player, equipment ("equipped creature has hexproof"), auras and
# statics ("creatures you control have indestructible").
_GRANT_KEYWORDS = r"(?:hexproof|shroud|indestructible|protection|ward|phasing)"
PATTERNS: dict[str, re.Pattern[str]] = {
    "grant_keyword": re.compile(
        rf"(?:gains?|gets?|has|have|with)\b[^.\n]{{0,80}}?\b{_GRANT_KEYWORDS}\b",
        re.IGNORECASE,
    ),
    "cant_be_targeted": re.compile(r"can't be (?:the )?targets?\b", re.IGNORECASE),
    "phase_out": re.compile(r"\bphases? out\b", re.IGNORECASE),
    "redirect_new_targets": re.compile(r"choose new targets?\b", re.IGNORECASE),
    "redirect_change_target": re.compile(r"change the targets?\b", re.IGNORECASE),
    "target_trigger": re.compile(r"becomes? the target of\b", re.IGNORECASE),
    "totem_armor": re.compile(r"\btotem armor\b", re.IGNORECASE),
    "cant_be_destroyed": re.compile(r"can't be destroyed\b", re.IGNORECASE),
}


def find_candidates() -> tuple[list[dict[str, str]], dict[str, int]]:
    """Stored cards whose oracle text fires any protection pattern.

    Returns the candidate batch rows (sorted by name) and per-pattern hit
    counts (a card can fire several patterns).
    """
    store = load_tags()
    if not store:
        raise SystemExit("tag store is empty: nothing to scan")
    stored_ids = set(store)

    candidates: list[dict[str, str]] = []
    hits_by_pattern: dict[str, int] = {name: 0 for name in PATTERNS}
    seen_ids: set[str] = set()
    with DEFAULT_POOL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            card = json.loads(line)
            oracle_id = card.get("oracle_id")
            if oracle_id not in stored_ids:
                continue
            seen_ids.add(oracle_id)
            text = card.get("oracle_text") or ""
            fired = [name for name, rx in PATTERNS.items() if rx.search(text)]
            if not fired:
                continue
            for name in fired:
                hits_by_pattern[name] += 1
            candidates.append(
                {field: card.get(field) or "" for field in BATCH_FIELDS}
            )

    missing = len(stored_ids) - len(seen_ids)
    if missing:
        logger.warning(
            "%d stored oracle_ids not found in the pool (not scanned)", missing
        )
    candidates.sort(key=lambda c: (c["name"], c["oracle_id"]))
    return candidates, hits_by_pattern


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    candidates, hits_by_pattern = find_candidates()

    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        for card in candidates:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")

    for name, count in sorted(hits_by_pattern.items(), key=lambda kv: -kv[1]):
        logger.info("  %-24s %4d", name, count)
    logger.info(
        "Wrote %d protection candidates to %s", len(candidates), OUTPUT_PATH
    )


if __name__ == "__main__":
    main()
