import json
from functools import lru_cache

from flask import Blueprint, jsonify

from ..services import airports as airport_data
from ..services import delay_model, route_history
from ..services.ml_engine import US_CARRIER_NAMES

analytics_bp = Blueprint("analytics", __name__)

MIN_FLIGHTS = 5000  # skip tiny carriers and airports


def _rows(table: str) -> list[tuple[str, list]]:
    if not delay_model.is_available():
        return []
    return [(k, v) for k, v in delay_model._load()["tables"][table].items() if v[0] >= MIN_FLIGHTS]


@lru_cache(maxsize=1)
def _carriers() -> list[dict]:
    data = [{
        "carrier": US_CARRIER_NAMES.get(code, code),
        "code": code,
        "flights": v[0],
        "otp": round((v[0] - v[1] - v[2]) / v[0] * 100, 1),
        "delay": round(v[1] / v[0] * 100, 1),
        "cancel": round(v[2] / v[0] * 100, 2),
    } for code, v in _rows("carrier")]
    return sorted(data, key=lambda x: x["otp"], reverse=True)


@lru_cache(maxsize=1)
def _airports() -> list[dict]:
    data = [{
        "airport": code,
        "flights": v[0],
        "delay_pct": round((v[1] + v[2]) / v[0] * 100, 1),
    } for code, v in _rows("origin")]
    # The busiest airports, ordered by how often their departures are disrupted
    busiest = sorted(data, key=lambda x: x["flights"], reverse=True)[:25]
    return sorted(busiest, key=lambda x: x["delay_pct"], reverse=True)


@analytics_bp.route("/analytics/carriers", methods=["GET"])
def carrier_stats():
    return jsonify({"data": _carriers()})


@analytics_bp.route("/analytics/airports", methods=["GET"])
def airport_stats():
    return jsonify({"data": _airports()})


@lru_cache(maxsize=1)
def _coverage() -> dict:
    """How much of the world has real data: countries, airports and airlines."""
    world = airport_data._world()
    country = lambda iata: (world.get(iata) or [None, None, None])[2]
    data = route_history._load()
    countries, airports = {"US"}, set()
    for key, e in data.get("airport", {}).items():
        iata = key.split("|")[0]
        if e[0] + e[2] >= route_history.MIN_FLIGHTS["airport"] and country(iata):
            airports.add(iata)
            countries.add(country(iata))
    for key, e in data.get("route", {}).items():
        if e[0] + e[2] >= route_history.MIN_FLIGHTS["route"]:
            countries.update(c for c in map(country, key.split("|")) if c)
    for key in data.get("airline_country", {}):
        countries.add(key.split("|")[1])
    euro = airport_data._eurocontrol()
    icao_country = {v[0]: v[2] for v in world.values()}
    countries.update(euro.get("countries", {}))
    countries.update(icao_country[i] for i in euro.get("airports", {}) if i in icao_country)
    airports.update(i for i, v in world.items() if v[0] in euro.get("airports", {}))
    airports.update(airport_data._acsa().get("airports", {}))
    if delay_model.is_available():
        airports.update(k for k, v in delay_model._load()["tables"]["origin"].items() if v[0] >= 1000)
        airlines = {k for k, v in delay_model._load()["tables"]["carrier"].items() if v[0] >= 1000}
    else:
        airlines = set()
    airlines.update(k for k, v in data.get("airline", {}).items()
                    if v[0] + v[2] >= route_history.MIN_FLIGHTS["airline"])
    airlines.update(k.split("|")[0] for k in data.get("airline_country", {}) if not k.startswith("*"))
    return {"countries": len(countries), "airports": len(airports), "airlines": len(airlines)}


@lru_cache(maxsize=1)
def _international_test() -> dict | None:
    """Held-out test of route-history predictions (scripts/backtest_route_history.py)."""
    path = delay_model.MODEL_DIR / "route_history_backtest.json"
    if not path.exists():
        return None
    r = json.loads(path.read_text())
    cal = r.get("recalibration") or {}
    after = cal.get("calibration_after") or []
    top = after[-1] if after else None
    return {"country": r["country"], "test_months": r["test_months"], "test_flights": r["test_flights"],
            "auc": r["with_route_history"]["auc"], "auc_before": r["before_route_history"]["auc"],
            "riskiest_predicted_pct": round(top["predicted"] * 100) if top else None,
            "riskiest_actual_pct": round(top["actual"] * 100) if top else None}


@analytics_bp.route("/analytics/global-stats", methods=["GET"])
def global_stats():
    meta = delay_model._load()["meta"] if delay_model.is_available() else {}
    rates = meta.get("class_rates", {})
    return jsonify({
        "total_flights_analyzed": meta.get("n_training"),
        "data_months": meta.get("data_months", []),
        "data_source": meta.get("data_source"),
        "avg_otp_pct": round(rates.get("on_time", 0) * 100, 1),
        "avg_delay_pct": round(rates.get("delayed", 0) * 100, 1),
        "avg_cancel_pct": round(rates.get("cancelled", 0) * 100, 2),
        "model_accuracy_pct": round(meta.get("accuracy", 0) * 100, 1) if meta.get("accuracy") else None,
        "baseline_accuracy_pct": round(meta.get("baseline_accuracy_always_on_time", 0) * 100, 1)
        if meta.get("baseline_accuracy_always_on_time") else None,
        "model_auc": meta.get("auc_delay_or_cancel"),
        "model_auc_with_forecast": (meta.get("with_weather") or {}).get("auc_delay_or_cancel"),
        "test_flights": meta.get("test_flights"),
        "international_test": _international_test(),
        "coverage": _coverage(),
        "test_months": meta.get("test_months", []),
        "calibration": meta.get("calibration", []),
        "monthly": meta.get("monthly", []),
        "airports_covered": len(_airports()),
        "carriers_covered": len(_carriers()),
    })
