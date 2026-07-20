"""Holdout eval for the functional tagging engine (Fase 8, step 2).

Answers the decision question: is the Opus/human golden dataset a good enough
training set, and does a *simple* model beat the regex tagger — the bar to
beat — on the categories that matter (the judgment ones: wincons, synergy,
protection, card_draw)?

Two baselines, evaluated on the SAME stratified holdout (seed-fixed) so the
comparison is fair:

  * regex  — the experiment regex tagger (``tags.audit``), a fixed rule. Zero
             training; this is the incumbent the ML must beat.
  * logreg — one-vs-rest logistic regression over a binary bag-of-words of
             ``type_line`` + ``oracle_text``, implemented in numpy (no sklearn
             dependency). The "can a simple, self-owned model learn this?" probe.

Reports per-category precision / recall / F1 / support at a 0.5 threshold, plus
micro/macro F1. Per-category threshold tuning and abstention (Fase 8 decision 4)
are deliberately NOT applied here — this is the honest floor, not the ceiling.

Run from ``backend/`` (so ``tags`` imports resolve):
    ../backend/.venv/Scripts/python.exe ../experiments/tagging/production/eval_holdout.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from tags.audit import load_regex_tagger, regex_labels_for  # noqa: E402
from tags.store import CATEGORIES, DEFAULT_POOL_PATH, load_tags  # noqa: E402

TEST_FRACTION = 0.15
SEED = 42
MIN_DOC_FREQ = 3          # a token must appear in >= this many train cards
MAX_VOCAB = 4000
LR = 0.5                  # gradient-descent step
EPOCHS = 300
L2 = 1e-3
THRESHOLD = 0.5

_TOKEN_RE = re.compile(r"[a-z]+")


def tokenize(card: dict) -> list[str]:
    """Bag of lowercased word tokens from type_line + oracle_text.

    MTG text is templated, so unigrams carry most of the signal ('destroy',
    'all', 'target', 'add', 'draw', 'search', 'counter', 'land', 'artifact')."""
    text = f"{card.get('type_line', '')} {card.get('oracle_text', '')}".lower()
    return [t for t in _TOKEN_RE.findall(text) if len(t) >= 2]


def load_dataset() -> list[dict]:
    """Cards present in BOTH the tag store and the pool, with text + labels."""
    store = load_tags()
    pool: dict[str, dict] = {}
    with DEFAULT_POOL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            c = json.loads(line)
            pool[c["oracle_id"]] = c
    rows: list[dict] = []
    for oid, entry in store.items():
        card = pool.get(oid)
        if card is None:
            continue
        rows.append(
            {
                "oracle_id": oid,
                "name": entry.name,
                "type_line": card.get("type_line", ""),
                "oracle_text": card.get("oracle_text", ""),
                "labels": set(entry.labels),
            }
        )
    return rows


def split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    rng = np.random.default_rng(SEED)
    idx = rng.permutation(len(rows))
    n_test = int(round(len(rows) * TEST_FRACTION))
    test_idx = set(idx[:n_test].tolist())
    train = [r for i, r in enumerate(rows) if i not in test_idx]
    test = [r for i, r in enumerate(rows) if i in test_idx]
    return train, test


def build_vocab(train: list[dict]) -> dict[str, int]:
    df: dict[str, int] = {}
    for r in train:
        for tok in set(tokenize(r)):
            df[tok] = df.get(tok, 0) + 1
    kept = [t for t, c in df.items() if c >= MIN_DOC_FREQ]
    kept.sort(key=lambda t: (-df[t], t))
    kept = kept[:MAX_VOCAB]
    return {t: i for i, t in enumerate(kept)}


def featurize(rows: list[dict], vocab: dict[str, int]) -> np.ndarray:
    X = np.zeros((len(rows), len(vocab)), dtype=np.float32)
    for i, r in enumerate(rows):
        for tok in set(tokenize(r)):
            j = vocab.get(tok)
            if j is not None:
                X[i, j] = 1.0
    return X


def labels_matrix(rows: list[dict]) -> np.ndarray:
    Y = np.zeros((len(rows), len(CATEGORIES)), dtype=np.float32)
    for i, r in enumerate(rows):
        for k, cat in enumerate(CATEGORIES):
            if cat in r["labels"]:
                Y[i, k] = 1.0
    return Y


def train_logreg(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    """Binary logistic regression via full-batch gradient descent (numpy)."""
    n, d = X.shape
    w = np.zeros(d, dtype=np.float64)
    b = 0.0
    for _ in range(EPOCHS):
        z = X @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        err = p - y
        gw = X.T @ err / n + L2 * w
        gb = float(np.mean(err))
        w -= LR * gw
        b -= LR * gb
    return w, b


def prf(pred: np.ndarray, true: np.ndarray) -> tuple[float, float, float, int]:
    tp = float(np.sum((pred == 1) & (true == 1)))
    fp = float(np.sum((pred == 1) & (true == 0)))
    fn = float(np.sum((pred == 0) & (true == 1)))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1, int(np.sum(true == 1))


def regex_predictions(test: list[dict], module) -> np.ndarray:
    P = np.zeros((len(test), len(CATEGORIES)), dtype=np.float32)
    for i, r in enumerate(test):
        fired = set(regex_labels_for(r, module))
        for k, cat in enumerate(CATEGORIES):
            if cat in fired:
                P[i, k] = 1.0
    return P


def report(name: str, pred: np.ndarray, true: np.ndarray) -> None:
    print(f"\n=== {name} ===")
    print(f"{'category':<12}{'prec':>7}{'rec':>7}{'f1':>7}{'support':>9}")
    f1s = []
    for k, cat in enumerate(CATEGORIES):
        prec, rec, f1, sup = prf(pred[:, k], true[:, k])
        f1s.append(f1)
        print(f"{cat:<12}{prec:>7.2f}{rec:>7.2f}{f1:>7.2f}{sup:>9}")
    micro_p, micro_r, micro_f1, _ = prf(pred.ravel(), true.ravel())
    print(f"{'MACRO-F1':<12}{'':>7}{'':>7}{np.mean(f1s):>7.2f}")
    print(f"{'MICRO-F1':<12}{micro_p:>7.2f}{micro_r:>7.2f}{micro_f1:>7.2f}")


def main() -> None:
    rows = load_dataset()
    train, test = split(rows)
    print(f"dataset: {len(rows)} cards  |  train {len(train)}  test {len(test)}")

    vocab = build_vocab(train)
    print(f"vocab: {len(vocab)} tokens (df>={MIN_DOC_FREQ}, cap {MAX_VOCAB})")

    Xtr, Xte = featurize(train, vocab), featurize(test, vocab)
    Ytr, Yte = labels_matrix(train), labels_matrix(test)

    regex_module = load_regex_tagger()
    report("regex tagger (the bar)", regex_predictions(test, regex_module), Yte)

    lr_pred = np.zeros_like(Yte)
    for k in range(len(CATEGORIES)):
        w, b = train_logreg(Xtr, Ytr[:, k])
        p = 1.0 / (1.0 + np.exp(-(Xte @ w + b)))
        lr_pred[:, k] = (p >= THRESHOLD).astype(np.float32)
    report("logreg BoW (numpy, thr=0.5)", lr_pred, Yte)


if __name__ == "__main__":
    main()
