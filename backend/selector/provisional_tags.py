"""Provisional tagger backed by the Scryfall oracle-tag experiment cache.

The greedy selector takes any ``Callable[[str], set[str]]`` as its tagging
engine; this module builds the *provisional* one from the otag name lists
cached by the Fase 2 experiment (``experiments/tagging/cache/scryfall_otags``,
zero network). When Guille picks the definitive tagging engine, a different
callable is plugged in and the selector stays untouched.

``lands`` has no otag: it is derived from the pool's ``type_line`` (front-face
only in the current pool model, so back-face MDFC lands are missed — a
declared limitation of the prototype, not of the selector).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OTAG_CACHE_DIR = REPO_ROOT / "experiments" / "tagging" / "cache" / "scryfall_otags"

# Mirror of the approved mapping in experiments/tagging/methods/scryfall_otags.py
# (kept in sync by hand; the experiment file is the documented source).
OTAG_TO_CATEGORY: dict[str, str] = {
    "ramp": "ramp",
    "spot-removal": "removal",
    "counterspell": "removal",
    "draw": "card_draw",
    "cantrip": "card_draw",
    "boardwipe": "board_wipe",
    "win-condition": "wincons",
    "extra-turn": "wincons",
    "extra-combat": "wincons",
    "overrun": "wincons",
    "damage-multiplier": "wincons",
}

LANDS_CATEGORY = "lands"

_LAND_TYPE_RE = re.compile(r"\bLand\b")


class TaggerError(Exception):
    """The otag cache is missing or unreadable."""


def load_otag_membership(
    cache_dir: Path | str = DEFAULT_OTAG_CACHE_DIR,
) -> dict[str, set[str]]:
    """category -> set of matchable card names (full "A // B" names plus faces).

    Raises ``TaggerError`` if any mapped otag cache file is missing or invalid:
    a hole in the cache is a setup bug, not an empty category.
    """
    cache_dir = Path(cache_dir)
    membership: dict[str, set[str]] = {}
    for tag, category in OTAG_TO_CATEGORY.items():
        path = cache_dir / f"{tag}.json"
        if not path.is_file():
            raise TaggerError(f"otag cache file not found: {path}")
        try:
            names = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TaggerError(f"could not read otag cache {path}: {exc}") from exc
        bucket = membership.setdefault(category, set())
        for name in names:
            bucket.add(name)
            if " // " in name:
                bucket.update(name.split(" // "))
    return membership


def _is_land_type_line(type_line: str) -> bool:
    return any(_LAND_TYPE_RE.search(face) for face in type_line.split(" // "))


def otag_tagger(
    pool_cards: Iterable[Mapping[str, Any]],
    cache_dir: Path | str = DEFAULT_OTAG_CACHE_DIR,
) -> Callable[[str], set[str]]:
    """Build the provisional ``name -> categories`` callable.

    Functional categories come from the otag cache; ``lands`` comes from the
    pool's ``type_line``. A name is matched by its full form or any face.
    Unknown names simply return an empty set (the selector maps that to the
    ``synergy`` bucket).
    """
    membership = load_otag_membership(cache_dir)
    land_names: set[str] = set()
    for card in pool_cards:
        if _is_land_type_line(card.get("type_line", "")):
            name = card["name"]
            land_names.add(name)
            if " // " in name:
                land_names.update(name.split(" // "))
    logger.debug(
        "otag tagger ready: %d land names, categories %s",
        len(land_names),
        sorted(membership),
    )

    def tag(name: str) -> set[str]:
        variants = {name}
        if " // " in name:
            variants.update(name.split(" // "))
        categories = {
            category
            for category, names in membership.items()
            if variants & names
        }
        if variants & land_names:
            categories.add(LANDS_CATEGORY)
        return categories

    return tag
