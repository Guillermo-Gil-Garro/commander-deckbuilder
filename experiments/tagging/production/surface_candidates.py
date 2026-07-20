"""Model-guided candidate surfacing for targeted labeling (Fase 8, wincons/protection).

Keyword fishing misses implicit wincons (Craterhoof has no keyword) and floods
protection with innate hexproof/indestructible (not protection per rubric). So
instead: train the tf-idf + LR model on the WHOLE store, run it over every
*untagged* pool card, and surface the top-N by predicted probability for the
weak categories. Those are the highest-value boundary cases — either true
positives to add or hard negatives to correct — and Opus labels them by hand.

Writes ``batches/candidates_<cat>.jsonl`` with the card fields a labeling
session needs plus the model's probability (advisory, never a label).

Run from ``backend/``:
    ../backend/.venv/Scripts/python.exe ../experiments/tagging/production/surface_candidates.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from tags.store import DEFAULT_POOL_PATH, load_tags  # noqa: E402

TARGET_CATEGORIES = ("wincons", "protection")
TOP_N = 100
BATCHES_DIR = Path(__file__).resolve().parent / "batches"
FIELDS = ("oracle_id", "name", "mana_cost", "type_line", "oracle_text")


def text_of(card: dict) -> str:
    return f"{card.get('type_line', '')} {card.get('oracle_text', '')}"


def main() -> None:
    store = load_tags()
    pool: dict[str, dict] = {}
    with DEFAULT_POOL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            c = json.loads(line)
            pool[c["oracle_id"]] = c

    train_ids = [oid for oid in store if oid in pool]
    train_txt = [text_of(pool[oid]) for oid in train_ids]

    word = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3, sublinear_tf=True)
    char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, sublinear_tf=True)
    Xtr = hstack([word.fit_transform(train_txt), char.fit_transform(train_txt)]).tocsr()

    # untagged pool cards with text (skip basics — never wincons/protection)
    cand_ids = [
        oid
        for oid, c in pool.items()
        if oid not in store
        and (c.get("oracle_text") or "").strip()
        and "Basic" not in c.get("type_line", "")
    ]
    Xca = hstack(
        [word.transform([text_of(pool[o]) for o in cand_ids]),
         char.transform([text_of(pool[o]) for o in cand_ids])]
    ).tocsr()
    print(f"train {len(train_ids)}  |  untagged candidates scored {len(cand_ids)}")

    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    for cat in TARGET_CATEGORIES:
        y = [1 if cat in store[o].labels else 0 for o in train_ids]
        if sum(y) == 0:
            continue
        clf = LogisticRegression(class_weight="balanced", C=4.0, max_iter=2000)
        clf.fit(Xtr, y)
        proba = clf.predict_proba(Xca)[:, 1]
        ranked = sorted(zip(cand_ids, proba), key=lambda t: -t[1])[:TOP_N]
        out = BATCHES_DIR / f"candidates_{cat}.jsonl"
        with out.open("w", encoding="utf-8", newline="\n") as fh:
            for oid, p in ranked:
                row = {f: pool[oid].get(f) or "" for f in FIELDS}
                row["model_p"] = round(float(p), 3)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{cat}: wrote {len(ranked)} candidates (p {ranked[-1][1]:.2f}..{ranked[0][1]:.2f}) -> {out.name}")


if __name__ == "__main__":
    main()
