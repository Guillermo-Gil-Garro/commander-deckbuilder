"""Method 2 — functional tagging from EDHREC commander-page category headers.

A card gets labeled by aggregating the cardlist headers it appears under
across the 55 featured commander pages (``featured_commanders.yaml``). Raw
pages are fetched via ``pipeline.edhrec.fetch_commander`` (cached in
``data/cache/edhrec/``; second run performs zero network requests).

Header -> label mapping (decided after inspecting the real headers across all
55 pages; EDHREC commander pages group recommendations by CARD TYPE, not by
function, so most of our target labels have no direct source header):

    Mapped:
      "Lands"              -> lands   (basic/color-fixing land slots)
      "Utility Lands"      -> lands   (nonbasic utility land slots)
      "Mana Artifacts"     -> ramp    (mana rocks; the only ramp signal —
                                       dorks and land-ramp sorceries are
                                       listed under Creatures/Sorceries and
                                       are therefore invisible to this method)
      "High Synergy Cards" -> synergy (see note below)

    Discarded (documented in DISCARDED_HEADERS):
      "New Cards"      — recency list, not functional
      "Top Cards"      — popularity list, not functional
      "Game Changers"  — WotC bracket power list (tutors, staples...), not a
                         functional category and NOT equivalent to wincons
      "Creatures", "Instants", "Sorceries", "Enchantments",
      "Planeswalkers", "Battles", "Utility Artifacts" — card-type buckets

    Expected but ABSENT on commander pages (they exist only on EDHREC theme /
    recs pages, which this method does not consume): "Mana Ramp", "Card Draw",
    "Removal", "Counterspells", "Board Wipes", "Finishers". Consequence: this
    method structurally cannot emit card_draw / removal / board_wipe / wincons.

Vote aggregation: a card receives a label if it appears under a mapped header
on >= VOTE_THRESHOLD (1) distinct commander pages. One page contributes at
most one vote per (card, label). Rationale for N=1: the mapped headers are
INTRINSIC classifications on EDHREC — a card never appears under "Lands" /
"Utility Lands" without being a land, nor under "Mana Artifacts" without
being a mana rock — so cross-page confirmation adds no information and only
costs recall for color-restricted cards that match few of the 55 pages
(measured on this run, N=2 dropped true lands like Flagstones of Trokair).

"High Synergy Cards" is the one commander-RELATIVE header (synergy score vs.
baseline inclusion, top ~10 per page). Decision: also N=1 — the list is
already highly selective, and requiring the same package card to be top-10
synergy for two different featured commanders zeroes out the label entirely
(0/200 at N=2 on this run vs. 4/200 at N=1, e.g. Lord of the Accursed via
Wilhelt — exactly our "package card" definition). Tradeoff, noted for the
report: a single-page synergy hit may be specific to that commander rather
than a package card in general; the ground-truth comparison will measure it.

Run (from repo root):
    backend/.venv/Scripts/python experiments/tagging/methods/edhrec_tagger.py
"""

from __future__ import annotations

import csv
import json
import logging
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pipeline.edhrec import (  # noqa: E402
    CACHE_DIR,
    EdhrecError,
    fetch_commander,
    slugify_commander,
)

logger = logging.getLogger(__name__)

FEATURED_YAML = REPO_ROOT / "featured_commanders.yaml"
TEST_SET_CSV = REPO_ROOT / "experiments" / "tagging" / "test_set_proposal.csv"
PREDICTIONS_DIR = REPO_ROOT / "experiments" / "tagging" / "predictions"

REQUEST_SLEEP_SECONDS = 0.5
VOTE_THRESHOLD = 1

HEADER_TO_LABEL: dict[str, str] = {
    "Lands": "lands",
    "Utility Lands": "lands",
    "Mana Artifacts": "ramp",
    "High Synergy Cards": "synergy",
}

# Headers seen across the 55 pages that intentionally map to nothing.
DISCARDED_HEADERS: frozenset[str] = frozenset(
    {
        "New Cards",
        "Top Cards",
        "Game Changers",
        "Creatures",
        "Instants",
        "Sorceries",
        "Enchantments",
        "Planeswalkers",
        "Battles",
        "Utility Artifacts",
    }
)

LABEL_ORDER = ["lands", "ramp", "card_draw", "removal", "board_wipe", "wincons", "synergy"]


def load_featured_commanders(path: Path = FEATURED_YAML) -> list[str]:
    """Parse the flat ``featured:`` list without requiring a YAML dependency."""
    names: list[str] = []
    in_featured = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.strip() == "featured:":
            in_featured = True
            continue
        if in_featured:
            stripped = line.strip()
            if stripped.startswith("- "):
                names.append(stripped[2:].strip())
            else:
                break
    if not names:
        raise RuntimeError(f"No featured commanders parsed from {path}")
    return names


