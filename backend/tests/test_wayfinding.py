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
    wayfinding._loading.clear()
    monkeypatch.setattr(wayfinding, "_fetch", lambda iata: IST_ELEMENTS)
    # Run map downloads inline instead of in a background thread
    monkeypatch.setattr(wayfinding, "_spawn", lambda target, *args: target(*args))
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


def test_first_load_returns_202_until_map_is_ready(client, monkeypatch):
    pending = []
    monkeypatch.setattr(wayfinding, "_spawn", lambda target, *args: pending.append((target, args)))
    r = client.get("/api/concierge/wayfinding?airport=IST")
    assert r.status_code == 202
    assert r.get_json()["loading"] is True
    # A second request while loading does not start another download
    assert client.get("/api/concierge/wayfinding?airport=IST").status_code == 202
    assert len(pending) == 1
    target, args = pending[0]
    target(*args)
    assert client.get("/api/concierge/wayfinding?airport=IST").status_code == 200


def test_failed_load_is_not_retried_immediately(client, monkeypatch):
    calls = []
    monkeypatch.setattr(wayfinding, "_fetch", lambda iata: calls.append(iata))
    assert client.get("/api/concierge/wayfinding?airport=LHR").status_code == 503
    assert client.get("/api/concierge/wayfinding?airport=LHR").status_code == 503
    assert calls == ["LHR"]


def test_known_airports_use_cheap_box_query():
    query = wayfinding._query("IST", "LTFM", "node")
    assert "area" not in query
    assert "(41.2484,28.7001,41.3021,28.8041)" in query


def test_unknown_airports_fall_back_to_outline_query():
    query = wayfinding._query("XYZ", None, "node")
    assert 'area["aeroway"="aerodrome"]["iata"="XYZ"]' in query


def test_without_gate_places_near_terminals_come_first(client, monkeypatch):
    far_cafe = {"type": "node", "id": 1, "lat": 41.40, "lon": 28.90,
                "tags": {"amenity": "cafe", "name": "Aaa Village Cafe"}}
    monkeypatch.setattr(wayfinding, "_fetch", lambda iata: IST_ELEMENTS + [far_cafe])
    data = client.get("/api/concierge/wayfinding?airport=IST&categories=coffee").get_json()["data"]
    names = [p["name"] for p in data["categories"]["coffee"]["nearest"]]
    assert "Aaa Village Cafe" not in names


def test_saved_snapshot_is_used_without_contacting_map_servers(client, monkeypatch, tmp_path):
    features = wayfinding.parse_elements(IST_ELEMENTS)
    snapshot = {"iata": "IST", "generated_at": "2026-10-01T03:17Z",
                "gates": features["gates"], "places": features["places"]}
    (tmp_path / "IST.json").write_text(json.dumps(snapshot))
    monkeypatch.setattr(wayfinding, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(wayfinding, "_fetch", lambda iata: pytest.fail("should not download"))
    r = client.get("/api/concierge/wayfinding?airport=IST&gate=G8")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["gate_found"] is True
    assert data["map_updated"] == "2026-10-01T03:17Z"


def test_fetch_uses_nodes_when_way_query_fails(monkeypatch):
    nodes = [e for e in IST_ELEMENTS if e["type"] == "node"]
    results = iter([(nodes, []), (None, ["timeout"])])
    monkeypatch.setattr(wayfinding, "_run", lambda query, timeout: next(results))
    assert wayfinding._fetch("IST") == nodes
