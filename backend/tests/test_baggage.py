import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.services import baggage  # noqa: E402
from app.utils import rate_limit  # noqa: E402


@pytest.fixture
def client():
    rate_limit.reset()
    return create_app().test_client()


def risk(client, **body):
    r = client.post("/api/concierge/baggage-risk", json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def test_direct_flight_is_low_risk(client):
    data = risk(client, route=["SIN", "HND"], connections=[], checked_bags=1, date="2026-03-10")
    assert data["risk_level"] == "low"
    assert data["risk_percent"] < 1
    assert data["one_in"] > 100


def test_tight_interline_connection_raises_risk(client):
    easy = risk(client, route=["IST", "FRA", "JFK"], checked_bags=1, date="2026-03-10",
                connections=[{"duration": "2_4h"}])
    tight = risk(client, route=["IST", "FRA", "JFK"], checked_bags=1, date="2026-03-10",
                 connections=[{"duration": "under_1h", "airline_change": True}])
    assert tight["risk_percent"] > easy["risk_percent"] * 3
    assert tight["risk_level"] == "high"
    assert any("short" in a for a in tight["actions"]["during_your_trip"])
    assert any("tagged all the way" in a for a in tight["actions"]["during_your_trip"])


def test_more_bags_and_peak_season_increase_risk():
    base = baggage.assess(["LHR", "JFK"], [], 1, "2026-03-10")["risk_percent"]
    assert baggage.assess(["LHR", "JFK"], [], 3, "2026-03-10")["risk_percent"] > base
    assert baggage.assess(["LHR", "JFK"], [], 1, "2026-07-10")["risk_percent"] > base


def test_response_includes_claim_guidance(client):
    data = risk(client, route=["DXB", "IST"], connections=[], checked_bags=1)
    assert any("Property Irregularity Report" in s for s in data["if_bag_missing"])
    assert data["compensation"]["rule"] == "Montreal Convention"


@pytest.mark.parametrize("body", [
    {"route": ["IST"], "connections": []},
    {"route": ["IST", "ISTANBUL"], "connections": []},
    {"route": ["IST", "IST"], "connections": []},
    {"route": ["IST", "FRA", "JFK"], "connections": []},
    {"route": ["IST", "FRA", "JFK"], "connections": [{"duration": "forever"}]},
    {"route": ["IST", "JFK"], "connections": [], "checked_bags": 0},
    {"route": ["IST", "JFK"], "connections": [], "date": "10/03/2026"},
])
def test_invalid_requests(client, body):
    assert client.post("/api/concierge/baggage-risk", json=body).status_code == 400
