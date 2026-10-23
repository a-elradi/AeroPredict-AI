import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.services import wayfinding  # noqa: E402
from app.utils import cache, rate_limit  # noqa: E402

# Real OpenStreetMap data for part of Istanbul Airport (© OpenStreetMap contributors, ODbL)
IST_ELEMENTS = json.loads((Path(__file__).parent / "fixtures_ist_osm.json").read_text())["elements"]


@pytest.fixture
def client(monkeypatch):
    cache.clear()
    rate_limit.reset()
    monkeypatch.setattr(wayfinding, "_fetch", lambda iata: IST_ELEMENTS)
    return create_app().test_client()


def a_gate():
    return next(e["tags"]["ref"] for e in IST_ELEMENTS if e["tags"].get("aeroway") == "gate")


def test_parses_gates_and_places():
    features = wayfinding.parse_elements(IST_ELEMENTS)
    assert len(features["gates"]) >= 10
    categories = {p["category"] for p in features["places"]}
    assert {"coffee", "dining", "toilets", "atm"} <= categories


def test_nearest_amenities_sorted_by_distance(client):
    gate = a_gate()
    r = client.get(f"/api/concierge/wayfinding?airport=IST&gate={gate.lower()}&categories=coffee,atm")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["gate_found"] is True
    assert data["gate"]["ref"] == gate
    coffee = data["categories"]["coffee"]["nearest"]
    assert coffee
    distances = [p["distance_m"] for p in coffee]
    assert distances == sorted(distances)
    assert all(p["walk_minutes"] >= 1 for p in coffee)
    assert "OpenStreetMap" in data["attribution"]


def test_unknown_gate_still_lists_amenities(client):
    data = client.get("/api/concierge/wayfinding?airport=IST&gate=Z999").get_json()["data"]
    assert data["gate_found"] is False
    assert data["categories"]["coffee"]["count"] > 0
    assert "distance_m" not in data["categories"]["coffee"]["nearest"][0]


def test_gate_names_are_normalized():
    assert wayfinding.normalize_gate("Gate b 4") == "B4"
    assert wayfinding.normalize_gate("A-12") == "A12"


@pytest.mark.parametrize("query", ["airport=ISTANBUL", "airport=IST&categories=casino"])
def test_invalid_requests(client, query):
    assert client.get(f"/api/concierge/wayfinding?{query}").status_code == 400


def test_map_service_down_returns_503(client, monkeypatch):
    monkeypatch.setattr(wayfinding, "_fetch", lambda iata: None)
    assert client.get("/api/concierge/wayfinding?airport=SAW").status_code == 503


def test_airport_without_map_data_returns_404(client, monkeypatch):
    monkeypatch.setattr(wayfinding, "_fetch", lambda iata: [])
    assert client.get("/api/concierge/wayfinding?airport=XXX").status_code == 404
