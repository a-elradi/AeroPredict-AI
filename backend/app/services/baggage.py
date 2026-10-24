"""
AeroPredict — Baggage risk alerts
Estimates the chance a checked bag is delayed or lost on an itinerary and
tells the passenger what to do before, during and after the trip.

Rates are approximations from SITA's annual Baggage IT Insights reports:
roughly 6-7 mishandled bags per 1,000 passengers worldwide, highest in
Europe and lowest in Asia, with transfer bags making up about 40% of
mishandled bags while being a much smaller share of all bags.
"""
from datetime import datetime

from . import airports

# Approximate mishandled bags per 1,000 passengers, by departure region
REGION_RATE_PER_1000 = {
    "europe": 12.0,
    "north_america": 6.3,
    "middle_east": 4.0,
    "asia_pacific": 2.5,
    "latin_america": 5.0,
    "africa": 6.0,
    "other": 6.5,
}

REGION_BY_COUNTRY = {
    **{c: "europe" for c in ("AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
                             "FR", "GB", "GR", "HR", "HU", "IE", "IS", "IT", "LT", "LU", "LV",
                             "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK", "TR", "UA",
                             "RS", "AL", "BA", "ME", "MK", "MD", "GE", "AM", "AZ")},
    **{c: "north_america" for c in ("US", "CA", "MX")},
    **{c: "middle_east" for c in ("AE", "BH", "IL", "IQ", "IR", "JO", "KW", "LB", "OM", "QA",
                                  "SA", "SY", "YE", "EG")},
    **{c: "asia_pacific" for c in ("AU", "BD", "CN", "HK", "ID", "IN", "JP", "KR", "LK", "MY",
                                   "NP", "NZ", "PH", "PK", "SG", "TH", "TW", "VN", "KZ", "UZ")},
    **{c: "latin_america" for c in ("AR", "BO", "BR", "CL", "CO", "CR", "CU", "DO", "EC", "GT",
                                    "PA", "PE", "PY", "UY", "VE")},
    **{c: "africa" for c in ("DZ", "ET", "GH", "KE", "MA", "NG", "SN", "TN", "TZ", "UG", "ZA")},
}

# Extra risk added by each transfer, relative to the hub's regional rate.
# Regional rates already include transfer bags, so this is a modest uplift.
TRANSFER_MULTIPLIER = 1.5
CONNECTION_FACTORS = {      # by connection length
    "under_1h": 2.5,
    "1_2h": 1.0,
    "2_4h": 0.7,
    "over_4h": 0.6,
}
CONNECTION_LABELS = {
    "under_1h": "under 1 hour",
    "1_2h": "1-2 hours",
    "2_4h": "2-4 hours",
    "over_4h": "over 4 hours",
}
AIRLINE_CHANGE_FACTOR = 1.5   # bags handed between airlines go astray more often
TERMINAL_CHANGE_FACTOR = 1.3
PEAK_MONTHS = {6, 7, 8, 12}   # summer and year-end holidays
PEAK_FACTOR = 1.2
MAX_PROBABILITY = 0.5


def region_of(iata: str) -> str:
    country = airports.lookup(iata)["country"]
    return REGION_BY_COUNTRY.get(country, "other")


