"""Tag the full pool with the trained model → data/tags/model_tags.jsonl.

Runs the linear tagger (``tags.model``, pure stdlib — no sklearn needed here)
over every pool card that has no explicit human/LLM tag, and writes:

  * ``model_tags.jsonl`` — the auto-labels the runtime serves (one line per card
    the model tagged with confidence; empty predictions are omitted). Gated
    categories (``wincons``) are excluded — they never auto-apply.
  * ``review_queue.jsonl`` — cards where a gated category fired, for a human/Opus
    pass. Not read at runtime.

Both are sorted by name for stable diffs. Re-run after retraining.

Run from ``backend/``:
    ../backend/.venv/Scripts/python.exe ../experiments/tagging/production/tag_pool.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from tags.model import LinearTagModel  # noqa: E402
from tags.store import (  # noqa: E402
    CATEGORIES,
    DEFAULT_MODEL_TAGS_PATH,
    DEFAULT_POOL_PATH,
    load_tags,
)

REVIEW_QUEUE_PATH = REPO_ROOT / "data" / "tags" / "review_queue.jsonl"


def text_of(card: dict) -> str:
    return f"{card.get('type_line', '')} {card.get('oracle_text', '')}"


def main() -> None:
    model = LinearTagModel.load()
    tagged = set(load_tags())

    auto_rows: list[dict] = []
    review_rows: list[dict] = []
    scored = 0
    with DEFAULT_POOL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            card = json.loads(line)
            oid = card.get("oracle_id")
            if oid in tagged or not (card.get("oracle_text") or "").strip():
                continue
            if "Basic" in card.get("type_line", ""):
                continue
            scored += 1
            auto, gated = model.predict(text_of(card))
            if auto:
                auto_rows.append(
                    {"oracle_id": oid, "name": card["name"],
                     "labels": [c for c in CATEGORIES if c in auto]}
                )
            if gated:
                review_rows.append(
                    {"oracle_id": oid, "name": card["name"],
                     "gated": {k: round(v, 3) for k, v in sorted(gated.items())}}
                )

    auto_rows.sort(key=lambda r: (r["name"], r["oracle_id"]))
    review_rows.sort(key=lambda r: (r["name"], r["oracle_id"]))

    DEFAULT_MODEL_TAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_MODEL_TAGS_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        for r in auto_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with REVIEW_QUEUE_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        for r in review_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"scored {scored} untagged pool cards")
    print(f"model_tags: {len(auto_rows)} auto-tagged -> {DEFAULT_MODEL_TAGS_PATH.name}")
    print(f"review_queue: {len(review_rows)} gated -> {REVIEW_QUEUE_PATH.name}")
    # label distribution for a sanity read
    dist: dict[str, int] = {c: 0 for c in CATEGORIES}
    for r in auto_rows:
        for label in r["labels"]:
            dist[label] += 1
    print("auto label distribution:", {k: v for k, v in dist.items() if v})


if __name__ == "__main__":
    main()
