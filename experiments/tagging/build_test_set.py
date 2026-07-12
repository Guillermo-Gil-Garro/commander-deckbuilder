"""Build the hand-labeling test set proposal for the Phase 2 tagging experiments.

Samples ~200 cards from the processed pool, stratified by functional category
using intentionally-simple regex heuristics. The heuristics are ONLY a sampling
aid (coverage + a suggested label to correct); they are NOT one of the tagging
methods under evaluation.

Deterministic: fixed seed, stable iteration order. Run twice -> identical CSV.

Usage:
    python build_test_set.py
"""

from __future__ import annotations

import csv
import json
import logging
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEED = 20260712
ROOT = Path(__file__).resolve().parents[2]
POOL_PATH = ROOT / "data" / "processed" / "cards.jsonl"
BANLIST_PATH = ROOT / "banlist.yaml"
OUT_PATH = Path(__file__).with_name("test_set_proposal.csv")

# Stratum -> target size (~200 total).
TARGETS: dict[str, int] = {
    "ramp": 30,
    "card_draw": 30,
    "removal": 30,
    "board_wipe": 15,
    "wincons": 15,
    "synergy": 15,
    "none": 25,
    "edge": 20,
    "random": 20,
}
TOTAL_TARGET = sum(TARGETS.values())  # 200
# Acceptance: no single color (by color_identity membership) may exceed 35%.
COLOR_CAP = int(TOTAL_TARGET * 0.35)  # 70

CANON_ORDER = ["lands", "ramp", "card_draw", "removal", "board_wipe", "wincons", "synergy", "none"]

# --------------------------------------------------------------------------
# Sampling heuristics (regex over lowercased oracle_text / type_line).
# Deliberately crude: they only need to pull varied candidates per category.
# --------------------------------------------------------------------------
RX_RAMP = [
    re.compile(r"\{t\}: add "),
    re.compile(r"\badds? (one|two|three|x) mana\b"),
    re.compile(r"search your library for (a|an|up to \w+)[^.]{0,60}land[^.]{0,80}battlefield"),
    re.compile(r"put (a|up to \w+) land cards? from your hand onto the battlefield"),
]
RX_DRAW = [
    re.compile(r"\bdraws? (a card|an additional card|two|three|four|five|x|that many|cards)\b"),
]
RX_REMOVAL = [
    re.compile(r"\bdestroy target\b"),
    re.compile(r"\bexile target\b"),
    re.compile(r"\bcounter target\b"),
    re.compile(r"deals? \d+ damage to (any target|target)"),
    re.compile(r"deals? x damage to (any target|target)"),
    re.compile(r"target creature gets -\d"),
    re.compile(r"\bfights? (target|up to|another target)"),
]
RX_WIPE = [
    re.compile(r"\bdestroy all\b"),
    re.compile(r"\bexile all\b"),
    re.compile(r"deals? (\d+|x) damage to each creature"),
    re.compile(r"\ball creatures get -\d"),
    re.compile(r"\beach (player|opponent) sacrifices\b"),
    re.compile(r"\breturn all\b"),
]
RX_WINCON = [
    re.compile(r"\bwins? the game\b"),
    re.compile(r"\bloses? the game\b"),
]
# "Obvious package" cards for the synergy stratum: tribal lords and the like.
RX_SYNERGY = [
    re.compile(r"other [a-z]+s you control get \+\d+/\+\d+"),
    re.compile(r"other [a-z]+ creatures you control get \+\d+/\+\d+"),
    re.compile(r"creatures you control of the chosen type"),
    re.compile(r"[a-z]+ spells you cast cost \{\d+\} less"),
]

RX_LAND_TYPE = re.compile(r"\bland\b")

EDGE_LAYOUTS = {"modal_dfc", "transform", "adventure", "split", "flip"}