def load_test_card_names(path: Path = TEST_SET_CSV) -> list[str]:
    """Read only the ``name`` column of the test set (labels stay unread)."""
    with path.open(encoding="utf-8", newline="") as fh:
        return [row["name"] for row in csv.DictReader(fh)]


def normalize_name(name: str) -> str:
    """Matching key: ascii-folded, casefolded, front face only for DFC/split.

    EDHREC sometimes lists double-faced cards by front face while Scryfall
    uses "Front // Back"; matching on the front face reconciles both.
    """
    front = name.split(" // ")[0].strip()
    folded = unicodedata.normalize("NFKD", front).encode("ascii", "ignore").decode("ascii")
    return folded.casefold()


def fetch_all_pages(commander_names: list[str]) -> tuple[list, list[tuple[str, str]]]:
    """Fetch every featured commander page, sleeping only on real downloads."""
    pages = []
    failures: list[tuple[str, str]] = []
    network_requests = 0
    for name in commander_names:
        cached = (CACHE_DIR / f"{slugify_commander(name)}.json").exists()
        try:
            pages.append(fetch_commander(name))
        except EdhrecError as exc:
            logger.warning("Skipping commander '%s': %s", name, exc)
            failures.append((name, str(exc)))
        finally:
            if not cached:
                network_requests += 1
                time.sleep(REQUEST_SLEEP_SECONDS)
    logger.info(
        "Fetched %d/%d commander pages (%d network requests this run)",
        len(pages),
        len(commander_names),
        network_requests,
    )
    print(f"network requests this run: {network_requests}")
    return pages, failures


def build_votes(pages) -> tuple[dict[str, dict[str, int]], set[str], Counter]:
    """Count, per normalized card name, on how many pages each label applies.

    Returns (votes, seen_names, header_counter). ``seen_names`` holds every
    normalized recommendation name across all pages regardless of header.
    """
    votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seen: set[str] = set()
    header_counter: Counter = Counter()
    for page in pages:
        for rec in page.recommendations:
            key = normalize_name(rec.name)
            seen.add(key)
            header_counter.update(rec.categories)
            page_labels = {
                HEADER_TO_LABEL[header]
                for header in rec.categories
                if header in HEADER_TO_LABEL
            }
            for label in page_labels:
                votes[key][label] += 1
    return votes, seen, header_counter


def predict(
    test_names: list[str],
    votes: dict[str, dict[str, int]],
    seen: set[str],
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    predictions: dict[str, list[str]] = {}
    seen_cards: list[str] = []
    unseen_cards: list[str] = []
    for name in test_names:
        key = normalize_name(name)
        if key in seen:
            seen_cards.append(name)
        else:
            unseen_cards.append(name)
        labels = [
            label
            for label in LABEL_ORDER
            if votes.get(key, {}).get(label, 0) >= VOTE_THRESHOLD
        ]
        predictions[name] = labels
    return predictions, seen_cards, unseen_cards


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    commanders = load_featured_commanders()
    test_names = load_test_card_names()
    pages, failures = fetch_all_pages(commanders)
    votes, seen, header_counter = build_votes(pages)
    predictions, seen_cards, unseen_cards = predict(test_names, votes, seen)

    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    predictions_path = PREDICTIONS_DIR / "edhrec.json"
    predictions_path.write_text(
        json.dumps(predictions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    coverage_path = PREDICTIONS_DIR / "edhrec_coverage.json"
    coverage_path.write_text(
        json.dumps({"seen": seen_cards, "unseen": unseen_cards}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    unknown_headers = set(header_counter) - set(HEADER_TO_LABEL) - DISCARDED_HEADERS
    label_counts = Counter(label for labels in predictions.values() for label in labels)
    print(f"commanders fetched: {len(pages)}/{len(commanders)}")
    for name, error in failures:
        print(f"  FAILED {name}: {error}")
    print(f"headers observed: {dict(header_counter.most_common())}")
    if unknown_headers:
        print(f"WARNING unmapped/undocumented headers: {sorted(unknown_headers)}")
    print(f"coverage: {len(seen_cards)} seen / {len(unseen_cards)} unseen of {len(test_names)}")
    print(f"label counts: {dict(label_counts)}")
    empty = sum(1 for labels in predictions.values() if not labels)
    print(f"cards with no label: {empty}")
    print(f"wrote {predictions_path}")
    print(f"wrote {coverage_path}")


if __name__ == "__main__":
    main()
