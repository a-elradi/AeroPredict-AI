import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import airports, ml_engine, route_history  # noqa: E402


def test_percentage_only_report_shows_no_flight_count():
    rh = route_history.lookup("6E", "123", "DEL", "BOM")  # DGCA gives percentages only
    assert rh["level"] == "airline_country" and rh["flights_known"] is False
    value = ml_engine._route_history_factors(rh, "6E", "6E123", "DEL", "BOM")[0]["value"]
    assert "flights" not in value


def test_report_with_counts_keeps_them():
    rh = route_history.lookup("VJ", "123", "SGN", "HAN")  # CAAV gives flight counts
    assert rh["flights_known"] is True and rh["flights"] > 1000


def test_reported_airport_punctuality():
    p = airports.punctuality("MEX", "AM")  # AFAC, per airline at Mexico City
    assert p["level"] == "airport_airline" and p["publisher"] == "AFAC" and 50 < p["dep_on_time_pct"] < 100
    nz = airports.punctuality("AKL")  # separate departure and arrival figures
    assert nz["sided"] and nz["dep_on_time_pct"] != nz["arr_on_time_pct"]
