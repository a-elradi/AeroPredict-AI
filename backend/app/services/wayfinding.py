"""
AeroPredict — Airport wayfinding
Finds gates and amenities inside an airport from OpenStreetMap
(free, via the Overpass API) and ranks amenities by walking distance
from the passenger's gate. Map data © OpenStreetMap contributors (ODbL).
"""
import logging
import json
import math
import re
import threading
from datetime import datetime
from pathlib import Path

import requests

from . import airports
from ..utils import cache

log = logging.getLogger(__name__)

# Public Overpass servers, tried in order
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
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

# Lounges and prayer rooms are often mapped only by name (e.g. a lounge tagged
# office=company), so names are matched too. Overpass and Python share these words.
NAME_WORDS = {
    "lounge": ["lounge", "salonu"],
    "prayer_room": ["prayer", "mescit", "namaz", "masjid", "musalla", "chapel", "multi-faith", "multifaith"],
}
_NAME_RE = "|".join(w for words in NAME_WORDS.values() for w in words)
_NOT_A_LOUNGE = {"bar", "pub", "nightclub", "hookah_lounge"}

# Big airports can take Overpass a minute or more on a first load, so loads
# run in a background thread and the browser checks back until they finish.
OVERPASS_TIMEOUT_S = 120
NAME_QUERY_TIMEOUT_S = 60
CACHE_TTL_S = 86400
FAILURE_COOLDOWN_S = 120

# Saved airport maps, refreshed monthly by .github/workflows/osm-snapshots.yml
SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "osm"

_loading: set[str] = set()
_lock = threading.Lock()

WALK_METERS_PER_MIN = 75   # relaxed walking pace with luggage
DETOUR_FACTOR = 1.3        # corridors are not straight lines


class AirportNotFound(Exception):
    pass


class MapLoading(Exception):
    """The airport's map is being downloaded; ask again shortly."""


def _query(iata: str, icao: str | None, kind: str, by_name: bool = False) -> str:
    """Overpass query for one element kind ("node", "way" or "nwr") at an airport.
    by_name searches place names only (lounges and prayer rooms tagged oddly)."""
    amenity_re = "|".join(_AMENITY_VALUES)
    head = f"[out:json][timeout:{OVERPASS_TIMEOUT_S - 10}];"
    bounds = airports.BOUNDS.get(iata)
    if bounds:
        scope = "({},{},{},{})".format(*bounds)
        setup = ""
    else:
        # Unknown airport: search inside its outline (slower for the server)
        setup = f'area["aeroway"="aerodrome"]["iata"="{iata}"]->.a1;'
        union = ".a1;"
        if icao:
            setup += f'area["aeroway"="aerodrome"]["icao"="{icao}"]->.a2;'
            union += ".a2;"
        setup += f"({union})->.a;"
        scope = "(area.a)"
    if by_name:
        body = f'{kind}{scope}["name"~"{_NAME_RE}",i];'
    else:
        body = (f'{kind}{scope}["amenity"~"^({amenity_re})$"];'
                f'{kind}{scope}["shop"];'
                f'{kind}{scope}["aeroway"="gate"];')
    return head + setup + "(" + body + ");out center tags;"


# Last failure reason per airport (no secrets), shown on /api/concierge/status
last_errors: dict[str, str] = {}


def _run(query: str, timeout: int, urls: list[str] = OVERPASS_URLS) -> tuple[list | None, list[str]]:
    reasons = []
    for url in urls:
        host = url.split("/")[2]
        try:
            resp = requests.post(url, data={"data": query}, timeout=timeout,
                                 headers={"User-Agent": "AeroPredict/1.0"})
            if resp.ok:
                data = resp.json()
                # Overpass reports query timeouts and memory limits in "remark" with HTTP 200
                if data.get("remark") and not data.get("elements"):
                    reasons.append(f"{host}: {data['remark'][:200]}")
                    continue
                return data.get("elements", []), reasons
            reasons.append(f"{host}: HTTP {resp.status_code} {resp.text[:200]}")
        except (requests.RequestException, ValueError) as e:
            reasons.append(f"{host}: {type(e).__name__}: {str(e)[:200]}")
    return None, reasons


