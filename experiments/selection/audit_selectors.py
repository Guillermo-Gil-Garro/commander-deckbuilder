"""Fase 3 — auditoría de decisión: greedy vs CP-SAT.

Compara los mazos generados por ambos selectores (``decks/`` vs ``decks_cpsat/``)
parseando los propios ficheros .txt (fuente de verdad de lo que vio Guille) y
cruzándolos con el pool (``data/processed/cards.jsonl``) para CMC, tipos y
fuentes de color. No modifica nada: solo imprime el informe por stdout.

Usage (from repo root):
    backend/.venv/Scripts/python.exe experiments/selection/audit_selectors.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

# Reutilizamos la heurística oficial de fuentes de color del propio CP-SAT
# para que ambos mazos se midan con la misma vara (es privada, pero este es
# un script de experimento de un solo uso — no merece promoverla a API).
from selector.cp_sat import _produced_colors  # noqa: E402

POOL_PATH = REPO_ROOT / "data" / "processed" / "cards.jsonl"
DECKS_GREEDY = Path(__file__).resolve().parent / "decks"
DECKS_CPSAT = Path(__file__).resolve().parent / "decks_cpsat"

SLUGS = (
    "krenko-mob-boss",
    "meren-of-clan-nel-toth",
    "niv-mizzet-parun",
    "omnath-locus-of-creation",
    "sythis-harvests-hand",
)

BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}

ENTRY_RE = re.compile(
    r"^(\d+)x (.+?)\s+score\s+([\d.]+|--)\s+\[([^\]]+)\]\s+(.*)$"
)
QUOTA_RE = re.compile(r"^(\w+)\s+(\d+)\s+\[\s*(\d+)-\s*(\d+)\]\s+(\w+)")


@dataclass
class Entry:
    name: str
    count: int
    score: float | None
    categories: tuple[str, ...]
    reason: str
    section: str  # categoría del ### bajo el que aparece


@dataclass
class Deck:
    path: Path
    header: list[str] = field(default_factory=list)
    quotas: dict[str, tuple[int, int, int, str]] = field(default_factory=dict)
    mainboard: list[Entry] = field(default_factory=list)
    maybeboard: list[Entry] = field(default_factory=list)

    @property
    def by_name(self) -> dict[str, Entry]:
        return {e.name: e for e in self.mainboard}


def parse_deck(path: Path) -> Deck:
    deck = Deck(path=path)
    zone = "header"
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Resumen"):
            zone = "quotas"
            continue
        if line.startswith("## Mainboard"):
            zone = "mainboard"
            continue
        if line.startswith("## Maybeboard"):
            zone = "maybeboard"
            continue
        if line.startswith("## Cartas nuevas"):
            zone = "new"
            continue
        if zone == "header" and line.startswith("#"):
            deck.header.append(line)
            continue
        if zone == "quotas":
            m = QUOTA_RE.match(line)
            if m:
                cat, n, lo, hi, status = m.groups()
                deck.quotas[cat] = (int(n), int(lo), int(hi), status)
            continue
        if zone in ("mainboard", "maybeboard"):
            if line.startswith("### "):
                section = line[4:].split(" (")[0]
                continue
            m = ENTRY_RE.match(line)
            if m:
                count, name, score, cats, reason = m.groups()
                entry = Entry(
                    name=name.strip(),
                    count=int(count),
                    score=None if score == "--" else float(score),
                    categories=tuple(cats.split("/")),
                    reason=reason.strip(),
                    section=section if zone == "mainboard" else "",
                )
                (deck.mainboard if zone == "mainboard" else deck.maybeboard).append(entry)
    total = sum(e.count for e in deck.mainboard)
    assert total == 99, f"{path.name}: mainboard suma {total}, esperaba 99"
    return deck


def load_pool(path: Path) -> dict[str, dict]:
    pool: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            card = json.loads(line)
            pool[card["name"]] = card
            if "//" in card["name"]:
                pool.setdefault(card["name"].split(" // ")[0], card)
    return pool


def is_land(card: dict) -> bool:
    return "Land" in (card.get("type_line") or "").split("—")[0]


def fmt_entry(e: Entry, card: dict | None) -> str:
    cmc = f"cmc {card['cmc']:.0f}" if card and not is_land(card) else "land "
    score = f"{e.score:.2f}" if e.score is not None else " -- "
    return f"    {e.name:<40} {score}  {cmc}  [{'/'.join(e.categories)}]"


def audit_commander(slug: str, pool: dict[str, dict]) -> dict:
    g = parse_deck(DECKS_GREEDY / f"{slug}.txt")
    c = parse_deck(DECKS_CPSAT / f"{slug}.txt")
    commander = g.path.read_text(encoding="utf-8").splitlines()[0].split(" — ")[0][2:]

    g_names = {e.name for e in g.mainboard if e.name not in BASICS}
    c_names = {e.name for e in c.mainboard if e.name not in BASICS}
    shared = g_names & c_names
    only_g = g_names - c_names
    only_c = c_names - g_names

    g_basics = sum(e.count for e in g.mainboard if e.name in BASICS)
    c_basics = sum(e.count for e in c.mainboard if e.name in BASICS)
    shared_basics = sum(
        min(
            sum(e.count for e in g.mainboard if e.name == b),
            sum(e.count for e in c.mainboard if e.name == b),
        )
        for b in BASICS
    )
    overlap_99 = len(shared) + shared_basics

    def stats(deck: Deck, names: set[str]) -> dict:
        entries = [deck.by_name[n] for n in sorted(names)]
        scores = [e.score for e in entries if e.score is not None]
        cards = [pool.get(e.name) for e in entries]
        nonland_cmc = [
            crd["cmc"] for crd in cards if crd is not None and not is_land(crd)
        ]
        cat_counter: Counter[str] = Counter()
        for e in entries:
            cat_counter.update(e.categories)
        return {
            "entries": entries,
            "n": len(entries),
            "score_total": sum(scores),
            "score_mean": mean(scores) if scores else 0.0,
            "cmc_mean": mean(nonland_cmc) if nonland_cmc else 0.0,
            "cats": cat_counter,
            "multi": sum(1 for e in entries if len(e.categories) > 1),
        }

    def deck_stats(deck: Deck) -> dict:
        cards = [(e, pool.get(e.name)) for e in deck.mainboard]
        nonland = [
            (e, crd) for e, crd in cards if crd is not None and not is_land(crd)
        ]
        lands = [(e, crd) for e, crd in cards if crd is not None and is_land(crd)]
        commander_card = pool.get(commander)
        identity = frozenset((commander_card or {}).get("color_identity") or [])
        sources: Counter[str] = Counter()
        for e, crd in lands:
            for color in _produced_colors(crd, identity):
                sources[color] += e.count
        n_basics = sum(e.count for e in deck.mainboard if e.name in BASICS)
        n_lands = sum(e.count for e, _ in lands)
        scores = [e.score for e in deck.mainboard if e.score is not None]
        cat_counter: Counter[str] = Counter()
        for e in deck.mainboard:
            for cat in e.categories:
                cat_counter[cat] += e.count
        return {
            "cmc_mean": mean(crd["cmc"] for _, crd in nonland),
            "score_total": sum(scores),
            "score_mean": mean(scores),
            "multi": sum(1 for e in deck.mainboard if len(e.categories) > 1),
            "n_lands": n_lands,
            "n_basics": n_basics,
            "n_nonbasic_lands": n_lands - n_basics,
            "sources": dict(sources),
            "cats": cat_counter,
            "unresolved_pool": [e.name for e, crd in cards if crd is None],
        }

    only_g_stats = stats(g, only_g)
    only_c_stats = stats(c, only_c)
    g_deck = deck_stats(g)
    c_deck = deck_stats(c)

    # ¿dónde acaban las exclusivas del otro lado? (maybeboard o ausentes)
    c_maybe = {e.name for e in c.maybeboard}
    g_maybe = {e.name for e in g.maybeboard}
    only_g_in_c_maybe = sorted(only_g & c_maybe)
    only_c_in_g_maybe = sorted(only_c & g_maybe)

    print("=" * 100)
    print(f"{commander}  [{slug}]")
    print("=" * 100)
    for line in c.header:
        if "Solver" in line or "Objetivo" in line or "Fixing" in line:
            print(f"  cpsat {line[1:].strip()}")
    print(
        f"  Solape (de 99): {overlap_99}  |  no-básicas compartidas: {len(shared)}"
        f"  |  solo-greedy: {len(only_g)}  |  solo-cpsat: {len(only_c)}"
        f"  |  básicas: greedy {g_basics} vs cpsat {c_basics} (compartidas {shared_basics})"
    )
    print()
    print(f"  {'métrica':<28} {'greedy':>10} {'cpsat':>10}")
    for label, key in (
        ("score total mainboard", "score_total"),
        ("score medio mainboard", "score_mean"),
        ("CMC medio (no-tierras)", "cmc_mean"),
        ("cartas multicategoría", "multi"),
        ("tierras totales", "n_lands"),
        ("  básicas", "n_basics"),
        ("  no básicas", "n_nonbasic_lands"),
    ):
        gv, cv = g_deck[key], c_deck[key]
        fmt = "{:>10.2f}" if isinstance(gv, float) else "{:>10d}"
        print(f"  {label:<28} {fmt.format(gv)} {fmt.format(cv)}")
    colors = sorted(set(g_deck["sources"]) | set(c_deck["sources"]))
    src = "  ".join(
        f"{col}: {g_deck['sources'].get(col, 0)}/{c_deck['sources'].get(col, 0)}"
        for col in colors
    )
    print(f"  fuentes color tierras (greedy/cpsat): {src}")
    cats = sorted(set(g_deck["cats"]) | set(c_deck["cats"]))
    print(
        "  composición por categoría (greedy/cpsat): "
        + "  ".join(f"{cat}: {g_deck['cats'][cat]}/{c_deck['cats'][cat]}" for cat in cats)
    )
    protection_g = g_deck["cats"].get("protection", 0)
    protection_c = c_deck["cats"].get("protection", 0)
    print(f"  protection (no está en el resumen impreso): greedy {protection_g} vs cpsat {protection_c}")
    for side, st in (("SOLO GREEDY", only_g_stats), ("SOLO CPSAT", only_c_stats)):
        print()
        print(
            f"  {side} ({st['n']}): score total {st['score_total']:.2f}, "
            f"medio {st['score_mean']:.3f}, CMC medio no-tierra {st['cmc_mean']:.2f}, "
            f"multicategoría {st['multi']}"
        )
        print(f"    categorías: {dict(sorted(st['cats'].items()))}")
        for e in sorted(st["entries"], key=lambda e: -(e.score or 0)):
            print(fmt_entry(e, pool.get(e.name)))
    print()
    print(f"  exclusivas greedy que cpsat dejó en maybeboard: {only_g_in_c_maybe or '—'}")
    print(f"  exclusivas cpsat que greedy dejó en maybeboard: {only_c_in_g_maybe or '—'}")
    if g_deck["unresolved_pool"] or c_deck["unresolved_pool"]:
        print(
            f"  ⚠️ sin resolver en pool: greedy {g_deck['unresolved_pool']}, "
            f"cpsat {c_deck['unresolved_pool']}"
        )
    print()

    return {
        "slug": slug,
        "commander": commander,
        "overlap_99": overlap_99,
        "shared": len(shared),
        "only_g": only_g_stats,
        "only_c": only_c_stats,
        "g_deck": g_deck,
        "c_deck": c_deck,
    }


def main() -> None:
    # consola Windows cp1252: forzamos utf-8 para los Δ del agregado
    sys.stdout.reconfigure(encoding="utf-8")
    pool = load_pool(POOL_PATH)
    results = [audit_commander(slug, pool) for slug in SLUGS]

    print("=" * 100)
    print("AGREGADO")
    print("=" * 100)
    print(
        f"  {'comandante':<28} {'solape/99':>9} {'excl':>5} "
        f"{'Δscore excl (c-g)':>18} {'Δscore medio':>13} {'ΔCMC (c-g)':>11}"
    )
    for r in results:
        d_total = r["only_c"]["score_total"] - r["only_g"]["score_total"]
        d_mean = r["only_c"]["score_mean"] - r["only_g"]["score_mean"]
        d_cmc = r["c_deck"]["cmc_mean"] - r["g_deck"]["cmc_mean"]
        print(
            f"  {r['commander']:<28} {r['overlap_99']:>9} {r['only_g']['n']:>5} "
            f"{d_total:>+18.2f} {d_mean:>+13.3f} {d_cmc:>+11.2f}"
        )
    print()
    print("  (Δ positivo = ventaja numérica del cpsat en sus exclusivas)")


if __name__ == "__main__":
    main()
