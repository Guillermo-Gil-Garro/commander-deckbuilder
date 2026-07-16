"""Build the EDHREC commander popularity ranking (``data/edhrec_ranking.json``).

EDHREC has no global "all commanders by deck count" bulk. What it does have is
one page per **color identity** — the 32 that partition every commander — each
listing the top ~100 commanders of that identity with ``num_decks``. Union the
32 and you have a popularity number for every commander anyone actually plays
(~62% of our pool; the long tail below rank 100 has no page and stays
unranked, which the API treats as "sort me last, alphabetically").

The 32 slugs are the WUBRG lattice: 5 mono, 10 guilds, 10 shards/wedges, 5
four-color, plus ``five-color`` and ``colorless``. Each is a *global* commander
page at ``https://json.edhrec.com/pages/commanders/<slug>.json`` whose
``container.json_dict.cardlists[0].cardviews`` are the commanders, each with a
``name`` and a ``num_decks``.

Output — ``data/edhrec_ranking.json``, a small map ``canonical pool name ->
num_decks`` — is **committed**, unlike ``data/cache/`` (gitignored, absent from
the Docker build context and so never on the Space). The API degrades
gracefully if it is missing (empty ranking = alphabetical order), so this is a
data artifact, not a config: regenerate and commit it when EDHREC drifts.

EDHREC names are resolved against the current pool via the same ``NameIndex``
the banlist uses, so a double-faced commander EDHREC pages under its front face
("Kefka, Court Mage") lands on our canonical "A // B" name. Names that do not
resolve (partner pairs, cards outside our pool) are logged and dropped.

The raw color pages are cached under ``data/cache/edhrec/<slug>.json`` (reusing
``pipeline.edhrec``'s atomic writer) with a 1 s delay between downloads: EDHREC
is a free public service and this is a 32-page maintenance run.

Usage (from repo root):
    backend/.venv/Scripts/python.exe scripts/precache_edhrec_ranking.py [--force]

    --force  re-download every page, ignoring the cache.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pipeline.edhrec import (  # noqa: E402
    HEADERS,
    EdhrecError,
    _cache_path,
    _download_page,
    _page_url,
)
from rules.resolve import (  # noqa: E402
    DEFAULT_POOL_PATH,
    ResolutionError,
    name_index_from_cards,
)
from selector.greedy import SelectorError, load_pool  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("precache_edhrec_ranking")

OUTPUT_PATH = REPO_ROOT / "data" / "edhrec_ranking.json"

# EDHREC is a free public service: one page per second is plenty for 32 pages.
DOWNLOAD_DELAY_S = 1.0

# The 32 color-identity commander pages that partition the whole commander pool.
COLOR_IDENTITY_SLUGS: tuple[str, ...] = (
    # 5 mono
    "mono-white", "mono-blue", "mono-black", "mono-red", "mono-green",
    # 10 guilds
    "azorius", "dimir", "rakdos", "gruul", "selesnya",
    "orzhov", "izzet", "golgari", "boros", "simic",
    # 10 shards / wedges
    "bant", "esper", "grixis", "jund", "naya",
    "abzan", "jeskai", "sultai", "mardu", "temur",
    # 5 four-color
    "yore-tiller", "glint-eye", "dune-brood", "ink-treader", "witch-maw",
    # nephilim / colorless
    "five-color", "colorless",
)


def _fetch_page(slug: str, *, force: bool) -> tuple[dict, bool]:
    """Return ``(raw JSON, downloaded)`` for one color-identity page.

    ``downloaded`` is ``False`` on a cache hit, so the caller only pays the
    inter-request delay when it actually hit the network.
    """
    cache_path = _cache_path(slug, None)
    if force and cache_path.exists():
        cache_path.unlink()
    downloaded = not cache_path.exists()
    if downloaded:
        _download_page(_page_url(slug, None), cache_path)
    return json.loads(cache_path.read_text(encoding="utf-8")), downloaded


def _cardviews(raw: dict, slug: str) -> list[dict]:
    """The commander rows of a color-identity page (``name`` + ``num_decks``)."""
    try:
        cardlists = raw["container"]["json_dict"]["cardlists"] or []
    except (KeyError, TypeError) as exc:
        raise EdhrecError(f"unexpected EDHREC page structure for {slug!r}: {exc!r}")
    views: list[dict] = []
    for cardlist in cardlists:
        views.extend(cardlist.get("cardviews") or [])
    return views


def build_ranking(*, force: bool) -> int:
    """Download the 32 pages, resolve names and write the ranking. Exit code."""
    try:
        pool = load_pool(DEFAULT_POOL_PATH)
    except SelectorError as exc:
        raise SystemExit(f"card pool not usable at {DEFAULT_POOL_PATH}: {exc}")
    name_index = name_index_from_cards(pool.cards())
    log.info("Loaded card pool: %d cards", len(pool.by_name))

    # Raw EDHREC name -> best num_decks seen (a commander lives on one identity
    # page, but max() is the safe merge if EDHREC ever lists one twice).
    raw_decks: dict[str, int] = {}
    pages_ok = 0
    for position, slug in enumerate(COLOR_IDENTITY_SLUGS, start=1):
        try:
            raw, downloaded = _fetch_page(slug, force=force)
            views = _cardviews(raw, slug)
        except EdhrecError as exc:
            log.error("[%d/%d] %s: FAILED (%s)", position, len(COLOR_IDENTITY_SLUGS), slug, exc)
            continue
        pages_ok += 1
        for view in views:
            name = view.get("name")
            num_decks = view.get("num_decks")
            if not name or num_decks is None:
                continue
            raw_decks[name] = max(raw_decks.get(name, 0), int(num_decks))
        log.info(
            "[%d/%d] %s: %d commanders",
            position, len(COLOR_IDENTITY_SLUGS), slug, len(views),
        )
        if downloaded:
            time.sleep(DOWNLOAD_DELAY_S)

    ranking: dict[str, int] = {}
    unresolved = 0
    for name, num_decks in raw_decks.items():
        try:
            canonical = name_index.resolve(name).canonical_name
        except ResolutionError as exc:
            unresolved += 1
            log.warning("dropping unresolved EDHREC commander %r: %s", name, exc)
            continue
        # A canonical card seen twice (front-face + full name both hitting it):
        # keep the larger deck count.
        ranking[canonical] = max(ranking.get(canonical, 0), num_decks)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(ranking, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    log.info(
        "Done: %d/%d pages OK, %d EDHREC names, %d resolved, %d dropped -> %s (%d bytes)",
        pages_ok,
        len(COLOR_IDENTITY_SLUGS),
        len(raw_decks),
        len(ranking),
        unresolved,
        OUTPUT_PATH,
        OUTPUT_PATH.stat().st_size,
    )
    return 0 if pages_ok == len(COLOR_IDENTITY_SLUGS) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download every page, ignoring the cache",
    )
    args = parser.parse_args()
    return build_ranking(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
