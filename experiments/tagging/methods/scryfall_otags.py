"""Method 3 — functional tagging via Scryfall oracle tags (Scryfall Tagger project).

Scryfall search supports ``otag:<tag>`` (community-maintained oracle tags).
For each mapped otag we download every page of ``otag:X legal:commander``,
cache the card names, and intersect them with the 200-card test set.

Probe results (2026-07-12, one page-1 request per candidate, total_cards shown).
Existing tags:
    ramp (2101) == mana-ramp (alias)
    removal (6113)              -> too broad: includes mass removal; not mapped
    spot-removal (4648)         -> mapped to removal (exactly our concept,
                                   large because MTG has lots of removal)
    creature-removal (5219)     -> overlaps boardwipes; not mapped
    counterspell (513)          -> mapped to removal (interaction)
    burn (2860)                 -> not mapped: includes player-only burn
    draw (3964)                 -> mapped to card_draw
    card-advantage (5776)       -> too broad (recursion, tutors...); not mapped
    cantrip (596)               -> mapped to card_draw (recall safety net)
    boardwipe (896) == board-wipe == sweeper == mass-removal (aliases)
    win-condition (60) == alternate-win-condition (alias)
    extra-turn (53), extra-combat (44), overrun (63), damage-multiplier (46)
                                -> mapped to wincons (small but precise)
    tutor (1097)                -> exists but has no category in our schema;
                                   not mapped (tutors are consistency, not draw)
Nonexistent tags (search returns HTTP 404 "not_found"):
    targeted-removal, card-draw, wrath, wincon, game-winner, wins-the-game,
    win-the-game, finisher, game-ender, infinite-combo, combo-piece,
    wins-game, synergy

Category notes:
    lands   -- no otag needed: derived from the test set's type_line column
               (any face containing the Land type counts).
    synergy -- no reasonable otag exists (synergy is commander-relative, and
               oracle tags are card-intrinsic). This method cannot predict
               synergy; that limitation is part of the experiment's result.

Usage (from repo root):
    backend/.venv/Scripts/python.exe experiments/tagging/methods/scryfall_otags.py

First run downloads ~80 pages into experiments/tagging/cache/scryfall_otags/;
subsequent runs make zero network requests.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("scryfall_otags")

EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
TEST_SET_CSV = EXPERIMENT_DIR / "test_set_proposal.csv"
CACHE_DIR = EXPERIMENT_DIR / "cache" / "scryfall_otags"
PREDICTIONS_PATH = EXPERIMENT_DIR / "predictions" / "scryfall.json"

SEARCH_URL = "https://api.scryfall.com/cards/search"
HEADERS = {
    "User-Agent": "commander-deckbuilder/0.1",
    "Accept": "application/json",
}
SLEEP_SECONDS = 0.13  # Scryfall asks for 50-100ms between requests; be polite

CATEGORIES = ("lands", "ramp", "card_draw", "removal", "board_wipe", "wincons", "synergy")

# otag -> our category. Several otags may feed the same category (union).
OTAG_TO_CATEGORY: dict[str, str] = {
    "ramp": "ramp",
    "spot-removal": "removal",
    "counterspell": "removal",
    "draw": "card_draw",
    "cantrip": "card_draw",
    "boardwipe": "board_wipe",
    "win-condition": "wincons",
    "extra-turn": "wincons",
    "extra-combat": "wincons",
    "overrun": "wincons",
    "damage-multiplier": "wincons",
}


def fetch_otag_names(client: httpx.Client, tag: str) -> list[str]:
    """Download all pages of ``otag:<tag> legal:commander`` and return card names.

    Results are cached in CACHE_DIR/<tag>.json; a cached tag costs zero requests.
    A nonexistent otag makes Scryfall return HTTP 404 (object=error, code=not_found).
    """
    cache_path = CACHE_DIR / f"{tag}.json"
    if cache_path.exists():
        names = json.loads(cache_path.read_text(encoding="utf-8"))
        log.info("otag:%s -> %d names (cache)", tag, len(names))
        return names

    names: list[str] = []
    url: str | None = SEARCH_URL
    params: dict[str, str] | None = {"q": f"otag:{tag} legal:commander"}
    pages = 0
    while url:
        resp = client.get(url, params=params)
        time.sleep(SLEEP_SECONDS)
        if resp.status_code == 404:
            # Either the otag does not exist or it matches zero commander-legal
            # cards; both mean "this tag yields nothing" -> fail loudly, a bad
            # tag name in the mapping table is a bug, not an empty result.
            raise ValueError(f"otag:{tag} matched no cards (does the tag exist?): {resp.json().get('details')}")
        resp.raise_for_status()
        data = resp.json()
        names.extend(card["name"] for card in data["data"])
        pages += 1
        url = data.get("next_page") if data.get("has_more") else None
        params = None  # next_page already carries the full query string

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("otag:%s -> %d names in %d pages (network)", tag, len(names), pages)
    return names


def load_test_set() -> list[dict[str, str]]:
    """Read name and type_line for the 200 test cards (final_labels is off-limits)."""
    with TEST_SET_CSV.open(encoding="utf-8", newline="") as fh:
        return [{"name": row["name"], "type_line": row["type_line"]} for row in csv.DictReader(fh)]


def build_membership(otag_names: dict[str, list[str]]) -> dict[str, set[str]]:
    """category -> set of matchable names (full 'A // B' names plus each face)."""
    membership: dict[str, set[str]] = {}
    for tag, category in OTAG_TO_CATEGORY.items():
        bucket = membership.setdefault(category, set())
        for name in otag_names[tag]:
            bucket.add(name)
            if " // " in name:
                bucket.update(name.split(" // "))
    return membership


def predict(cards: list[dict[str, str]], membership: dict[str, set[str]]) -> dict[str, list[str]]:
    predictions: dict[str, list[str]] = {}
    for card in cards:
        labels = [cat for cat in CATEGORIES if card["name"] in membership.get(cat, ())]
        # lands comes from the type line, not from an otag (any land face counts)
        if any("Land" in face for face in card["type_line"].split(" // ")):
            labels.insert(0, "lands")
        predictions[card["name"]] = labels
    return predictions


def main() -> None:
    cards = load_test_set()
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        otag_names = {tag: fetch_otag_names(client, tag) for tag in OTAG_TO_CATEGORY}

    predictions = predict(cards, build_membership(otag_names))

    PREDICTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREDICTIONS_PATH.write_text(
        json.dumps(predictions, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    counts = {cat: sum(cat in labels for labels in predictions.values()) for cat in CATEGORIES}
    unlabeled = sum(not labels for labels in predictions.values())
    log.info("wrote %s (%d cards)", PREDICTIONS_PATH, len(predictions))
    log.info("label counts: %s | unlabeled: %d", counts, unlabeled)


if __name__ == "__main__":
    main()
