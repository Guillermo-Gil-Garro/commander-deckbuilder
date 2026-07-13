"""Compare the four tagging methods against Guille's hand labels.

Usage (from experiments/tagging/):

    python evaluate.py [--labels test_set_filled.csv]

The labels CSV must have ``name`` and a filled ``final_labels`` column
(``|``-separated, empty = none). Rows with an empty ``final_labels`` are
treated as labeled-none, so run this only when the labeling is FINISHED —
a half-filled file silently skews every metric. The script warns loudly
about how many rows are unlabeled for that reason.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
METHODS = ("regex", "edhrec", "scryfall", "llm")
CATEGORIES = ("lands", "ramp", "card_draw", "removal", "board_wipe", "wincons", "synergy")


def load_truth(labels_path: Path) -> dict[str, set[str]]:
    with labels_path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    truth: dict[str, set[str]] = {}
    for row in rows:
        labels = {part.strip() for part in row["final_labels"].split("|") if part.strip()}
        unknown = labels - set(CATEGORIES) - {"none"}
        if unknown:
            sys.exit(f"Unknown labels {sorted(unknown)} in row {row['name']!r}")
        truth[row["name"]] = labels - {"none"}
    empty = sum(1 for row in rows if not row["final_labels"].strip())
    if empty:
        print(f"WARNING: {empty}/{len(rows)} rows have empty final_labels (counted as none)\n")
    return truth


def f1_line(tp: int, fp: int, fn: int) -> str:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return f"P={precision:.2f} R={recall:.2f} F1={f1:.2f} (tp={tp} fp={fp} fn={fn})"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default="test_set_filled.csv")
    args = parser.parse_args()

    truth = load_truth(BASE / args.labels)
    for method in METHODS:
        preds_raw = json.loads(
            (BASE / "predictions" / f"{method}.json").read_text(encoding="utf-8")
        )
        preds = {name: set(labels) for name, labels in preds_raw.items()}
        missing = set(truth) - set(preds)
        if missing:
            sys.exit(f"{method}: predictions missing {len(missing)} cards, e.g. {sorted(missing)[:3]}")

        print(f"== {method} ==")
        for category in CATEGORIES:
            tp = sum(1 for n in truth if category in truth[n] and category in preds[n])
            fp = sum(1 for n in truth if category not in truth[n] and category in preds[n])
            fn = sum(1 for n in truth if category in truth[n] and category not in preds[n])
            print(f"  {category:11} {f1_line(tp, fp, fn)}")
        exact = sum(1 for n in truth if truth[n] == preds[n])
        micro_tp = sum(len(truth[n] & preds[n]) for n in truth)
        micro_fp = sum(len(preds[n] - truth[n]) for n in truth)
        micro_fn = sum(len(truth[n] - preds[n]) for n in truth)
        print(f"  {'micro-avg':11} {f1_line(micro_tp, micro_fp, micro_fn)}")
        print(f"  exact-match {exact}/{len(truth)}\n")


if __name__ == "__main__":
    main()
