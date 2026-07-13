"""Audit layer: LLM store labels vs the experiment regex tagger.

Fase 2 decision: the regex tagger is kept as an *audit contrast* for the
LLM-cached primary engine. For every store entry with ``source == "llm"``,
the card's oracle text is re-tagged with the regex rules; any label
disagreement becomes a line in ``data/tags/audit_queue.jsonl`` for human
review. ``source == "human"`` entries are ground truth and are never queued.

The regex rules live in ``experiments/tagging/methods/regex_tagger.py``
(read-only for this package); they are loaded by file path so the experiment
tree does not need to be an installed package.
"""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping

from tags.store import (
    CATEGORIES,
    DEFAULT_POOL_PATH,
    REPO_ROOT,
    TagEntry,
    _iter_pool_cards,
)

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_PATH = REPO_ROOT / "data" / "tags" / "audit_queue.jsonl"
REGEX_TAGGER_PATH = REPO_ROOT / "experiments" / "tagging" / "methods" / "regex_tagger.py"


class AuditError(Exception):
    """The regex tagger or the audit inputs are missing/invalid."""


def load_regex_tagger(path: Path | str = REGEX_TAGGER_PATH) -> ModuleType:
    """Import the experiment regex tagger module from its file path."""
    path = Path(path)
    if not path.is_file():
        raise AuditError(f"regex tagger not found: {path}")
    spec = importlib.util.spec_from_file_location("experiment_regex_tagger", path)
    if spec is None or spec.loader is None:
        raise AuditError(f"could not build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "tag_card"):
        raise AuditError(f"{path} does not expose tag_card()")
    return module


def regex_labels_for(card: Mapping[str, Any], regex_module: ModuleType) -> list[str]:
    """Canonical label list the regex rules produce for one pool card."""
    fired = regex_module.tag_card(dict(card))
    return [c for c in CATEGORIES if c in fired]


def run_audit(
    store: Mapping[str, TagEntry],
    pool_cards: Iterable[Mapping[str, Any]] | None = None,
    queue_path: Path | str = DEFAULT_QUEUE_PATH,
    pool_path: Path | str = DEFAULT_POOL_PATH,
) -> list[dict[str, Any]]:
    """Contrast every ``source == "llm"`` entry against the regex tagger.

    Rewrites ``queue_path`` (JSONL, sorted by name) with one entry per
    disagreement: oracle_id, name, llm_labels, regex_labels and the label
    ``diff`` (``only_llm`` / ``only_regex``). Returns the discrepancy list.
    LLM entries whose card is missing from the pool are reported with a
    warning (they cannot be audited) but do not abort the run.
    """
    regex_module = load_regex_tagger()
    if pool_cards is None:
        pool_cards = _iter_pool_cards(Path(pool_path))

    llm_entries = {oid: e for oid, e in store.items() if e.source == "llm"}
    cards_by_id = {
        card["oracle_id"]: card
        for card in pool_cards
        if card.get("oracle_id") in llm_entries
    }

    missing = sorted(
        e.name for oid, e in llm_entries.items() if oid not in cards_by_id
    )
    if missing:
        logger.warning(
            "%d LLM-tagged cards missing from the pool, not auditable: %s",
            len(missing), missing[:10],
        )

    discrepancies: list[dict[str, Any]] = []
    for oracle_id, entry in llm_entries.items():
        card = cards_by_id.get(oracle_id)
        if card is None:
            continue
        regex_labels = regex_labels_for(card, regex_module)
        if list(entry.labels) == regex_labels:
            continue
        llm_set, regex_set = set(entry.labels), set(regex_labels)
        discrepancies.append(
            {
                "oracle_id": oracle_id,
                "name": entry.name,
                "llm_labels": list(entry.labels),
                "regex_labels": regex_labels,
                "diff": {
                    "only_llm": [c for c in CATEGORIES if c in llm_set - regex_set],
                    "only_regex": [c for c in CATEGORIES if c in regex_set - llm_set],
                },
            }
        )

    discrepancies.sort(key=lambda d: (d["name"], d["oracle_id"]))
    queue_path = Path(queue_path)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with queue_path.open("w", encoding="utf-8", newline="\n") as fh:
        for item in discrepancies:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    logger.info(
        "Audited %d LLM entries: %d discrepancies -> %s",
        len(llm_entries), len(discrepancies), queue_path,
    )
    return discrepancies


def build_audit_report(
    queue_path: Path | str = DEFAULT_QUEUE_PATH,
) -> dict[str, Any]:
    """Summarize the audit queue: totals and per-category disagreement counts.

    ``by_category[cat]["only_llm"]`` counts cards where the LLM added ``cat``
    and the regex did not; ``"only_regex"`` the reverse.
    """
    queue_path = Path(queue_path)
    if not queue_path.is_file():
        raise AuditError(f"audit queue not found: {queue_path} (run run_audit first)")
    by_category: dict[str, dict[str, int]] = {
        c: {"only_llm": 0, "only_regex": 0} for c in CATEGORIES
    }
    total = 0
    with queue_path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                diff = item["diff"]
                only_llm, only_regex = diff["only_llm"], diff["only_regex"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise AuditError(f"invalid audit entry at {queue_path}:{line_no}: {exc}") from exc
            total += 1
            for cat in only_llm:
                by_category[cat]["only_llm"] += 1
            for cat in only_regex:
                by_category[cat]["only_regex"] += 1
    return {
        "total_discrepancies": total,
        "by_category": {
            c: counts for c, counts in by_category.items()
            if counts["only_llm"] or counts["only_regex"]
        },
    }
