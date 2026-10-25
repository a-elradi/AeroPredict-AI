"""
AeroPredict — Airport reference data
IATA -> ICAO / name / country, used when live flight data is unavailable.
"""
import json
from functools import lru_cache
from pathlib import Path

AIRPORTS = {
    # North America
    "ATL": ("KATL", "Atlanta Hartsfield-Jackson", "US"),
    "BOS": ("KBOS", "Boston Logan", "US"),
    "DEN": ("KDEN", "Denver Intl", "US"),
    "DFW": ("KDFW", "Dallas/Fort Worth", "US"),
    "EWR": ("KEWR", "Newark Liberty", "US"),
    "IAD": ("KIAD", "Washington Dulles", "US"),
    "JFK": ("KJFK", "New York JFK", "US"),
    "LAS": ("KLAS", "Las Vegas Harry Reid", "US"),
    "LAX": ("KLAX", "Los Angeles Intl", "US"),
    "LGA": ("KLGA", "New York LaGuardia", "US"),
    "MCO": ("KMCO", "Orlando Intl", "US"),
    "MIA": ("KMIA", "Miami Intl", "US"),
    "ORD": ("KORD", "Chicago O'Hare", "US"),
    "PHX": ("KPHX", "Phoenix Sky Harbor", "US"),
    "SEA": ("KSEA", "Seattle-Tacoma", "US"),
    "SFO": ("KSFO", "San Francisco Intl", "US"),
    "YYZ": ("CYYZ", "Toronto Pearson", "CA"),
    # Europe
    "AMS": ("EHAM", "Amsterdam Schiphol", "NL"),
    "BCN": ("LEBL", "Barcelona El Prat", "ES"),
    "CDG": ("LFPG", "Paris Charles de Gaulle", "FR"),
    "FCO": ("LIRF", "Rome Fiumicino", "IT"),
    "FRA": ("EDDF", "Frankfurt", "DE"),
    "LGW": ("EGKK", "London Gatwick", "GB"),
    "LHR": ("EGLL", "London Heathrow", "GB"),
    "MAD": ("LEMD", "Madrid Barajas", "ES"),
    "MUC": ("EDDM", "Munich", "DE"),
    "VIE": ("LOWW", "Vienna", "AT"),
    "ZRH": ("LSZH", "Zurich", "CH"),
    # Türkiye
    "ADB": ("LTBJ", "Izmir Adnan Menderes", "TR"),
    "AYT": ("LTAI", "Antalya", "TR"),
    "ESB": ("LTAC", "Ankara Esenboga", "TR"),
    "IST": ("LTFM", "Istanbul Airport", "TR"),
    "SAW": ("LTFJ", "Istanbul Sabiha Gokcen", "TR"),
    # Middle East & Africa
    "AUH": ("OMAA", "Abu Dhabi Intl", "AE"),
    "BAH": ("OBBI", "Bahrain Intl", "BH"),
    "CAI": ("HECA", "Cairo Intl", "EG"),
    "DOH": ("OTHH", "Hamad Intl Doha", "QA"),
    "DXB": ("OMDB", "Dubai Intl", "AE"),
    "JED": ("OEJN", "Jeddah King Abdulaziz", "SA"),
    "KWI": ("OKBK", "Kuwait Intl", "KW"),
    "MCT": ("OOMS", "Muscat Intl", "OM"),
    "MED": ("OEMA", "Madinah Prince Mohammad", "SA"),
    "RUH": ("OERK", "Riyadh King Khalid", "SA"),
    # Asia-Pacific
    "BKK": ("VTBS", "Bangkok Suvarnabhumi", "TH"),
    "BOM": ("VABB", "Mumbai", "IN"),
    "DEL": ("VIDP", "Delhi Indira Gandhi", "IN"),
    "HKG": ("VHHH", "Hong Kong Intl", "HK"),
    "HND": ("RJTT", "Tokyo Haneda", "JP"),
    "ICN": ("RKSI", "Seoul Incheon", "KR"),
    "KUL": ("WMKK", "Kuala Lumpur Intl", "MY"),
    "NRT": ("RJAA", "Tokyo Narita", "JP"),
    "SIN": ("WSSS", "Singapore Changi", "SG"),
    "SYD": ("YSSY", "Sydney Kingsford Smith", "AU"),
    # South America
    "GRU": ("SBGR", "Sao Paulo Guarulhos", "BR"),
}

