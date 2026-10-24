import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.services import delay_model  # noqa: E402

needs_model = pytest.mark.skipif(not delay_model.is_available(), reason="trained model not present")

US_FLIGHT = {"flight_number": "DL1", "airline": "DL", "dep_airport": "ATL", "arr_airport": "JFK",
             "date": "2026-11-20", "dep_time": "08:00"}


@pytest.fixture
def client():
    return create_app().test_client()


@needs_model
def test_us_route_uses_flight_history(client):
    r = client.post("/api/predict", json=US_FLIGHT)
    assert r.status_code == 200
    d = r.get_json()["data"]
    p = d["probabilities"]
    assert abs(p["on_time"] + p["delayed"] + p["cancelled"] - 100) < 0.5
    assert d["data_basis"]["confidence"] in ("high", "medium")
    assert d["expected_delay_minutes"] > 0
    assert d["delay_severity"] in ("Minor", "Moderate", "Severe")
    assert any("route" in f["label"] for f in d["factors"])


@needs_model
def test_route_without_us_history_is_labelled_general(client):
    r = client.post("/api/predict", json=dict(US_FLIGHT, flight_number="TK1", airline="TK",
                                              dep_airport="IST", arr_airport="DXB"))
    d = r.get_json()["data"]
    assert d["data_basis"]["confidence"] == "general"


@needs_model
def test_evening_flights_riskier_than_morning_on_average(client):
    def disrupted(t):
        p = client.post("/api/predict", json=dict(US_FLIGHT, flight_number="DL9999", dep_time=t)).get_json()["data"]["probabilities"]
        return p["delayed"] + p["cancelled"]
    assert disrupted("19:00") > disrupted("07:00")


@pytest.mark.parametrize("field,value", [
    ("dep_airport", "<script>"),
    ("airline", "DELTA"),
    ("flight_number", "hello"),
    ("date", "20/11/2026"),
    ("dep_time", "8am"),
])
def test_invalid_input_is_rejected(client, field, value):
    assert client.post("/api/predict", json=dict(US_FLIGHT, **{field: value})).status_code == 400


def test_weather_forecast_factors():
    from app.services.ml_engine import _weather_factors
    wx = {"o_ts": 1, "o_vis": 0.5, "o_ceiling": 400, "o_gust": 35, "o_snow": 0, "o_freezing": 0,
          "d_ts": 0, "d_vis": 10, "d_ceiling": 12000, "d_gust": 8, "d_snow": 0, "d_freezing": 0}
    factors = _weather_factors(wx, "ORD", "LGA")
    values = [f["value"] for f in factors if f["label"] == "Forecast at ORD"]
    assert "thunderstorms" in values and any("visibility" in v for v in values)
    assert [f["value"] for f in factors if f["label"] == "Forecast at LGA"] == ["No significant weather"]
    assert _weather_factors(None, "ORD", "LGA") == []


def test_holiday_distance():
    from datetime import date
    from app.services.delay_model import holiday_distance
    assert holiday_distance(date(2026, 11, 26)) == 0   # Thanksgiving 2026
    assert holiday_distance(date(2026, 12, 23)) == 2
    assert holiday_distance(date(2026, 3, 15)) == 15
