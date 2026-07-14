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
from selector.cp_sat import build_deck_cpsat  # noqa: E402
from selector.deck_rules import (  # noqa: E402
    archetype_for,
    load_rules,
    validate_rules_names,
)
from selector.greedy import build_deck_greedy, load_pool  # noqa: E402
from tags.store import load_tags, tagger_from_store  # noqa: E402

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


def format_archidekt(result) -> str:
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
    rules = load_rules(valid_archetypes=set(config.archetypes))
    validate_rules_names(rules, pool.resolve)
    tagger = tagger_from_store(load_tags(), pool.cards())
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    builders = {"greedy": build_deck_greedy, "cpsat": build_deck_cpsat}
    for commander in COMMANDERS:
        # Guille decision 2026-07-14: bracket-4 ("optimized") pages only.
        data = fetch_commander(commander, variant="optimized")
        bands = resolve_bands(config, commander)
        for method, builder in builders.items():
            result = builder(
                commander,
                pool=pool,
                recommendations=data.recommendations,
                bands=bands,
                tagger=tagger,
                banned_names=banned,
                watchlist_names=watchlist,
                rules=rules,
                archetype=archetype_for(config, commander),
            )
            out_dir = OUT_DIR if method == "greedy" else OUT_DIR.parent / "archidekt_cpsat"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{slugify_commander(commander)}.txt"
            out_path.write_text(format_archidekt(result), encoding="utf-8")
            total = sum(e.count for e in result.mainboard)
            log.info("%s [%s]: %d cartas + comandante -> %s", commander, method, total, out_path.name)


if __name__ == "__main__":
    main()