def suggest_labels(card: dict[str, Any]) -> list[str]:
    """Heuristic suggested labels (sampling aid only), in canonical order."""
    text = (card.get("oracle_text") or "").lower()
    type_line = (card.get("type_line") or "").lower()
    labels: list[str] = []
    is_land = bool(RX_LAND_TYPE.search(type_line))
    if is_land:
        labels.append("lands")
    if not is_land and any(rx.search(text) for rx in RX_RAMP):
        labels.append("ramp")
    if any(rx.search(text) for rx in RX_DRAW):
        labels.append("card_draw")
    # A card matching a wipe pattern is sampled as wipe, not spot removal.
    wipe = any(rx.search(text) for rx in RX_WIPE)
    if wipe:
        labels.append("board_wipe")
    elif any(rx.search(text) for rx in RX_REMOVAL):
        labels.append("removal")
    if any(rx.search(text) for rx in RX_WINCON):
        labels.append("wincons")
    if any(rx.search(text) for rx in RX_SYNERGY):
        labels.append("synergy")
    if not labels:
        labels.append("none")
    return labels


def edge_kind(card: dict[str, Any]) -> str | None:
    """Classify deliberate edge cases; returns a kind key or None."""
    text = (card.get("oracle_text") or "").lower()
    type_line = card.get("type_line") or ""
    tl = type_line.lower()
    mana_cost = card.get("mana_cost") or ""
    labels = suggest_labels(card)

    if card.get("layout") in EDGE_LAYOUTS:
        return "mdfc_modal"
    if "creature" in tl and re.search(r"\{t\}: add ", text):
        return "mana_dork"
    if "card_draw" in labels and ("removal" in labels or "board_wipe" in labels):
        return "cantrip_removal"
    if "board_wipe" in labels and ("you don't control" in text or "each opponent" in text):
        return "unilateral_wipe"
    if RX_LAND_TYPE.search(tl) and (
        "saga" in tl
        or "creature" in tl
        or "search your library" in text
        or ("sacrifice" in text and "add" not in text)
    ):
        return "weird_land"
    if "{x}" in mana_cost.lower() and labels != ["none"]:
        return "x_spell"
    return None


def load_banned_names() -> set[str]:
    """Names banned by the group: manual `cards` plus resolved programmatic bans."""
    data = yaml.safe_load(BANLIST_PATH.read_text(encoding="utf-8"))
    banned: set[str] = set()
    for entry in data.get("cards", []):
        banned.add(entry["name"])
    for rule in data.get("rules", []):
        if rule.get("status", "").startswith("banned"):
            banned.update(rule.get("resolved_cards") or [])
    return banned


def is_banned(card: dict[str, Any], banned: set[str]) -> bool:
    name = card["name"]
    if name in banned:
        return True
    if " // " in name:
        return any(face in banned for face in name.split(" // "))
    return False


def color_bucket(card: dict[str, Any]) -> str:
    ci = card.get("color_identity") or []
    if not ci:
        return "C"
    if len(ci) == 1:
        return ci[0]
    return "M"


