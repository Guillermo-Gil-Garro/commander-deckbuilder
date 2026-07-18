"""Scryfall printings fetcher with a disk cache (art/language picker).

The art picker needs every printing of a card in Spanish and English — the
pool (``oracle_cards`` bulk) only carries one English printing per card, and a
Spanish version is a *different card object* in Scryfall, so printings must be
searched per card. Results are cached under ``data/cache/prints/`` keyed by
oracle_id, with the same discipline as ``pipeline.edhrec``: one User-Agent,
atomic ``.tmp`` -> ``replace`` writes, no time-based expiry (delete a file to
refresh it after a new set drops).

Each printing is normalized to the fields the picker needs: id, set, language,
release date, whether the scan is high-resolution, and the face image URLs.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from pipeline.scryfall import REPO_ROOT

logger = logging.getLogger(__name__)

CACHE_DIR = REPO_ROOT / "data" / "cache" / "prints"

# Same identity as pipeline.edhrec / pipeline.card_images: one project, one
# User-Agent, so Scryfall sees a single well-behaved caller.
HEADERS = {"User-Agent": "commander-deckbuilder/0.1"}

_TIMEOUT = httpx.Timeout(30.0)

# Scryfall asks for 50-100 ms between requests. Applied after every *network*
# page fetch; cache hits never sleep.
_REQUEST_SLEEP_S = 0.1

# Search endpoint. `include_multilingual` is required for `lang:` filters;
# `-is:digital` drops Arena/MTGO renders (useless as proxies); `unique=prints`
# returns every physical printing.
_SEARCH_URL = "https://api.scryfall.com/cards/search"
_QUERY_TEMPLATE = "oracleid:{oracle_id} (lang:en or lang:es) -is:digital"

# Basics have 500+ printings; anything else fits comfortably. 3 pages
# (175/page) bounds the pathological cases without truncating real cards.
_MAX_PAGES = 3


class PrintsError(Exception):
    """The printings for a card could not be fetched or parsed."""


def _cache_path(oracle_id: str) -> Path:
    return CACHE_DIR / f"{oracle_id}.json"


def _face_images(card: dict[str, Any]) -> tuple[str, str]:
    """(front, back) image URLs of one printing; empty strings when absent.

    Single-faced printings carry ``image_uris`` at the top level; double-faced
    ones carry per-face ``image_uris`` under ``card_faces``.
    """
    uris = card.get("image_uris")
    if uris:
        return uris.get("normal") or "", ""
    faces = card.get("card_faces") or []
    front = back = ""
    if faces and faces[0].get("image_uris"):
        front = faces[0]["image_uris"].get("normal") or ""
    if len(faces) > 1 and faces[1].get("image_uris"):
        back = faces[1]["image_uris"].get("normal") or ""
    return front, back


# Real scans, by Scryfall's own verdict. `placeholder`/`missing` printings are
# stock filler images, not the card: they are dropped at normalization so no
# downstream policy can ever pick one.
_REAL_SCANS = ("highres_scan", "lowres")


def _normalize(card: dict[str, Any]) -> dict[str, Any] | None:
    """One Scryfall card object -> the picker's printing row, or None to skip.

    A printing without a front image cannot be shown or printed, and a
    placeholder "scan" is not the card's art at all — both are dropped here
    rather than handled downstream.
    """
    image_status = card.get("image_status") or ""
    if image_status not in _REAL_SCANS:
        return None
    front, back = _face_images(card)
    if not front:
        return None
    return {
        "scryfall_id": card.get("id") or "",
        "set_code": card.get("set") or "",
        "set_name": card.get("set_name") or "",
        "collector_number": card.get("collector_number") or "",
        "lang": card.get("lang") or "en",
        "released_at": card.get("released_at") or "",
        "image_status": image_status,
        "highres": image_status == "highres_scan",
        "image_uri_normal": front,
        "image_uri_back_normal": back,
    }


def _download_prints(oracle_id: str) -> list[dict[str, Any]]:
    prints: list[dict[str, Any]] = []
    params: dict[str, Any] | None = {
        "q": _QUERY_TEMPLATE.format(oracle_id=oracle_id),
        "unique": "prints",
        "include_multilingual": "true",
        "order": "released",
        "dir": "desc",
    }
    url = _SEARCH_URL
    for _ in range(_MAX_PAGES):
        try:
            response = httpx.get(url, params=params, headers=HEADERS, timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            raise PrintsError(f"Scryfall search failed for {oracle_id}: {exc}") from exc
        time.sleep(_REQUEST_SLEEP_S)
        if response.status_code == 404:
            # Scryfall answers 404 for a search with no results: an unknown
            # oracle_id or a card with no non-digital es/en printing. An empty
            # list is the honest answer either way.
            break
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise PrintsError(f"Scryfall search failed for {oracle_id}: {exc}") from exc
        for card in payload.get("data") or []:
            row = _normalize(card)
            if row is not None:
                prints.append(row)
        if not payload.get("has_more"):
            break
        url = payload.get("next_page") or ""
        params = None  # next_page already carries the query string
        if not url:
            break
    else:
        logger.warning(
            "Printings for %s truncated at %d pages (%d rows kept)",
            oracle_id, _MAX_PAGES, len(prints),
        )
    return prints


def fetch_prints(oracle_id: str) -> list[dict[str, Any]]:
    """All es/en physical printings of a card, cached on disk by oracle_id.

    Serves ``data/cache/prints/<oracle_id>.json`` when present; otherwise
    searches Scryfall (paginated), normalizes, caches atomically and returns.
    Raises ``PrintsError`` on a network/parse failure with no cache to fall
    back on. An unknown oracle_id yields an empty list, not an error — the
    caller decides whether that is a 404.
    """
    cache_path = _cache_path(oracle_id)
    if cache_path.exists():
        try:
            rows = json.loads(cache_path.read_text(encoding="utf-8"))
            # Schema check: rows cached before `image_status` existed may hide
            # placeholder "scans" the current policies must never pick.
            if all("image_status" in row for row in rows):
                return rows
            logger.info("Prints cache %s predates image_status; refetching", cache_path)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Corrupt prints cache %s (%s); refetching", cache_path, exc)

    prints = _download_prints(oracle_id)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(prints, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(cache_path)
    logger.info("Cached %d printings for %s", len(prints), oracle_id)
    return prints