def _fetch(iata: str, timeout: int = OVERPASS_TIMEOUT_S) -> list | None:
    """Nodes hold most gates and shops and are cheap to query; ways are a bonus."""
    icao = airports.lookup(iata)["icao"]
    nodes, reasons = _run(_query(iata, icao, "node"), timeout)
    if nodes is None:
        last_errors[iata] = " | ".join(reasons)
        log.warning("Overpass failed for %s: %s", iata, last_errors[iata])
        return None
    ways, reasons = _run(_query(iata, icao, "way"), timeout)
    if ways is None:
        log.warning("Overpass ways query failed for %s, using nodes only: %s", iata, reasons)
    # Name search is slow for the servers, so it gets a short limit and is optional
    named, reasons = _run(_query(iata, icao, "nwr", by_name=True), min(timeout, NAME_QUERY_TIMEOUT_S),
                          OVERPASS_URLS[:2])
    if named is None:
        log.warning("Overpass name query failed for %s: %s", iata, reasons)
    last_errors.pop(iata, None)
    elements, seen = [], set()
    for el in nodes + (ways or []) + (named or []):
        key = (el.get("type"), el.get("id"))
        if key not in seen:
            seen.add(key)
            elements.append(el)
    return elements


def _load(iata: str) -> None:
    try:
        elements = _fetch(iata)
        if elements is None:
            cache.put(f"osm-failed:{iata}", True, FAILURE_COOLDOWN_S)
        else:
            features = parse_elements(elements)
            features["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")
            cache.put(f"osm:{iata}", features, CACHE_TTL_S)
    finally:
        with _lock:
            _loading.discard(iata)


def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def load_snapshot(iata: str) -> dict | None:
    path = SNAPSHOT_DIR / f"{iata}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("Bad map snapshot %s", path)
        return None
    return {"gates": data["gates"], "places": data["places"], "updated_at": data.get("generated_at")}


def get_airport_features(iata: str) -> dict:
    """Gates and amenities for an airport. Raises MapLoading while downloading."""
    features = cache.get(f"osm:{iata}")
    if features is not None:
        return features
    features = load_snapshot(iata)
    if features is not None:
        cache.put(f"osm:{iata}", features, CACHE_TTL_S)
        return features
    if cache.get(f"osm-failed:{iata}"):
        raise ConnectionError("Map service is temporarily unavailable")
    with _lock:
        start = iata not in _loading
        if start:
            _loading.add(iata)
    if start:
        _spawn(_load, iata)
    features = cache.get(f"osm:{iata}")  # set already if the load ran synchronously
    if features is not None:
        return features
    if cache.get(f"osm-failed:{iata}"):
        raise ConnectionError("Map service is temporarily unavailable")
    raise MapLoading(iata)


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
    label = (tags.get("name") or "").lower()
    if label and tags.get("amenity") not in _NOT_A_LOUNGE:
        for category, words in NAME_WORDS.items():
            if any(w in label for w in words):
                return category
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


def _gates_center(gates: dict) -> tuple[float, float] | None:
    if not gates:
        return None
    return (sum(g["lat"] for g in gates.values()) / len(gates),
            sum(g["lon"] for g in gates.values()) / len(gates))


def find_nearby(iata: str, gate: str | None, categories: list[str], limit: int = 5) -> dict:
    features = get_airport_features(iata)
    if not features["gates"] and not features["places"]:
        raise AirportNotFound(f"No map data found for {iata}")

    gate_ref = normalize_gate(gate) if gate else None
    origin = features["gates"].get(gate_ref) if gate_ref else None
    center = _gates_center(features["gates"])

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
        elif center:
            # Near the terminals first; places outside the airport sink to the end
            items.sort(key=lambda p: (p["name"] is None,
                                      distance_m(center[0], center[1], p["lat"], p["lon"])))
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
        "map_updated": features.get("updated_at"),
        "distance_note": ("Walking distances are estimates from map positions and may not "
                          "reflect security or passport-control routes.") if origin else None,
    }
