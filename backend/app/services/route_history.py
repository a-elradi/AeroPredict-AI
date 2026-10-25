"""
AeroPredict — route and flight history outside the US, from official
national data (UK CAA, Brazil ANAC, Australia BITRE, Korea Airportal, Hong Kong
airport, Japan MLIT), plus each
airline's overall record across all of them. Built by scripts/build_route_stats.py.
"""
import gzip
import json
from functools import lru_cache
from pathlib import Path

from . import airports

STATS_FILE = Path(__file__).resolve().parents[2] / "data" / "route_stats.json.gz"
MIN_FLIGHTS = {"flight": 20, "route_airline": 20, "route": 30, "airline_country": 200, "airline": 1000}
SOURCE_COUNTRY = {"uk": "the UK", "brazil": "Brazil", "australia": "Australia", "korea": "South Korea",
                  "hongkong": "Hong Kong", "japan": "Japan", "norway": "Norway"}
SOURCE_BY_COUNTRY = {"BR": "brazil", "GB": "uk", "JE": "uk", "GG": "uk", "IM": "uk"}


@lru_cache(maxsize=1)
def _load() -> dict:
    if not STATS_FILE.exists():
        return {}
    with gzip.open(STATS_FILE, "rt", encoding="utf-8") as f:
        return json.load(f)


def lookup(carrier: str, flight_no: str, origin: str, dest: str) -> dict | None:
    """The most specific history available: this flight, this airline on the route, or the route."""
    data = _load()
    if not data:
        return None
    o_country, d_country = airports.lookup(origin)["country"], airports.lookup(dest)["country"]
    domestic = o_country if o_country and o_country == d_country else None
    for level, key in (("flight", f"{carrier}{flight_no}|{origin}"),
                       ("route_airline", f"{carrier}|{origin}|{dest}"),
                       ("route", f"{origin}|{dest}"),
                       ("airline_country", f"{carrier}|{domestic}" if domestic else None)):
        if key is None:
            continue
        e = data.get(level, {}).get(key)
        if not e or e[0] + e[2] < MIN_FLIGHTS[level]:
            continue
        if level == "flight" and not _same_route(data, carrier, origin, dest):
            continue  # flight numbers can be reused on other routes
        flights, late, cancelled, typical = e
        total = flights + cancelled
        return {"level": level, "flights": total, "late": late, "cancelled": cancelled, "country": domestic,
                "disrupted_share": (late + cancelled) / total, "cancel_share_of_disrupted":
                cancelled / (late + cancelled) if late + cancelled else 0.0,
                "typical_delay": typical, "source": _source(data, origin, dest)}
    return None


def airline_record(carrier: str) -> dict | None:
    """
    An airline's overall record on every route we have data for (any country), and the
    average across all airlines, so callers can tell how this airline compares.
    """
    data = _load()
    table = data.get("airline", {})
    e = table.get(carrier)
    if not e or e[0] + e[2] < MIN_FLIGHTS["airline"]:
        return None
    flights, late, cancelled, typical = e
    total = flights + cancelled
    all_total = sum(v[0] + v[2] for v in table.values())
    all_disrupted = sum(v[1] + v[2] for v in table.values())
    names = [SOURCE_COUNTRY.get(k, k) for k in data.get("airline_sources", {}).get(carrier, [])]
    return {"level": "airline", "flights": total, "late": late, "cancelled": cancelled, "country": None,
            "disrupted_share": (late + cancelled) / total,
            "cancel_share_of_disrupted": cancelled / (late + cancelled) if late + cancelled else 0.0,
            "typical_delay": typical, "average_share": all_disrupted / all_total,
            "source": {"name": "official punctuality statistics from " + _join(names), "period": "the last 12 months"}}


def _join(names: list[str]) -> str:
    if len(names) < 2:
        return names[0] if names else "national statistics"
    return ", ".join(names[:-1]) + " and " + names[-1]


def _same_route(data: dict, carrier: str, origin: str, dest: str) -> bool:
    return f"{carrier}|{origin}|{dest}" in data.get("route_airline", {})


def _source(data: dict, origin: str, dest: str) -> dict:
    countries = {airports.lookup(origin)["country"], airports.lookup(dest)["country"]}
    key = ("brazil" if "BR" in countries else "korea" if "KR" in countries else "hongkong" if "HK" in countries
           else "australia" if countries == {"AU"} else "japan" if countries == {"JP"}
           else "norway" if "NO" in countries and "GB" not in countries else "uk")
    return data.get("sources", {}).get(key, {"name": "national aviation statistics"})
