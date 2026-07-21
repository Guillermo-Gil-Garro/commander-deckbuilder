"""Tests for the pure-stdlib linear tagger and its wiring into the store tagger."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tags.model import (
    DEFAULT_MODEL_PATH,
    LinearTagModel,
    raw_features,
    transform,
)
from tags.store import TagEntry, load_model_labels, tagger_from_store


def test_raw_features_words_bigrams_and_chars():
    feats = raw_features("Destroy target creature")
    assert feats["w:destroy"] == 1
    assert feats["w:target"] == 1
    assert feats["w:destroy_target"] == 1  # adjacent bigram
    assert feats["w:target_creature"] == 1
    assert any(k.startswith("c:") for k in feats)  # in-word char n-grams present
    # single-letter tokens are dropped
    assert not any(k == "w:a" for k in feats)


def test_transform_is_l2_normalized_and_drops_unknown():
    vocab = {"w:destroy": 0, "w:target": 1}
    idf = [1.0, 1.0]
    vec = transform("destroy target unknownword", vocab, idf)
    assert set(vec) == {0, 1}  # 'unknownword' dropped (not in vocab)
    norm = math.sqrt(sum(v * v for v in vec.values()))
    assert norm == pytest.approx(1.0)


def _toy_model() -> LinearTagModel:
    # One feature 'w:win'; two categories. Both fire on the word 'win', but
    # wincons is gated and removal is auto.
    return LinearTagModel(
        vocab={"w:win": 0},
        idf=[1.0],
        categories=("wincons", "removal"),
        coef={"wincons": [10.0], "removal": [10.0]},
        intercept={"wincons": -1.0, "removal": -1.0},
        threshold={"wincons": 0.5, "removal": 0.5},
        policy={"wincons": "gate", "removal": "auto"},
    )


def test_predict_gate_category_is_not_auto_applied():
    auto, gated = _toy_model().predict("win")
    assert auto == {"removal"}          # auto category applied
    assert set(gated) == {"wincons"}    # gate category surfaced, not auto
    assert gated["wincons"] > 0.5


def test_predict_below_threshold_yields_nothing():
    auto, gated = _toy_model().predict("nothing relevant here")
    assert auto == set()
    assert gated == {}


def test_load_model_labels_missing_file(tmp_path: Path):
    assert load_model_labels(tmp_path / "nope.jsonl") == {}


def test_load_model_labels_reads_and_splits_faces(tmp_path: Path):
    p = tmp_path / "model_tags.jsonl"
    p.write_text(
        json.dumps({"oracle_id": "x", "name": "A // B", "labels": ["ramp"]}) + "\n",
        encoding="utf-8",
    )
    labels = load_model_labels(p)
    assert labels["A // B"] == {"ramp"}
    assert labels["A"] == {"ramp"}      # each face is indexed too
    assert labels["B"] == {"ramp"}


def test_tagger_model_layer_precedence():
    pool = [
        {"oracle_id": "1", "name": "Explicit Card", "type_line": "Creature"},
        {"oracle_id": "2", "name": "Model Card", "type_line": "Instant"},
        {"oracle_id": "3", "name": "Some Land", "type_line": "Land"},
    ]
    store = {
        "1": TagEntry("1", "Explicit Card", ("removal",), "v3", "human"),
    }
    model_labels = {
        "Model Card": {"card_draw"},
        "Some Land": {"lands", "ramp"},  # model beats the plain lands fallback
    }
    tag = tagger_from_store(store, pool, model_labels=model_labels)
    assert tag("Explicit Card") == {"removal"}       # explicit store wins
    assert tag("Model Card") == {"card_draw"}         # model fills the untagged
    assert tag("Some Land") == {"lands", "ramp"}      # model beats lands fallback
    assert tag("Unknown Card") == set()               # still empty -> synergy


def test_tagger_lands_fallback_when_no_model_entry():
    pool = [{"oracle_id": "3", "name": "Bare Land", "type_line": "Land"}]
    tag = tagger_from_store({}, pool, model_labels={})
    assert tag("Bare Land") == {"lands"}


@pytest.mark.skipif(
    not DEFAULT_MODEL_PATH.is_file(), reason="model.json not built in this checkout"
)
def test_exported_model_loads_and_scores():
    model = LinearTagModel.load()
    scores = model.scores("Destroy all creatures.")
    assert set(scores) == set(model.categories)
    assert all(0.0 <= v <= 1.0 for v in scores.values())
    assert scores["board_wipe"] > 0.5  # 'destroy all creatures' is a wipe
