"""Scan the tag store's cards for `stax` candidates (rubric v4).

The v4 rubric (2026-07-18) added the ``stax`` category (prison / resource
denial), but the cards already in ``data/tags/llm_tags.jsonl`` were labeled
under v2/v3, which had no such label. This script scans the oracle text of
every stored card (resolved against the processed pool by oracle_id) with
recall-oriented regexes for prison patterns — cost taxes, don't-untap locks,
one-spell-per-turn limits, can't-draw/can't-search denial, ETB/ability
shutdowns, mass mana denial, attack taxes (pillow fort), forced sacrifice
engines — and writes the candidates to ``batches/batch_stax.jsonl`` in the
standard batch format (oracle_id, name, mana_cost, type_line, oracle_text) for
a labeling session to re-label via ``tags.store.add_label``.

Deliberately generous: precision is the labeler's job, not this scanner's.
Counterspells, self-protection and one-shot removal WILL show up and must be
rejected by the labeler per the rubric.

NOTE: ``make_batches.py`` deletes every ``batches/batch_*.jsonl`` on rerun,
so regenerate this file (or merge its labels first) after running it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from tags.store import DEFAULT_POOL_PATH, is_land_card, load_tags  # noqa: E402

logger = logging.getLogger("find_stax_candidates")

BATCHES_DIR = Path(__file__).resolve().parent / "batches"
OUTPUT_PATH = BATCHES_DIR / "batch_stax.jsonl"

BATCH_FIELDS = ("oracle_id", "name", "mana_cost", "type_line", "oracle_text")

# Recall-oriented. Each pattern targets a family of prison effects from the
# rubric; a card firing any of them is a candidate for the labeler to judge.
# ``enters_tapped`` is symmetric/broad on purpose (catches Root Maze / Kismet):
# the own-tapland noise it would create is filtered out in ``find_candidates``
# by dropping cards whose only signal is enters_tapped and that are lands.
PATTERNS: dict[str, re.Pattern[str]] = {
    # Taxes: spells / abilities / actions cost more mana or life.
    "cost_more": re.compile(
        r"cost[s]?\b[^.\n]{0,60}?\b(?:more to cast|\{\d+\} more|"
        r"an additional)", re.IGNORECASE
    ),
    "cost_fixed_to_cast": re.compile(
        r"cost[s]?\b[^.\n]{0,60}?\b(?:three|four|\{\d+\}|\d+) (?:more )?mana to cast",
        re.IGNORECASE,
    ),
    "additional_cost_to_act": re.compile(
        r"as an additional cost to (?:cast|attack|activate)", re.IGNORECASE
    ),
    "unless_pays": re.compile(
        r"unless (?:that|its|their|the) (?:player|controller|owner)[^.\n]{0,20}?pay",
        re.IGNORECASE,
    ),
    "opp_cast_punish": re.compile(
        r"(?:whenever|when) (?:an|a) (?:opponent|player)[^.\n]{0,50}?cast[^.\n]{0,70}?"
        r"(?:loses? \d+ life|sacrifices?|mills?|pays?|that player discards)",
        re.IGNORECASE,
    ),
    # Don't-untap / stasis locks.
    "dont_untap": re.compile(r"(?:don't|doesn't|do not|does not) untap", re.IGNORECASE),
    "cant_untap": re.compile(r"can(?:'|no)t untap", re.IGNORECASE),
    "untap_only": re.compile(r"untap[s]?\b[^.\n]{0,40}?\bonly\b", re.IGNORECASE),
    "skip_untap": re.compile(r"skip[s]?\b[^.\n]{0,20}?untap step", re.IGNORECASE),
    # Enters tapped locks — broad; own-tapland noise filtered in code.
    "enters_tapped": re.compile(
        r"(?:artifacts|creatures|lands|permanents|nonland permanents|"
        r"opponents? control|you don't control|each opponent)"
        r"[^.\n]{0,60}?enter(?:s|ing)?(?: the battlefield)? tapped",
        re.IGNORECASE,
    ),
    # One action per turn limits / sorcery speed.
    "one_per_turn": re.compile(
        r"(?:can't cast more than|only one|no more than one|can't cast|"
        r"more than one spell|one spell each turn|"
        r"can only cast (?:one|sorceries|creature))", re.IGNORECASE
    ),
    "cast_during_own": re.compile(
        r"can(?:'|no)t cast (?:spells|creature|noncreature)", re.IGNORECASE
    ),
    "sorcery_speed": re.compile(
        r"cast (?:spells |noncreature spells )?only(?: any time)?[^.\n]{0,30}?"
        r"(?:sorcery|could cast a sorcery)|"
        r"only (?:cast|activate)[^.\n]{0,40}?(?:sorcery|any time you could)",
        re.IGNORECASE,
    ),
    # Draw / search denial.
    "cant_draw": re.compile(r"can(?:'|no)t draw", re.IGNORECASE),
    "skip_draw": re.compile(r"skip[s]?\b[^.\n]{0,30}?draw step", re.IGNORECASE),
    "cant_search": re.compile(r"can(?:'|no)t search", re.IGNORECASE),
    "search_hate": re.compile(
        r"(?:while|whenever)[^.\n]{0,40}?search[^.\n]{0,40}?librar[^.\n]{0,60}?"
        r"(?:exile|instead|can't|control)", re.IGNORECASE
    ),
    "search_would": re.compile(
        r"(?:if|whenever)[^.\n]{0,40}?would search[^.\n]{0,20}?(?:a |their )?librar",
        re.IGNORECASE,
    ),
    "extra_draw_denial": re.compile(
        r"if (?:a|an opponent|each) player would draw", re.IGNORECASE
    ),
    # ETB / ability shutdowns.
    "abilities_cant": re.compile(
        r"(?:abilities|activated abilities)[^.\n]{0,40}?can(?:'|no)t be activated",
        re.IGNORECASE,
    ),
    "cant_activate": re.compile(r"can(?:'|no)t activate", re.IGNORECASE),
    "etb_no_trigger": re.compile(
        r"enter(?:s|ing)?\b[^.\n]{0,40}?(?:abilities don't trigger|don't cause)",
        re.IGNORECASE,
    ),
    # Attack / block taxes (pillow fort).
    "attack_tax": re.compile(
        r"can(?:'|no)t attack(?: you| or planeswalkers you control)?"
        r"[^.\n]{0,30}?unless", re.IGNORECASE
    ),
    "cant_attack_block": re.compile(
        r"can(?:'|no)t (?:attack|block)(?:\b| you)", re.IGNORECASE
    ),
    "one_attacker": re.compile(
        r"can(?:'|no)t attack[^.\n]{0,40}?more than one|no more than one creature "
        r"can attack", re.IGNORECASE
    ),
    # Mass mana denial / land locks.
    "moon": re.compile(r"are (?:Mountains|Swamps|Islands|Forests|Plains)\b", re.IGNORECASE),
    "produces_instead": re.compile(
        r"tapped for mana[^.\n]{0,40}?produces?[^.\n]{0,20}?instead", re.IGNORECASE
    ),
    "destroy_all_lands": re.compile(
        r"destroy all (?:lands|nonbasic lands)", re.IGNORECASE
    ),
    # Forced sacrifice engines.
    "each_player_sacrifices": re.compile(
        r"(?:each|that) (?:player|opponent) sacrifices", re.IGNORECASE
    ),
    "sac_a_permanent": re.compile(
        r"sacrifices (?:a|one|that many) permanent", re.IGNORECASE
    ),
    # Generic prison verb catch-alls.
    "players_cant": re.compile(
        r"(?:players|opponents|each opponent|your opponents) (?:can(?:'|no)t|skip)",
        re.IGNORECASE,
    ),
    "cant_gain_life": re.compile(r"can(?:'|no)t gain life", re.IGNORECASE),
}


def find_candidates() -> tuple[list[dict[str, str]], dict[str, int]]:
    """Stored cards whose oracle text fires any stax pattern.

    Returns the candidate batch rows (sorted by name) and per-pattern hit
    counts (a card can fire several patterns).
    """
    store = load_tags()
    if not store:
        raise SystemExit("tag store is empty: nothing to scan")
    stored_ids = set(store)

    candidates: list[dict[str, str]] = []
    hits_by_pattern: dict[str, int] = {name: 0 for name in PATTERNS}
    seen_ids: set[str] = set()
    with DEFAULT_POOL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            card = json.loads(line)
            oracle_id = card.get("oracle_id")
            if oracle_id not in stored_ids:
                continue
            seen_ids.add(oracle_id)
            text = card.get("oracle_text") or ""
            fired = [name for name, rx in PATTERNS.items() if rx.search(text)]
            if not fired:
                continue
            # Own taplands/duals fire only enters_tapped and are NOT stax; drop
            # them so the symmetric enters_tapped pattern keeps its recall on
            # real prison pieces (Root Maze, Kismet) without 300 land false hits.
            if fired == ["enters_tapped"] and is_land_card(card):
                continue
            for name in fired:
                hits_by_pattern[name] += 1
            candidates.append({field: card.get(field) or "" for field in BATCH_FIELDS})

    missing = len(stored_ids) - len(seen_ids)
    if missing:
        logger.warning("%d stored oracle_ids not found in the pool (not scanned)", missing)
    candidates.sort(key=lambda c: (c["name"], c["oracle_id"]))
    return candidates, hits_by_pattern


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    candidates, hits_by_pattern = find_candidates()

    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        for card in candidates:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")

    for name, count in sorted(hits_by_pattern.items(), key=lambda kv: -kv[1]):
        logger.info("  %-24s %4d", name, count)
    logger.info("Wrote %d stax candidates to %s", len(candidates), OUTPUT_PATH)


if __name__ == "__main__":
    main()
