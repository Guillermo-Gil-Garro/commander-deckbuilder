"""User-facing Spanish messages for the API frontier.

The code, the logs and the ``Violation.code`` values stay in English; only
what the player reads is translated here. Keeping the table in one module
(instead of f-strings inlined at each raise site) is what makes the wording
reviewable and the codes reusable by the frontend.

``Violation`` and ``Severity`` are re-exported, never redefined:
``selector.constraints`` owns them and cannot import from ``app/`` — the
scripts under ``experiments/`` import the selector without FastAPI installed.
"""

from __future__ import annotations

from selector.constraints import Severity, Violation

__all__ = [
    "CATEGORY_LABELS",
    "DECK_BUILD_INFEASIBLE",
    "EDHREC_UNAVAILABLE",
    "INVALID_DIALS",
    "POOL_UNAVAILABLE",
    "Severity",
    "Violation",
    "candidate_reason",
    "card_not_in_deck",
    "card_not_in_pool",
    "category_label",
    "commander_banned",
    "commander_not_found",
    "deck_size_mismatch",
    "edhrec_not_found",
    "invalid_dial_param",
    "relaxed_stage_message",
    "violation_message",
]

# Service-level messages.
POOL_UNAVAILABLE = (
    "El servicio no tiene cargado el pool de cartas. "
    "Es un problema de despliegue, no de tu mazo: revisa /health."
)
EDHREC_UNAVAILABLE = (
    "EDHREC no responde ahora mismo, así que no podemos recomendar cartas. "
    "Es un problema nuestro (o suyo), no de tu comandante: prueba en un rato."
)
INVALID_DIALS = (
    "Alguno de los diales no es válido. Cada categoría admite 'low', 'center', "
    "'high' o null, y solo valen las categorías con dial definido en quotas.yaml."
)
DECK_BUILD_INFEASIBLE = (
    "No se puede construir un mazo de 99 cartas con este comandante ni "
    "relajando las cuotas. Revisa las cuotas y los diales."
)


def invalid_dial_param(raw: str) -> str:
    """A ``dial`` query param that is not a ``category:position`` pair.

    Syntax only: whether the category and position exist is ``quotas.yaml``'s
    call and comes back as ``INVALID_DIALS``.
    """
    return (
        f"El dial «{raw}» no tiene el formato «categoria:posicion» "
        f"(por ejemplo «ramp:high»)."
    )


def commander_not_found(name: str) -> str:
    """Unknown, non-eligible or unresolvable commander name."""
    return (
        f"No encontramos ningún comandante llamado «{name}». Comprueba el "
        f"nombre exacto en el buscador: no hay búsqueda difusa."
    )


def commander_banned(name: str) -> str:
    """A card the group banned as a commander (banlist.yaml)."""
    return (
        f"«{name}» está en la banlist del grupo y no puede ser comandante. "
        f"Elige otro."
    )


def edhrec_not_found(name: str) -> str:
    """EDHREC has no page for this commander: their data gap, not our outage."""
    return (
        f"EDHREC no tiene página de recomendaciones para «{name}», así que no "
        f"podemos proponerte un mazo. Suele pasar con comandantes muy nuevos."
    )


def card_not_in_pool(name: str) -> str:
    return (
        f"La carta «{name}» no está en nuestro pool de cartas legales en "
        f"Commander. Comprueba el nombre exacto."
    )


def card_not_in_deck(name: str) -> str:
    return f"La carta «{name}» no está en el mazo, así que no puedes quitarla."


def deck_size_mismatch(actual: int, expected: int) -> str:
    return (
        f"El mazo enviado tiene {actual} cartas y debe tener exactamente "
        f"{expected} (el comandante va aparte)."
    )


def candidate_reason(category: str, score: float) -> str:
    """Why a swap candidate is being offered, in the selectors' vocabulary.

    Candidates are ranked within the outgoing card's own category (swapping a
    removal for a removal is the question the UI asked), so the category IS
    most of the answer; the score is the rest of it.
    """
    return f"alternativa de {category_label(category)}, score {score:.2f}"


def relaxed_stage_message(stage: str) -> str:
    """The deck came out of a relaxed solver stage: playable, but off-quota."""
    return (
        f"No hemos podido cumplir todas las cuotas con las cartas disponibles, "
        f"así que hemos relajado restricciones (etapa «{stage}»). El mazo es "
        f"legal y jugable, pero mira el panel de cuotas: alguna se queda corta."
    )

# Display names for the quota categories (quotas.config.CATEGORIES).
CATEGORY_LABELS: dict[str, str] = {
    "lands": "tierras",
    "ramp": "ramp",
    "card_draw": "robo",
    "removal": "removal",
    "board_wipe": "removal masivo",
    "wincons": "wincons",
    "protection": "protección",
    "synergy": "sinergia",
}

# One template per Violation.code: the numeric rules from selector.constraints
# and the policy rules from selector.swap. The policy templates take no
# placeholders because their codes carry no counts worth reading ("banned" is
# 1 copy vs 0 allowed); which card each one is about is implicit in the code
# (``remove_always`` speaks about the outgoing card, the rest about the
# incoming one) and the caller sent both.
VIOLATION_MESSAGES: dict[str, str] = {
    "deck_size": "El mazo tiene {actual} cartas y debe tener exactamente {limit}.",
    "lands_floor": "Faltan tierras: hay {actual} y el mínimo es {limit}.",
    "lands_ceiling": "Sobran tierras: hay {actual} y el máximo es {limit}.",
    "category_floor": "Faltan cartas de {category}: hay {actual} y el mínimo es {limit}.",
    "category_ceiling": "Sobran cartas de {category}: hay {actual} y el máximo es {limit}.",
    "color_identity": (
        "La carta que quieres meter tiene colores fuera de la identidad de tu "
        "comandante."
    ),
    "banned": "La carta que quieres meter está en la banlist del grupo.",
    "duplicate_card": (
        "El mazo ya tiene esa carta y Commander es singleton: solo las tierras "
        "básicas se repiten."
    ),
    "commander_duplicate": (
        "Esa carta es tu comandante: no puede estar además en las 99."
    ),
    "add_never_manually": (
        "Esta carta no se recomienda sola (regla «never» en rules.yaml), pero "
        "el mazo sigue siendo válido si la metes a mano."
    ),
    "watchlist": (
        "Esta carta está en la watchlist del grupo: no la recomendamos sola, "
        "pero puedes jugarla."
    ),
    "remove_always": (
        "Estás quitando una carta marcada como «always» en rules.yaml. El mazo "
        "sigue siendo válido y exportable."
    ),
}


def category_label(category: str) -> str:
    """Spanish display name for a quota category (unknown ones pass through)."""
    return CATEGORY_LABELS.get(category, category)


def violation_message(violation: Violation) -> str:
    """Render a ``Violation`` as the Spanish sentence the player reads.

    An unknown ``code`` degrades to a factual fallback instead of raising: a
    new constraint must never turn a live validation into a 500.
    """
    template = VIOLATION_MESSAGES.get(violation.code)
    if template is None:
        return (
            f"Regla incumplida ({violation.code}): "
            f"{violation.actual} frente a un límite de {violation.limit}."
        )
    return template.format(
        actual=violation.actual,
        limit=violation.limit,
        category=category_label(violation.category or ""),
    )
