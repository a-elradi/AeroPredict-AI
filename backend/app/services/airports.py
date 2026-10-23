"""
AeroPredict — Airport reference data
IATA -> ICAO / name / country, used when live flight data is unavailable.
"""
from .ml_engine import AIRPORT_CONGESTION

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

# Schengen-area countries: no passport control between them
SCHENGEN = {"AT", "BE", "CH", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HU",
            "IS", "IT", "LI", "LT", "LU", "LV", "MT", "NL", "NO", "PL", "PT", "SE",
            "SI", "SK", "HR", "BG", "RO"}


def lookup(iata: str) -> dict:
    code = (iata or "").upper().strip()
    icao, name, country = AIRPORTS.get(code, (None, None, None))
    return {"iata": code, "icao": icao, "name": name, "country": country}


def needs_passport_control(dep_country: str | None, arr_country: str | None) -> bool:
    """Unknown countries are treated as international (the safer assumption)."""
    if not dep_country or not arr_country:
        return True
    if dep_country == arr_country:
        return False
    return not (dep_country in SCHENGEN and arr_country in SCHENGEN)


def congestion(iata: str) -> float:
    return AIRPORT_CONGESTION.get((iata or "").upper(), 0.60)
