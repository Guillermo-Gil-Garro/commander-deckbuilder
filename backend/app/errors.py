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
    "POOL_UNAVAILABLE",
    "Severity",
    "Violation",
    "category_label",
    "violation_message",
]

# Service-level messages.
POOL_UNAVAILABLE = (
    "El servicio no tiene cargado el pool de cartas. "
    "Es un problema de despliegue, no de tu mazo: revisa /api/health."
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

# One template per Violation.code (see selector.constraints).
VIOLATION_MESSAGES: dict[str, str] = {
    "deck_size": "El mazo tiene {actual} cartas y debe tener exactamente {limit}.",
    "lands_floor": "Faltan tierras: hay {actual} y el mínimo es {limit}.",
    "lands_ceiling": "Sobran tierras: hay {actual} y el máximo es {limit}.",
    "category_floor": "Faltan cartas de {category}: hay {actual} y el mínimo es {limit}.",
    "category_ceiling": "Sobran cartas de {category}: hay {actual} y el máximo es {limit}.",
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
