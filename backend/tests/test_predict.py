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


def predict_route(client, origin, dest, **extra):
    body = {**US_FLIGHT, "flight_number": "XX100", "airline": "XX", "dep_airport": origin, "arr_airport": dest, **extra}
    return client.post("/api/predict", json=body).get_json()["data"]


@needs_model
def test_european_route_uses_eurocontrol_airport_punctuality(client):
    d = predict_route(client, "IST", "FCO")
    assert d["data_basis"]["confidence"] == "airport"
    assert any("EUROCONTROL" in f["label"] for f in d["factors"])


@needs_model
def test_unknown_routes_differ_by_airport_punctuality(client):
    def disrupted(origin, dest):
        p = predict_route(client, origin, dest)["probabilities"]
        return p["delayed"] + p["cancelled"]
    # Zurich departures are far less punctual than Istanbul's (EUROCONTROL)
    assert disrupted("ZRH", "FCO") > disrupted("IST", "FCO") + 3


@needs_model
def test_route_without_any_data_is_labelled_estimate_only(client):
    d = predict_route(client, "ULN", "PBH")
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


def test_forecast_works_for_non_us_airports_and_ignores_prob30(monkeypatch):
    from datetime import datetime, timezone
    from app.services import weather_features as wf
    from app.utils import cache
    cache.clear()
    start, end = 1_800_000_000, 1_800_086_400
    fake_taf = {"fcsts": [
        {"timeFrom": start, "timeTo": end, "visib": "6+", "wspd": 10, "wgst": None, "wxString": None,
         "clouds": [{"cover": "SCT", "base": 3000}]},
        {"timeFrom": start, "timeTo": end, "fcstChange": "TEMPO", "visib": 1.55, "wspd": 15, "wgst": 25,
         "wxString": "TSRA", "clouds": [{"cover": "BKN", "base": 800}]},
        {"timeFrom": start, "timeTo": end, "fcstChange": "TEMPO", "probability": 30, "visib": 0.2,
         "wxString": "FG", "clouds": []},
    ]}
    asked = []
    monkeypatch.setattr(wf, "_fetch_taf", lambda icao: asked.append(icao) or fake_taf)
    fc = wf.forecast_for("IST", datetime.fromtimestamp(start + 3600, tz=timezone.utc))
    assert asked == ["LTFM"]
    assert fc["ts"] == 1 and fc["ceiling"] == 800 and fc["vis"] == 1.55
    assert fc["fog"] == 0  # the PROB30 fog group is ignored


@needs_model
def test_uk_route_history_is_used_for_airline_on_route(client):
    from app.services import route_history
    if not route_history._load():
        pytest.skip("route statistics not built")
    d = predict_route(client, "DXB", "LHR", flight_number="EK1", airline="EK")
    assert d["data_basis"]["confidence"] == "medium"
    assert "UK Civil Aviation Authority" in d["data_basis"]["note"]
    assert any(f["label"] == "EK on DXB → LHR" for f in d["factors"])


@needs_model
def test_brazil_flight_history_gives_high_confidence(client):
    from app.services import route_history
    if not route_history._load():
        pytest.skip("route statistics not built")
    d = predict_route(client, "IST", "GRU", flight_number="TK15", airline="TK")
    assert d["data_basis"]["confidence"] == "high"
    assert "ANAC" in d["data_basis"]["note"]


@needs_model
def test_australian_domestic_route_uses_bitre(client):
    from app.services import route_history
    if not route_history._load():
        pytest.skip("route statistics not built")
    d = predict_route(client, "SYD", "MEL", flight_number="QF401", airline="QF")
    assert d["data_basis"]["confidence"] in ("high", "medium")
    assert "BITRE" in d["data_basis"]["note"] or "Australia" in d["data_basis"]["note"]


def test_airline_national_record_used_for_unlisted_domestic_route():
    from app.services import route_history
    if not route_history._load():
        pytest.skip("route statistics not built")
    h = route_history.lookup("JQ", "9999", "CBR", "BME")
    assert h and h["level"] == "airline_country" and h["country"] == "AU"
    assert route_history.lookup("JQ", "9999", "CBR", "LHR") is None or \
        route_history.lookup("JQ", "9999", "CBR", "LHR")["level"] != "airline_country"


@needs_model
def test_airline_record_used_where_no_route_or_airport_data(client):
    from app.services import route_history
    if not route_history.airline_record("EK"):
        pytest.skip("route statistics not built")
    ek = predict_route(client, "ULN", "PBH", flight_number="EK384", airline="EK")
    sq = predict_route(client, "ULN", "PBH", flight_number="SQ1", airline="SQ")
    assert ek["data_basis"]["confidence"] == "low"
    assert "EK flights" in ek["data_basis"]["note"]
    assert ek["probabilities"] != sq["probabilities"]


