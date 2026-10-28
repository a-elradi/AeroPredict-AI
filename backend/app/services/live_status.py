"""
AeroPredict — live airport status that changes the odds on the day.

US airports: the FAA's National Airspace System status (ground stops, ground delay
programs and general arrival/departure delays), https://nasstatus.faa.gov.
It describes what is happening now, so it is only applied to flights leaving
in the next few hours.
"""
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

FAA_URL = "https://nasstatus.faa.gov/api/airport-status-information"
CACHE_SECONDS = 300
LIVE_WINDOW = timedelta(hours=6)  # the status says nothing useful about flights further ahead

_lock = threading.Lock()
_cache = {"at": 0.0, "events": {}}


def minutes(text: str | None) -> int | None:
    """'1 hour and 5 minutes' -> 65, '31-45 minutes' -> 45 (the upper end)."""
    if not text:
        return None
    text = text.lower()
    hours = re.search(r"(\d+)\s*hour", text)
    mins = re.findall(r"(\d+)\s*(?:-\s*(\d+)\s*)?minute", text)
    total = int(hours.group(1)) * 60 if hours else 0
    if mins:
        low, high = mins[-1]
        total += int(high or low)
    return total if (hours or mins) else None


def parse_faa(xml_text: str) -> dict:
    """{airport: [event, ...]}; each event {kind, side, avg, max, reason}."""
    events = {}
    root = ET.fromstring(xml_text)

    def add(arpt, event):
        if arpt:
            events.setdefault(arpt.strip().upper(), []).append(event)

    for block in root.iter("Delay_type"):
        name = (block.findtext("Name") or "").lower()
        if "ground stop" in name:
            for p in block.iter("Program"):
                add(p.findtext("ARPT"), {"kind": "ground_stop", "side": "arrival",
                                         "reason": p.findtext("Reason"), "until": p.findtext("End_Time")})
        elif "ground delay" in name:
            for g in block.iter("Ground_Delay"):
                add(g.findtext("ARPT"), {"kind": "ground_delay", "side": "arrival", "reason": g.findtext("Reason"),
                                         "avg": minutes(g.findtext("Avg")), "max": minutes(g.findtext("Max"))})
        elif "arrival/departure" in name:
            for d in block.iter("Delay"):
                for ad in d.iter("Arrival_Departure"):
                    side = "departure" if (ad.get("Type") or "").lower().startswith("dep") else "arrival"
                    add(d.findtext("ARPT"), {"kind": "general_delay", "side": side, "reason": d.findtext("Reason"),
                                             "avg": None, "max": minutes(ad.findtext("Max")),
                                             "trend": ad.findtext("Trend")})
    return events


def faa_events() -> dict:
    """Current FAA events by airport, cached for a few minutes; empty if the FAA can't be reached."""
    with _lock:
        if time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["events"]
    try:
        r = requests.get(FAA_URL, timeout=5)
        r.raise_for_status()
        events = parse_faa(r.text)
    except (requests.RequestException, ET.ParseError):
        events = {}
    with _lock:
        _cache.update(at=time.time(), events=events)
    return events


def _floor(event: dict) -> float:
    """Smallest sensible chance of a 15+ minute delay while this event is in force."""
    if event["kind"] == "ground_stop":
        return 0.85
    worst = event.get("avg") or event.get("max") or 0
    if event["kind"] == "ground_delay":
        return 0.8 if worst >= 60 else 0.65 if worst >= 30 else 0.45 if worst >= 15 else 0.3
    return 0.6 if worst >= 45 else 0.5 if worst >= 30 else 0.35 if worst >= 15 else 0.0


def describe(airport: str, event: dict) -> str:
    if event["kind"] == "ground_stop":
        return f"Ground stop: flights to {airport} are being held"
    if event["kind"] == "ground_delay":
        return f"Flights to {airport} delayed an average of {event['avg']} min" if event.get("avg") \
            else f"Ground delay program for flights to {airport}"
    what = "Departures from" if event["side"] == "departure" else "Arrivals at"
    return f"{what} {airport} running up to {event['max']} min late" if event.get("max") \
        else f"{what} {airport} delayed"


def apply(proba: list, origin: str, dest: str, dep_utc: datetime | None) -> tuple[list, list, int | None]:
    """
    Raise the delay odds when the FAA reports trouble that affects this flight now.
    Returns (proba, notes, typical_delay_minutes_or_None).
    """
    now = datetime.now(timezone.utc)
    if dep_utc is None or not (now - timedelta(hours=1) <= dep_utc <= now + LIVE_WINDOW):
        return proba, [], None
    events = faa_events()
    relevant = [(origin, e) for e in events.get(origin, []) if e["side"] == "departure"] + \
               [(dest, e) for e in events.get(dest, []) if e["side"] == "arrival"]
    if not relevant:
        return proba, [], None
    floor = max(_floor(e) for _, e in relevant)
    on, delayed, cancelled = proba
    if delayed + cancelled < floor:
        delayed = floor - cancelled
        on = 1 - floor
    typical = max((e.get("avg") or e.get("max") or 0) for _, e in relevant) or None
    return [on, delayed, cancelled], [describe(a, e) for a, e in relevant], typical
