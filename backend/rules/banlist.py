"""Group banlist: pydantic models for ``banlist.yaml`` plus the resolved view.

``load_banlist`` parses and schema-validates the YAML; ``resolve_banlist``
turns it into oracle_id sets by resolving every card name against the pool
(``rules.resolve``) and FAILS on any unresolvable or ambiguous name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from rules.resolve import (
    REPO_ROOT,
    NameIndex,
    ResolutionError,
)

logger = logging.getLogger(__name__)

DEFAULT_BANLIST_PATH = REPO_ROOT / "banlist.yaml"

# Statuses that make a card illegal in the 99.
BANNED_STATUSES: frozenset[str] = frozenset({"banned", "banned_pending_review"})


class BanlistError(Exception):
    """Invalid, unreadable or unresolvable banlist."""


class BanlistMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    status: str
    updated: str
    review_cycle: str
    notes: str


class RuleException(BaseModel):
    """A card explicitly exempted from a programmatic rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    reason: str


class BanRule(BaseModel):
    """Programmatic rule with its resolved-cards snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    status: Literal["banned", "banned_pending_review"]
    predicate: str
    reason: str
    resolved_cards: list[str]
    exceptions: list[RuleException] = Field(default_factory=list)
    review_condition: str | None = None


class CardBan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: Literal["banned", "banned_pending_review"]
    reason: str
    # Tag linking bans that close the same line (e.g. alt_win_empty_library).
    reason_group: str | None = None
    # Archetypes (quotas.yaml) where this otherwise-banned card is on-theme and
    # therefore legal: the card stays globally banned, but a deck of one of
    # these archetypes may run it. Empty (the default) = banned everywhere.
    # Colour identity filters on its own — a blue card only reaches blue decks.
    legal_in_archetypes: list[str] = Field(default_factory=list)


class CommanderBan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: Literal["banned_as_commander"]
    reason: str


class WatchlistEntry(BaseModel):
    """Legal but never auto-recommended; ``scope`` narrows where it applies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    reason: str
    scope: str | None = None


class ExplicitlyLegalEntry(BaseModel):
    """Either a named card or a whole category, declared legal for the record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = None
    category: str | None = None
    note: str

    @model_validator(mode="after")
    def _name_xor_category(self) -> "ExplicitlyLegalEntry":
        if (self.name is None) == (self.category is None):
            raise ValueError("exactly one of 'name' or 'category' is required")
        return self


class NominationRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    who: str
    cooldown: str
    threshold: str
    effect: str
    logging: str


class Banlist(BaseModel):
    """Parsed ``banlist.yaml`` (names not yet resolved against the pool)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    meta: BanlistMeta
    rules: list[BanRule]
    cards: list[CardBan]
    commanders: list[CommanderBan]
    watchlist: list[WatchlistEntry]
    explicitly_legal: list[ExplicitlyLegalEntry]
    nomination_rule: NominationRule


@dataclass(frozen=True)
class ResolvedBanlist:
    """Banlist projected onto pool oracle_ids.

    ``banned``: illegal in the 99 (manual bans + rule snapshots, minus rule
    exceptions). ``banned_as_commander``: hidden from the commander selector,
    legal in the 99. ``watchlist``: oracle_id -> scope (``None`` = everywhere).
    ``explicitly_legal``: named entries, resolved for the record.
    """

    banned: frozenset[str]
    banned_as_commander: frozenset[str]
    watchlist: Mapping[str, str | None]
    explicitly_legal: frozenset[str]


def load_banlist(path: Path | str = DEFAULT_BANLIST_PATH) -> Banlist:
    """Load and schema-validate a banlist YAML file.

    Raises ``BanlistError`` for a missing file, malformed YAML or any schema
    violation (unknown key, wrong status, name+category, ...).
    """
    path = Path(path)
    if not path.is_file():
        raise BanlistError(f"banlist file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BanlistError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BanlistError(
            f"banlist file must be a mapping at the top level, "
            f"got {type(raw).__name__}: {path}"
        )
    try:
        banlist = Banlist.model_validate(raw)
    except ValidationError as exc:
        raise BanlistError(f"invalid banlist in {path}: {exc}") from exc
    logger.debug(
        "Loaded banlist %s (version %s): %d rules, %d cards, %d commanders, "
        "%d watchlist, %d explicitly legal",
        path,
        banlist.meta.version,
        len(banlist.rules),
        len(banlist.cards),
        len(banlist.commanders),
        len(banlist.watchlist),
        len(banlist.explicitly_legal),
    )
    return banlist


def _resolve(index: NameIndex, name: str, context: str) -> str:
    try:
        return index.resolve(name).oracle_id
    except ResolutionError as exc:
        raise BanlistError(f"banlist {context}: {exc}") from exc


