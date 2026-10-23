"""
AeroPredict — Airport weather (METAR from aviationweather.gov, free, no key)
"""
import logging

import requests

from ..utils.cache import cached

log = logging.getLogger(__name__)

METAR_URL = "https://aviationweather.gov/api/data/metar"

FLIGHT_CATEGORY_IMPACT = {
    "VFR": "none",       # good visibility, high clouds
    "MVFR": "low",       # marginal
    "IFR": "moderate",   # low cloud / poor visibility
    "LIFR": "high",      # very low cloud / fog
}


def get_metar(icao: str | None) -> dict | None:
    if not icao:
        return None
    return cached(f"metar:{icao}", 600, lambda: _fetch(icao.upper()))


def _fetch(icao: str) -> dict | None:
    try:
        resp = requests.get(
            METAR_URL,
            params={"ids": icao, "format": "json"},
            headers={"User-Agent": "AeroPredict/1.0"},
            timeout=6,
        )
        if not resp.ok or not resp.text.strip():
            return None
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("METAR lookup for %s failed: %s", icao, e)
        return None
    if not isinstance(data, list) or not data:
        return None
    m = data[0]
    category = m.get("fltCat")
    return {
        "icao": icao,
        "observed_at": m.get("reportTime"),
        "raw": m.get("rawOb"),
        "temperature_c": m.get("temp"),
        "wind_kt": m.get("wspd"),
        "gust_kt": m.get("wgst"),
        "visibility": m.get("visib"),
        "weather": m.get("wxString"),
        "flight_category": category,
        "delay_impact": FLIGHT_CATEGORY_IMPACT.get(category, "unknown"),
    }
