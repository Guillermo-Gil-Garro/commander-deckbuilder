import json
from pathlib import Path

import pytest

from tags.audit import build_audit_report, run_audit
from tags.store import (
    TagStoreError,
    load_tags,
    merge_batch,
    tagger_from_store,
)


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return path


BATCH = [
    {"oracle_id": "oid-bolt", "name": "Lightning Bolt", "labels": ["removal"]},
    {
        "oracle_id": "oid-spikefield",
        "name": "Spikefield Hazard // Spikefield Cave",
        "labels": ["lands", "removal"],
    },
    {"oracle_id": "oid-fog", "name": "Fog", "labels": []},
]


# ---------------------------------------------------------------- merge


def test_merge_batch_creates_store_and_is_idempotent(tmp_path: Path) -> None:
    batch = _write_jsonl(tmp_path / "batch.jsonl", BATCH)
    store_path = tmp_path / "llm_tags.jsonl"

    assert merge_batch(batch, store_path) == (3, 0)
    first_content = store_path.read_text(encoding="utf-8")

    # Re-merging the identical batch changes nothing.
    assert merge_batch(batch, store_path) == (0, 3)
    assert store_path.read_text(encoding="utf-8") == first_content

    store = load_tags(store_path)
    assert store["oid-bolt"].labels == ("removal",)
    assert store["oid-bolt"].source == "llm"
    assert store["oid-fog"].labels == ()


def test_merge_batch_conflicting_labels_is_an_error(tmp_path: Path) -> None:
    store_path = tmp_path / "llm_tags.jsonl"
    merge_batch(_write_jsonl(tmp_path / "batch1.jsonl", BATCH), store_path)

    conflicting = [{"oracle_id": "oid-bolt", "name": "Lightning Bolt", "labels": ["wincons"]}]
    with pytest.raises(TagStoreError, match="label conflict.*Lightning Bolt"):
        merge_batch(_write_jsonl(tmp_path / "batch2.jsonl", conflicting), store_path)

    # The failed merge must not have touched the store.
    assert load_tags(store_path)["oid-bolt"].labels == ("removal",)


def test_merge_batch_rejects_unknown_labels(tmp_path: Path) -> None:
    bad = [{"oracle_id": "oid-x", "name": "X", "labels": ["stax"]}]
    with pytest.raises(TagStoreError, match="unknown labels"):
        merge_batch(_write_jsonl(tmp_path / "bad.jsonl", bad), tmp_path / "s.jsonl")


def test_labels_are_stored_in_canonical_order(tmp_path: Path) -> None:
    rows = [{"oracle_id": "oid-y", "name": "Y", "labels": ["synergy", "lands", "ramp"]}]
    store_path = tmp_path / "s.jsonl"
    merge_batch(_write_jsonl(tmp_path / "b.jsonl", rows), store_path)
    assert load_tags(store_path)["oid-y"].labels == ("lands", "ramp", "synergy")


# ---------------------------------------------------------------- tagger


POOL = [
    {
        "oracle_id": "oid-bolt",
        "name": "Lightning Bolt",
        "type_line": "Instant",
        "layout": "normal",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
    },
    {
        "oracle_id": "oid-island",
        "name": "Island",
        "type_line": "Basic Land — Island",
        "layout": "normal",
        "oracle_text": "({T}: Add {U}.)",
    },
    {
        "oracle_id": "oid-mimic",
        "name": "Glasspool Mimic // Glasspool Shore",
        "type_line": "Creature — Shapeshifter Rogue",
        "layout": "modal_dfc",
        "oracle_text": "You may have this creature enter as a copy...\n//\nThis land enters tapped.\n{T}: Add {U}.",
    },
    {
        "oracle_id": "oid-ojer",
        "name": "Ojer Pakpatiq, Deepest Epoch // Temple of Cyclical Time",
        "type_line": "Legendary Creature — God",
        "layout": "transform",
        "oracle_text": "Flying\n//\nTemple of Cyclical Time enters tapped.",
    },
]


def test_tagger_from_store_with_lands_fallback(tmp_path: Path) -> None:
    store_path = tmp_path / "llm_tags.jsonl"
    merge_batch(_write_jsonl(tmp_path / "batch.jsonl", BATCH), store_path)
    tag = tagger_from_store(load_tags(store_path), pool_cards=POOL)

    # Store labels win, matched by full name or face name.
    assert tag("Lightning Bolt") == {"removal"}
    assert tag("Spikefield Hazard // Spikefield Cave") == {"lands", "removal"}
    assert tag("Spikefield Cave") == {"lands", "removal"}
    assert tag("Fog") == set()

    # Fallback layer: untagged pool lands (type_line or playable MDFC land face).
    assert tag("Island") == {"lands"}
    assert tag("Glasspool Mimic // Glasspool Shore") == {"lands"}
    assert tag("Glasspool Shore") == {"lands"}
    # Transform backs are not playable lands (rubric v2).
    assert tag("Ojer Pakpatiq, Deepest Epoch // Temple of Cyclical Time") == set()
    # Unknown names return the empty set (selector's synergy bucket).
    assert tag("Card Not Anywhere") == set()


# ---------------------------------------------------------------- audit


def test_audit_detects_synthetic_discrepancy(tmp_path: Path) -> None:
    store_path = tmp_path / "llm_tags.jsonl"
    rows = [
        # Regex will tag Lightning Bolt as removal; the LLM "forgot" it and
        # invented wincons -> both directions of the diff appear.
        {"oracle_id": "oid-bolt", "name": "Lightning Bolt", "labels": ["wincons"]},
        # Agreement: no queue entry.
        {"oracle_id": "oid-island", "name": "Island", "labels": ["lands"]},
        # Human entries are ground truth: never audited even if regex disagrees.
        {
            "oracle_id": "oid-mimic",
            "name": "Glasspool Mimic // Glasspool Shore",
            "labels": [],
            "source": "human",
        },
    ]
    merge_batch(_write_jsonl(tmp_path / "batch.jsonl", rows), store_path)

    queue_path = tmp_path / "audit_queue.jsonl"
    discrepancies = run_audit(
        load_tags(store_path), pool_cards=POOL, queue_path=queue_path
    )

    assert [d["name"] for d in discrepancies] == ["Lightning Bolt"]
    entry = discrepancies[0]
    assert entry["llm_labels"] == ["wincons"]
    assert entry["regex_labels"] == ["removal"]
    assert entry["diff"] == {"only_llm": ["wincons"], "only_regex": ["removal"]}
    assert len(queue_path.read_text(encoding="utf-8").splitlines()) == 1

    report = build_audit_report(queue_path)
    assert report["total_discrepancies"] == 1
    assert report["by_category"] == {
        "removal": {"only_llm": 0, "only_regex": 1},
        "wincons": {"only_llm": 1, "only_regex": 0},
    }
