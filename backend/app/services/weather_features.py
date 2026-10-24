"""
AeroPredict — weather features for the delay model.

Training uses observed hourly weather (METAR archive from Iowa State
University's IEM). Live predictions use the airport forecast (TAF), which
only covers the next ~24-30 hours; further ahead the features are missing
and the model falls back on schedule history alone.
"""
import math
import re
from datetime import datetime, timedelta, timezone

import requests

from ..utils.cache import cached

WX_FIELDS = ["vis", "wind", "gust", "ceiling", "ts", "snow", "rain", "fog", "freezing"]
WX_FEATURES = [f"{side}_{f}" for side in ("o", "d") for f in WX_FIELDS]

# Airports with weather in the training data, and their time zones (BTS
# schedule times are local; forecasts are in UTC).
AIRPORT_TZ = {
    **{a: "America/New_York" for a in (
        "ATL", "CLT", "MCO", "BOS", "DCA", "LGA", "EWR", "MIA", "JFK", "PHL", "BWI", "FLL", "TPA",
        "IAD", "RDU", "CMH", "PIT", "CLE", "RSW", "CVG", "PBI", "JAX", "CHS")},
    **{a: "America/Chicago" for a in (
        "ORD", "DFW", "IAH", "MSP", "BNA", "AUS", "MDW", "DAL", "STL", "HOU", "MCI", "MSY", "SAT",
        "MKE", "OMA", "MEM")},
    **{a: "America/Denver" for a in ("DEN", "SLC", "ABQ")},
    "PHX": "America/Phoenix",
    **{a: "America/Los_Angeles" for a in (
        "LAX", "LAS", "SEA", "SFO", "SAN", "PDX", "SMF", "SNA", "SJC", "OAK", "BUR", "ONT")},
    "DTW": "America/Detroit",
    "IND": "America/Indiana/Indianapolis",
    "HNL": "Pacific/Honolulu", "OGG": "Pacific/Honolulu",
    "SJU": "America/Puerto_Rico",
}

NO_CEILING_FT = 12000  # "no low cloud" is treated as a high ceiling


def flags(wx: str) -> dict:
    """Weather phenomena from a METAR/TAF weather string like '-TSRA BR'."""
    wx = (wx or "").upper()
    return {
        "ts": int("TS" in wx),
        "snow": int(bool(re.search(r"SN|SG|PL|GS", wx))),
        "rain": int(bool(re.search(r"RA|DZ|SH", wx))),
        "fog": int(bool(re.search(r"FG|BR|HZ", wx))),
        "freezing": int("FZ" in wx),
    }


def empty() -> dict:
    return {f: math.nan for f in WX_FEATURES}


# ── Live forecasts ───────────────────────────────────────────────────────────

def _fetch_taf(icao: str):
    try:
        resp = requests.get("https://aviationweather.gov/api/data/taf",
                            params={"ids": icao, "format": "json"},
                            headers={"User-Agent": "AeroPredict/1.0"}, timeout=6)
        data = resp.json() if resp.ok and resp.text.strip() else None
    except (requests.RequestException, ValueError):
        return None
    return data[0] if isinstance(data, list) and data else None


def _vis_miles(v) -> float:
    if v is None:
        return math.nan
    s = str(v).replace("+", "")
    try:
        return min(float(s), 10.0)
    except ValueError:
        return math.nan


def forecast_for(iata: str, when_utc: datetime) -> dict | None:
    """Forecast conditions at an airport around a UTC time, or None if not covered."""
    if iata not in AIRPORT_TZ:
        return None
    icao = "P" + iata if iata in ("HNL", "OGG") else "T" + iata if iata == "SJU" else "K" + iata
    taf = cached(f"taf:{icao}", 1800, lambda: _fetch_taf(icao))
    if not taf:
        return None
    ts = when_utc.timestamp()
    period = None
    for f in taf.get("fcsts", []):
        start, end = f.get("timeFrom"), f.get("timeTo")
        if start and end and start <= ts < end:
            # Later groups (FM/TEMPO/BECMG) refine earlier ones; keep the last match
            period = {**(period or {}), **{k: v for k, v in f.items() if v not in (None, [], "")}}
    if not period:
        return None
    ceiling = NO_CEILING_FT
    for c in period.get("clouds") or []:
        if c.get("cover") in ("BKN", "OVC", "VV") and c.get("base") is not None:
            ceiling = min(ceiling, int(c["base"]))
    out = {
        "vis": _vis_miles(period.get("visib")),
        "wind": float(period.get("wspd") or 0),
        "gust": float(period.get("wgst") or period.get("wspd") or 0),
        "ceiling": float(ceiling),
    }
    out.update(flags(period.get("wxString")))
    return out


def live_features(origin: str, dest: str, dep_local: datetime, arr_hour_offset: int) -> tuple[dict, bool]:
    """Weather features for a flight from current forecasts. Returns (features, any_found)."""
    from zoneinfo import ZoneInfo
    feats = empty()
    found = False
    for side, airport, local in (("o", origin, dep_local),
                                 ("d", dest, dep_local + timedelta(hours=arr_hour_offset))):
        tz = AIRPORT_TZ.get(airport)
        if not tz:
            continue
        # Arrival offsets are local-to-local; close enough for picking a forecast hour
        when = local.replace(tzinfo=ZoneInfo(AIRPORT_TZ.get(origin, tz))).astimezone(timezone.utc)
        if not (datetime.now(timezone.utc) - timedelta(hours=2) <= when <= datetime.now(timezone.utc) + timedelta(hours=30)):
            continue
        fc = forecast_for(airport, when)
        if fc:
            found = True
            for k, v in fc.items():
                feats[f"{side}_{k}"] = v
    return feats, found
