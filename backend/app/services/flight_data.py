"""
AeroPredict — Live flight data (AeroDataBox via RapidAPI)
Needs the RAPIDAPI_KEY environment variable. Without it, every lookup
returns None and callers fall back to what the passenger typed in.
"""
import logging
import os
import re
from urllib.parse import quote

import requests

from ..utils.cache import cached

log = logging.getLogger(__name__)

AERODATABOX_HOST = "aerodatabox.p.rapidapi.com"
_LOCAL_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})")


def is_configured() -> bool:
    return bool(os.environ.get("RAPIDAPI_KEY"))


def normalize_flight_number(flight_number: str) -> str:
    return re.sub(r"\s+", "", flight_number or "").upper()


def get_flight(flight_number: str, date: str, dep_airport: str | None = None) -> dict | None:
    """Live schedule/status for one flight on a local date (YYYY-MM-DD), or None."""
    if not is_configured():
        return None
    number = normalize_flight_number(flight_number)
    flights = cached(f"adb:{number}:{date}", 300, lambda: _fetch(number, date))
    if not flights:
        return None
    return _pick_leg(flights, dep_airport)


def _fetch(number: str, date: str) -> list | None:
    url = f"https://{AERODATABOX_HOST}/flights/number/{quote(number)}/{quote(date)}"
    try:
        resp = requests.get(
            url,
            headers={
                "X-RapidAPI-Key": os.environ["RAPIDAPI_KEY"],
                "X-RapidAPI-Host": AERODATABOX_HOST,
            },
            params={"withAircraftImage": "false", "withLocation": "false"},
            timeout=8,
        )
    except requests.RequestException as e:
        log.warning("AeroDataBox request failed: %s", e)
        return None
    if resp.status_code in (204, 404):
        return None
    if not resp.ok:
        log.warning("AeroDataBox returned HTTP %s", resp.status_code)
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, list) else [data]


def _pick_leg(flights: list, dep_airport: str | None) -> dict | None:
    legs = [_normalize(f) for f in flights if isinstance(f, dict)]
    legs = [leg for leg in legs if leg["departure"]["iata"]]
    if not legs:
        return None
    if dep_airport:
        for leg in legs:
            if leg["departure"]["iata"] == dep_airport.upper():
                return leg
    return legs[0]


def _local(time_obj) -> tuple[str | None, str | None]:
    """AeroDataBox time object -> (YYYY-MM-DD, HH:MM) in airport local time."""
    if isinstance(time_obj, dict):
        time_obj = time_obj.get("local")
    if not isinstance(time_obj, str):
        return None, None
    m = _LOCAL_TIME.match(time_obj)
    return (m.group(1), m.group(2)) if m else (None, None)


def _leg(raw: dict) -> dict:
    airport = raw.get("airport") or {}
    sched_date, sched_time = _local(raw.get("scheduledTime") or raw.get("scheduledTimeLocal"))
    _, revised_time = _local(raw.get("revisedTime") or raw.get("predictedTime"))
    return {
        "iata": (airport.get("iata") or "").upper() or None,
        "icao": airport.get("icao"),
        "name": airport.get("name"),
        "country": airport.get("countryCode"),
        "scheduled_date": sched_date,
        "scheduled_time": sched_time,
        "revised_time": revised_time,
        "terminal": raw.get("terminal"),
        "gate": raw.get("gate"),
        "check_in_desk": raw.get("checkInDesk"),
        "baggage_belt": raw.get("baggageBelt"),
    }


def _normalize(raw: dict) -> dict:
    return {
        "number": raw.get("number"),
        "status": raw.get("status"),
        "airline": (raw.get("airline") or {}).get("name"),
        "airline_iata": (raw.get("airline") or {}).get("iata"),
        "aircraft": (raw.get("aircraft") or {}).get("model"),
        "departure": _leg(raw.get("departure") or {}),
        "arrival": _leg(raw.get("arrival") or {}),
    }
