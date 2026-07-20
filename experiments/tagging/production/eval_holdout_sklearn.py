"""Serious ML baseline for the tagging engine (Fase 8, step 2).

Upgrades ``eval_holdout.py``'s numpy probe to the model we'd actually ship:
tf-idf over word AND char n-grams (char n-grams catch MTG templating like
'destroy all', 'add {', 'ward') + one-vs-rest logistic regression with
``class_weight='balanced'`` (the rare categories — wincons, protection — no
longer collapse) + a per-category decision threshold tuned on a held-out
VALIDATION split (never on test).

Honest protocol: 70/15/15 train/val/test (seed-fixed). Thresholds are chosen
on val; every reported number is on test. The regex tagger — the bar to beat —
is evaluated on the same test set.

sklearn is a dev-only dependency (backend/pyproject.toml [dev]); a shipped
model would be exported to numpy/onnx, so the runtime never imports sklearn.

Run from ``backend/``:
    ../backend/.venv/Scripts/python.exe ../experiments/tagging/production/eval_holdout_sklearn.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from eval_holdout import load_dataset  # noqa: E402  (shared loader)
from tags.audit import load_regex_tagger, regex_labels_for  # noqa: E402
from tags.store import CATEGORIES  # noqa: E402

SEED = 42
VAL_FRACTION = 0.15
TEST_FRACTION = 0.15


def text_of(row: dict) -> str:
    return f"{row['type_line']} {row['oracle_text']}"


def three_way_split(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
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


def labels_matrix(rows: list[dict]) -> np.ndarray:
    Y = np.zeros((len(rows), len(CATEGORIES)), dtype=np.int8)
    for i, r in enumerate(rows):
        for k, cat in enumerate(CATEGORIES):
            if cat in r["labels"]:
                Y[i, k] = 1
    return Y


def vectorize(train_txt, *other_txts):
    word = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3, sublinear_tf=True)
    char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, sublinear_tf=True)
    Xw_tr, Xc_tr = word.fit_transform(train_txt), char.fit_transform(train_txt)
    mats = [hstack([Xw_tr, Xc_tr]).tocsr()]
    for txt in other_txts:
        mats.append(hstack([word.transform(txt), char.transform(txt)]).tocsr())
    n_feats = Xw_tr.shape[1] + Xc_tr.shape[1]
    return mats, n_feats


def best_threshold(y_true: np.ndarray, proba: np.ndarray) -> float:
    """Threshold maximizing F1 on the validation fold; 0.5 if no positives."""
    if y_true.sum() == 0:
        return 0.5
    best_t, best_f1 = 0.5, -1.0
    for t in np.linspace(0.05, 0.95, 19):
        pred = (proba >= t).astype(np.int8)
        tp = int(((pred == 1) & (y_true == 1)).sum())
        fp = int(((pred == 1) & (y_true == 0)).sum())
        fn = int(((pred == 0) & (y_true == 1)).sum())
        f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return best_t


def prf(pred: np.ndarray, true: np.ndarray) -> tuple[float, float, float, int]:
    tp = float(((pred == 1) & (true == 1)).sum())
    fp = float(((pred == 1) & (true == 0)).sum())
    fn = float(((pred == 0) & (true == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1, int((true == 1).sum())


def report(name: str, pred: np.ndarray, true: np.ndarray, thr: list[float] | None = None) -> None:
    print(f"\n=== {name} ===")
    head = f"{'category':<12}{'prec':>7}{'rec':>7}{'f1':>7}{'support':>9}"
    print(head + ("   thr" if thr else ""))
    f1s = []
    for k, cat in enumerate(CATEGORIES):
        prec, rec, f1, sup = prf(pred[:, k], true[:, k])
        f1s.append(f1)
        line = f"{cat:<12}{prec:>7.2f}{rec:>7.2f}{f1:>7.2f}{sup:>9}"
        print(line + (f"  {thr[k]:.2f}" if thr else ""))
    mp, mr, mf1, _ = prf(pred.ravel(), true.ravel())
    print(f"{'MACRO-F1':<12}{'':>7}{'':>7}{np.mean(f1s):>7.2f}")
    print(f"{'MICRO-F1':<12}{mp:>7.2f}{mr:>7.2f}{mf1:>7.2f}")


def main() -> None:
    rows = load_dataset()
    train, val, test = three_way_split(rows)
    print(f"dataset {len(rows)}  |  train {len(train)}  val {len(val)}  test {len(test)}")

    (Xtr, Xval, Xte), n_feats = vectorize(
        [text_of(r) for r in train], [text_of(r) for r in val], [text_of(r) for r in test]
    )
    print(f"features: {n_feats} (word 1-2gram + char_wb 3-5gram, tf-idf)")
    Ytr, Yval, Yte = labels_matrix(train), labels_matrix(val), labels_matrix(test)

    # regex bar
    regex_module = load_regex_tagger()
    R = np.zeros_like(Yte)
    for i, r in enumerate(test):
        fired = set(regex_labels_for(r, regex_module))
        for k, cat in enumerate(CATEGORIES):
            if cat in fired:
                R[i, k] = 1
    report("regex tagger (the bar)", R, Yte)

    # one-vs-rest LR, class-weighted; threshold tuned on val
    proba_val = np.zeros((len(val), len(CATEGORIES)))
    proba_te = np.zeros((len(test), len(CATEGORIES)))
    for k in range(len(CATEGORIES)):
        if Ytr[:, k].sum() == 0:
            continue
        clf = LogisticRegression(class_weight="balanced", C=4.0, max_iter=2000)
        clf.fit(Xtr, Ytr[:, k])
        proba_val[:, k] = clf.predict_proba(Xval)[:, 1]
        proba_te[:, k] = clf.predict_proba(Xte)[:, 1]

    report("logreg tf-idf (thr=0.5)", (proba_te >= 0.5).astype(np.int8), Yte)

    thr = [best_threshold(Yval[:, k], proba_val[:, k]) for k in range(len(CATEGORIES))]
    tuned = np.zeros_like(Yte)
    for k in range(len(CATEGORIES)):
        tuned[:, k] = (proba_te[:, k] >= thr[k]).astype(np.int8)
    report("logreg tf-idf (per-category tuned thr)", tuned, Yte, thr)


if __name__ == "__main__":
    main()
