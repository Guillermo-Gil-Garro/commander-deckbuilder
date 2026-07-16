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
``degraded`` on ``/health`` and answer 503 on the deck endpoints — the
Space must come up and show the diagnosis instead of crash-looping.

Everything else is versioned config, and a half-loaded config is worse than
no service at all: no ``quotas.yaml`` means no bands, broken ``rules.yaml``
means decks silently shipped without Sol Ring, a half-resolved banlist means
illegal decks, and a missing tag store means every card falls into
``synergy`` while the solver happily builds a plausible 99 that violates
every quota *and nobody notices*. Those raise and the app refuses to start.
"""

from __future__ import annotations

import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from pipeline.edhrec import EdhrecCommanderData
from quotas.config import DEFAULT_QUOTAS_PATH, QuotasConfig, load_quotas
from rules.banlist import (
    DEFAULT_BANLIST_PATH,
    ResolvedBanlist,
    banlist_names,
    load_banlist,
    resolve_banlist,
)
from rules.featured import (
    DEFAULT_FEATURED_PATH,
    FeaturedCommander,
    FeaturedError,
    load_featured,
)
from rules.resolve import DEFAULT_POOL_PATH, REPO_ROOT, NameIndex, name_index_from_cards
from selector.deck_rules import DEFAULT_RULES_PATH, RulesConfig, load_rules, validate_rules_names
from selector.greedy import PoolIndex, SelectorError, load_pool
from tags.store import DEFAULT_STORE_PATH, TagStoreError, load_tags, tagger_from_store

logger = logging.getLogger(__name__)

# 10 s of solver on the Space's 2 shared vCPUs can be 30 s of wall clock.
SOLVER_TIME_LIMIT_ENV = "DECKBUILDER_SOLVER_TIME_LIMIT"
DEFAULT_SOLVER_TIME_LIMIT_S = 10.0

# EDHREC popularity ranking (canonical name -> num_decks), built offline by
# scripts/precache_edhrec_ranking.py. A committed data artifact, not config: it
# is optional and degrades to an empty map (alphabetical commander order).
DEFAULT_RANKING_PATH = REPO_ROOT / "data" / "edhrec_ranking.json"

# EDHREC pages are ~1 MB parsed; a bounded memo keeps a busy Space from
# growing without limit while still absorbing repeat picks of the same
# commander (the featured list is what most players click).
EDHREC_MEMO_MAX = 64

# Name search (commanders and the whole pool alike). Below the minimum we
# return nothing rather than 30k rows; the limit is clamped (not rejected)
# because a bad limit is not worth a 422.
COMMANDER_SEARCH_MIN_CHARS = 2
COMMANDER_SEARCH_LIMIT_DEFAULT = 20
COMMANDER_SEARCH_LIMIT_MIN = 1
COMMANDER_SEARCH_LIMIT_MAX = 50

# The card typeahead reuses the commander search's policy verbatim: same
# minimum, same clamp, same ranking. One rule for "search a name here", so the
# two boxes in the UI cannot drift apart.
CARD_SEARCH_MIN_CHARS = COMMANDER_SEARCH_MIN_CHARS
CARD_SEARCH_LIMIT_DEFAULT = COMMANDER_SEARCH_LIMIT_DEFAULT
CARD_SEARCH_LIMIT_MIN = COMMANDER_SEARCH_LIMIT_MIN
CARD_SEARCH_LIMIT_MAX = COMMANDER_SEARCH_LIMIT_MAX


def _ranked_names(
    names: Iterable[str], query: str, *, min_chars: int, limit: int, lo: int, hi: int
) -> tuple[str, ...]:
    """Substring search over pre-sorted ``names``, prefix matches first.

    The ranking every name box in this API shares: case-insensitive
    exact-substring, prefix matches ahead of mere substring ones ("Krenko"
    must not sit under "Fake Krenko Impersonator"), each group keeping the
    caller's alphabetical order — so the result is fully deterministic and
    there is no fuzzy matching (a typo returns nothing, never a guess).

    ``names`` must already be sorted; a query shorter than ``min_chars``
    returns nothing, and ``limit`` is clamped to ``[lo, hi]``.
    """
    needle = query.strip().lower()
    if len(needle) < min_chars:
        return ()
    limit = min(max(limit, lo), hi)

    prefix: list[str] = []
    contains: list[str] = []
    for name in names:
        lowered = name.lower()
        if lowered.startswith(needle):
            prefix.append(name)
        elif needle in lowered:
            contains.append(name)
    return tuple((prefix + contains)[:limit])


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
    # Canonical commander name -> EDHREC num_decks. Empty when the ranking
    # artifact is absent; the picker then falls back to alphabetical order.
    edhrec_num_decks: Mapping[str, int]
    # reasons and the human-facing card/rule structure come from.
    commanders: tuple[CommanderRow, ...] = field(init=False)
    card_names: tuple[str, ...] = field(init=False)
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
        # Sorted once here rather than per search: the whole pool is ~31k names
        # and the typeahead is on the keystroke path.
        object.__setattr__(self, "card_names", tuple(sorted(self.pool.by_name)))
        object.__setattr__(
            self, "_by_lower_name", {row.name.lower(): row for row in commanders}
        )
        object.__setattr__(self, "_edhrec", EdhrecMemo())

    def commander_by_name(self, name: str) -> CommanderRow | None:
        """Case-insensitive exact lookup by canonical pool name."""
        return self._by_lower_name.get(name.lower())

    def search_commanders(
        self, query: str, limit: int = COMMANDER_SEARCH_LIMIT_DEFAULT
    ) -> tuple[CommanderRow, ...]:
        """Substring search over **selectable commander** names, best first.

        See ``_ranked_names`` for the ranking. Banned commanders are not in
        this index and can never appear. A query shorter than
        ``COMMANDER_SEARCH_MIN_CHARS`` returns nothing; ``limit`` is clamped
        to ``[1, 50]``.
        """
        by_name = {row.name: row for row in self.commanders}
        names = _ranked_names(
            by_name,  # already sorted by name
            query,
            min_chars=COMMANDER_SEARCH_MIN_CHARS,
            limit=limit,
            lo=COMMANDER_SEARCH_LIMIT_MIN,
            hi=COMMANDER_SEARCH_LIMIT_MAX,
        )
        return tuple(by_name[name] for name in names)

    def search_cards(
        self, query: str, limit: int = CARD_SEARCH_LIMIT_DEFAULT
    ) -> tuple[str, ...]:
        """Substring search over the **whole pool**, best matches first.

        Every Commander-legal card, not just the commanders and not just a
        deck's cards: this is what the "add a card" box searches, so a card
        the group banned *is* here. That it can be typed does not mean it can
        be played — ``/why-not`` is what answers that.

        Canonical names only ("Fire // Ice", never "Fire"); the pool's own
        face-name fallback resolves the halves everywhere a name is accepted.
        """
        return _ranked_names(
            self.card_names,
            query,
            min_chars=CARD_SEARCH_MIN_CHARS,
            limit=limit,
            lo=CARD_SEARCH_LIMIT_MIN,
            hi=CARD_SEARCH_LIMIT_MAX,
        )

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


def load_edhrec_ranking(path: Path | str = DEFAULT_RANKING_PATH) -> dict[str, int]:
    """Load the EDHREC popularity map, or an empty one if it is unusable.

    A **data artifact**, not config (see the module docstring's failure rule):
    it is committed but optional, so a missing/corrupt file degrades to an
    empty ranking — the commander picker falls back to alphabetical order — and
    never stops the app from starting. Keys are canonical pool names; values
    are ``num_decks`` on EDHREC.
    """
    path = Path(path)
    if not path.is_file():
        logger.warning(
            "EDHREC ranking not found at %s; commanders will be ordered "
            "alphabetically. Run scripts/precache_edhrec_ranking.py to build it.",
            path,
        )
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "EDHREC ranking at %s is unreadable (%s); ordering commanders "
            "alphabetically instead.",
            path,
            exc,
        )
        return {}
    if not isinstance(raw, dict):
        logger.warning(
            "EDHREC ranking at %s is not a JSON object; ignoring it.", path
        )
        return {}
    ranking: dict[str, int] = {}
    for name, num_decks in raw.items():
        if isinstance(name, str) and isinstance(num_decks, int):
            ranking[name] = num_decks
    logger.info("Loaded EDHREC ranking from %s: %d commanders", path, len(ranking))
    return ranking


def build_app_state(
    *,
    pool_path: Path | str = DEFAULT_POOL_PATH,
    quotas_path: Path | str = DEFAULT_QUOTAS_PATH,
    rules_path: Path | str = DEFAULT_RULES_PATH,
    banlist_path: Path | str = DEFAULT_BANLIST_PATH,
    featured_path: Path | str = DEFAULT_FEATURED_PATH,
    tags_path: Path | str = DEFAULT_STORE_PATH,
    ranking_path: Path | str = DEFAULT_RANKING_PATH,
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
            "and /health will report 'degraded': %s",
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

    edhrec_num_decks = load_edhrec_ranking(ranking_path)

    if solver_time_limit_s is None:
        solver_time_limit_s = _solver_time_limit()

    state = AppState(
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
        edhrec_num_decks=edhrec_num_decks,
    )

    # load_featured rejects `banned_as_commander` but not `banned`, so a card
    # could be featured on the landing page yet absent from search and refused
    # by the deck endpoint. That contradiction between two versioned configs
    # is a startup error, not something for a handler to paper over.
    unselectable = [
        commander.name
        for commander in state.featured
        if state.commander_by_name(commander.name) is None
    ]
    if unselectable:
        raise FeaturedError(
            f"featured commanders are not selectable (banned in the group "
            f"banlist): {unselectable}"
        )
    return state
