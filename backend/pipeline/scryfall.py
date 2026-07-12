"""Scryfall bulk data client.

Downloads the ``oracle_cards`` bulk file to ``data/cache/`` (relative to the
repo root) with a small metadata sidecar used to skip re-downloads when the
local copy is up to date.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
BULK_TYPE = "oracle_cards"

# Scryfall API guidelines require an identifying User-Agent and Accept header.
HEADERS = {
    "User-Agent": "commander-deckbuilder/0.1",
    "Accept": "application/json",
}

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data" / "cache"
BULK_FILE = CACHE_DIR / "oracle_cards.json"
META_FILE = CACHE_DIR / "oracle_cards.meta.json"

_DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, read=300.0)


class ScryfallError(RuntimeError):
    """Raised when the Scryfall bulk data cannot be fetched."""


def fetch_bulk_metadata(client: httpx.Client) -> dict:
    """Return the bulk-data entry for the oracle_cards file."""
    response = client.get(BULK_DATA_URL, headers=HEADERS)
    response.raise_for_status()
    payload = response.json()
    for entry in payload.get("data", []):
        if entry.get("type") == BULK_TYPE:
            return entry
    raise ScryfallError(f"Bulk type '{BULK_TYPE}' not found in {BULK_DATA_URL}")


def _load_cached_meta() -> dict | None:
    if not META_FILE.exists():
        return None
    try:
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read cache metadata %s: %s", META_FILE, exc)
        return None


def _is_cache_fresh(bulk_meta: dict) -> bool:
    if not BULK_FILE.exists():
        return False
    cached = _load_cached_meta()
    if cached is None:
        return False
    return cached.get("updated_at") == bulk_meta.get("updated_at")


def _download_bulk(client: httpx.Client, bulk_meta: dict) -> None:
    download_uri = bulk_meta.get("download_uri")
    if not download_uri:
        raise ScryfallError("Bulk metadata is missing 'download_uri'")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = BULK_FILE.with_suffix(".json.tmp")
    logger.info("Downloading %s to %s", download_uri, BULK_FILE)

    try:
        with client.stream(
            "GET", download_uri, headers=HEADERS, timeout=_DOWNLOAD_TIMEOUT
        ) as response:
            response.raise_for_status()
            with tmp_path.open("wb") as fh:
                for chunk in response.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        tmp_path.unlink(missing_ok=True)
        raise ScryfallError(f"Failed to download bulk data: {exc}") from exc

    tmp_path.replace(BULK_FILE)
    META_FILE.write_text(
        json.dumps(
            {
                "updated_at": bulk_meta.get("updated_at"),
                "download_uri": download_uri,
                "size": bulk_meta.get("size"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Bulk data saved (%d bytes)", BULK_FILE.stat().st_size)


def ensure_oracle_cards() -> Path:
    """Download the oracle_cards bulk file if the local cache is stale.

    Returns the path to the local JSON file.
    """
    with httpx.Client(timeout=_DOWNLOAD_TIMEOUT) as client:
        bulk_meta = fetch_bulk_metadata(client)
        if _is_cache_fresh(bulk_meta):
            logger.info(
                "Cache is fresh (updated_at=%s), skipping download",
                bulk_meta.get("updated_at"),
            )
            return BULK_FILE
        _download_bulk(client, bulk_meta)
    return BULK_FILE
