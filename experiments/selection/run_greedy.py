"""Fase 3 — greedy selector smoke run: build real decks for the test commanders.

Builds a 99-card deck (plus maybeboard) for each test commander using only
local data (pool JSONL, EDHREC cache, otag cache) and writes one readable
decklist per commander to ``experiments/selection/decks/<slug>.txt`` so
Guille can eyeball the results.

Usage (from repo root):
    backend/.venv/Scripts/python.exe experiments/selection/run_greedy.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pipeline.edhrec import fetch_commander, slugify_commander  # noqa: E402
from quotas.config import load_quotas  # noqa: E402
from quotas.resolver import resolve_bands  # noqa: E402
from selector.deck_rules import (  # noqa: E402
    archetype_for,
    load_rules,
    validate_rules_names,
)
from selector.greedy import DECK_SIZE, GreedyResult, load_pool  # noqa: E402
from tags.store import load_tags, tagger_from_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("run_greedy")

POOL_PATH = REPO_ROOT / "data" / "processed" / "cards.jsonl"
BANLIST_PATH = REPO_ROOT / "banlist.yaml"
DECKS_DIR = Path(__file__).resolve().parent / "decks"

COMMANDERS = (
    "Krenko, Mob Boss",
    "Omnath, Locus of Creation",
    "Meren of Clan Nel Toth",
    "Niv-Mizzet, Parun",
    "Sythis, Harvest's Hand",
)

BANNED_STATUSES = ("banned", "banned_pending_review")

CATEGORY_ORDER = (
    "lands",
    "ramp",
    "card_draw",
    "removal",
    "board_wipe",
    "wincons",
    "synergy",
)


def load_banlist(path: Path) -> tuple[set[str], set[str]]:
    """(banned_names, watchlist_names) parsed directly from banlist.yaml.

    Prototype-level resolution by exact NAME only: ``cards`` entries plus the
    ``resolved_cards`` snapshots of programmatic rules (statuses banned /
    banned_pending_review), minus rule exceptions. ``banned_as_commander``
    stays legal in the 99. The formal resolver lives in backend/rules/ (other
    work stream) — this is deliberately NOT that.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    banned: set[str] = set()
    for entry in raw.get("cards", []):
        if entry.get("status") in BANNED_STATUSES:
            banned.add(entry["name"])
    for rule in raw.get("rules", []):
        if rule.get("status") not in BANNED_STATUSES:
            continue
        resolved = set(rule.get("resolved_cards", []))
        exceptions = {exc["name"] for exc in rule.get("exceptions", [])}
        banned |= resolved - exceptions
    watchlist = {entry["name"] for entry in raw.get("watchlist", [])}
    return banned, watchlist


def format_deck(result: GreedyResult, bands, build_seconds: float) -> str:
    lines: list[str] = []
    lines.append(f"# {result.commander_name} — selector greedy (prototipo Fase 3)")
    lines.append(f"# Construido en {build_seconds:.2f}s | mainboard: {result.total_cards} cartas")
    lines.append("")
    lines.append("## Resumen de cuotas")
    lines.append(
        f"{'categoría':<12} {'n':>3}  {'banda':<10} estado"
    )
    for category in CATEGORY_ORDER:
        band = bands[category]
        status = result.statuses[category].value
        extra = ""
        if category == "lands":
            extra = f"  (suelo Karsten: {result.karsten_floor}, objetivo: {result.lands_target})"
        lines.append(
            f"{category:<12} {result.counts.get(category, 0):>3}  "
            f"[{band.min:>2}-{band.max:>2}]    {status}{extra}"
        )
    lines.append("")
    lines.append("# Nota: una carta multicategoría cuenta en todas sus categorías,")
    lines.append("# por eso la suma de conteos puede superar 99.")
    lines.append("")

    lines.append(f"## Mainboard ({result.total_cards})")
    for category in CATEGORY_ORDER:
        entries = [e for e in result.mainboard if e.slot == category]
        if not entries:
            continue
        total = sum(e.count for e in entries)
        lines.append("")
        lines.append(f"### {category} ({total})")
        for entry in sorted(entries, key=lambda e: (-(e.score or -1), e.name)):
            prefix = f"{entry.count}x " if entry.count > 1 else "1x "
            score = f"{entry.score:.2f}" if entry.score is not None else " -- "
            cats = "/".join(entry.categories)
            lines.append(f"{prefix}{entry.name:<42} score {score}  [{cats}]  {entry.reason}")

    lines.append("")
    lines.append(f"## Maybeboard ({len(result.maybeboard)})")
    for entry in result.maybeboard:
        cats = "/".join(entry.categories)
        lines.append(f"1x {entry.name:<42} score {entry.score:.2f}  [{cats}]  {entry.reason}")
    lines.append("")
    lines.append(f"## Cartas nuevas (arranque en frío) ({len(result.new_cards)})")
    lines.append("# Lista 'New Cards' de EDHREC que no entró al mainboard; el score")
    lines.append("# EDHREC tarda meses en reflejarlas — el jugador ve y decide.")
    for entry in result.new_cards:
        cats = "/".join(entry.categories)
        lines.append(f"1x {entry.name:<42} score {entry.score:.2f}  [{cats}]  {entry.reason}")
    if result.unresolved:
        lines.append("")
        lines.append(
            f"# {len(result.unresolved)} recomendaciones EDHREC sin resolver en el pool "
            "(descartadas):"
        )
        for name in result.unresolved:
            lines.append(f"#   {name}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    from selector.greedy import build_deck_greedy

    pool = load_pool(POOL_PATH)
    config = load_quotas()
    banned, watchlist = load_banlist(BANLIST_PATH)
    rules = load_rules(valid_archetypes=set(config.archetypes))
    validate_rules_names(rules, pool.resolve)
    tagger = tagger_from_store(load_tags(), pool.cards())
    DECKS_DIR.mkdir(parents=True, exist_ok=True)

    log.info("pool: %d cartas | banlist: %d baneadas, %d watchlist", len(pool.by_name), len(banned), len(watchlist))

    for commander in COMMANDERS:
        # Guille decision 2026-07-14: metrics and candidates come from the
        # bracket-4 ("optimized") pages only, not the global aggregate.
        data = fetch_commander(commander, variant="optimized")
        bands = resolve_bands(config, commander)
        start = time.perf_counter()
        result = build_deck_greedy(
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
        elapsed = time.perf_counter() - start
        assert result.total_cards == DECK_SIZE
        out_path = DECKS_DIR / f"{slugify_commander(commander)}.txt"
        out_path.write_text(format_deck(result, bands, elapsed), encoding="utf-8")
        below = [c for c, s in result.statuses.items() if s.value == "below"]
        log.info(
            "%s: %d cartas, %.2fs, tierras %d (suelo %d), below=%s -> %s",
            commander,
            result.total_cards,
            elapsed,
            result.counts.get("lands", 0),
            result.karsten_floor,
            below or "ninguna",
            out_path.name,
        )


if __name__ == "__main__":
    main()