@needs_model
def test_airline_record_adjusts_airport_punctuality(client):
    from app.services import route_history
    if not route_history.airline_record("FR"):
        pytest.skip("route statistics not built")
    d = predict_route(client, "IST", "FCO", flight_number="FR1", airline="FR")
    assert d["data_basis"]["confidence"] == "airport"
    assert "FR's own punctuality" in d["data_basis"]["note"]


def test_brazil_placeholder_cancellations_are_dropped():
    from app.services import route_history
    data = route_history._load()
    if not data:
        pytest.skip("route statistics not built")
    for key in ("G3|BR", "LA|BR", "AD|BR"):
        flights, _, cancelled, _ = data["airline_country"][key]
        assert cancelled / (flights + cancelled) < 0.05


def _source_built(name):
    from app.services import route_history
    if name not in route_history._load().get("sources", {}):
        pytest.skip(f"{name} statistics not built")


@needs_model
def test_korean_domestic_flight_uses_airportal(client):
    _source_built("korea")
    d = predict_route(client, "GMP", "CJU", flight_number="KE1201", airline="KE")
    assert d["data_basis"]["confidence"] in ("high", "medium")
    assert "Airportal" in d["data_basis"]["note"]


@needs_model
def test_hong_kong_route_uses_airport_records(client):
    _source_built("hongkong")
    d = predict_route(client, "HKG", "TPE", flight_number="CX400", airline="CX")
    assert d["data_basis"]["confidence"] in ("high", "medium")
    assert "Hong Kong" in d["data_basis"]["note"]


def test_japanese_airline_national_record():
    _source_built("japan")
    from app.services import route_history
    h = route_history.lookup("NH", "9999", "HND", "KMQ")
    assert h and h["level"] in ("airline_country", "route_airline", "route")
    if h["level"] == "airline_country":
        assert h["country"] == "JP" and "MLIT" in h["source"]["name"]


def test_collected_flights_round_trip(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import collect_flights
    monkeypatch.setattr(collect_flights, "COLLECTED", tmp_path)
    assert collect_flights.flight_code("DY 1302") == ("DY", "1302")
    assert collect_flights.flight_code("WF012") == ("WF", "12")
    rec = {"1|D": ["2026-10-24", "DY", "1302", "OSL", "BGO", 7, 0]}
    assert collect_flights.save("avinor", rec) == 1
    assert collect_flights.save("avinor", {"1|D": ["2026-10-24", "DY", "1302", "OSL", "BGO", 9, 0]}) == 0
    assert collect_flights.load("avinor", 12) == [["2026-10-24", "DY", "1302", "OSL", "BGO", 9, 0]]


@needs_model
def test_middle_east_airports_use_flight_records(client):
    from app.services import route_history
    if not route_history.airport_record("DXB", "dep"):
        pytest.skip("route statistics not built")
    dxb = predict_route(client, "DXB", "KWI", flight_number="FZ1", airline="FZ")
    doh = predict_route(client, "DOH", "MCT", flight_number="XX1", airline="XX")
    assert dxb["data_basis"]["confidence"] == "airport"
    assert any("DXB departures (flight records)" in f["label"] for f in dxb["factors"])
    assert dxb["probabilities"] != doh["probabilities"]


@needs_model
def test_south_africa_uses_acsa_airline_figures(client):
    from app.services import airports
    if not airports._acsa():
        pytest.skip("ACSA data not built")
    fa = predict_route(client, "JNB", "CPT", flight_number="FA101", airline="FA")
    cemair = predict_route(client, "JNB", "CPT", flight_number="5Z1", airline="5Z")
    assert any(f["label"] == "FA at JNB (ACSA)" for f in fa["factors"])
    assert fa["probabilities"]["on_time"] > cemair["probabilities"]["on_time"]


def test_nigeria_manual_ncaa_records():
    from app.services import route_history
    own = route_history.lookup("P4", "9999", "LOS", "ABV")
    other = route_history.lookup("W3", "9999", "LOS", "PHC")
    assert own and own["level"] == "airline_country" and "NCAA" not in own["source"]["period"]
    assert "Nigerian Civil Aviation Authority" in own["source"]["name"]
    assert other and other["level"] == "country"
    assert route_history.lookup("W3", "9999", "LOS", "LHR") is None or \
        route_history.lookup("W3", "9999", "LOS", "LHR")["level"] != "country"


def test_small_airports_are_smoothed():
    from app.services import route_history
    rec = route_history.airport_record("EBB", "dep")
    if not rec:
        pytest.skip("route statistics not built")
    assert 0.15 < rec["late_share"] < 0.45


@needs_model
def test_us_airport_to_airport_without_route_history(client):
    # a US airport record next to a non-US airport record used to crash (missing "level")
    for dest in ("DXB", "LOS", "BOG"):
        r = client.post("/api/predict", json={**US_FLIGHT, "flight_number": "AA1", "airline": "AA",
                                              "dep_airport": "JFK", "arr_airport": dest})
        assert r.status_code == 200
