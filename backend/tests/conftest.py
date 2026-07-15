"""Shared fixtures for the API tests.

The real artifacts load in well under a second, so the API tests exercise the
production wiring instead of a mock of it (same call as
``test_featured.test_real_featured_commanders_load``). Building it once per
session keeps that honest and cheap.
"""

from __future__ import annotations

import pytest

from app.state import AppState, build_app_state


@pytest.fixture(scope="session")
def real_app_state() -> AppState:
    """The AppState the app builds at startup, from the real repo artifacts."""
    state = build_app_state()
    if state is None:
        pytest.fail(
            "build_app_state() degraded: data/processed/cards.jsonl is missing. "
            "Run the Scryfall pipeline before the API tests."
        )
    return state
