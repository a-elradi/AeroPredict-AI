"""
AeroPredict — Airport wayfinding
Finds gates and amenities inside an airport from OpenStreetMap
(free, via the Overpass API) and ranks amenities by walking distance
from the passenger's gate. Map data © OpenStreetMap contributors (ODbL).
"""
import logging
import math
import re

import requests

from . import airports
from ..utils.cache import cached

log = logging.getLogger(__name__)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
ATTRIBUTION = "Map data © OpenStreetMap contributors (openstreetmap.org/copyright)"

# Passenger-facing category -> OSM tags that count as that category
CATEGORIES = {
    "coffee":      {"amenity": {"cafe"}},
    "dining":      {"amenity": {"restaurant", "fast_food", "food_court"}},
    "toilets":     {"amenity": {"toilets"}},
    "atm":         {"amenity": {"atm", "bureau_de_change"}},
    "prayer_room": {"amenity": {"place_of_worship"}},
    "lounge":      {"amenity": {"lounge"}},
    "pharmacy":    {"amenity": {"pharmacy"}},
    "water":       {"amenity": {"drinking_water"}},
    "charging":    {"amenity": {"charging_station", "device_charging_station"}},
    "shopping":    {"shop": None},  # any shop
}
_AMENITY_VALUES = sorted({v for c in CATEGORIES.values() for v in (c.get("amenity") or ())})

WALK_METERS_PER_MIN = 75   # relaxed walking pace with luggage
DETOUR_FACTOR = 1.3        # corridors are not straight lines


class AirportNotFound(Exception):
    pass


def _query(iata: str, icao: str | None) -> str:
    areas = f'area["aeroway"="aerodrome"]["iata"="{iata}"]->.a1;'
    union = ".a1;"
    if icao:
        areas += f'area["aeroway"="aerodrome"]["icao"="{icao}"]->.a2;'
        union += ".a2;"
    amenity_re = "|".join(_AMENITY_VALUES)
    return (
        f"[out:json][timeout:25];{areas}({union})->.a;"
        "("
        f'nwr(area.a)["amenity"~"^({amenity_re})$"];'
        'nwr(area.a)["shop"];'
        'nwr(area.a)["aeroway"="gate"];'
        ");out center tags;"
    )


def _fetch(iata: str) -> list | None:
    query = _query(iata, airports.lookup(iata)["icao"])
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(url, data={"data": query}, timeout=40,
                                 headers={"User-Agent": "AeroPredict/1.0"})
            if resp.ok:
                return resp.json().get("elements", [])
            log.warning("Overpass %s returned HTTP %s", url, resp.status_code)
        except (requests.RequestException, ValueError) as e:
            log.warning("Overpass %s failed: %s", url, e)
    return None


def get_airport_features(iata: str) -> dict:
    """Gates and amenities for an airport, cached for a day."""
    elements = cached(f"osm:{iata}", 86400, lambda: _fetch(iata))
    if elements is None:
        raise ConnectionError("Map service is temporarily unavailable")
    return parse_elements(elements)


def parse_elements(elements: list) -> dict:
    gates, places = {}, []
    for el in elements:
        tags = el.get("tags") or {}
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        if tags.get("aeroway") == "gate":
            ref = normalize_gate(tags.get("ref") or tags.get("name") or "")
            if ref:
                gates.setdefault(ref, {"ref": ref, "lat": lat, "lon": lon,
                                       "level": tags.get("level")})
            continue
        category = _category(tags)
        if not category:
            continue
        places.append({
            "id": f"{el.get('type', 'node')}/{el.get('id')}",
            "category": category,
            "name": tags.get("name") or tags.get("brand") or tags.get("operator"),
            "type": tags.get("amenity") or tags.get("shop"),
            "lat": lat,
            "lon": lon,
            "level": tags.get("level"),
            "opening_hours": tags.get("opening_hours"),
            "cuisine": tags.get("cuisine"),
            "religion": tags.get("religion"),
            "wheelchair": tags.get("wheelchair"),
            "airside": _airside(tags),
        })
    return {"gates": gates, "places": places}


def _category(tags: dict) -> str | None:
    for name, rule in CATEGORIES.items():
        for key, values in rule.items():
            value = tags.get(key)
            if value and (values is None or value in values):
                return name
    return None


def _airside(tags: dict) -> bool | None:
    value = tags.get("airside") or tags.get("airport:side")
    if value in ("yes", "airside"):
        return True
    if value in ("no", "landside"):
        return False
    return None


def normalize_gate(gate: str) -> str:
    gate = re.sub(r"^(GATE|KAPI)\s*", "", (gate or "").strip().upper())
    return re.sub(r"[\s\-]", "", gate)


def distance_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def find_nearby(iata: str, gate: str | None, categories: list[str], limit: int = 5) -> dict:
    features = get_airport_features(iata)
    if not features["gates"] and not features["places"]:
        raise AirportNotFound(f"No map data found for {iata}")

    gate_ref = normalize_gate(gate) if gate else None
    origin = features["gates"].get(gate_ref) if gate_ref else None

    results = {}
    for category in categories:
        items = [dict(p) for p in features["places"] if p["category"] == category]
        if origin:
            for p in items:
                straight = distance_m(origin["lat"], origin["lon"], p["lat"], p["lon"])
                p["distance_m"] = int(round(straight * DETOUR_FACTOR / 10) * 10)
                p["walk_minutes"] = max(1, round(p["distance_m"] / WALK_METERS_PER_MIN))
            # Nearest first; unnamed places last when distances tie
            items.sort(key=lambda p: (p.get("distance_m", 0), p["name"] is None))
        else:
            items.sort(key=lambda p: (p["name"] is None, p["name"] or ""))
        results[category] = {"count": len(items), "nearest": items[:limit]}

    return {
        "airport": airports.lookup(iata),
        "gate": origin,
        "gate_requested": gate_ref,
        "gate_found": bool(origin),
        "gates_on_map": len(features["gates"]),
        "categories": results,
        "attribution": ATTRIBUTION,
        "distance_note": ("Walking distances are estimates from map positions and may not "
                          "reflect security or passport-control routes.") if origin else None,
    }
