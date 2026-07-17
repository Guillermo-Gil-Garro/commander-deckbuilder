"""Internal card model parsed from Scryfall card objects."""

from __future__ import annotations

import re

from pydantic import BaseModel

PIP_COLORS = ("W", "U", "B", "R", "G", "C")

_SYMBOL_RE = re.compile(r"\{([^{}]+)\}")


def image_uris(data: dict) -> dict[str, str]:
    """Return the image_uris object for a raw Scryfall card object.

    Scryfall puts image_uris at the root for cards printed on a single physical
    face, even when they expose card_faces (split, adventure, prepare, flip):
    those faces carry no image_uris of their own. Cards with two physical faces
    (transform, modal_dfc) have no root image_uris and hold them per face, so we
    fall back to the front face -- consistent with slugify_commander, which also
    keys DFCs by their front face.
    """
    root = data.get("image_uris")
    if root:
        return root
    faces = data.get("card_faces") or []
    if faces:
        return faces[0].get("image_uris") or {}
    return {}


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


class TokenPart(BaseModel):
    """A token a card can create, from Scryfall ``all_parts`` (``component:
    token``). ``scryfall_id`` is the token printing's id, enough to fetch its
    art; ``type_line`` (e.g. "Token Creature — Goblin") tells creature tokens
    apart from artifact/food/treasure ones for the proxy copy-count rule."""

    name: str
    scryfall_id: str
    type_line: str


class Card(BaseModel):
    name: str
    oracle_id: str
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
    # Empty when Scryfall ships no image for the card; the frontend degrades to
    # a name-only placeholder. No card in the current pool hits this.
    image_uri_normal: str = ""
    image_uri_art_crop: str = ""
    # Back face of a two-physical-face card (transform, modal_dfc): its own
    # image_uris live in card_faces[1]. Empty for single-face cards, which have
    # no back to show.
    image_uri_back_normal: str = ""
    image_uri_back_art_crop: str = ""
    # Scryfall USD price, kept only as a RELATIVE "cheap vs expensive" signal
    # (the group plays proxies): EDHREC popularity is depressed by price, so the
    # later layers correct for it. usd has the best coverage of the price keys;
    # None when Scryfall ships no usd price. Lives at the card root even for
    # multi-faced cards (no per-face prices).
    price_usd: float | None = None
    # Tokens this card can create (Scryfall ``all_parts``, ``component: token``),
    # used to fill the empty cells of the proxy sheet. Empty for cards that make
    # none. Non-token related parts (the card itself, meld/combo pieces) are
    # dropped.
    tokens: list[TokenPart] = []

    @classmethod
    def from_scryfall(cls, data: dict) -> "Card":
        """Build a Card from a raw Scryfall card object.

        For multi-faced cards the front face provides mana_cost/type_line
        (and pips), while oracle_text concatenates all faces.
        """
        faces = data.get("card_faces") or []
        front = faces[0] if faces else data
        images = image_uris(data)
        # Two physical faces (transform, modal_dfc) carry their own image_uris
        # per face; a single-face card (split, adventure, flip) has one face's
        # worth of art at the root and none on the back.
        back_images = faces[1].get("image_uris") or {} if len(faces) > 1 else {}

        mana_cost = front.get("mana_cost") or ""
        type_line = front.get("type_line") or data.get("type_line") or ""

        # prices is at the root even for multi-faced cards; a null/absent usd
        # (or an unparseable value, which Scryfall never ships) means "unknown".
        raw_price = (data.get("prices") or {}).get("usd")
        price_usd = float(raw_price) if raw_price is not None else None

        tokens = [
            TokenPart(
                name=part.get("name") or "",
                scryfall_id=part.get("id") or "",
                type_line=part.get("type_line") or "",
            )
            for part in (data.get("all_parts") or [])
            if part.get("component") == "token" and part.get("id")
        ]

        if faces:
            oracle_text = "\n//\n".join(
                face.get("oracle_text") or "" for face in faces
            )
        else:
            oracle_text = data.get("oracle_text") or ""

        return cls(
            name=data["name"],
            # oracle_id is top-level even for multi-faced cards; Scryfall only
            # moves it into the faces for reversible_card layouts, hence the
            # fallback.
            oracle_id=data.get("oracle_id") or front.get("oracle_id"),
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
            image_uri_normal=images.get("normal") or "",
            image_uri_art_crop=images.get("art_crop") or "",
            image_uri_back_normal=back_images.get("normal") or "",
            image_uri_back_art_crop=back_images.get("art_crop") or "",
            price_usd=price_usd,
            tokens=tokens,
        )


def _is_commander_eligible(type_line: str, oracle_text: str) -> bool:
    if "Legendary" in type_line and "Creature" in type_line:
        return True
    return "can be your commander" in oracle_text.lower()
