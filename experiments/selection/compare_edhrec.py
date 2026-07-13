"""Comparativa Fase 3: nuestros mazos (greedy y CP-SAT) vs average decks EDHREC B4.

Endpoints EDHREC sondeados (2026-07-14, una peticion por candidato):

* ``https://json.edhrec.com/pages/average-decks/<slug>.json``            -> 200 (average deck sin filtro)
* ``https://json.edhrec.com/pages/average-decks/<slug>/bracket-4.json``  -> 403 (NO existe)
* ``https://json.edhrec.com/pages/commanders/<slug>/bracket-4.json``     -> 403 (NO existe)
* ``https://json.edhrec.com/pages/average-decks/<slug>/optimized.json``  -> 200 (average deck Bracket 4)
* ``https://json.edhrec.com/pages/commanders/<slug>/optimized.json``     -> 200 (pagina de comandante B4)

EDHREC nombra los brackets, no los numera: 1=exhibition, 2=core, 3=upgraded,
4=optimized, 5=cedh. Usamos ``average-decks/<slug>/optimized.json`` (header:
"Average Deck for <name> - Optimized"), que es exactamente el filtro Bracket 4.

Todo se cachea en ``data/cache/edhrec_avg/<slug>-optimized.json``; con la cache
poblada el script no toca la red. Los scores de inclusion/sinergia salen de la
cache ya existente de ``data/cache/edhrec/`` (via ``pipeline.edhrec``).

Usage (from repo root):
    backend/.venv/Scripts/python.exe experiments/selection/compare_edhrec.py
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.edhrec import fetch_commander, slugify_commander  # noqa: E402
from quotas.config import load_quotas  # noqa: E402
from quotas.resolver import resolve_bands  # noqa: E402
from run_greedy import load_banlist  # noqa: E402
from selector.greedy import PoolIndex, load_pool  # noqa: E402
from tags.store import FACE_SEPARATOR, load_tags, tagger_from_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("compare_edhrec")

AVG_URL_TEMPLATE = "https://json.edhrec.com/pages/average-decks/{slug}/optimized.json"
HEADERS = {"User-Agent": "commander-deckbuilder/0.1", "Accept": "application/json"}
AVG_CACHE_DIR = REPO_ROOT / "data" / "cache" / "edhrec_avg"
POOL_PATH = REPO_ROOT / "data" / "processed" / "cards.jsonl"
BANLIST_PATH = REPO_ROOT / "banlist.yaml"
SELECTION_DIR = Path(__file__).resolve().parent
DECK_DIRS = {"greedy": SELECTION_DIR / "decks", "cpsat": SELECTION_DIR / "decks_cpsat"}
REQUEST_SLEEP_S = 0.7

COMMANDERS = (
    "Krenko, Mob Boss",
    "Atraxa, Praetors' Voice",
    "Meren of Clan Nel Toth",
    "Niv-Mizzet, Parun",
    "Sythis, Harvest's Hand",
)

CATEGORY_ORDER = ("lands", "ramp", "card_draw", "removal", "board_wipe", "wincons", "synergy")

BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}

# "1x Name    score 0.62  [cats]  reason" (nuestros .txt) / "21x Mountain ...".
_DECK_LINE_RE = re.compile(r"^(\d+)x (.+?) +score +(--|-?\d+\.\d+)")
_SUMMARY_RE = re.compile(r"^(\w+)\s+(\d+)\s+\[\s*(\d+)-\s*(\d+)\]\s+(\w+)")
_KARSTEN_RE = re.compile(r"suelo Karsten: (\d+)")
_AVG_ENTRY_RE = re.compile(r"^(\d+) (.+)$")


@dataclass
class OurDeck:
    """Un decklist nuestro parseado desde experiments/selection/decks*/<slug>.txt."""

    selector: str
    cards: dict[str, int]  # nombre tal cual en el fichero -> copias
    maybeboard: list[str]
    counts: dict[str, int]  # tabla "Resumen de cuotas" del fichero
    statuses: dict[str, str]
    karsten_floor: int
    unresolved: list[str] = field(default_factory=list)


def parse_our_deck(path: Path, selector: str) -> OurDeck:
    """Parsea decklist, maybeboard y resumen de cuotas de nuestro formato .txt."""
    cards: dict[str, int] = {}
    maybeboard: list[str] = []
    counts: dict[str, int] = {}
    statuses: dict[str, str] = {}
    karsten_floor = -1
    unresolved: list[str] = []
    section = "header"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Mainboard"):
            section = "main"
            continue
        if line.startswith("## Maybeboard"):
            section = "maybe"
            continue
        if line.startswith("# ") and "sin resolver" in line:
            section = "unresolved"
            continue
        if section == "header":
            m = _SUMMARY_RE.match(line)
            if m and m.group(1) in CATEGORY_ORDER:
                counts[m.group(1)] = int(m.group(2))
                statuses[m.group(1)] = m.group(5)
                km = _KARSTEN_RE.search(line)
                if km:
                    karsten_floor = int(km.group(1))
        elif section == "main":
            m = _DECK_LINE_RE.match(line)
            if m:
                cards[m.group(2).rstrip()] = cards.get(m.group(2).rstrip(), 0) + int(m.group(1))
        elif section == "maybe":
            m = _DECK_LINE_RE.match(line)
            if m:
                maybeboard.append(m.group(2).rstrip())
        elif section == "unresolved" and line.startswith("#   "):
            unresolved.append(line[4:].strip())
    total = sum(cards.values())
    if total != 99:
        raise RuntimeError(f"{path}: mainboard parseado con {total} cartas, esperaba 99")
    return OurDeck(selector, cards, maybeboard, counts, statuses, karsten_floor, unresolved)


def fetch_average_deck(slug: str) -> dict:
    """Average deck B4 (optimized) desde cache, descargando una sola vez."""
    cache_path = AVG_CACHE_DIR / f"{slug}-optimized.json"
    if not cache_path.exists():
        url = AVG_URL_TEMPLATE.format(slug=slug)
        log.info("Descargando %s", url)
        time.sleep(REQUEST_SLEEP_S)
        response = httpx.get(url, headers=HEADERS, timeout=30.0)
        response.raise_for_status()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".json.tmp")
        tmp.write_bytes(response.content)
        tmp.replace(cache_path)
    else:
        log.info("Cache: %s", cache_path)
    return json.loads(cache_path.read_text(encoding="utf-8"))


def parse_average_deck(raw: dict, commander: str) -> dict[str, int]:
    """``deck`` de EDHREC ("N Nombre") -> {nombre: copias}, sin el comandante."""
    entries: dict[str, int] = {}
    for item in raw["deck"]:
        m = _AVG_ENTRY_RE.match(item)
        if not m:
            raise RuntimeError(f"entrada de average deck no reconocida: {item!r}")
        entries[m.group(2)] = entries.get(m.group(2), 0) + int(m.group(1))
    if entries.get(commander) == 1:
        del entries[commander]
    else:
        log.warning("%s: el comandante no aparece 1x en el average deck", commander)
    return entries


def canonical(pool: PoolIndex, name: str) -> str:
    """Nombre canonico del pool (cara -> nombre completo); si no resuelve, tal cual."""
    card = pool.resolve(name)
    return card["name"] if card is not None else name


def is_land(card: Mapping) -> bool:
    """Land por la cara frontal del type_line (las MDFC spell//land no son tierra)."""
    front_type = card["type_line"].split(FACE_SEPARATOR)[0]
    return "Land" in front_type


def mean_cmc_nonland(pool: PoolIndex, cards: Mapping[str, int]) -> tuple[float, int]:
    """(CMC medio de no-tierras ponderado por copias, n de cartas sin resolver)."""
    total = 0.0
    n = 0
    unresolved = 0
    for name, count in cards.items():
        card = pool.resolve(name)
        if card is None:
            unresolved += 1
            continue
        if is_land(card):
            continue
        total += card["cmc"] * count
        n += count
    return (total / n if n else 0.0, unresolved)


def categorize(
    pool: PoolIndex,
    tagger,
    store_names: set[str],
    cards: Mapping[str, int],
) -> tuple[dict[str, int], list[str], list[str]]:
    """Composicion por categoria de un mazo ajeno usando el tag store.

    Devuelve (conteos por categoria, cartas sin tag, cartas en store sin labels).
    Semantica del selector: multicategoria cuenta en todas; carta del store con
    labels vacios cae en synergy; carta sin entrada en el store y que no es
    tierra por fallback se reporta aparte como "sin tag" (no la etiquetamos).
    """
    counts = {c: 0 for c in CATEGORY_ORDER}
    sin_tag: list[str] = []
    empty_label: list[str] = []
    for name, count in cards.items():
        canon = canonical(pool, name)
        labels = tagger(name) or tagger(canon)
        if labels:
            for label in labels:
                counts[label] += count
            continue
        in_store = name in store_names or canon in store_names
        if in_store:
            counts["synergy"] += count
            empty_label.append(canon)
        else:
            sin_tag.append(canon)
    return counts, sin_tag, empty_label


def multiset_overlap(a: Mapping[str, int], b: Mapping[str, int]) -> int:
    return sum(min(count, b.get(name, 0)) for name, count in a.items())


def main() -> None:
    pool = load_pool(POOL_PATH)
    store = load_tags()
    tagger = tagger_from_store(store, pool.cards())
    store_names: set[str] = set()
    for entry in store.values():
        store_names.add(entry.name)
        if FACE_SEPARATOR in entry.name:
            store_names.update(entry.name.split(FACE_SEPARATOR))
    config = load_quotas()
    banned, watchlist = load_banlist(BANLIST_PATH)

    # Acumuladores para patrones sistematicos.
    agg: dict[str, list] = {
        "overlap": [], "lands": [], "cmc": [], "out_of_band": [], "solo_edhrec": []
    }

    print("=" * 78)
    print("COMPARATIVA: nuestros mazos vs average decks EDHREC Bracket 4 (optimized)")
    print("=" * 78)

    for commander in COMMANDERS:
        slug = slugify_commander(commander)
        bands = resolve_bands(config, commander)
        raw = fetch_average_deck(slug)
        avg_cards = parse_average_deck(raw, commander)
        avg_total = sum(avg_cards.values())
        avg_canon = {canonical(pool, n): c for n, c in avg_cards.items()}

        recs = {r.name: r for r in fetch_commander(commander).recommendations}
        rec_canon = {canonical(pool, n): r for n, r in recs.items()}

        ours = {
            sel: parse_our_deck(deck_dir / f"{slug}.txt", sel)
            for sel, deck_dir in DECK_DIRS.items()
        }

        avg_lands = sum(
            count for name, count in avg_canon.items()
            if (card := pool.resolve(name)) is not None and is_land(card)
        )
        avg_cmc, avg_unres = mean_cmc_nonland(pool, avg_canon)
        avg_counts, sin_tag, empty_label = categorize(pool, tagger, store_names, avg_canon)
        not_in_pool = sorted(n for n in avg_canon if pool.resolve(n) is None)

        print(f"\n{'-' * 78}\n## {commander}  (slug: {slug})")
        print(f"Average deck B4: {avg_total} cartas tras quitar comandante"
              + (f" | SIN RESOLVER en pool: {not_in_pool}" if not_in_pool else ""))
        if sin_tag:
            print(f"Sin tag en el store ({len(sin_tag)}): {sorted(sin_tag)}")
        if empty_label:
            print(f"En store con labels vacios -> synergy ({len(empty_label)}): {sorted(empty_label)}")

        print(f"\nTierras:  EDHREC {avg_lands}"
              + "".join(f" | {s} {d.counts['lands']}" for s, d in ours.items())
              + f" | suelo Karsten {ours['greedy'].karsten_floor}"
              + f" | banda [{bands['lands'].min}-{bands['lands'].max}]")
        cmcs = {"EDHREC": avg_cmc}
        for sel, deck in ours.items():
            cmcs[sel], _ = mean_cmc_nonland(pool, deck.cards)
        print("CMC medio no-tierras: "
              + " | ".join(f"{k} {v:.2f}" for k, v in cmcs.items()))
        agg["lands"].append((commander, avg_lands, ours["greedy"].counts["lands"],
                             ours["cpsat"].counts["lands"], ours["greedy"].karsten_floor))
        agg["cmc"].append((commander, cmcs["EDHREC"], cmcs["greedy"], cmcs["cpsat"]))

        print("\nComposicion del average deck B4 vs nuestras bandas (midrange):")
        out_of_band: list[str] = []
        for cat in CATEGORY_ORDER:
            band = bands[cat]
            n = avg_counts[cat]
            status = "below" if n < band.min else ("above" if n > band.max else "in_range")
            if status != "in_range":
                out_of_band.append(f"{cat}:{status}({n} vs [{band.min}-{band.max}])")
            ours_str = "".join(f"  {s}={d.counts[cat]}" for s, d in ours.items())
            print(f"  {cat:<11} EDHREC {n:>3}  banda [{band.min:>2}-{band.max:>2}]  {status:<9}{ours_str}")
        agg["out_of_band"].append((commander, out_of_band))

        for sel, deck in ours.items():
            our_canon = {canonical(pool, n): c for n, c in deck.cards.items()}
            inter = multiset_overlap(avg_canon, our_canon)
            pct_avg = 100.0 * inter / avg_total
            pct_ours = 100.0 * inter / 99
            print(f"\nSolape {sel}: {inter} cartas | {pct_avg:.1f}% del avg B4 esta en el nuestro"
                  f" | {pct_ours:.1f}% del nuestro esta en el avg B4")
            agg["overlap"].append((commander, sel, inter, pct_avg))

            maybe_canon = {canonical(pool, n) for n in deck.maybeboard}
            solo_edhrec = sorted(
                (n for n in avg_canon if n not in our_canon and n not in BASICS),
                key=lambda n: -(rec_canon[n].inclusion if n in rec_canon else -1.0),
            )
            solo_ours = sorted(n for n in our_canon if n not in avg_canon and n not in BASICS)
            print(f"Solo-EDHREC ({sel}): {len(solo_edhrec)} | Solo-nuestras ({sel}): {len(solo_ours)}")
            print(f"  Top solo-EDHREC por inclusion ({sel}):")
            for name in solo_edhrec[:15]:
                rec = rec_canon.get(name)
                incl = f"incl {100 * rec.inclusion:.0f}% syn {rec.synergy:+.2f}" if rec else "NO en recs pagina comandante"
                labels = tagger(name)
                flags = []
                if name in banned:
                    flags.append("BANNED grupo")
                if name in watchlist:
                    flags.append("WATCHLIST")
                if name in maybe_canon:
                    flags.append("en maybeboard")
                if pool.resolve(name) is None:
                    flags.append("no en pool")
                elif not labels and name not in store_names:
                    flags.append("sin tag")
                print(f"    {name:<38} {incl:<32} [{'/'.join(sorted(labels)) or '-'}]"
                      + (f"  <{', '.join(flags)}>" if flags else ""))
                if sel == "greedy":
                    agg["solo_edhrec"].append((commander, name, rec.inclusion if rec else -1,
                                               sorted(labels), flags))
            print(f"  Solo-nuestras ({sel}): {solo_ours}")

    print(f"\n{'=' * 78}\n## AGREGADOS (5 comandantes)")
    for sel in DECK_DIRS:
        vals = [pct for _, s, _, pct in agg["overlap"] if s == sel]
        print(f"Solape medio {sel}: {sum(vals) / len(vals):.1f}% (min {min(vals):.1f}, max {max(vals):.1f})")
    dl_g = [g - e for _, e, g, _, _ in agg["lands"]]
    dl_c = [c - e for _, e, _, c, _ in agg["lands"]]
    print(f"Tierras nuestras - EDHREC: greedy {dl_g} (media {sum(dl_g) / 5:+.1f}) | "
          f"cpsat {dl_c} (media {sum(dl_c) / 5:+.1f})")
    dc_g = [g - e for _, e, g, _ in agg["cmc"]]
    dc_c = [c - e for _, e, _, c in agg["cmc"]]
    print(f"CMC nuestro - EDHREC: greedy media {sum(dc_g) / 5:+.2f} {[f'{d:+.2f}' for d in dc_g]} | "
          f"cpsat media {sum(dc_c) / 5:+.2f} {[f'{d:+.2f}' for d in dc_c]}")
    print("Categorias del avg B4 fuera de nuestras bandas, por comandante:")
    for commander, oob in agg["out_of_band"]:
        print(f"  {commander}: {oob or 'ninguna'}")


if __name__ == "__main__":
    main()