class Picker:
    """Greedy stratified picker with a global per-color cap for balance."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.picked: dict[str, dict[str, Any]] = {}  # name -> card
        self.stratum_of: dict[str, str] = {}
        self.color_counts: Counter[str] = Counter()

    def _fits_cap(self, card: dict[str, Any]) -> bool:
        return all(self.color_counts[c] < COLOR_CAP for c in card.get("color_identity") or [])

    def _take(self, card: dict[str, Any], stratum: str) -> None:
        self.picked[card["name"]] = card
        self.stratum_of[card["name"]] = stratum
        self.color_counts.update(card.get("color_identity") or [])

    def pick(self, stratum: str, candidates: list[dict[str, Any]], n: int) -> int:
        """Round-robin over color buckets so no stratum comes out mono-color."""
        buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in ["W", "U", "B", "R", "G", "M", "C"]}
        for card in candidates:
            if card["name"] not in self.picked:
                buckets[color_bucket(card)].append(card)
        for lst in buckets.values():
            lst.sort(key=lambda c: c["name"])  # stable base order before shuffle
            self.rng.shuffle(lst)
        taken = 0
        order = ["W", "U", "B", "R", "G", "M", "C"]
        while taken < n and any(buckets[k] for k in order):
            for key in order:
                if taken >= n:
                    break
                while buckets[key]:
                    card = buckets[key].pop()
                    if card["name"] in self.picked or not self._fits_cap(card):
                        continue
                    self._take(card, stratum)
                    taken += 1
                    break
        return taken


def main() -> None:
    rng = random.Random(SEED)
    banned = load_banned_names()

    pool: list[dict[str, Any]] = []
    with POOL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            card = json.loads(line)
            type_line = card.get("type_line") or ""
            # Stickers/Attractions pass the legality filter upstream but are
            # not deck cards; no point spending labeling budget on them.
            if type_line.startswith("Stickers") or "Attraction" in type_line:
                continue
            if not is_banned(card, banned):
                pool.append(card)
    log.info("pool: %d cards after banlist exclusion (%d banned names)", len(pool), len(banned))

    # Precompute labels once.
    labels_of = {c["name"]: suggest_labels(c) for c in pool}

    def cands(category: str) -> list[dict[str, Any]]:
        return [c for c in pool if category in labels_of[c["name"]] and "lands" not in labels_of[c["name"]]]

    picker = Picker(rng)

    # Edge cases first: they are the scarcest and most deliberate picks.
    edge_pool: dict[str, list[dict[str, Any]]] = {}
    for card in pool:
        kind = edge_kind(card)
        if kind:
            edge_pool.setdefault(kind, []).append(card)
    edge_kinds = sorted(edge_pool)
    per_kind = max(1, TARGETS["edge"] // len(edge_kinds)) if edge_kinds else 0
    edge_taken = 0
    for kind in edge_kinds:
        want = min(per_kind, TARGETS["edge"] - edge_taken)
        got = picker.pick("edge", edge_pool[kind], want)
        edge_taken += got
        log.info("edge/%s: %d (candidates %d)", kind, got, len(edge_pool[kind]))
    # top up edge from all edge candidates if the even split fell short
    if edge_taken < TARGETS["edge"]:
        all_edge = [c for lst in edge_pool.values() for c in lst]
        edge_taken += picker.pick("edge", all_edge, TARGETS["edge"] - edge_taken)

    for category in ["ramp", "card_draw", "removal", "board_wipe", "wincons", "synergy"]:
        got = picker.pick(category, cands(category), TARGETS[category])
        if got < TARGETS[category]:
            log.warning("stratum %s short: %d/%d", category, got, TARGETS[category])

    none_cands = [
        c
        for c in pool
        if labels_of[c["name"]] == ["none"] and (c.get("oracle_text") or "").strip()
    ]
    picker.pick("none", none_cands, TARGETS["none"])

    picker.pick("random", pool, TARGETS["random"])

    total = len(picker.picked)
    if not 195 <= total <= 210:
        raise SystemExit(f"total {total} outside acceptance range 195-210")

    def sort_key(card: dict[str, Any]) -> tuple[int, str]:
        return (CANON_ORDER.index(labels_of[card["name"]][0]), card["name"])

    rows = sorted(picker.picked.values(), key=sort_key)
    with OUT_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["name", "type_line", "mana_cost", "suggested_labels", "final_labels", "notes"])
        for card in rows:
            writer.writerow(
                [
                    card["name"],
                    card.get("type_line") or "",
                    card.get("mana_cost") or "",
                    "|".join(labels_of[card["name"]]),
                    "",
                    "",
                ]
            )

    # Summary for review.
    strata = Counter(picker.stratum_of.values())
    sugg = Counter(labels_of[n][0] for n in picker.picked)
    log.info("written %s (%d rows)", OUT_PATH, total)
    edge_names = sorted(n for n, s in picker.stratum_of.items() if s == "edge")
    log.info("edge picks: %s", ", ".join(edge_names))
    log.info("strata: %s", dict(sorted(strata.items())))
    log.info("first suggested label: %s", dict(sorted(sugg.items())))
    log.info(
        "color identity counts (cap %d): %s | colorless: %d",
        COLOR_CAP,
        {c: picker.color_counts[c] for c in "WUBRG"},
        sum(1 for c in picker.picked.values() if not c.get("color_identity")),
    )


if __name__ == "__main__":
    main()
