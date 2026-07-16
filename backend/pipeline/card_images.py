"""Scryfall card-image fetcher with a disk cache.

The proxy-PDF export needs the raw JPEG bytes of each card's
``image_uri_normal`` (and the back face of double-faced cards). Scryfall asks
callers to cache images and not hammer their CDN, so every download is written
under ``data/cache/card_images/``, keyed by a hash of the URL, and served from
there on every later request. Mirrors ``pipeline.edhrec``'s cache discipline:
same ``User-Agent``, atomic ``.tmp`` -> ``replace`` writes, and no time-based
expiry (a printing's image never changes).
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import httpx

from pipeline.scryfall import REPO_ROOT

logger = logging.getLogger(__name__)

CACHE_DIR = REPO_ROOT / "data" / "cache" / "card_images"

# The same identity the EDHREC client sends: one project, one User-Agent, so
# Scryfall sees a single well-behaved caller.
HEADERS = {"User-Agent": "commander-deckbuilder/0.1"}

_TIMEOUT = httpx.Timeout(30.0)

# Scryfall asks callers not to hammer their CDN. A short pause after each
# *uncached* download keeps a first-time build (~78 images) polite; the
# cache-hit path never sleeps, so a rebuilt deck is instant.
_DOWNLOAD_SLEEP_S = 0.1


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.jpg"


def fetch_card_image(url: str) -> bytes:
    """Return the bytes of a Scryfall card image, downloading it at most once.

    Serves ``data/cache/card_images/<sha256(url)>.jpg`` when it exists;
    otherwise downloads the image, writes it atomically to that path and pauses
    briefly to stay a good Scryfall citizen. Raises ``httpx.HTTPError`` when the
    download fails and there is no cached copy to fall back on.
    """
    cache_path = _cache_path(url)
    if cache_path.exists():
        return cache_path.read_bytes()

    logger.info("Downloading card image %s", url)
    response = httpx.get(url, headers=HEADERS, timeout=_TIMEOUT)
    response.raise_for_status()
    content = response.content

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".jpg.tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(cache_path)
    logger.info("Card image cached at %s (%d bytes)", cache_path, len(content))

    time.sleep(_DOWNLOAD_SLEEP_S)
    return content
