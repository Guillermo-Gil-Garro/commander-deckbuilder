"""EDHREC commander recommendations client.

Fetches the public page JSON from ``https://json.edhrec.com/pages/commanders/
<slug>.json`` and caches the raw response under ``data/cache/edhrec/``. The
cache never expires by time; parsing always starts from the cached raw file.

Bracket-filtered subpages (e.g. ``optimized`` = Bracket 4) live at
``.../commanders/<slug>/<variant>.json`` and share the exact cardlist
structure; pass ``variant="optimized"`` to fetch them. Variant pages are
cached side by side as ``data/cache/edhrec/<slug>--<variant>.json`` so the
global cache is never overwritten.

A card can appear in several EDHREC cardlists (e.g. "High Synergy Cards" and
"Creatures"); recommendations are deduplicated by name and keep every list
header in ``categories``.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path

import httpx
from pydantic import BaseModel

from pipeline.scryfall import REPO_ROOT

logger = logging.getLogger(__name__)

PAGE_URL_TEMPLATE = "https://json.edhrec.com/pages/commanders/{slug}.json"
VARIANT_PAGE_URL_TEMPLATE = (
    "https://json.edhrec.com/pages/commanders/{slug}/{variant}.json"
)

HEADERS = {
    "User-Agent": "commander-deckbuilder/0.1",
    "Accept": "application/json",
}

CACHE_DIR = REPO_ROOT / "data" / "cache" / "edhrec"

_TIMEOUT = httpx.Timeout(30.0)

# Apostrophes (straight and curly) are removed, not turned into hyphens:
# "K'rrik" -> "krrik". Any other non-alphanumeric run becomes one hyphen.
_APOSTROPHES_RE = re.compile(r"['’]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


class EdhrecError(RuntimeError):
    """Raised when EDHREC data cannot be fetched or parsed."""


class EdhrecRecommendation(BaseModel):
    name: str
    synergy: float
    num_decks: int
    potential_decks: int
    inclusion: float
    categories: list[str]


class EdhrecCommanderData(BaseModel):
    name: str
    slug: str
    num_decks: int
    recommendations: list[EdhrecRecommendation]


def slugify_commander(name: str) -> str:
    """Normalize a commander name to its EDHREC URL slug.

    "Atraxa, Praetors' Voice" -> "atraxa-praetors-voice".
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    without_apostrophes = _APOSTROPHES_RE.sub("", ascii_name)
    return _NON_ALNUM_RE.sub("-", without_apostrophes).strip("-")


def _page_url(slug: str, variant: str | None) -> str:
    if variant is None:
        return PAGE_URL_TEMPLATE.format(slug=slug)
    return VARIANT_PAGE_URL_TEMPLATE.format(slug=slug, variant=variant)


def _cache_path(slug: str, variant: str | None) -> Path:
    stem = slug if variant is None else f"{slug}--{variant}"
    return CACHE_DIR / f"{stem}.json"


def _download_page(url: str, cache_path: Path) -> None:
    logger.info("Downloading EDHREC page %s", url)
    try:
        response = httpx.get(url, headers=HEADERS, timeout=_TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Missing pages surface as 403 (S3 AccessDenied) on json.edhrec.com.
        if exc.response.status_code in (403, 404):
            raise EdhrecError(
                f"EDHREC page not found "
                f"(HTTP {exc.response.status_code} at {url})"
            ) from exc
        raise EdhrecError(f"EDHREC request failed: {exc}") from exc
    except httpx.HTTPError as exc:
        raise EdhrecError(f"EDHREC request failed: {exc}") from exc

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".json.tmp")
    tmp_path.write_bytes(response.content)
    tmp_path.replace(cache_path)
    logger.info("EDHREC page cached at %s (%d bytes)", cache_path, len(response.content))


def parse_commander_page(raw: dict, slug: str) -> EdhrecCommanderData:
    """Extract commander info and deduplicated recommendations from raw page JSON."""
    try:
        json_dict = raw["container"]["json_dict"]
        card = json_dict["card"]
        cardlists = json_dict["cardlists"]
        commander_name = card["name"]
        commander_decks = int(card["num_decks"])
    except (KeyError, TypeError) as exc:
        raise EdhrecError(
            f"Unexpected EDHREC JSON structure for '{slug}': {exc!r}"
        ) from exc

    by_name: dict[str, EdhrecRecommendation] = {}
    for cardlist in cardlists:
        try:
            header = cardlist["header"]
            cardviews = cardlist["cardviews"]
        except (KeyError, TypeError) as exc:
            raise EdhrecError(
                f"Unexpected EDHREC cardlist structure for '{slug}': {exc!r}"
            ) from exc
        for view in cardviews:
            try:
                name = view["name"]
                if name in by_name:
                    by_name[name].categories.append(header)
                    continue
                num_decks = int(view["num_decks"])
                potential_decks = int(view["potential_decks"])
                inclusion = (
                    num_decks / potential_decks if potential_decks > 0 else 0.0
                )
                by_name[name] = EdhrecRecommendation(
                    name=name,
                    synergy=float(view["synergy"]),
                    num_decks=num_decks,
                    potential_decks=potential_decks,
                    inclusion=inclusion,
                    categories=[header],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise EdhrecError(
                    f"Unexpected EDHREC cardview structure for '{slug}': {exc!r}"
                ) from exc

    return EdhrecCommanderData(
        name=commander_name,
        slug=slug,
        num_decks=commander_decks,
        recommendations=list(by_name.values()),
    )


def fetch_commander(name: str, variant: str | None = None) -> EdhrecCommanderData:
    """Return EDHREC recommendations for a commander, downloading once.

    ``variant=None`` fetches the global commander page; ``variant="optimized"``
    fetches the Bracket 4 subpage (same structure). Uses
    ``data/cache/edhrec/<slug>.json`` (global) or ``<slug>--<variant>.json``
    if present; otherwise downloads the raw page JSON there first. Parsing
    always reads the cached file.
    """
    slug = slugify_commander(name)
    cache_path = _cache_path(slug, variant)
    if cache_path.exists():
        logger.info("Using cached EDHREC page %s", cache_path)
    else:
        _download_page(_page_url(slug, variant), cache_path)

    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EdhrecError(f"Could not read cached EDHREC page {cache_path}: {exc}") from exc
    return parse_commander_page(raw, slug)
