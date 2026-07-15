"""Application state: every artifact the API needs, loaded once at startup.

``build_app_state`` is called from the lifespan and returns an immutable
``AppState`` that handlers read through ``request.app.state``. Nothing here
touches the network and nothing is loaded per request — the 16 MB pool is
parsed exactly once, and the name index, the banlist projection and the
tagger are all built from that same in-memory copy.

**Failure policy — one rule.** A gitignored *data artifact* degrades; a
*versioned config* fails hard.

The only data artifact is the card pool: it is gitignored, 16 MB and not in
the Docker image today. Missing it makes the app start anyway, report
``degraded`` on ``/api/health`` and answer 503 on the deck endpoints — the
Space must come up and show the diagnosis instead of crash-looping.

Everything else is versioned config, and a half-loaded config is worse than
no service at all: no ``quotas.yaml`` means no bands, broken ``rules.yaml``
means decks silently shipped without Sol Ring, a half-resolved banlist means
illegal decks, and a missing tag store means every card falls into
``synergy`` while the solver happily builds a plausible 99 that violates
every quota *and nobody notices*. Those raise and the app refuses to start.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from pipeline.edhrec import EdhrecCommanderData
from quotas.config import DEFAULT_QUOTAS_PATH, QuotasConfig, load_quotas
from rules.banlist import (
    DEFAULT_BANLIST_PATH,
    ResolvedBanlist,
    banlist_names,
    load_banlist,
    resolve_banlist,
)
from rules.featured import DEFAULT_FEATURED_PATH, FeaturedCommander, load_featured
from rules.resolve import DEFAULT_POOL_PATH, NameIndex, name_index_from_cards
from selector.deck_rules import DEFAULT_RULES_PATH, RulesConfig, load_rules, validate_rules_names
from selector.greedy import PoolIndex, SelectorError, load_pool
from tags.store import DEFAULT_STORE_PATH, TagStoreError, load_tags, tagger_from_store

logger = logging.getLogger(__name__)

# 10 s of solver on the Space's 2 shared vCPUs can be 30 s of wall clock.
SOLVER_TIME_LIMIT_ENV = "DECKBUILDER_SOLVER_TIME_LIMIT"
DEFAULT_SOLVER_TIME_LIMIT_S = 10.0

# EDHREC pages are ~1 MB parsed; a bounded memo keeps a busy Space from
# growing without limit while still absorbing repeat picks of the same
# commander (the featured list is what most players click).
EDHREC_MEMO_MAX = 64


class EdhrecMemo:
    """Bounded FIFO memo of parsed EDHREC pages, keyed by ``(slug, variant)``.

    FIFO rather than LRU on purpose: the win here is capping memory, and the
    access pattern (one fetch per deck build) makes recency worth little.
    """

    def __init__(self, max_entries: int = EDHREC_MEMO_MAX) -> None:
        self._entries: OrderedDict[tuple[str, str | None], EdhrecCommanderData] = (
            OrderedDict()
        )
        self._max_entries = max_entries

    def get(self, slug: str, variant: str | None = None) -> EdhrecCommanderData | None:
        return self._entries.get((slug, variant))

    def put(
        self, slug: str, data: EdhrecCommanderData, variant: str | None = None
    ) -> None:
        key = (slug, variant)
        if key in self._entries:
            return
        self._entries[key] = data
        while len(self._entries) > self._max_entries:
            evicted, _ = self._entries.popitem(last=False)
            logger.debug("EDHREC memo full: evicted %s", evicted)

    def __len__(self) -> int:
        return len(self._entries)


@dataclass(frozen=True)
class CommanderRow:
    """One selectable commander, projected from the pool for the API."""

    name: str
    oracle_id: str
    scryfall_id: str
    color_identity: tuple[str, ...]


@dataclass(frozen=True)
class AppState:
    """Everything the handlers need, built once and never mutated."""

    pool: PoolIndex
    name_index: NameIndex
    quotas: QuotasConfig
    rules: RulesConfig
    resolved_banlist: ResolvedBanlist
    banned_names: frozenset[str]
    watchlist_names: frozenset[str]
    tagger: Callable[[str], set[str]]
    featured: tuple[FeaturedCommander, ...]
    tags_count: int
    solver_time_limit_s: float
    commanders: tuple[CommanderRow, ...] = field(init=False)
    _by_lower_name: Mapping[str, CommanderRow] = field(init=False)
    _edhrec: EdhrecMemo = field(init=False)

    def __post_init__(self) -> None:
        commanders = tuple(
            sorted(
                (
                    _commander_row(card)
                    for card in self.pool.cards()
                    if _is_selectable_commander(card, self.resolved_banlist)
                ),
                key=lambda row: row.name,
            )
        )
        # frozen=True blocks plain assignment; these are derived, not inputs.
        object.__setattr__(self, "commanders", commanders)
        object.__setattr__(
            self, "_by_lower_name", {row.name.lower(): row for row in commanders}
        )
        object.__setattr__(self, "_edhrec", EdhrecMemo())

    def commander_by_name(self, name: str) -> CommanderRow | None:
        """Case-insensitive exact lookup by canonical pool name."""
        return self._by_lower_name.get(name.lower())

    @property
    def edhrec_memo(self) -> EdhrecMemo:
        return self._edhrec


def _is_selectable_commander(
    card: Mapping[str, Any], banlist: ResolvedBanlist
) -> bool:
    """Commander-eligible and not banned in any form.

    ``banned`` literally speaks about the 99, but a card the group threw out
    of the format must not come back as the face of the deck (Guille's call),
    so both ban sets hide a card from the commander selector.
    """
    if not card.get("is_commander_eligible"):
        return False
    oracle_id = card.get("oracle_id")
    return (
        oracle_id not in banlist.banned
        and oracle_id not in banlist.banned_as_commander
    )


def _commander_row(card: Mapping[str, Any]) -> CommanderRow:
    return CommanderRow(
        name=card["name"],
        oracle_id=card["oracle_id"],
        scryfall_id=card["scryfall_id"],
        color_identity=tuple(card.get("color_identity") or ()),
    )


def _solver_time_limit() -> float:
    raw = os.environ.get(SOLVER_TIME_LIMIT_ENV)
    if raw is None:
        return DEFAULT_SOLVER_TIME_LIMIT_S
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(
            f"{SOLVER_TIME_LIMIT_ENV} must be a number, got {raw!r}"
        ) from None
    if value <= 0:
        raise ValueError(f"{SOLVER_TIME_LIMIT_ENV} must be positive, got {value}")
    return value


def build_app_state(
    *,
    pool_path: Path | str = DEFAULT_POOL_PATH,
    quotas_path: Path | str = DEFAULT_QUOTAS_PATH,
    rules_path: Path | str = DEFAULT_RULES_PATH,
    banlist_path: Path | str = DEFAULT_BANLIST_PATH,
    featured_path: Path | str = DEFAULT_FEATURED_PATH,
    tags_path: Path | str = DEFAULT_STORE_PATH,
    solver_time_limit_s: float | None = None,
) -> AppState | None:
    """Load every artifact the API serves from. ``None`` means degraded.

    Returns ``None`` only when the card pool is unavailable — the app still
    starts and reports the problem. Every other failure raises its own typed
    domain error (``QuotasError``, ``DeckRulesError``, ``BanlistError``,
    ``TagStoreError``, ``FeaturedError``) and aborts startup. See the module
    docstring for why the line is drawn there.

    Every path is a parameter so tests point at fixtures without patching
    module globals.
    """
    try:
        pool = load_pool(pool_path)
    except SelectorError as exc:
        logger.error(
            "Card pool not usable at %s; every deck endpoint will return 503 "
            "and /api/health will report 'degraded': %s",
            pool_path,
            exc,
        )
        return None
    logger.info("Loaded card pool from %s: %d cards", pool_path, len(pool.by_name))

    pool_cards = list(pool.cards())
    name_index = name_index_from_cards(pool_cards)

    quotas = load_quotas(quotas_path)
    rules = load_rules(rules_path, valid_archetypes=set(quotas.archetypes))
    validate_rules_names(rules, pool.resolve)

    resolved_banlist = resolve_banlist(load_banlist(banlist_path), name_index)
    banned_names, watchlist_names = banlist_names(resolved_banlist, pool_cards)

    tags_path = Path(tags_path)
    # load_tags treats a missing store as empty, which is right for the batch
    # tooling and catastrophic here: llm_tags.jsonl is versioned, so its
    # absence is a packaging bug, and an empty store would dump every card
    # into `synergy` and quietly break every quota.
    if not tags_path.is_file():
        raise TagStoreError(
            f"tag store not found: {tags_path} (it is versioned in git; a "
            f"missing store would silently tag every card as 'synergy')"
        )
    tag_store = load_tags(tags_path)
    if not tag_store:
        raise TagStoreError(
            f"tag store {tags_path} is empty: every card would fall into "
            f"'synergy' and every quota would be silently violated"
        )
    tagger = tagger_from_store(tag_store, pool_cards)

    featured = load_featured(
        featured_path, resolved_banlist=resolved_banlist, name_index=name_index
    )

    if solver_time_limit_s is None:
        solver_time_limit_s = _solver_time_limit()

    return AppState(
        pool=pool,
        name_index=name_index,
        quotas=quotas,
        rules=rules,
        resolved_banlist=resolved_banlist,
        banned_names=banned_names,
        watchlist_names=watchlist_names,
        tagger=tagger,
        featured=tuple(featured),
        tags_count=len(tag_store),
        solver_time_limit_s=solver_time_limit_s,
    )
