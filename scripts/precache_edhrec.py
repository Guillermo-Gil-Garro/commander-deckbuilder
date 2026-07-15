"""Precache the EDHREC Bracket-4 pages for the featured commanders.

Fetches ``variant="optimized"`` (Bracket 4, the group's power level — never
cEDH) for every commander in ``featured_commanders.yaml`` and leaves the raw
pages in ``data/cache/edhrec/<slug>--optimized.json``, which is exactly what
the API reads at request time.

LIMITATION — this does NOT reach the Hugging Face Space: ``data/cache/`` is
gitignored (``.gitignore:26``) and excluded from the Docker build context, so
this is a dev/local optimisation only. In production every commander still
pays its first fetch in-flight (~1 s) until Fase 6 decides whether to version
or mount the cache.

It also runs the API's startup banlist check (``resolve_banlist`` against the
current pool), which fails hard: a ``banlist.yaml`` out of sync with the pool
stops the app from starting and CI cannot catch it because the pool is
gitignored. This script is the natural place to notice before deploying.

Failures never abort the run (this is maintenance tooling, not a test): every
commander is attempted, errors are collected and the exit code is 1 if any
failed.

Usage (from repo root):
    backend/.venv/Scripts/python.exe scripts/precache_edhrec.py [--force]

    --force  re-download every page, ignoring the cache.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pipeline.edhrec import (  # noqa: E402
    CACHE_DIR,
    EdhrecError,
    fetch_commander,
    slugify_commander,
)
from rules.banlist import BanlistError, load_banlist, resolve_banlist  # noqa: E402
from rules.featured import FeaturedError, load_featured  # noqa: E402
from rules.resolve import DEFAULT_POOL_PATH, name_index_from_cards  # noqa: E402
from selector.greedy import SelectorError, load_pool  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("precache_edhrec")

VARIANT = "optimized"

# EDHREC is a free public service: one page per second is plenty for a 55-page
# maintenance run and keeps us well clear of abusing it.
DOWNLOAD_DELAY_S = 1.0


def cache_path(name: str) -> Path:
    """Path where ``fetch_commander(name, variant="optimized")`` caches its page."""
    return CACHE_DIR / f"{slugify_commander(name)}--{VARIANT}.json"


def featured_names() -> list[str]:
    """Canonical pool names of the featured commanders.

    Uses the pool-resolved names (not the YAML strings) because those are what
    the API passes to ``fetch_commander``: caching any other spelling would
    warm a slug nobody asks for.

    Raises ``SystemExit`` with a diagnosis if the pool, the banlist or the
    featured list is unusable — none of them is optional for this run.
    """
    try:
        pool = load_pool(DEFAULT_POOL_PATH)
    except SelectorError as exc:
        raise SystemExit(f"card pool not usable at {DEFAULT_POOL_PATH}: {exc}")
    log.info("Loaded card pool: %d cards", len(pool.by_name))

    name_index = name_index_from_cards(pool.cards())

    try:
        resolved_banlist = resolve_banlist(load_banlist(), name_index)
    except BanlistError as exc:
        raise SystemExit(
            f"banlist.yaml does not resolve against the current pool: {exc}\n"
            f"This would stop the API from starting; fix the banlist or "
            f"rebuild the pool before deploying."
        )
    log.info(
        "banlist.yaml resolves against the pool: %d banned, %d banned as commander",
        len(resolved_banlist.banned),
        len(resolved_banlist.banned_as_commander),
    )

    try:
        featured = load_featured(
            resolved_banlist=resolved_banlist, name_index=name_index
        )
    except FeaturedError as exc:
        raise SystemExit(f"featured_commanders.yaml is not usable: {exc}")
    return [commander.name for commander in featured]


def precache(names: list[str], *, force: bool) -> int:
    """Fetch every commander's page. Returns the process exit code."""
    already = 0
    downloaded = 0
    failures: list[tuple[str, str]] = []

    for position, name in enumerate(names, start=1):
        path = cache_path(name)
        if path.exists():
            if not force:
                log.info("[%d/%d] %s: already cached", position, len(names), name)
                already += 1
                continue
            path.unlink()
            log.info("[%d/%d] %s: --force, cache dropped", position, len(names), name)

        try:
            data = fetch_commander(name, variant=VARIANT)
        except EdhrecError as exc:
            log.error("[%d/%d] %s: FAILED (%s)", position, len(names), name, exc)
            failures.append((name, str(exc)))
        else:
            log.info(
                "[%d/%d] %s: downloaded, %d recommendations",
                position,
                len(names),
                name,
                len(data.recommendations),
            )
            downloaded += 1
        time.sleep(DOWNLOAD_DELAY_S)

    log.info(
        "Done: %d already cached, %d downloaded, %d failed (of %d featured)",
        already,
        downloaded,
        len(failures),
        len(names),
    )
    for name, error in failures:
        log.error("Failed: %s: %s", name, error)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download every page, ignoring the cache",
    )
    args = parser.parse_args()
    return precache(featured_names(), force=args.force)


if __name__ == "__main__":
    sys.exit(main())
