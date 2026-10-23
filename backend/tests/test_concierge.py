import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.services import claude_client, flight_data, journey_planner, weather  # noqa: E402
from app.utils import cache, rate_limit  # noqa: E402

BASE_REQUEST = {
    "flight_number": "TK1",
    "date": "2026-11-02",
    "dep_airport": "IST",
    "arr_airport": "JFK",
    "dep_time": "13:30",
    "checked_bags": 1,
}

LIVE_FLIGHT = {
    "number": "TK 1", "status": "Expected", "airline": "Turkish Airlines",
    "airline_iata": "TK", "aircraft": "Boeing 777",
    "departure": {"iata": "IST", "icao": "LTFM", "name": "Istanbul", "country": "TR",
                  "scheduled_date": "2026-11-02", "scheduled_time": "14:05",
                  "revised_time": None, "terminal": "1", "gate": "F4",
                  "check_in_desk": "K", "baggage_belt": None},
    "arrival": {"iata": "JFK", "icao": "KJFK", "name": "New York JFK", "country": "US",
                "scheduled_date": "2026-11-02", "scheduled_time": "17:35",
                "revised_time": None, "terminal": "1", "gate": None,
                "check_in_desk": None, "baggage_belt": None},
}


@pytest.fixture
def client(monkeypatch):
    cache.clear()
    rate_limit.reset()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    monkeypatch.setattr(weather, "get_metar", lambda icao: None)
    return create_app().test_client()


def test_rule_based_plan_without_api_keys(client):
    r = client.post("/api/concierge/journey-plan", json=BASE_REQUEST)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["ai_generated"] is False
    plan = data["plan"]
    # International, one checked bag, economy: arrive 3 hours before departure
    assert plan["arrive_airport_by"] == "10:30"
    assert plan["leave_by"] == "09:45"
    categories = [s["category"] for s in plan["timeline"]]
    assert categories[0] == "travel"
    assert "bag_drop" in categories and "passport_control" in categories
    assert categories[-1] == "departure"
    times = [s["time"] for s in plan["timeline"]]
    assert times == sorted(times)


def test_live_flight_data_overrides_typed_values(client, monkeypatch):
    monkeypatch.setattr(flight_data, "get_flight", lambda *a, **k: LIVE_FLIGHT)
    r = client.post("/api/concierge/journey-plan", json=BASE_REQUEST)
    data = r.get_json()["data"]
    assert data["flight"]["departure"]["scheduled_time"] == "14:05"
    assert data["flight"]["departure"]["gate"] == "F4"
    assert data["flight"]["live_data"] is True
    assert any("Gate F4" in s["location"] for s in data["plan"]["timeline"])


def test_domestic_trip_skips_passport_control(client):
    req = dict(BASE_REQUEST, arr_airport="ESB", checked_bags=0, online_check_in=True)
    plan = client.post("/api/concierge/journey-plan", json=req).get_json()["data"]["plan"]
    categories = [s["category"] for s in plan["timeline"]]
    assert "passport_control" not in categories
    assert "bag_drop" not in categories and "check_in" not in categories


def test_missing_time_without_live_data_is_rejected(client):
    req = {k: v for k, v in BASE_REQUEST.items() if k != "dep_time"}
    r = client.post("/api/concierge/journey-plan", json=req)
    assert r.status_code == 400
    assert "departure time" in r.get_json()["error"]


@pytest.mark.parametrize("field,value", [
    ("flight_number", "hello world"),
    ("date", "02/11/2026"),
    ("dep_airport", "ISTANBUL"),
    ("dep_time", "25:00"),
    ("cabin", "spaceship"),
    ("preferences", ["sauna"]),
    ("travel_minutes", 5000),
])
def test_invalid_input_is_rejected(client, field, value):
    r = client.post("/api/concierge/journey-plan", json=dict(BASE_REQUEST, **{field: value}))
    assert r.status_code == 400


def test_claude_plan_is_used_when_configured(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}

    def fake_generate_json(system, prompt, schema, **kwargs):
        captured["prompt"] = prompt
        return {
            "summary": "Plan from Claude",
            "leave_by": "09:30", "arrive_airport_by": "10:15",
            "timeline": [{"time": "09:30", "title": "Leave", "details": "", "location": "Home",
                          "duration_minutes": 45, "category": "travel", "is_estimate": True}],
            "tips": [], "warnings": [],
        }

    monkeypatch.setattr(claude_client, "generate_json", fake_generate_json)
    data = client.post("/api/concierge/journey-plan",
                       json=dict(BASE_REQUEST, language="tr")).get_json()["data"]
    assert data["ai_generated"] is True
    assert data["plan"]["summary"] == "Plan from Claude"
    assert "Turkish" in captured["prompt"]
    assert "baseline_schedule" in captured["prompt"]


def test_falls_back_when_claude_returns_bad_times(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(claude_client, "generate_json", lambda *a, **k: {
        "summary": "x", "leave_by": "soon", "arrive_airport_by": "10:00",
        "timeline": [], "tips": [], "warnings": [],
    })
    data = client.post("/api/concierge/journey-plan", json=BASE_REQUEST).get_json()["data"]
    assert data["ai_generated"] is False
    assert data["plan"]["leave_by"] == "09:45"


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(journey_planner, "build_plan", lambda req: {})
    for _ in range(10):
        assert client.post("/api/concierge/journey-plan", json=BASE_REQUEST).status_code == 200
    r = client.post("/api/concierge/journey-plan", json=BASE_REQUEST)
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_model_retrain_requires_admin_token(client, monkeypatch):
    assert client.post("/api/model/train").status_code == 403
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    assert client.post("/api/model/train", headers={"X-Admin-Token": "wrong"}).status_code == 403


def test_flight_data_parses_aerodatabox_response():
    raw = [{
        "number": "TK 1", "status": "Expected",
        "airline": {"name": "Turkish Airlines", "iata": "TK"},
        "aircraft": {"model": "Boeing 777"},
        "departure": {"airport": {"iata": "IST", "icao": "LTFM", "countryCode": "TR"},
                      "scheduledTime": {"utc": "2026-11-02 11:05Z", "local": "2026-11-02 14:05+03:00"},
                      "terminal": "1", "gate": "F4"},
        "arrival": {"airport": {"iata": "JFK", "icao": "KJFK", "countryCode": "US"},
                    "scheduledTime": {"utc": "2026-11-02 22:35Z", "local": "2026-11-02 17:35-05:00"}},
    }]
    leg = flight_data._pick_leg(raw, "IST")
    assert leg["departure"]["scheduled_time"] == "14:05"
    assert leg["departure"]["gate"] == "F4"
    assert leg["arrival"]["country"] == "US"
