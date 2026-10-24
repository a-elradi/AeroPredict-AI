import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.services import passenger_rights as pr  # noqa: E402
from app.utils import rate_limit  # noqa: E402


@pytest.fixture
def client():
    rate_limit.reset()
    return create_app().test_client()


def codes(result):
    return [r["code"] for r in result["regimes"]]


def regime(result, code):
    return next(r for r in result["regimes"] if r["code"] == code)


def test_long_delay_from_eu_pays_600_euros():
    r = pr.assess("FRA", "JFK", "LH", "delay", 4, "airline", None)
    assert "EU261" in codes(r) and "US DOT" in codes(r)
    eu = regime(r, "EU261")["compensation"]
    assert eu["eligible"] and eu["amount"] == 600
    assert r["headline"]["amount"] == 600


def test_short_eu_flight_pays_250():
    r = pr.assess("AMS", "LHR", "KL", "delay", 3.5, "airline", None)
    assert regime(r, "EU261")["compensation"]["amount"] == 250
    assert regime(r, "UK261")["compensation"]["amount"] == 220


def test_weather_means_no_compensation_but_care_still_applies():
    r = pr.assess("FRA", "MAD", "LH", "cancellation", 0, "weather", 2)
    eu = regime(r, "EU261")
    assert not eu["compensation"]["eligible"]
    assert any("hotel" in c for c in eu["care"])


def test_cancellation_with_early_notice_has_no_compensation():
    r = pr.assess("CDG", "BCN", "AF", "cancellation", 0, "airline", 30)
    assert not regime(r, "EU261")["compensation"]["eligible"]


def test_into_eu_on_non_eu_airline_is_not_eu261():
    r = pr.assess("DXB", "FRA", "EK", "delay", 5, "airline", None)
    assert "EU261" not in codes(r)
    assert codes(r) == ["Montreal"]


def test_into_eu_on_eu_airline_is_eu261():
    r = pr.assess("JFK", "FRA", "LH", "delay", 5, "airline", None)
    assert "EU261" in codes(r)


def test_turkey_domestic_cancellation_is_100_euros():
    r = pr.assess("IST", "ESB", "TK", "cancellation", 0, "airline", 3)
    tr = regime(r, "SHY-YOLCU")["compensation"]
    assert tr["eligible"] and tr["amount"] == 100


def test_turkey_delay_has_no_cash_compensation():
    r = pr.assess("IST", "DXB", "TK", "delay", 6, "airline", None)
    assert not regime(r, "SHY-YOLCU")["compensation"]["eligible"]


def test_canada_appr_amounts():
    r = pr.assess("YYZ", "JFK", "AC", "delay", 7, "airline", None)
    assert regime(r, "APPR")["compensation"]["amount"] == 700


def test_us_refund_threshold_mentions_hours():
    r = pr.assess("JFK", "LAX", "AA", "delay", 1, "airline", None)
    us = regime(r, "US DOT")
    assert not us["compensation"]["eligible"]
    assert any("3 hours" in c for c in us["choices"])


def test_endpoint_returns_desk_script(client):
    r = client.post("/api/concierge/rights", json={
        "origin": "ist", "destination": "lhr", "airline": "TK",
        "disruption": "cancellation", "cause": "airline", "notice_days": 1})
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["route"]["distance_km"] > 2000
    assert data["ask_at_the_desk"]
    assert len(data["rebooking_options"]) == 4


@pytest.mark.parametrize("body", [
    {"origin": "IST", "destination": "IST", "disruption": "delay"},
    {"origin": "IST", "destination": "LHR", "disruption": "lost"},
    {"origin": "IST", "destination": "LHR", "disruption": "delay", "delay_hours": "long"},
    {"origin": "IST", "destination": "LHR", "disruption": "delay", "cause": "aliens"},
])
def test_invalid_requests(client, body):
    assert client.post("/api/concierge/rights", json=body).status_code == 400


def test_claim_button_hidden_without_partner_link(client, monkeypatch):
    monkeypatch.delenv("CLAIM_PARTNER_URL", raising=False)
    data = client.post("/api/concierge/rights", json={
        "origin": "FRA", "destination": "JFK", "airline": "LH",
        "disruption": "delay", "delay_hours": 4, "cause": "airline"}).get_json()["data"]
    assert data["claim_partner"] is None


def test_claim_button_shown_for_eligible_claims(client, monkeypatch):
    monkeypatch.setenv("CLAIM_PARTNER_URL", "https://partner.example/claim?a=123&from={origin}&to={destination}")
    monkeypatch.setenv("CLAIM_PARTNER_NAME", "ClaimCo")
    data = client.post("/api/concierge/rights", json={
        "origin": "FRA", "destination": "JFK", "airline": "LH",
        "disruption": "delay", "delay_hours": 4, "cause": "airline"}).get_json()["data"]
    assert data["claim_partner"]["url"] == "https://partner.example/claim?a=123&from=FRA&to=JFK"
    assert data["claim_partner"]["name"] == "ClaimCo"
    assert "free" in data["claim_partner"]["disclosure"]


def test_claim_button_hidden_when_no_compensation_due(client, monkeypatch):
    monkeypatch.setenv("CLAIM_PARTNER_URL", "https://partner.example/claim")
    data = client.post("/api/concierge/rights", json={
        "origin": "FRA", "destination": "MAD", "airline": "LH",
        "disruption": "cancellation", "cause": "weather", "notice_days": 1}).get_json()["data"]
    assert data["claim_partner"] is None
