"""Method 1 — rule/regex-based functional tagger over oracle text (100% offline).

Reads the 200 test card names from ``test_set_proposal.csv`` (only the ``name``
column; ``final_labels`` is never touched), looks each card up in
``data/processed/cards.jsonl`` and applies hand-written rules per category.
Output: ``experiments/tagging/predictions/regex.json`` mapping name -> labels.

Design decisions (each rule cites these):

* Reminder text (parenthesized) is stripped before matching, so Treasure/Clue
  token reminders never fire mana/draw rules.
* Quoted granted abilities ("gains \"...\"", emblems) are stripped too, EXCEPT
  that creating a token whose quoted text has a mana ability counts as ramp
  when the creator is a permanent (Freyalise yes, one-shot instants no).
* Multiface cards: oracle faces are split on ``\\n//\\n``; a card gets a label
  if ANY face fires. A face whose text mentions "this land" is treated as a
  land face: its own mana ability is NOT ramp. ``lands`` is tagged from the
  type line, plus modal DFC land faces (playable as lands); transform backs
  (e.g. Ojer Pakpatiq's temple) are NOT lands because you cannot play them.
* ramp = mana abilities/rituals on nonland faces, repeatable or multiple
  Treasure making (one-shot single Treasure/Food is marginal -> ignored),
  Powerstone making, tokens with mana abilities, fetching lands onto the
  battlefield (on a land face only if it fetches 2+, so Flagstones-style
  replacement is not ramp), putting land cards from hand onto the
  battlefield, extra land drops, and static cost reduction of broad spell
  classes (medallions, The Immortal Sun; narrow classes like "Aura spells"
  are considered package cards, not ramp).
* card_draw = real card advantage: burst draws of 2+ / "for each" draws, and
  repeatable single draws (activated, or triggered by Whenever/At-the-
  beginning). One-shot single draws (cantrips, ETB "draw a card"), looting/
  rummaging (draw+discard 1-for-1), cycling, investigate and learn do NOT
  count. Draws given to opponents don't count; symmetric wheels do.
* removal = targeted destroy/exile/bounce-to-hand, counterspells, fights,
  -N/-N (toughness actually reduced), targeted damage of 2+/X (1 damage only
  with a "would die -> exile" rider), and one-per-opponent sacrifice edicts.
* board_wipe = "all/each" destroy/exile/bounce/sacrifice of permanents and
  mass damage of 2+/X to each creature (1-damage pingers are not wipes).
* wincons (conservative) = "you win the game", making an opponent lose the
  game as an effect (not as a trigger condition), and Overrun-style mass
  pump WITH trample. Poison, drains and generic beaters are left untagged.
* synergy (only unequivocal package cards) = tribal lords/anthems, "chosen
  type" payoffs, tribal-targeted effects, tribal counting, kinship, and
  tribal tutors. Generic anthems ("Other creatures you control...") and
  color-agnostic goodstuff never fire; oracle capitalizes creature types,
  which the patterns rely on.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from collections import Counter
from pathlib import Path

logger = logging.getLogger("regex_tagger")

TAGGING_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
CSV_PATH = TAGGING_DIR / "test_set_proposal.csv"
CARDS_PATH = REPO_ROOT / "data" / "processed" / "cards.jsonl"
OUT_PATH = TAGGING_DIR / "predictions" / "regex.json"

CATEGORIES = ["lands", "ramp", "card_draw", "removal", "board_wipe", "wincons", "synergy"]

# ---------------------------------------------------------------- text prep

PAREN_RE = re.compile(r"\([^()]*\)")
QUOTED_RE = re.compile(r'"[^"]*"')
D20_ROW_RE = re.compile(r"^\d+(?:—\d+)?\s*\|.*$", re.M)


def strip_parens(text: str) -> str:
    """Remove (possibly nested) parenthesized reminder text."""
    prev = None
    while prev != text:
        prev = text
        text = PAREN_RE.sub("", text)
    return text


def clean_face(face: str) -> str:
    """Face text for most rules: no reminder text, no quoted granted
    abilities, no d20 result-table rows (their effects are too conditional)."""
    face = strip_parens(face)
    face = QUOTED_RE.sub('""', face)
    face = D20_ROW_RE.sub("", face)
    return face


# ---------------------------------------------------------------- ramp

ACTIVATED_ADD = re.compile(r":\s*Add (?:\{|one |two |three |X )")
INLINE_ADD = re.compile(r"(?<![A-Za-z])add \{[WUBRGCXP0-9]")
RITUAL_ADD = re.compile(r"^Add \{", re.M)
FETCH_LANDS_BF = re.compile(
    r"search your library for (?:up to )?[^.]*?\b(?:land|Plains|Island|Swamp|Mountain|Forest)\b"
    r"[^.]*?put (?:it|them|those|that card|one|both)[^.]*?onto the battlefield",
    re.I,
)
FETCH_MULTI = re.compile(r"for (?:up to )?(?:two|three|four|X|that many)", re.I)
LANDS_FROM_HAND = re.compile(
    r"put (?:up to )?(?:a|one|two|three|four|X|any number of) land cards? from your hand onto the battlefield",
    re.I,
)
EXTRA_LANDS = re.compile(r"play (?:an|one|two|three|X) additional lands?", re.I)
COST_REDUCTION_LINE = re.compile(r"^([^.\n]*?)[Ss]pells you cast cost \{[\dX]+\} less to cast", re.M)
BROAD_SPELL_CLASSES = {
    "", "and", "or", "the", "first", "creature", "artifact", "enchantment",
    "instant", "sorcery", "noncreature", "nonland", "white", "blue", "black",
    "red", "green", "colorless", "multicolored", "monocolored", "historic",
    "legendary",
}
TREASURE_RE = re.compile(r"[Cc]reates? (?:a|an|two|three|four|X|that many|\d+ )?[^.]*?Treasure tokens?")
POWERSTONE_RE = re.compile(r"[Cc]reates? [^.]*?Powerstone tokens?")
OTHER_PLAYER_SUBJECT = re.compile(
    r"(its controller|that player|each opponent|an opponent|target opponent|defending player)[^.]{0,50}$",
    re.I,
)
TOKEN_WITH_MANA = re.compile(r'tokens?[^"\n]{0,60}with "[^"]*?Add \{')
PERMANENT_TYPES = re.compile(r"\b(Creature|Artifact|Enchantment|Planeswalker|Land|Battle)\b")


def treasure_ramp(line: str) -> bool:
    """Treasure making is ramp when repeatable (Whenever-trigger or activated)
    or when it mints several at once (Big Score style); a single one-shot
    Treasure (Ant-Man's Army) is marginal. Treasures minted for another
    player (Buy Your Silence) never count."""
    for m in TREASURE_RE.finditer(line):
        pre = line[: m.start()]
        if OTHER_PLAYER_SUBJECT.search(pre[-60:]):
            continue
        multiple = bool(re.search(r"\b(two|three|four|X|that many|for each)\b|tokens", m.group(0)))
        repeatable = "whenever" in line.lower() or ":" in pre
        if multiple or repeatable:
            return True
    return False


def ramp_rules(face: str, raw_face: str, is_land_face: bool, is_permanent: bool) -> list[str]:
    fired: list[str] = []
    if not is_land_face and (
        ACTIVATED_ADD.search(face) or INLINE_ADD.search(face) or RITUAL_ADD.search(face)
    ):
        fired.append("ramp:mana_ability")
    for m in FETCH_LANDS_BF.finditer(face):
        # On a land face, fetching a single land only replaces itself
        # (Flagstones); require 2+ lands there (Blighted Woodland).
        if not is_land_face or FETCH_MULTI.search(m.group(0)):
            fired.append("ramp:fetch_land_to_battlefield")
            break
    if LANDS_FROM_HAND.search(face):
        fired.append("ramp:land_from_hand")
    if EXTRA_LANDS.search(face):
        fired.append("ramp:extra_land_drop")
    for m in COST_REDUCTION_LINE.finditer(face):
        qualifier_words = {w.lower().strip(",") for w in m.group(1).split()}
        if qualifier_words <= BROAD_SPELL_CLASSES:
            fired.append("ramp:cost_reduction")
            break
    if any(treasure_ramp(line) for line in face.splitlines()):
        fired.append("ramp:treasures")
    for m in POWERSTONE_RE.finditer(face):
        if not OTHER_PLAYER_SUBJECT.search(face[: m.start()][-60:]):
            fired.append("ramp:powerstone")
            break
    # Token with a quoted mana ability (Freyalise). Checked on raw (unquoted)
    # face text; instants/sorceries are one-shot and don't qualify.
    if is_permanent and TOKEN_WITH_MANA.search(strip_parens(raw_face)):
        fired.append("ramp:mana_token")
    return fired


# ---------------------------------------------------------------- card draw

MULTI_DRAW = re.compile(
    r"draws? (?:up to )?(?:two|three|four|five|six|seven|X|that many) cards|draws? a card for each",
    re.I,
)
SINGLE_DRAW = re.compile(r"\bdraws? (?:a card|an additional card)\b", re.I)
LOOTING = re.compile(
    r"draws? (?:a card|two cards), then discards?|discard [^.:]*\. if you do, draw a card", re.I
)
TRIGGER_WORD = re.compile(r"\b(whenever|at the beginning of)\b", re.I)
ETB_TRIGGER = re.compile(r"\bwhen(?:ever)? [^,\n]*\benters\b", re.I)
SAC_SELF_COST = re.compile(r"sacrifice this", re.I)


def opponent_draws(line: str, start: int) -> bool:
    pre = line[:start][-55:]
    return bool(OTHER_PLAYER_SUBJECT.search(pre)) and "you" not in pre.lower()


def draw_rules(face: str) -> list[str]:
    fired: list[str] = []
    for line in face.splitlines():
        if "mulligan" in line.lower():
            continue
        for m in MULTI_DRAW.finditer(line):
            if not opponent_draws(line, m.start()):
                fired.append("card_draw:burst_multi_draw")
                break
        if LOOTING.search(line):
            continue  # 1-for-1 filtering, not card advantage
        m = re.search(r"^(.*?):[^:\n]*?\bdraw a card\b", line, re.I)
        if m and not SAC_SELF_COST.search(m.group(1)):
            fired.append("card_draw:activated_repeatable")
        m2 = SINGLE_DRAW.search(line)
        if (
            m2
            and TRIGGER_WORD.search(line)
            and not ETB_TRIGGER.search(line)  # ETB single draw = cantrip
            and not opponent_draws(line, m2.start())
        ):
            fired.append("card_draw:triggered_repeatable")
    return sorted(set(fired))


# ---------------------------------------------------------------- removal

DESTROY_TGT = re.compile(r"destroys? (?:up to \w+ |another |any number of )?target ", re.I)
EXILE_TGT = re.compile(r"exiles? (?:up to \w+ |another )?target ([^.\n]*)", re.I)
REMOVAL_OBJECTS = re.compile(r"\b(creature|permanent|artifact|enchantment|planeswalker|battle)s?\b", re.I)
COUNTERSPELL = re.compile(r"counter target [^.\n]*?spell", re.I)
TARGETED_DMG = re.compile(
    r"deals? (?:(\d+|X) )?damage(?: equal to [^.]*?)? to "
    r"(any target|target creature|target attacking|target player or planeswalker|up to \w+ target creatures?)",
    re.I,
)
DIE_EXILE_RIDER = re.compile(r"would die this turn, exile", re.I)
NEG_TOUGHNESS = re.compile(r"target creature[^.\n]*? gets? [+-]?[\dX]+/-(?:[1-9]|X)", re.I)
FIGHT = re.compile(r"fights? (?:another )?(?:up to \w+ )?target creature", re.I)
BOUNCE_TGT = re.compile(r"return (?:up to \w+ )?target ([^.\n]*?) to (?:its|their) owners?' hands?", re.I)
EDICT = re.compile(r"each (?:opponent|player) sacrifices (?:a|an|one|two|X) [^.\n]*?(creature|permanent)", re.I)


def removal_rules(face: str) -> list[str]:
    fired: list[str] = []
    if DESTROY_TGT.search(face):
        fired.append("removal:destroy_target")
    for m in EXILE_TGT.finditer(face):
        obj = m.group(1)
        if REMOVAL_OBJECTS.search(obj) and "graveyard" not in obj.lower():
            fired.append("removal:exile_target")
            break
    if COUNTERSPELL.search(face):
        fired.append("removal:counterspell")
    for line in face.splitlines():
        for m in TARGETED_DMG.finditer(line):
            amount = m.group(1)
            if amount is None or amount.upper() == "X" or int(amount) >= 2 or DIE_EXILE_RIDER.search(line):
                fired.append("removal:targeted_damage")
                break
    if NEG_TOUGHNESS.search(face):
        fired.append("removal:minus_toughness")
    if FIGHT.search(face):
        fired.append("removal:fight")
    for m in BOUNCE_TGT.finditer(face):
        if "you control" not in m.group(1) and "you own" not in m.group(1):
            fired.append("removal:bounce")
            break
    if EDICT.search(face):
        fired.append("removal:sac_edict")
    return sorted(set(fired))


# ---------------------------------------------------------------- board wipe

DESTROY_ALL = re.compile(r"destroys? (?:all|each|all other) ([^.\n]*)", re.I)
EXILE_ALL = re.compile(r"exiles? (?:all|each) ([^.\n]*)", re.I)
BOUNCE_ALL = re.compile(r"return (?:all|each) [^.\n]*?(?:creature|permanent)s?[^.\n]*? to their owners' hands", re.I)
DMG_EACH = re.compile(r"deals? (\d+|X) damage to each [^.\n]*?creature", re.I)
SAC_ALL = re.compile(r"sacrifices (?:all|each) [^.\n]*?(?:creature|permanent)", re.I)


def board_wipe_rules(face: str) -> list[str]:
    fired: list[str] = []
    for m in DESTROY_ALL.finditer(face):
        if "named" not in m.group(1).lower():  # "destroy all permanents named X" is self-referential
            fired.append("board_wipe:destroy_all")
            break
    for m in EXILE_ALL.finditer(face):
        obj = m.group(1).lower()
        if REMOVAL_OBJECTS.search(obj) and "graveyard" not in obj:
            fired.append("board_wipe:exile_all")
            break
    if BOUNCE_ALL.search(face):
        fired.append("board_wipe:mass_bounce")
    for m in DMG_EACH.finditer(face):
        if m.group(1).upper() == "X" or int(m.group(1)) >= 2:
            fired.append("board_wipe:mass_damage")
            break
    if SAC_ALL.search(face):
        fired.append("board_wipe:mass_sacrifice")
    return sorted(set(fired))


# ---------------------------------------------------------------- wincons

YOU_WIN = re.compile(r"you win the game", re.I)
OPPONENT_LOSES = re.compile(
    r"(?<!can't )\b(that player|each opponent|target opponent|each other player|they) loses? the game",
    re.I,
)
TRIGGER_CONDITION_PRE = re.compile(r"\b(whenever|when|if)\s+$", re.I)
OVERRUN = re.compile(
    r"creatures you control (?:get \+[2-9X]+/\+[2-9X]+|gain trample)[^.\n]*"
    r"(?:trample|get \+[2-9X]+/\+[2-9X]+)[^.\n]*until end of turn",
    re.I,
)


def wincon_rules(face_noparens: str) -> list[str]:
    """Runs on reminder-stripped but quote-KEPT text: granted abilities like
    Primal Odin's Zantetsuken ("that player loses the game") are wincons."""
    fired: list[str] = []
    if YOU_WIN.search(face_noparens):
        fired.append("wincons:you_win")
    for m in OPPONENT_LOSES.finditer(face_noparens):
        if not TRIGGER_CONDITION_PRE.search(face_noparens[: m.start()]):
            fired.append("wincons:opponent_loses")
            break
    if OVERRUN.search(face_noparens):
        fired.append("wincons:overrun_finisher")
    return fired


# ---------------------------------------------------------------- synergy

# Oracle text capitalizes creature types mid-sentence; common nouns stay
# lowercase, so [A-Z]\w+ reliably picks up tribes ("Other Zombies", "target
# Elves") and skips "other creatures" / "target creatures".
LORD = re.compile(
    r"\b[Oo]ther (?:[A-Z]\w+s?(?: creatures)?|(?:white|blue|black|red|green) creatures) you control (?:get \+|have |gain )",
)
CHOSEN_TYPE = re.compile(r"of the chosen type", re.I)
ALL_TRIBE = re.compile(r"\b[Aa]ll [A-Z]\w+s (?:gain|get|have) ")
TGT_TRIBE_PLURAL = re.compile(r"\btarget [A-Z]\w+?(?:s|ves)\b")
TGT_TRIBE_CTRL = re.compile(r"\btarget ([A-Z]\w+) you control\b")
TRIBE_COUNT = re.compile(r"number of [A-Z]\w+s (?:on the battlefield|you control)")
KINSHIP = re.compile(r"shares a creature type", re.I)
TRIBE_TUTOR = re.compile(r"[Ss]earch your library for (?:a|an|up to \w+) ([A-Z]\w+) (?:creature )?cards?")
NON_TRIBE_WORDS = {
    "Saga", "Sagas", "Vehicle", "Vehicles", "Equipment", "Aura", "Auras",
    "Clue", "Clues", "Treasure", "Treasures", "Food", "Foods", "Plains",
    "Island", "Islands", "Swamp", "Swamps", "Mountain", "Mountains",
    "Forest", "Forests", "Gate", "Gates", "Lesson", "Lessons", "Case",
    "Cases", "Class", "Classes", "Role", "Roles", "Room", "Rooms",
}


def synergy_rules(face: str) -> list[str]:
    fired: list[str] = []
    if LORD.search(face):
        fired.append("synergy:tribal_lord")
    if CHOSEN_TYPE.search(face):
        fired.append("synergy:chosen_type_payoff")
    if ALL_TRIBE.search(face):
        fired.append("synergy:tribal_anthem")
    for m in TGT_TRIBE_PLURAL.finditer(face):
        if m.group(0).split()[-1] not in NON_TRIBE_WORDS:
            fired.append("synergy:tribal_target")
            break
    for m in TGT_TRIBE_CTRL.finditer(face):
        if m.group(1) not in NON_TRIBE_WORDS:
            fired.append("synergy:tribal_target")
            break
    if TRIBE_COUNT.search(face):
        fired.append("synergy:tribal_count")
    if KINSHIP.search(face):
        fired.append("synergy:kinship")
    for m in TRIBE_TUTOR.finditer(face):
        if m.group(1) not in NON_TRIBE_WORDS:
            fired.append("synergy:tribal_tutor")
            break
    return sorted(set(fired))


# ---------------------------------------------------------------- tagging

LAND_TYPE = re.compile(r"\bLand\b")
LAND_FACE_TEXT = re.compile(r"\bthis land\b", re.I)


def tag_card(card: dict) -> dict[str, list[str]]:
    """Return {category: [rule names that fired]} for one card."""
    type_line = card.get("type_line", "")
    layout = card.get("layout", "normal")
    oracle = card.get("oracle_text", "") or ""
    raw_faces = oracle.split("\n//\n")
    is_permanent = bool(PERMANENT_TYPES.search(type_line))

    fired: dict[str, list[str]] = {c: [] for c in CATEGORIES}

    card_is_land = bool(LAND_TYPE.search(type_line))
    if card_is_land:
        fired["lands"].append("lands:type_line")

    for raw_face in raw_faces:
        face = clean_face(raw_face)
        face_noparens = strip_parens(raw_face)
        # A face talking about "this land" is a land face: its own mana
        # ability is not ramp. Modal DFC land faces are playable as lands ->
        # tag lands; transform backs (can't be played) are not.
        is_land_face = card_is_land or bool(LAND_FACE_TEXT.search(face_noparens))
        if layout == "modal_dfc" and not card_is_land and LAND_FACE_TEXT.search(face_noparens):
            fired["lands"].append("lands:mdfc_land_face")
        fired["ramp"] += ramp_rules(face, raw_face, is_land_face, is_permanent)
        fired["card_draw"] += draw_rules(face)
        fired["removal"] += removal_rules(face)
        fired["board_wipe"] += board_wipe_rules(face)
        fired["wincons"] += wincon_rules(face_noparens)
        fired["synergy"] += synergy_rules(face)

    return {c: sorted(set(rules)) for c, rules in fired.items() if rules}


# ---------------------------------------------------------------- main

def load_test_names() -> list[str]:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return [row["name"] for row in csv.DictReader(f)]


def load_cards(names: list[str]) -> dict[str, dict]:
    wanted = set(names)
    cards: dict[str, dict] = {}
    with CARDS_PATH.open(encoding="utf-8") as f:
        for line in f:
            card = json.loads(line)
            if card["name"] in wanted:
                cards[card["name"]] = card
    missing = wanted - cards.keys()
    if missing:
        raise SystemExit(f"cards missing from {CARDS_PATH}: {sorted(missing)}")
    return cards


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--explain", nargs="*", metavar="NAME",
                        help="print fired rules (for the given card names, or all)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    names = load_test_names()
    cards = load_cards(names)

    predictions: dict[str, list[str]] = {}
    explanations: dict[str, dict[str, list[str]]] = {}
    for name in names:
        fired = tag_card(cards[name])
        predictions[name] = [c for c in CATEGORIES if c in fired]
        explanations[name] = fired

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)
        f.write("\n")

    counts = Counter(label for labels in predictions.values() for label in labels)
    logger.info("wrote %s (%d cards)", OUT_PATH, len(predictions))
    logger.info("label counts: %s", dict(sorted(counts.items())))
    logger.info("cards with no label: %d", sum(1 for v in predictions.values() if not v))

    if args.explain is not None:
        targets = args.explain or names
        for name in targets:
            if name not in explanations:
                logger.warning("not in test set: %s", name)
                continue
            rules = [r for rs in explanations[name].values() for r in rs]
            logger.info("%s -> %s | %s", name, predictions[name], ", ".join(rules) or "-")


if __name__ == "__main__":
    main()
