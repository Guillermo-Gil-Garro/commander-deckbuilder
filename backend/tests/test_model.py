from pipeline.model import Card

BEAR = {
    "id": "aaaa1111-1111-1111-1111-111111111111",
    "oracle_id": "oracle-aaaa-1111",
    "name": "Grizzly Bears",
    "mana_cost": "{1}{G}",
    "cmc": 2.0,
    "type_line": "Creature — Bear",
    "oracle_text": "",
    "colors": ["G"],
    "color_identity": ["G"],
    "layout": "normal",
    "legalities": {"commander": "legal"},
}

LEGENDARY_CREATURE = {
    "id": "bbbb2222-2222-2222-2222-222222222222",
    "oracle_id": "oracle-bbbb-2222",
    "name": "Test Commander",
    "mana_cost": "{W}{U}{B}",
    "cmc": 3.0,
    "type_line": "Legendary Creature — Human Wizard",
    "oracle_text": "Flying",
    "colors": ["W", "U", "B"],
    "color_identity": ["W", "U", "B"],
    "layout": "normal",
    "legalities": {"commander": "legal"},
}

SORCERY = {
    "id": "cccc3333-3333-3333-3333-333333333333",
    "oracle_id": "oracle-cccc-3333",
    "name": "Legendary Ritual",
    "mana_cost": "{R}",
    "cmc": 1.0,
    "type_line": "Legendary Sorcery",
    "oracle_text": "Do a thing.",
    "colors": ["R"],
    "color_identity": ["R"],
    "layout": "normal",
    "legalities": {"commander": "legal"},
}

PLANESWALKER_COMMANDER = {
    "id": "dddd4444-4444-4444-4444-444444444444",
    "oracle_id": "oracle-dddd-4444",
    "name": "Test Walker",
    "mana_cost": "{2}{B}{G}",
    "cmc": 4.0,
    "type_line": "Legendary Planeswalker — Test",
    "oracle_text": "Test Walker can be your commander.",
    "colors": ["B", "G"],
    "color_identity": ["B", "G"],
    "layout": "normal",
    "legalities": {"commander": "legal"},
}

TWO_FACED = {
    "id": "eeee5555-5555-5555-5555-555555555555",
    "oracle_id": "oracle-eeee-5555",
    "name": "Front Side // Back Side",
    "cmc": 3.0,
    "color_identity": ["U"],
    "layout": "transform",
    "legalities": {"commander": "legal"},
    "card_faces": [
        {
            "name": "Front Side",
            "mana_cost": "{2}{U}",
            "type_line": "Creature — Human Rogue",
            "oracle_text": "Front ability.",
            "colors": ["U"],
        },
        {
            "name": "Back Side",
            "mana_cost": "",
            "type_line": "Creature — Vampire",
            "oracle_text": "Back ability.",
            "colors": ["U"],
        },
    ],
}


def test_basic_parsing() -> None:
    card = Card.from_scryfall(BEAR)
    assert card.name == "Grizzly Bears"
    assert card.mana_cost == "{1}{G}"
    assert card.cmc == 2.0
    assert card.pips["G"] == 1
    assert card.scryfall_id == BEAR["id"]
    assert card.oracle_id == BEAR["oracle_id"]
    assert card.layout == "normal"


def test_two_faced_uses_front_face() -> None:
    card = Card.from_scryfall(TWO_FACED)
    assert card.oracle_id == TWO_FACED["oracle_id"]
    assert card.mana_cost == "{2}{U}"
    assert card.type_line == "Creature — Human Rogue"
    assert card.pips["U"] == 1


def test_two_faced_concatenates_oracle_text() -> None:
    card = Card.from_scryfall(TWO_FACED)
    assert "Front ability." in card.oracle_text
    assert "Back ability." in card.oracle_text


def test_legendary_creature_is_commander_eligible() -> None:
    assert Card.from_scryfall(LEGENDARY_CREATURE).is_commander_eligible


def test_nonlegendary_creature_is_not_eligible() -> None:
    assert not Card.from_scryfall(BEAR).is_commander_eligible


def test_legendary_sorcery_is_not_eligible() -> None:
    assert not Card.from_scryfall(SORCERY).is_commander_eligible


def test_can_be_your_commander_text_is_eligible() -> None:
    assert Card.from_scryfall(PLANESWALKER_COMMANDER).is_commander_eligible
