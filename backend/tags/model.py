"""Pure-stdlib linear tagger: featurizer + inference for the functional tags.

Runtime-safe by design: this module imports only the standard library (no numpy,
no sklearn), so the HF image never gains an ML dependency. The SAME featurizer
is used by the offline trainer (``experiments/tagging/production/train_model.py``)
to build the training matrix, which guarantees train/inference parity — there is
no second implementation to drift.

The trained weights live in ``data/tags/model.json`` (vocabulary, idf,
per-category dense coefficients, decision thresholds and per-category policy)
and are produced offline with sklearn. At runtime we only score a linear model:
tf-idf features (sublinear tf, L2-normalized) dotted with the category weights,
squashed by a sigmoid.

Policy: ``auto`` categories are trusted enough to auto-apply; ``gate`` categories
(``wincons`` — intrinsically heterogeneous, see ROADMAP Fase 8) never auto-apply,
they surface a review queue for a human/Opus pass instead.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PATH = REPO_ROOT / "data" / "tags" / "model.json"

_WORD_RE = re.compile(r"[a-z]+")


def _words(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower()) if len(t) >= 2]


def raw_features(text: str) -> dict[str, int]:
    """Feature -> count. Word 1&2-grams (``w:``) + in-word char 3&4-grams (``c:``).

    Char n-grams are bounded to word interiors (each word padded with spaces),
    the analogue of sklearn's ``char_wb`` — they catch MTG templating fragments
    ('destroy', 'hexpro', ' add ') that plain words miss.
    """
    feats: dict[str, int] = {}
    words = _words(text)
    for i, w in enumerate(words):
        wk = "w:" + w
        feats[wk] = feats.get(wk, 0) + 1
        if i + 1 < len(words):
            bg = "w:" + w + "_" + words[i + 1]
            feats[bg] = feats.get(bg, 0) + 1
        padded = " " + w + " "
        for n in (3, 4):
            for j in range(len(padded) - n + 1):
                ck = "c:" + padded[j : j + n]
                feats[ck] = feats.get(ck, 0) + 1
    return feats


def transform(text: str, vocab: dict[str, int], idf: list[float]) -> dict[int, float]:
    """Raw counts -> sublinear tf -> *idf -> L2-normalized, keyed by vocab index.

    Shared by trainer and runtime; unseen features (not in ``vocab``) are dropped,
    exactly as a fixed vocabulary vectorizer would."""
    vec: dict[int, float] = {}
    for feat, count in raw_features(text).items():
        j = vocab.get(feat)
        if j is not None:
            vec[j] = (1.0 + math.log(count)) * idf[j]
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm > 0.0:
        for j in vec:
            vec[j] /= norm
    return vec


@dataclass(frozen=True)
class LinearTagModel:
    vocab: dict[str, int]
    idf: list[float]
    categories: tuple[str, ...]
    coef: dict[str, list[float]]      # category -> dense weights over the vocab
    intercept: dict[str, float]
    threshold: dict[str, float]
    policy: dict[str, str]            # category -> "auto" | "gate"

    @classmethod
    def load(cls, path: Path | str = DEFAULT_MODEL_PATH) -> "LinearTagModel":
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            vocab=blob["vocab"],
            idf=blob["idf"],
            categories=tuple(blob["categories"]),
            coef=blob["coef"],
            intercept=blob["intercept"],
            threshold=blob["threshold"],
            policy=blob["policy"],
        )

    def scores(self, text: str) -> dict[str, float]:
        vec = transform(text, self.vocab, self.idf)
        out: dict[str, float] = {}
        for cat in self.categories:
            weights = self.coef[cat]
            z = self.intercept[cat]
            for j, v in vec.items():
                z += weights[j] * v
            out[cat] = 1.0 / (1.0 + math.exp(-z))
        return out

    def predict(self, text: str) -> tuple[set[str], dict[str, float]]:
        """(auto_labels, gated) — auto_labels are trusted; gated maps a gate
        category over its threshold to its probability (for the review queue)."""
        s = self.scores(text)
        auto: set[str] = set()
        gated: dict[str, float] = {}
        for cat in self.categories:
            if s[cat] >= self.threshold[cat]:
                if self.policy.get(cat, "auto") == "gate":
                    gated[cat] = s[cat]
                else:
                    auto.add(cat)
        return auto, gated
