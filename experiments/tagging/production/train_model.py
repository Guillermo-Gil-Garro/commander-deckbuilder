"""Train the production tagging model and export it to data/tags/model.json.

Uses ``backend/tags/model.py``'s featurizer (the SAME code the runtime scores
with) to build the training matrix, fits one-vs-rest class-weighted logistic
regression per category, tunes a decision threshold per category on a held-out
validation fold, and exports dense weights + vocab + idf + thresholds + policy.

sklearn/scipy are dev-only (backend/pyproject.toml [dev]); the exported JSON is
scored at runtime with pure stdlib. Reports test-fold precision/recall/F1 so the
exported model's quality is on record.

Run from ``backend/``:
    ../backend/.venv/Scripts/python.exe ../experiments/tagging/production/train_model.py
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.linear_model import LogisticRegression

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from eval_holdout import load_dataset  # noqa: E402
from tags.model import DEFAULT_MODEL_PATH, raw_features, transform  # noqa: E402
from tags.store import CATEGORIES  # noqa: E402

SEED = 42
VAL_FRACTION = 0.15
TEST_FRACTION = 0.15
MIN_DF = 4
C = 4.0
GATE_CATEGORIES = frozenset({"wincons"})


def text_of(row: dict) -> str:
    return f"{row['type_line']} {row['oracle_text']}"


def split(rows):
    rng = np.random.default_rng(SEED)
    idx = rng.permutation(len(rows))
    n = len(rows)
    n_test = int(round(n * TEST_FRACTION))
    n_val = int(round(n * VAL_FRACTION))
    test_i = set(idx[:n_test].tolist())
    val_i = set(idx[n_test : n_test + n_val].tolist())
    train = [r for i, r in enumerate(rows) if i not in test_i and i not in val_i]
    val = [r for i, r in enumerate(rows) if i in val_i]
    test = [r for i, r in enumerate(rows) if i in test_i]
    return train, val, test


def build_vocab_idf(train):
    df: dict[str, int] = defaultdict(int)
    for r in train:
        for f in raw_features(text_of(r)):
            df[f] += 1
    feats = sorted(f for f, c in df.items() if c >= MIN_DF)
    vocab = {f: i for i, f in enumerate(feats)}
    n = len(train)
    idf = [math.log((1.0 + n) / (1.0 + df[f])) + 1.0 for f in feats]
    return vocab, idf


def matrix(rows, vocab, idf) -> csr_matrix:
    data, indices, indptr = [], [], [0]
    for r in rows:
        vec = transform(text_of(r), vocab, idf)
        for j, v in vec.items():
            indices.append(j)
            data.append(v)
        indptr.append(len(indices))
    return csr_matrix((data, indices, indptr), shape=(len(rows), len(vocab)))


def y_of(rows, cat) -> np.ndarray:
    return np.array([1 if cat in r["labels"] else 0 for r in rows], dtype=np.int8)


def best_threshold(y, proba) -> float:
    if y.sum() == 0:
        return 0.5
    best_t, best_f1 = 0.5, -1.0
    for t in np.linspace(0.05, 0.95, 19):
        pred = (proba >= t).astype(np.int8)
        tp = int(((pred == 1) & (y == 1)).sum())
        fp = int(((pred == 1) & (y == 0)).sum())
        fn = int(((pred == 0) & (y == 1)).sum())
        f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return best_t


def prf(pred, true):
    tp = float(((pred == 1) & (true == 1)).sum())
    fp = float(((pred == 1) & (true == 0)).sum())
    fn = float(((pred == 0) & (true == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1, int((true == 1).sum())


def main() -> None:
    rows = load_dataset()
    train, val, test = split(rows)
    vocab, idf = build_vocab_idf(train)
    print(f"dataset {len(rows)}  train {len(train)} val {len(val)} test {len(test)}")
    print(f"vocab {len(vocab)} (min_df={MIN_DF})")

    Xtr, Xval, Xte = matrix(train, vocab, idf), matrix(val, vocab, idf), matrix(test, vocab, idf)

    coef: dict[str, list[float]] = {}
    intercept: dict[str, float] = {}
    threshold: dict[str, float] = {}
    policy: dict[str, str] = {}
    f1s = []
    print(f"\n{'category':<12}{'prec':>7}{'rec':>7}{'f1':>7}{'support':>9}   thr  policy")
    for cat in CATEGORIES:
        ytr = y_of(train, cat)
        if ytr.sum() == 0:
            coef[cat] = [0.0] * len(vocab)
            intercept[cat] = -20.0
            threshold[cat] = 0.5
            policy[cat] = "gate" if cat in GATE_CATEGORIES else "auto"
            continue
        clf = LogisticRegression(class_weight="balanced", C=C, max_iter=3000)
        clf.fit(Xtr, ytr)
        pv = clf.predict_proba(Xval)[:, 1]
        pt = clf.predict_proba(Xte)[:, 1]
        thr = best_threshold(y_of(val, cat), pv)
        coef[cat] = clf.coef_[0].astype(float).tolist()
        intercept[cat] = float(clf.intercept_[0])
        threshold[cat] = thr
        policy[cat] = "gate" if cat in GATE_CATEGORIES else "auto"
        prec, rec, f1, sup = prf((pt >= thr).astype(np.int8), y_of(test, cat))
        f1s.append(f1)
        print(f"{cat:<12}{prec:>7.2f}{rec:>7.2f}{f1:>7.2f}{sup:>9}  {thr:.2f}  {policy[cat]}")
    print(f"{'MACRO-F1':<12}{'':>7}{'':>7}{np.mean(f1s):>7.2f}")

    DEFAULT_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    blob = {
        "categories": list(CATEGORIES),
        "vocab": vocab,
        "idf": idf,
        "coef": coef,
        "intercept": intercept,
        "threshold": threshold,
        "policy": policy,
    }
    DEFAULT_MODEL_PATH.write_text(json.dumps(blob), encoding="utf-8")
    size_mb = DEFAULT_MODEL_PATH.stat().st_size / 1e6
    print(f"\nexported {DEFAULT_MODEL_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
