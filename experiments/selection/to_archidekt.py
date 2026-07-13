"""Export the greedy test decks in Archidekt import format.

Writes ``experiments/selection/decks/archidekt/<slug>.txt`` with one line per
card: ``<count>x <name> [<Category>]``, the commander under ``[Commander]``,
and the maybeboard as a ``# Sideboard`` section (Archidekt's import syntax).

Usage (from repo root):
    backend/.venv/Scripts/python.exe experiments/selection/to_archidekt.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.edhrec import fetch_commander, slugify_commander  # noqa: E402
from quotas.config import load_quotas  # noqa: E402
from quotas.resolver import resolve_bands  # noqa: E402
from selector.greedy import GreedyResult, build_deck_greedy, load_pool  # noqa: E402
from selector.provisional_tags import otag_tagger  # noqa: E402

from run_greedy import BANLIST_PATH, COMMANDERS, POOL_PATH, load_banlist  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("to_archidekt")

OUT_DIR = Path(__file__).resolve().parent / "decks" / "archidekt"

# Archidekt category shown per card (slot the selector assigned, not every tag).
CATEGORY_LABELS = {
    "lands": "Lands",
    "ramp": "Ramp",
    "card_draw": "Card Draw",
    "removal": "Removal",
    "board_wipe": "Board Wipe",
    "wincons": "Wincons",
    "synergy": "Synergy",
}


def format_archidekt(result: GreedyResult) -> str:
    lines = [f"1x {result.commander_name} [Commander]"]
    for entry in result.mainboard:
        label = CATEGORY_LABELS.get(entry.slot, entry.slot)
        lines.append(f"{entry.count}x {entry.name} [{label}]")
    lines.append("")
    lines.append("# Sideboard")
    for entry in result.maybeboard:
        lines.append(f"1x {entry.name}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    pool = load_pool(POOL_PATH)
    config = load_quotas()
    banned, watchlist = load_banlist(BANLIST_PATH)
    tagger = otag_tagger(pool.cards())
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for commander in COMMANDERS:
        data = fetch_commander(commander)
        result = build_deck_greedy(
            commander,
            pool=pool,
            recommendations=data.recommendations,
            bands=resolve_bands(config, commander),
            tagger=tagger,
            banned_names=banned,
            watchlist_names=watchlist,
        )
        out_path = OUT_DIR / f"{slugify_commander(commander)}.txt"
        out_path.write_text(format_archidekt(result), encoding="utf-8")
        total = sum(e.count for e in result.mainboard)
        log.info("%s: %d cartas + comandante -> %s", commander, total, out_path.name)


if __name__ == "__main__":
    main()
