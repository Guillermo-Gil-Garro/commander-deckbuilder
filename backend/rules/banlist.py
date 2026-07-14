"""Group banlist: pydantic models for ``banlist.yaml`` plus the resolved view.

``load_banlist`` parses and schema-validates the YAML; ``resolve_banlist``
turns it into oracle_id sets by resolving every card name against the pool
(``rules.resolve``) and FAILS on any unresolvable or ambiguous name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

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