def assess(route: list[str], connections: list[dict], bags: int, travel_date: str | None) -> dict:
    """
    route: airports in order, e.g. ["IST", "FRA", "JFK"]
    connections: one per intermediate airport:
        {"duration": "under_1h" | "1_2h" | "2_4h" | "over_4h",
         "airline_change": bool, "terminal_change": bool}
    """
    factors = []
    month = None
    if travel_date:
        month = datetime.strptime(travel_date, "%Y-%m-%d").month
    season = PEAK_FACTOR if month in PEAK_MONTHS else 1.0

    origin_region = region_of(route[0])
    per_bag = REGION_RATE_PER_1000[origin_region] / 1000 * season
    factors.append({
        "factor": f"Departing from {route[0]}",
        "impact": "high" if origin_region == "europe" else "low" if origin_region == "asia_pacific" else "medium",
        "detail": (f"Airports in this region mishandle about "
                   f"{REGION_RATE_PER_1000[origin_region]:g} bags per 1,000 passengers."),
    })

    for i, conn in enumerate(connections):
        hub = route[i + 1]
        rate = REGION_RATE_PER_1000[region_of(hub)] / 1000
        factor = TRANSFER_MULTIPLIER * CONNECTION_FACTORS[conn["duration"]]
        if conn.get("airline_change"):
            factor *= AIRLINE_CHANGE_FACTOR
        if conn.get("terminal_change"):
            factor *= TERMINAL_CHANGE_FACTOR
        per_bag = 1 - (1 - per_bag) * (1 - min(rate * factor * season, MAX_PROBABILITY))

        tight = conn["duration"] == "under_1h"
        detail = f"Connection of {CONNECTION_LABELS[conn['duration']]} in {hub}."
        if tight:
            detail += " Bags often miss short connections even when passengers make it."
        if conn.get("airline_change"):
            detail += " Your bag changes airline here, which adds a hand-over."
        if conn.get("terminal_change"):
            detail += " It also has to move between terminals."
        factors.append({
            "factor": f"Transfer at {hub}",
            "impact": "high" if tight or conn.get("airline_change") else "medium",
            "detail": detail,
        })

    if season > 1:
        factors.append({"factor": "Peak travel season", "impact": "medium",
                        "detail": "Summer and year-end holidays bring more mishandled bags."})

    per_bag = min(per_bag, MAX_PROBABILITY)
    # Bags on the same trip share their fate (same flights, same transfers), so
    # each extra bag adds less risk than an independent bag would.
    trip = 1 - (1 - per_bag) ** (1 + 0.5 * (max(bags, 1) - 1))
    pct = round(trip * 100, 1)
    level = "high" if pct >= 4 else "moderate" if pct >= 1.5 else "low"

    return {
        "route": route,
        "checked_bags": bags,
        "risk_percent": pct,
        "risk_level": level,
        "one_in": int(round(1 / trip)) if trip > 0 else None,
        "factors": factors,
        "actions": _actions(level, connections),
        "if_bag_missing": _missing_bag_steps(),
        "compensation": _compensation(route),
        "method": ("Estimate based on industry-wide mishandled-baggage rates (SITA Baggage IT "
                   "Insights) adjusted for your connections, airline changes and season. "
                   "It is not airline-specific."),
    }


def _actions(level: str, connections: list[dict]) -> dict:
    before = [
        "Put a tracker (e.g. AirTag or SmartTag) inside each checked bag.",
        "Photograph your bag and its contents, and note the brand and colour.",
        "Put your name, phone number and email on a label outside and a card inside the bag.",
        "Remove old airline barcode stickers so scanners don't read the wrong one.",
        "Pack medicines, valuables, chargers and one change of clothes in your carry-on.",
        "Keep the baggage tag receipt the agent gives you. You need its number to trace the bag.",
    ]
    during = []
    if any(c["duration"] == "under_1h" for c in connections):
        during.append("Your connection is short. Ask at check-in whether your bag can make it, "
                      "and consider a later flight if you have the choice.")
    if any(c.get("airline_change") for c in connections):
        during.append("Check that your bag is tagged all the way to your final airport, "
                      "not just to the connection.")
    if level != "low":
        during.append("Check your tracker after each landing to see whether the bag made the flight.")
    during.append("If you are rebooked onto another flight, tell the agent you have checked bags "
                  "so they can be re-routed too.")
    return {"before_you_fly": before, "during_your_trip": during}


def _missing_bag_steps() -> list[str]:
    return [
        "Don't leave the baggage hall. Go to the airline's baggage service desk first.",
        "File a Property Irregularity Report (PIR) and keep the file reference number.",
        "Give the desk a phone number and the address where the bag should be delivered.",
        "Track the bag online with the file reference. Most airlines use WorldTracer.",
        "Keep receipts for essentials you buy (toiletries, clothes). Airlines usually refund "
        "reasonable costs.",
        "Send a written claim to the airline within 21 days of getting the bag back "
        "(7 days for damage). A bag still missing after 21 days counts as lost.",
    ]


def _compensation(route: list[str]) -> dict:
    return {
        "rule": "Montreal Convention",
        "summary": ("On most international trips, airlines are liable for delayed, damaged or "
                    "lost bags up to about 1,519 Special Drawing Rights (roughly US$2,000) "
                    "per passenger. You must show the value of what you lost."),
        "deadlines": [
            "Damaged bag: complain in writing within 7 days.",
            "Delayed bag: complain in writing within 21 days of receiving it.",
            "Lost bag: claim once the airline confirms it is lost, or 21 days after it was due.",
        ],
        "note": "Domestic flights can follow national rules instead. Check your airline's conditions of carriage.",
    }
