"""Fase 3 — CP-SAT selector smoke run: same 5 commanders as run_greedy.py.

Builds a 99-card deck (plus maybeboard) per test commander with the CP-SAT
selector (TFM port, simplified) using only local data, and writes one
decklist per commander to ``experiments/selection/decks_cpsat/<slug>.txt``
with the SAME layout as the greedy decks for side-by-side comparison, plus
solver status, solve time and the relaxation stage used (if any).

Usage (from repo root):
    backend/.venv/Scripts/python.exe experiments/selection/run_cpsat.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.edhrec import fetch_commander, slugify_commander  # noqa: E402
from quotas.config import load_quotas  # noqa: E402
from quotas.resolver import resolve_bands  # noqa: E402
from selector.cp_sat import CpSatResult, build_deck_cpsat  # noqa: E402
from selector.greedy import DECK_SIZE, load_pool  # noqa: E402
from tags.store import load_tags, tagger_from_store  # noqa: E402

# Reuse the greedy runner's constants and banlist parsing (read-only import).
from run_greedy import BANLIST_PATH, CATEGORY_ORDER, COMMANDERS, POOL_PATH, load_banlist  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("run_cpsat")

DECKS_DIR = Path(__file__).resolve().parent / "decks_cpsat"

TIME_LIMIT_S = 10.0


def format_deck(result: CpSatResult, bands, build_seconds: float) -> str:
    lines: list[str] = []
    lines.append(f"# {result.commander_name} — selector CP-SAT (prototipo Fase 3)")
    lines.append(
        f"# Construido en {build_seconds:.2f}s | mainboard: {result.total_cards} cartas"
    )
    lines.append(
        f"# Solver: status {result.solver_status}, {result.solve_time_s:.2f}s de resolución, "
        f"etapa de relajación: {result.relaxation_stage}"
    )
    lines.append(
        f"# Objetivo: {result.objective_value:.4f} (score bruto {result.raw_score_sum:.4f}, "
        f"penalizaciones escaladas {result.penalties.get('total_scaled', 0)})"
    )
    color_rows = result.penalties.get("color_sources", {})
    if color_rows:
        fixing = ", ".join(
            f"{color}: {row['sources']}/{row['target']}"
            + (f" (déficit {row['deficit']})" if row["deficit"] else "")
            for color, row in color_rows.items()
        )
        lines.append(f"# Fixing color (fuentes/objetivo): {fixing}")
    soft = result.penalties.get("soft_floors", {})
    if soft:
        misses = ", ".join(f"{cat}: -{row['deficit']}" for cat, row in soft.items())
        lines.append(f"# Suelos blandos incumplidos: {misses}")
    lines.append("")
    lines.append("## Resumen de cuotas")
    lines.append(f"{'categoría':<12} {'n':>3}  {'banda':<10} estado")
    for category in CATEGORY_ORDER:
        band = bands[category]
        status = result.statuses[category].value
        extra = ""
        if category == "lands":
            extra = (
                f"  (suelo Karsten: {result.karsten_floor}, "
                f"mínimo efectivo: {result.lands_target})"
            )
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
            lines.append(
                f"{prefix}{entry.name:<42} score {score}  [{cats}]  {entry.reason}"
            )

    lines.append("")
    lines.append(f"## Maybeboard ({len(result.maybeboard)})")
    for entry in result.maybeboard:
        cats = "/".join(entry.categories)
        lines.append(
            f"1x {entry.name:<42} score {entry.score:.2f}  [{cats}]  {entry.reason}"
        )
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
    pool = load_pool(POOL_PATH)
    config = load_quotas()
    banned, watchlist = load_banlist(BANLIST_PATH)
    tagger = tagger_from_store(load_tags(), pool.cards())
    DECKS_DIR.mkdir(parents=True, exist_ok=True)

    log.info(
        "pool: %d cartas | banlist: %d baneadas, %d watchlist",
        len(pool.by_name),
        len(banned),
        len(watchlist),
    )

    for commander in COMMANDERS:
        data = fetch_commander(commander)
        bands = resolve_bands(config, commander)
        start = time.perf_counter()
        result = build_deck_cpsat(
            commander,
            pool=pool,
            recommendations=data.recommendations,
            bands=bands,
            tagger=tagger,
            banned_names=banned,
            watchlist_names=watchlist,
            time_limit_s=TIME_LIMIT_S,
        )
        elapsed = time.perf_counter() - start
        assert result.total_cards == DECK_SIZE
        out_path = DECKS_DIR / f"{slugify_commander(commander)}.txt"
        out_path.write_text(format_deck(result, bands, elapsed), encoding="utf-8")
        log.info(
            "%s: %d cartas, %.2fs (solver %.2fs, %s, etapa %s), tierras %d "
            "(suelo %d, min efectivo %d) -> %s",
            commander,
            result.total_cards,
            elapsed,
            result.solve_time_s,
            result.solver_status,
            result.relaxation_stage,
            result.counts.get("lands", 0),
            result.karsten_floor,
            result.lands_target,
            out_path.name,
        )


if __name__ == "__main__":
    main()
