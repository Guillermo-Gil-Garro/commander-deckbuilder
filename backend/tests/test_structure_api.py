"""Tests for GET /structure: the quota bands, published without a build.

``tests/test_resolver.py`` owns band resolution itself. What is tested here is
the frontier: that the endpoint publishes exactly what ``resolve_bands``
resolves, that the ``dial=category:position`` query syntax works, and that
``source`` tells the truth about which layer of ``quotas.yaml`` answered.

Nothing here touches the network or the solver — that is the point of the
endpoint.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.state import AppState
from quotas.config import CATEGORIES
from quotas.resolver import resolve_bands

COMMANDER = "Krenko, Mob Boss"


@pytest.fixture()
def client(real_app_state: AppState) -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = real_app_state
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


@pytest.fixture()
def degraded_client() -> Iterator[TestClient]:
    app_main.app.state.deckbuilder = None
    yield TestClient(app_main.app)
    del app_main.app.state.deckbuilder


def test_structure_publishes_every_category_as_a_lo_hi_band(
    client: TestClient,
) -> None:
    response = client.get("/structure", params={"commander": COMMANDER})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["commander"]["name"] == COMMANDER
    assert set(body["categories"]) == set(CATEGORIES)
    for band in body["categories"].values():
        assert set(band) == {"lo", "hi"}
        assert band["lo"] <= band["hi"]


def test_structure_matches_the_resolver(
    client: TestClient, real_app_state: AppState
) -> None:
    """The endpoint must publish resolve_bands' answer, not its own arithmetic."""
    body = client.get("/structure", params={"commander": COMMANDER}).json()

    expected = resolve_bands(real_app_state.quotas, COMMANDER, {})
    assert body["categories"] == {
        category: {"lo": band.min, "hi": band.max}
        for category, band in expected.items()
    }


def test_structure_applies_a_dial(client: TestClient) -> None:
    centered = client.get("/structure", params={"commander": COMMANDER}).json()
    lowered = client.get(
        "/structure", params={"commander": COMMANDER, "dial": "ramp:low"}
    ).json()

    assert lowered["dials"] == {"ramp": "low"}
    assert lowered["categories"]["ramp"]["hi"] < centered["categories"]["ramp"]["hi"]
    # A dial moves its own category and nothing else.
    del lowered["categories"]["ramp"], centered["categories"]["ramp"]
    assert lowered["categories"] == centered["categories"]


def test_structure_applies_several_dials(client: TestClient) -> None:
    """Repeated `dial` params are the documented way to send more than one."""
    response = client.get(
        "/structure",
        params={"commander": COMMANDER, "dial": ["ramp:high", "removal:low"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["dials"] == {"ramp": "high", "removal": "low"}


def test_structure_center_dial_changes_nothing(client: TestClient) -> None:
    centered = client.get(
        "/structure", params={"commander": COMMANDER, "dial": "ramp:center"}
    ).json()
    bare = client.get("/structure", params={"commander": COMMANDER}).json()

    assert centered["categories"] == bare["categories"]


def test_structure_reports_the_archetype(
    client: TestClient, real_app_state: AppState
) -> None:
    body = client.get("/structure", params={"commander": COMMANDER}).json()

    assert body["archetype"] in real_app_state.quotas.archetypes
    assert body["source"] in {"commander", "archetype"}


def test_structure_source_is_commander_when_quotas_yaml_says_so(
    client: TestClient, real_app_state: AppState
) -> None:
    """Pick a real individualised commander and prove `source` admits it."""
    individualised = [
        name
        for name, entry in real_app_state.quotas.commanders.items()
        if entry.archetype is not None or entry.overrides
    ]
    assert individualised, "quotas.yaml should individualise some commander"

    for name in individualised:
        body = client.get("/structure", params={"commander": name}).json()
        assert body["source"] == "commander", name


def test_structure_source_is_archetype_for_an_unlisted_commander(
    client: TestClient, real_app_state: AppState
) -> None:
    """Most commanders are not in quotas.yaml: they fall back to an archetype.

    The commander is found rather than hardcoded — naming one here would put
    this test one quotas.yaml edit away from silently testing nothing.
    """
    unlisted = next(
        row
        for row in real_app_state.commanders
        if row.name not in real_app_state.quotas.commanders
    )

    body = client.get("/structure", params={"commander": unlisted.name}).json()

    assert body["source"] == "archetype"
    assert body["archetype"] == real_app_state.quotas.defaults.archetype


def test_structure_handles_commas_and_apostrophes(client: TestClient) -> None:
    """The reason commander is a query param and not a path segment."""
    response = client.get(
        "/structure", params={"commander": "Atraxa, Praetors' Voice"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["commander"]["name"] == "Atraxa, Praetors' Voice"


def test_structure_never_publishes_a_karsten_floor(client: TestClient) -> None:
    """The land floor needs a deck's curve, so it cannot exist here.

    Guarding the absence on purpose: publishing a floor from bands alone would
    mean inventing one, and `lands.lo` here is NOT the effective minimum a
    build will enforce.
    """
    body = client.get("/structure", params={"commander": COMMANDER}).json()

    assert "karsten_floor" not in body


def test_structure_unknown_commander_is_a_404(client: TestClient) -> None:
    response = client.get("/structure", params={"commander": "Not A Commander"})

    assert response.status_code == 404
    assert "Not A Commander" in response.json()["detail"]


def test_structure_without_commander_is_a_422(client: TestClient) -> None:
    assert client.get("/structure").status_code == 422


@pytest.mark.parametrize("dial", ["ramp", "ramp:", ":high", "", "  :  "])
def test_structure_malformed_dial_is_a_422(client: TestClient, dial: str) -> None:
    """Syntax errors are caught at the frontier, with the offending pair named."""
    response = client.get(
        "/structure", params={"commander": COMMANDER, "dial": dial}
    )

    assert response.status_code == 422, response.text
    assert "categoria:posicion" in response.json()["detail"]


def test_structure_unknown_dial_position_is_a_422(client: TestClient) -> None:
    """Well-formed but meaningless: quotas.yaml rejects it, not the parser."""
    response = client.get(
        "/structure", params={"commander": COMMANDER, "dial": "ramp:sideways"}
    )

    assert response.status_code == 422
    assert "diales" in response.json()["detail"]


def test_structure_dial_on_a_category_without_one_is_a_422(
    client: TestClient, real_app_state: AppState
) -> None:
    without_dial = next(
        c for c in CATEGORIES if c not in real_app_state.quotas.dials
    )
    response = client.get(
        "/structure", params={"commander": COMMANDER, "dial": f"{without_dial}:high"}
    )

    assert response.status_code == 422


def test_structure_is_degraded_without_a_pool(degraded_client: TestClient) -> None:
    response = degraded_client.get("/structure", params={"commander": COMMANDER})

    assert response.status_code == 503