def resolve_banlist(banlist: Banlist, index: NameIndex) -> ResolvedBanlist:
    """Resolve every banlist name against the pool.

    Raises ``BanlistError`` if any name (including rule exceptions and named
    ``explicitly_legal`` entries) does not resolve to exactly one pool card.
    """
    banned: set[str] = set()
    for rule in banlist.rules:
        if rule.status not in BANNED_STATUSES:  # pragma: no cover - Literal-guarded
            continue
        rule_ids = {
            _resolve(index, name, f"rule {rule.id!r}") for name in rule.resolved_cards
        }
        exception_ids = {
            _resolve(index, exc.name, f"rule {rule.id!r} exception")
            for exc in rule.exceptions
        }
        banned |= rule_ids - exception_ids

    for card in banlist.cards:
        if card.status in BANNED_STATUSES:
            banned.add(_resolve(index, card.name, "cards"))

    banned_as_commander = {
        _resolve(index, commander.name, "commanders")
        for commander in banlist.commanders
    }

    watchlist = {
        _resolve(index, entry.name, "watchlist"): entry.scope
        for entry in banlist.watchlist
    }

    explicitly_legal = {
        _resolve(index, entry.name, "explicitly_legal")
        for entry in banlist.explicitly_legal
        if entry.name is not None
    }

    return ResolvedBanlist(
        banned=frozenset(banned),
        banned_as_commander=frozenset(banned_as_commander),
        watchlist=watchlist,
        explicitly_legal=frozenset(explicitly_legal),
    )


def banlist_names(
    resolved: ResolvedBanlist, cards: Iterable[Mapping[str, Any]]
) -> tuple[frozenset[str], frozenset[str]]:
    """Canonical (banned_names, watchlist_names): the resolved oracle_ids
    projected onto the pool's names — the bridge between the formal resolver
    (oracle_ids) and the name-based contract of the selectors.

    Only ``banned`` and ``watchlist`` are projected: the API consumes
    ``banned_as_commander`` and ``explicitly_legal`` by oracle_id.

    Names are the pool's canonical ``name`` field ("A // B" for multi-faced
    cards); the selectors match faces themselves. ``cards.jsonl`` is oracle
    cards (one entry per oracle_id), so the projection is a bijection.

    v1 ignores the watchlist ``scope``: every watchlist entry lands in
    ``watchlist_names`` regardless of where it was meant to apply. That is
    exactly today's behaviour — no new semantics invented here.

    Raises ``BanlistError`` if a resolved oracle_id is absent from ``cards``.
    ``resolve_banlist`` resolved against this same pool, so a miss means the
    two came from different pools — a caller bug, never silently ignored.
    """
    wanted = resolved.banned | frozenset(resolved.watchlist)
    names: dict[str, str] = {}
    for card in cards:
        oracle_id = card.get("oracle_id")
        if oracle_id in wanted and oracle_id not in names:
            names[oracle_id] = card["name"]

    missing = wanted - names.keys()
    if missing:
        raise BanlistError(
            f"resolved banlist does not match the given cards: "
            f"{len(missing)} oracle_id(s) absent from the pool "
            f"({sorted(missing)}) — resolve_banlist and banlist_names must "
            f"run against the same pool"
        )

    banned_names = frozenset(names[oracle_id] for oracle_id in resolved.banned)
    watchlist_names = frozenset(names[oracle_id] for oracle_id in resolved.watchlist)
    logger.debug(
        "Projected banlist onto pool names: %d banned, %d watchlist",
        len(banned_names),
        len(watchlist_names),
    )
    return banned_names, watchlist_names


def banlist_archetype_exceptions(
    banlist: Banlist, index: NameIndex
) -> dict[str, frozenset[str]]:
    """Archetype -> the banned oracle_ids that are *legal* in that archetype.

    Reads the ``legal_in_archetypes`` field of each manual card ban: a card so
    tagged stays in the global ``banned`` set (``resolve_banlist`` is untouched)
    but is exempted for decks of the named archetypes. The result is the
    per-archetype exception set the API subtracts from the global ban.

    Raises ``BanlistError`` if a tagged card name does not resolve to exactly
    one pool card — same policy as ``resolve_banlist``: a versioned config that
    cannot be resolved is a startup failure, never a silent drop.
    """
    exceptions: dict[str, set[str]] = {}
    for card in banlist.cards:
        if card.status not in BANNED_STATUSES or not card.legal_in_archetypes:
            continue
        oracle_id = _resolve(index, card.name, "cards legal_in_archetypes")
        for archetype in card.legal_in_archetypes:
            exceptions.setdefault(archetype, set()).add(oracle_id)
    return {archetype: frozenset(ids) for archetype, ids in exceptions.items()}


def banlist_archetype_exception_names(
    exceptions: Mapping[str, frozenset[str]], cards: Iterable[Mapping[str, Any]]
) -> dict[str, frozenset[str]]:
    """Project the per-archetype exception oracle_ids onto pool names.

    The name-based twin of ``banlist_archetype_exceptions``, mirroring
    ``banlist_names``: the selectors speak names, so the exceptions must too.
    Names are the pool's canonical ``name`` field.

    Raises ``BanlistError`` if an exception oracle_id is absent from ``cards``
    — same contract as ``banlist_names``: a miss means the exceptions and the
    pool came from different sources, which is a caller bug, not to be ignored.
    """
    wanted: frozenset[str] = frozenset().union(*exceptions.values()) if exceptions else frozenset()
    names: dict[str, str] = {}
    for card in cards:
        oracle_id = card.get("oracle_id")
        if oracle_id in wanted and oracle_id not in names:
            names[oracle_id] = card["name"]

    missing = wanted - names.keys()
    if missing:
        raise BanlistError(
            f"archetype exceptions do not match the given cards: "
            f"{len(missing)} oracle_id(s) absent from the pool "
            f"({sorted(missing)}) — banlist_archetype_exceptions and "
            f"banlist_archetype_exception_names must run against the same pool"
        )

    return {
        archetype: frozenset(names[oracle_id] for oracle_id in ids)
        for archetype, ids in exceptions.items()
    }