# Airport bounding boxes (south, west, north, east) from OpenStreetMap / Nominatim.
# Box queries are far cheaper for the map server than airport-outline queries.
BOUNDS = {
    "ATL": (33.6174, -84.4535, 33.6574, -84.4011), "BOS": (42.3467, -71.0332, 42.3796, -70.9853),
    "DEN": (39.8174, -104.7376, 39.9042, -104.6079), "DFW": (32.8687, -97.0862, 32.9238, -96.9933),
    "EWR": (40.6701, -74.1921, 40.7081, -74.1532), "IAD": (38.9143, -77.5188, 38.9902, -77.4296),
    "JFK": (40.6211, -73.8232, 40.6648, -73.7484), "LAS": (36.0715, -115.1729, 36.1008, -115.1189),
    "LAX": (33.9312, -118.4389, 33.9531, -118.3788), "LGA": (40.765, -73.8893, 40.7864, -73.8512),
    "MCO": (28.3747, -81.3431, 28.4512, -81.2767), "MIA": (25.783, -80.319, 25.8081, -80.2647),
    "ORD": (41.9476, -87.9412, 42.0089, -87.8708), "PHX": (33.4216, -112.0339, 33.4439, -111.9802),
    "SEA": (47.4281, -122.3207, 47.4669, -122.296), "SFO": (37.6044, -122.4028, 37.6392, -122.3551),
    "YYZ": (43.6515, -79.667, 43.7031, -79.59), "AMS": (52.2857, 4.7061, 52.3679, 4.8095),
    "BCN": (41.2802, 2.0557, 41.3136, 2.1097), "CDG": (48.9838, 2.4922, 49.03, 2.6329),
    "FCO": (41.7759, 12.2201, 41.8542, 12.2814), "FRA": (49.9957, 8.4878, 50.0531, 8.6018),
    "LGW": (51.1436, -0.2155, 51.1646, -0.1613), "LHR": (51.4561, -0.4944, 51.4794, -0.4152),
    "MAD": (40.4478, -3.5974, 40.5422, -3.5265), "MUC": (48.3369, 11.7364, 48.3705, 11.8357),
    "VIE": (48.078, 16.5205, 48.1312, 16.598), "ZRH": (47.4389, 8.5275, 47.4874, 8.5762),
    "ADB": (38.2673, 27.1451, 38.3113, 27.1741), "AYT": (36.8731, 30.7753, 36.9261, 30.8169),
    "ESB": (40.0922, 32.9782, 40.1541, 33.0249), "IST": (41.2484, 28.7001, 41.3021, 28.8041),
    "SAW": (40.8748, 29.2864, 40.9138, 29.3488), "AUH": (24.4119, 54.6202, 24.47, 54.6767),
    "BAH": (26.2565, 50.6128, 26.2832, 50.6551), "CAI": (30.0871, 31.3676, 30.141, 31.4602),
    "DOH": (25.248, 51.5794, 25.3019, 51.6305), "DXB": (25.2306, 55.337, 25.2725, 55.4034),
    "JED": (21.6354, 39.115, 21.7339, 39.2081), "KWI": (29.1834, 47.9292, 29.2572, 48.007),
    "MCT": (23.5834, 58.2478, 23.6155, 58.3131), "MED": (24.5314, 39.6873, 24.5846, 39.7214),
    "RUH": (24.9193, 46.661, 24.9933, 46.7565), "BKK": (13.647, 100.7221, 13.7164, 100.7825),
    "BOM": (19.0791, 72.8439, 19.1012, 72.882), "DEL": (28.5362, 77.0612, 28.5747, 77.1331),
    "HKG": (22.2902, 113.8765, 22.3349, 113.9462), "HND": (35.521, 139.754, 35.5704, 139.8257),
    "ICN": (37.433, 126.4031, 37.4946, 126.4757), "KUL": (2.7015, 101.6728, 2.7848, 101.7281),
    "NRT": (35.737, 140.3619, 35.8148, 140.4052), "SIN": (1.3135, 103.9709, 1.3891, 104.0193),
    "SYD": (-33.974, 151.1614, -33.926, 151.1955), "GRU": (-23.4578, -46.494, -23.4179, -46.4376),
}

# Rough airport busyness (0-1), used only for security-queue estimates in the
# journey planner. Not used by the delay model.
AIRPORT_CONGESTION = {
    "ORD": 0.91, "ATL": 0.88, "LAX": 0.85, "DFW": 0.84, "JFK": 0.83,
    "EWR": 0.82, "SFO": 0.80, "BOS": 0.78, "LGA": 0.87, "MIA": 0.76,
    "SEA": 0.72, "DEN": 0.70, "MCO": 0.68, "LAS": 0.65, "PHX": 0.63,
    "BAH": 0.45, "DXB": 0.88, "DOH": 0.75, "AUH": 0.70, "RUH": 0.55,
    "CDG": 0.82, "LHR": 0.91, "FRA": 0.85, "AMS": 0.80, "IST": 0.78,
}

# Schengen-area countries: no passport control between them
SCHENGEN = {"AT", "BE", "CH", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HU",
            "IS", "IT", "LI", "LT", "LU", "LV", "MT", "NL", "NO", "PL", "PT", "SE",
            "SI", "SK", "HR", "BG", "RO"}


_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@lru_cache(maxsize=1)
def _world() -> dict:
    """All airports with scheduled service (OurAirports), see scripts/build_reference_data.py."""
    path = _DATA_DIR / "airports_world.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@lru_cache(maxsize=1)
def _eurocontrol() -> dict:
    path = _DATA_DIR / "eurocontrol_punctuality.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def lookup(iata: str) -> dict:
    code = (iata or "").upper().strip()
    w = _world().get(code)
    icao, name, country = AIRPORTS.get(code, (None, None, None))
    if w:
        icao, country = icao or w[0], country or w[2]
        name = name or w[1]
    return {"iata": code, "icao": icao, "name": name, "country": country,
            "lat": w[3] if w else None, "lon": w[4] if w else None}


def punctuality(iata: str) -> dict | None:
    """
    Official EUROCONTROL punctuality for a European airport (or, failing that,
    its country): share of departures/arrivals within 15 minutes of schedule.
    """
    euro = _eurocontrol()
    if not euro:
        return None
    info = lookup(iata)
    apt = euro["airports"].get(info["icao"] or "")
    if apt and apt.get("dep_on_time_pct") is not None:
        return {**apt, "level": "airport", "period": euro["period"], "source": euro["source"]}
    cty = euro["countries"].get(info["country"] or "")
    if cty and cty.get("dep_on_time_pct") is not None:
        return {**cty, "level": "country", "period": euro["period"], "source": euro["source"]}
    return None


def needs_passport_control(dep_country: str | None, arr_country: str | None) -> bool:
    """Unknown countries are treated as international (the safer assumption)."""
    if not dep_country or not arr_country:
        return True
    if dep_country == arr_country:
        return False
    return not (dep_country in SCHENGEN and arr_country in SCHENGEN)


def congestion(iata: str) -> float:
    return AIRPORT_CONGESTION.get((iata or "").upper(), 0.60)
