"""Deck rules config: pydantic models and the ``rules.yaml`` loader.

``rules.yaml`` (repo root) supersedes ``staples.yaml`` (2026-07-14) as the
single source of truth for the group's composition policy, consumed by both
selectors (greedy and CP-SAT). Non-land cards only — lands belong to the
manabase module and are never forced from here.

Strict precedence: **ban > never > always > prefer**.

- ``always``: the card MUST be in the 99 when its ``when`` predicate matches
  (no ``when`` = unconditional). It consumes a slot of its ``quota_category``
  like any other card (``null`` = no quota category in v1: the card takes a
  general filler slot). The banlist and a matching ``never`` always win.
- ``never``: when its ``when`` matches, the card is never auto-recommended —
  neither mainboard nor maybeboard (same treatment as the banlist watchlist).
- ``preferred``: flat score ``boost`` when the commander identity matches the
  card's color predicates — ``colors_any`` (contains ANY listed color) and/or
  ``color_identity_contains`` (contains ALL listed colors, for two-color
  fixing like duals/fetches); both empty = every deck. Never forces the card
  in, but the selector injects a matching preferred card into the candidate
  pool with this boost as its base score (see ``cp_sat``).

``when`` predicates (v1) only use facts known BEFORE selection: commander
color identity, identity size, quota archetype and commander name. Predicates
over the deck under construction (``creature_count``, ...) are rejected at
load time with an explicit error.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Collection, Iterable, Iterator, Mapping

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from quotas.config import QuotasConfig, load_quotas

logger = logging.getLogger(__name__)

# Same anchoring pattern as quotas.config: the YAML lives at the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES_PATH = REPO_ROOT / "rules.yaml"

DEFAULT_PREFERRED_BOOST = 0.3
FACE_SEPARATOR = " // "
_WUBRG = ("W", "U", "B", "R", "G")

# Quota categories an always rule may consume. Lands are excluded on purpose:
# rules.yaml is non-lands only (the manabase module owns the lands).
FORCEABLE_QUOTA_CATEGORIES: tuple[str, ...] = (
    "ramp",
    "card_draw",
    "removal",
    "board_wipe",
    "wincons",
    "protection",
    "stax",
    "synergy",
)

_SIZE_COMPARATOR_RE = re.compile(r"(>=|<=|==|>|<)\s*(\d+)$")


class DeckRulesError(Exception):
    """Invalid, unreadable or unresolvable deck rules configuration."""


# ── `when` predicates ────────────────────────────────────────────────────────


class When(BaseModel):
    """One predicate block. Keys are ANDed; ``any_of`` ORs its sub-blocks.

    v1 only supports pre-selection facts. Unknown keys fail loudly, with a
    dedicated message for predicates over the already-built deck.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    color_identity_contains: tuple[str, ...] | None = None
    color_identity_size: int | str | None = None
    archetype_in: tuple[str, ...] | None = None
    archetype_not_in: tuple[str, ...] | None = None
    commander_in: tuple[str, ...] | None = None
    commander_not_in: tuple[str, ...] | None = None
    any_of: tuple["When", ...] | None = None

    @model_validator(mode="before")
    @classmethod
    def _clear_error_for_unknown_predicates(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        unknown = set(data) - set(cls.model_fields)
        if unknown:
            built_deck = {
                key
                for key in unknown
                if key.endswith("_count") or key.startswith("deck_")
            }
            if built_deck:
                raise ValueError(
                    f"predicados sobre el mazo construido no soportados en v1: "
                    f"{sorted(built_deck)} (v1 solo evalúa hechos previos a la "
                    f"selección: {sorted(cls.model_fields)})"
                )
            raise ValueError(
                f"predicado(s) desconocido(s): {sorted(unknown)} "
                f"(v1 soporta: {sorted(cls.model_fields)})"
            )
        return data

    @field_validator("color_identity_contains")
    @classmethod
    def _valid_colors(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return value
        unknown = set(value) - set(_WUBRG)
        if unknown:
            raise ValueError(
                f"unknown colors {sorted(unknown)} (expected one of {list(_WUBRG)})"
            )
        if not value:
            raise ValueError("color_identity_contains cannot be empty")
        return value

    @field_validator("color_identity_size")
    @classmethod
    def _valid_size(cls, value: int | str | None) -> int | str | None:
        if value is None or isinstance(value, int):
            if isinstance(value, int) and value < 0:
                raise ValueError(f"color_identity_size must be >= 0, got {value}")
            return value
        if not _SIZE_COMPARATOR_RE.fullmatch(value.strip()):
            raise ValueError(
                f"invalid color_identity_size {value!r}: expected an int or a "
                f"comparator string like '>=2', '<=1', '==3'"
            )
        return value

    @model_validator(mode="after")
    def _at_least_one_predicate(self) -> "When":
        if all(
            getattr(self, name) is None for name in type(self).model_fields
        ):
            raise ValueError("empty `when` block: at least one predicate required")
        return self


# ── rule entries ─────────────────────────────────────────────────────────────


class AlwaysRule(BaseModel):
    """One forced card: must be in the 99 when its ``when`` matches."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    # None = no quota category in v1 (e.g. protection): general filler slot.
    quota_category: str | None
    when: When | None = None
    note: str | None = None

    @field_validator("quota_category")
    @classmethod
    def _forceable_category(cls, value: str | None) -> str | None:
        if value is not None and value not in FORCEABLE_QUOTA_CATEGORIES:
            raise ValueError(
                f"quota_category {value!r} is not forceable (allowed: "
                f"{list(FORCEABLE_QUOTA_CATEGORIES)} or null; lands are owned "
                f"by the manabase module)"
            )
        return value


class NeverRule(BaseModel):
    """One excluded card: never auto-recommended when its ``when`` matches."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    when: When | None = None
    reason: str | None = None


class PreferredCard(BaseModel):
    """One allowed staple: flat score boost when the deck matches its colors.

    Two independent, ANDed color predicates (both empty = every deck):

    - ``colors_any``: identity contains ANY listed color. Right for a
      mono-colored staple usable across shards (Swords in any W deck).
    - ``color_identity_contains``: identity contains ALL listed colors — the
      ``When`` semantics. Right for two-color fixing (an ABUR dual or a
      fetchland is only fixing when the deck plays BOTH of its colors: an
      Underground Sea does nothing in a mono-U deck).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    colors_any: tuple[str, ...] = ()  # empty = applies to every deck
    color_identity_contains: tuple[str, ...] = ()  # empty = no all-of gate
    boost: float = Field(default=DEFAULT_PREFERRED_BOOST, gt=0)

    @field_validator("colors_any", "color_identity_contains")
    @classmethod
    def _valid_colors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = set(value) - set(_WUBRG)
        if unknown:
            raise ValueError(
                f"unknown colors {sorted(unknown)} (expected one of {list(_WUBRG)})"
            )
        if len(set(value)) != len(value):
            raise ValueError(f"duplicated colors in {list(value)!r}")
        return value


# ── documental / governance blocks ───────────────────────────────────────────


class UserOverride(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ban: str
    remove_always: str
    add_never_manually: str


class Semantics(BaseModel):
    """Documental block; the app honors these semantics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    precedence: tuple[str, ...]
    always: str
    never: str
    prefer: str
    user_override: UserOverride

    @field_validator("precedence")
    @classmethod
    def _strict_precedence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        expected = ("ban", "never", "always", "prefer")
        if value != expected:
            raise ValueError(f"precedence must be {list(expected)}, got {list(value)}")
        return value


class NominationRule(BaseModel):
    """Governance mirror of banlist.yaml's nomination_rule (same process)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    who: str
    cooldown: str
    threshold: str
    effect: str
    logging: str


class RulesMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "0"
    status: str = "draft"
    updated: str = ""
    # Max number of always rules that may match one legal commander.
    forced_slot_budget: int = Field(default=12, gt=0)
    notes: str = ""


class RulesConfig(BaseModel):
    """Parsed ``rules.yaml``: always/never/preferred plus governance blocks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    meta: RulesMeta = Field(default_factory=RulesMeta)
    semantics: Semantics | None = None
    always: tuple[AlwaysRule, ...] = ()
    never: tuple[NeverRule, ...] = ()
    preferred: tuple[PreferredCard, ...] = ()
    nomination_rule: NominationRule | None = None

    @model_validator(mode="after")
    def _no_duplicate_names(self) -> "RulesConfig":
        for label, items in (
            ("always", self.always),
            ("never", self.never),
            ("preferred", self.preferred),
        ):
            names = [item.name for item in items]
            if len(set(names)) != len(names):
                dupes = sorted({n for n in names if names.count(n) > 1})
                raise ValueError(f"duplicated names in {label}: {dupes}")
        return self


# ── loading and cross-validation ─────────────────────────────────────────────


def _iter_whens(config: RulesConfig) -> Iterator[When]:
    stack: list[When] = [
        rule.when
        for rule in (*config.always, *config.never)
        if rule.when is not None
    ]
    while stack:
        when = stack.pop()
        yield when
        if when.any_of is not None:
            stack.extend(when.any_of)


def _validate_archetypes(config: RulesConfig, valid: Collection[str]) -> None:
    valid_set = set(valid)
    for when in _iter_whens(config):
        for field_name in ("archetype_in", "archetype_not_in"):
            values = getattr(when, field_name)
            if values is None:
                continue
            unknown = set(values) - valid_set
            if unknown:
                raise DeckRulesError(
                    f"unknown archetype(s) {sorted(unknown)} in {field_name} "
                    f"(quotas.yaml defines: {sorted(valid_set)})"
                )


def load_rules(
    path: Path | str = DEFAULT_RULES_PATH,
    *,
    valid_archetypes: Collection[str] | None = None,
) -> RulesConfig:
    """Load and validate a rules YAML file.

    ``valid_archetypes`` gates every ``archetype_in`` / ``archetype_not_in``
    predicate; when omitted, the archetype names are read from the default
    ``quotas.yaml``. Raises ``DeckRulesError`` for a missing file, malformed
    YAML, any schema violation (unknown predicate, built-deck predicate,
    unforceable quota_category, duplicated names, ...) or an unknown
    archetype name.
    """
    path = Path(path)
    if not path.is_file():
        raise DeckRulesError(f"rules file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DeckRulesError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise DeckRulesError(
            f"rules file must be a mapping at the top level, "
            f"got {type(raw).__name__}: {path}"
        )
    try:
        config = RulesConfig.model_validate(raw)
    except ValidationError as exc:
        raise DeckRulesError(f"invalid rules config in {path}: {exc}") from exc

    if valid_archetypes is None:
        valid_archetypes = set(load_quotas().archetypes)
    _validate_archetypes(config, valid_archetypes)

    logger.debug(
        "Loaded rules config from %s: %d always, %d never, %d preferred",
        path,
        len(config.always),
        len(config.never),
        len(config.preferred),
    )
    return config


def iter_card_names(config: RulesConfig) -> Iterator[str]:
    """Every card name the config references (rules + commander_in lists)."""
    for rule in (*config.always, *config.never, *config.preferred):
        yield rule.name
    for when in _iter_whens(config):
        for field_name in ("commander_in", "commander_not_in"):
            values = getattr(when, field_name)
            if values is not None:
                yield from values


def validate_rules_names(
    config: RulesConfig, resolve: Callable[[str], object | None]
) -> None:
    """Fail loudly if any referenced card name is not resolvable in the pool.

    ``resolve`` is the two-step resolver (full name, then face name) — e.g.
    ``PoolIndex.resolve``. Raises ``DeckRulesError`` listing every
    unresolvable name.
    """
    unresolved = sorted(
        {name for name in iter_card_names(config) if resolve(name) is None}
    )
    if unresolved:
        raise DeckRulesError(
            f"rules.yaml references card names not resolvable in the pool: "
            f"{unresolved}"
        )


# ── evaluation ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuleContext:
    """Pre-selection facts a ``when`` predicate may look at."""

    commander_name: str
    color_identity: frozenset[str]
    archetype: str


def _size_matches(spec: int | str, size: int) -> bool:
    if isinstance(spec, int):
        return size == spec
    match = _SIZE_COMPARATOR_RE.fullmatch(spec.strip())
    if match is None:  # pragma: no cover - validated at load time
        raise DeckRulesError(f"invalid color_identity_size comparator: {spec!r}")
    op, n = match.group(1), int(match.group(2))
    return {
        ">=": size >= n,
        "<=": size <= n,
        "==": size == n,
        ">": size > n,
        "<": size < n,
    }[op]


def matches(when: When | None, ctx: RuleContext) -> bool:
    """Whether a predicate block matches the context (``None`` = always)."""
    if when is None:
        return True
    if when.color_identity_contains is not None and not (
        set(when.color_identity_contains) <= ctx.color_identity
    ):
        return False
    if when.color_identity_size is not None and not _size_matches(
        when.color_identity_size, len(ctx.color_identity)
    ):
        return False
    if when.archetype_in is not None and ctx.archetype not in when.archetype_in:
        return False
    if when.archetype_not_in is not None and ctx.archetype in when.archetype_not_in:
        return False
    if when.commander_in is not None and ctx.commander_name not in when.commander_in:
        return False
    if (
        when.commander_not_in is not None
        and ctx.commander_name in when.commander_not_in
    ):
        return False
    if when.any_of is not None and not any(
        matches(sub, ctx) for sub in when.any_of
    ):
        return False
    return True


def resolve_never(config: RulesConfig, ctx: RuleContext) -> frozenset[str]:
    """Card names that must never be auto-recommended for this context."""
    return frozenset(
        rule.name for rule in config.never if matches(rule.when, ctx)
    )


def resolve_always(
    config: RulesConfig,
    ctx: RuleContext,
    banned_names: Iterable[str] = frozenset(),
) -> tuple[AlwaysRule, ...]:
    """Always rules applying to this context, honoring ban > never > always.

    Banned cards are dropped here (the banlist always wins), and so is any
    card whose ``never`` rule also matches (never beats always).
    """
    banned = set(banned_names)
    never_names = resolve_never(config, ctx)
    resolved: list[AlwaysRule] = []
    for rule in config.always:
        if not matches(rule.when, ctx):
            continue
        if rule.name in banned:
            logger.info("always rule %r is banned: banlist wins", rule.name)
            continue
        if rule.name in never_names:
            logger.info("always rule %r matches a never rule: never wins", rule.name)
            continue
        resolved.append(rule)
    return tuple(resolved)


def validate_forced_slot_budget(
    config: RulesConfig,
    ctx: RuleContext,
    banned_names: Iterable[str] = frozenset(),
) -> int:
    """Number of always rules matching this context; fails over budget.

    Raises ``DeckRulesError`` when the count exceeds
    ``meta.forced_slot_budget``.
    """
    count = len(resolve_always(config, ctx, banned_names))
    budget = config.meta.forced_slot_budget
    if count > budget:
        raise DeckRulesError(
            f"{ctx.commander_name!r}: {count} always rules match but "
            f"meta.forced_slot_budget is {budget}"
        )
    return count


def _preferred_applies(card: PreferredCard, identity: set[str]) -> bool:
    """Whether a preferred card's color predicates match this identity.

    ``colors_any`` (any-of) and ``color_identity_contains`` (all-of) are
    ANDed; each empty predicate is a no-op, so both empty matches every deck.
    """
    if card.colors_any and not (set(card.colors_any) & identity):
        return False
    if card.color_identity_contains and not (
        set(card.color_identity_contains) <= identity
    ):
        return False
    return True


def preferred_boosts(
    config: RulesConfig, color_identity: Iterable[str]
) -> dict[str, float]:
    """``name -> boost`` for the preferred cards matching this identity.

    See ``_preferred_applies`` for the matching rule. The boost is a flat
    score bonus; the selector uses it as the card's base score when the card
    is injected into the candidate pool (see ``cp_sat``), but a preferred
    card never *forces* its way in.
    """
    identity = set(color_identity)
    return {
        card.name: card.boost
        for card in config.preferred
        if _preferred_applies(card, identity)
    }


def boost_for(boosts: Mapping[str, float], full_name: str) -> float:
    """Boost for a card by full Scryfall name, with face-name fallback."""
    boost = boosts.get(full_name)
    if boost is not None:
        return boost
    if FACE_SEPARATOR in full_name:
        for face in full_name.split(FACE_SEPARATOR):
            if face in boosts:
                return boosts[face]
    return 0.0


def archetype_for(quotas: QuotasConfig, commander_name: str) -> str:
    """Effective quota archetype for a commander (same layering as resolver)."""
    commander = quotas.commanders.get(commander_name)
    if commander is not None and commander.archetype is not None:
        return commander.archetype
    return quotas.defaults.archetype
