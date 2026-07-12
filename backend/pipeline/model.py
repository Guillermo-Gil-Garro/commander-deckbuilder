"""Internal card model parsed from Scryfall card objects."""

from __future__ import annotations

import re

from pydantic import BaseModel

PIP_COLORS = ("W", "U", "B", "R", "G", "C")

_SYMBOL_RE = re.compile(r"\{([^{}]+)\}")


def count_pips(mana_cost: str) -> dict[str, int]:
    """Count colored (and {C}) pips in a mana cost string.

    Hybrid symbols like {W/U} count 1 for each color; {2/W} counts 1 for W;
    Phyrexian {W/P} counts 1 for W. Generic and X symbols do not count.
    """
    pips = {color: 0 for color in PIP_COLORS}
    for symbol in _SYMBOL_RE.findall(mana_cost):
        for part in symbol.split("/"):
            if part in pips:
                pips[part] += 1
    return pips


class Card(BaseModel):
    name: str
    mana_cost: str
    cmc: float
    type_line: str
    oracle_text: str
    colors: list[str]
    color_identity: list[str]
    pips: dict[str, int]
    is_commander_eligible: bool
    layout: str
    scryfall_id: str

    @classmethod
    def from_scryfall(cls, data: dict) -> "Card":
        """Build a Card from a raw Scryfall card object.

        For multi-faced cards the front face provides mana_cost/type_line
        (and pips), while oracle_text concatenates all faces.
        """
        faces = data.get("card_faces") or []
        front = faces[0] if faces else data

        mana_cost = front.get("mana_cost") or ""
        type_line = front.get("type_line") or data.get("type_line") or ""

        if faces:
            oracle_text = "\n//\n".join(
                face.get("oracle_text") or "" for face in faces
            )
        else:
            oracle_text = data.get("oracle_text") or ""

        return cls(
            name=data["name"],
            mana_cost=mana_cost,
            cmc=float(data.get("cmc") or 0.0),
            type_line=type_line,
            oracle_text=oracle_text,
            colors=front.get("colors") or data.get("colors") or [],
            color_identity=data.get("color_identity") or [],
            pips=count_pips(mana_cost),
            is_commander_eligible=_is_commander_eligible(type_line, oracle_text),
            layout=data.get("layout") or "",
            scryfall_id=data["id"],
        )


def _is_commander_eligible(type_line: str, oracle_text: str) -> bool:
    if "Legendary" in type_line and "Creature" in type_line:
        return True
    return "can be your commander" in oracle_text.lower()
