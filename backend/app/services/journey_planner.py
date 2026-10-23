"""
AeroPredict — Personalized airport journey planner
Combines live flight data, airport weather and the delay model into a
door-to-gate timeline. Claude personalizes the plan; if Claude is not
available, a rule-based plan is returned instead.
"""
import json
import logging
from datetime import datetime, timedelta

from . import airports, claude_client, flight_data, weather
from .ml_engine import predict as predict_delay

log = logging.getLogger(__name__)

TRANSPORT_MODES = ["car", "taxi", "public_transport", "airport_shuttle", "walk"]
CABINS = ["economy", "premium_economy", "business", "first"]
LOYALTY_TIERS = ["none", "silver", "gold", "platinum"]
PREFERENCES = ["dining", "coffee", "lounge", "shopping", "quiet_area",
               "prayer_room", "family", "accessibility", "work_space", "minimal_waiting"]
LANGUAGES = {"en": "English", "tr": "Turkish", "ar": "Arabic", "de": "German",
             "fr": "French", "es": "Spanish", "ru": "Russian"}

STEP_CATEGORIES = ["travel", "arrival", "check_in", "bag_drop", "security",
                   "passport_control", "free_time", "dining", "lounge",
                   "walk_to_gate", "boarding", "gate_closes", "departure", "other"]

DISCLAIMER = ("Times marked as estimates are based on typical airport and airline "
              "procedures. Always follow your airline's instructions and the airport "
              "departure screens.")


class JourneyInputError(ValueError):
    """The request does not contain enough information to build a plan."""


# ─────────────────────────────────────────────
#  Facts gathering
# ─────────────────────────────────────────────
def build_plan(req: dict) -> dict:
    facts, sources = _gather_facts(req)
    baseline = _baseline_schedule(facts)

    plan, ai_generated = None, False
    if claude_client.is_configured():
        try:
            plan = _claude_plan(facts, baseline, req)
            ai_generated = True
        except claude_client.ClaudeUnavailable as e:
            log.warning("Falling back to rule-based plan: %s", e)
    if plan is None:
        plan = _rule_based_plan(facts, baseline)
    if ai_generated:
        sources.append({"name": "Claude AI (personalized plan)", "type": "ai"})

    return {
        "flight": facts["flight"],
        "plan": plan,
        "delay_prediction": facts["delay_prediction"],
        "weather": facts["weather"],
        "ai_generated": ai_generated,
        "data_sources": sources,
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "disclaimer": DISCLAIMER,
    }


def _gather_facts(req: dict) -> tuple[dict, list]:
    sources = []
    live = flight_data.get_flight(req["flight_number"], req["date"], req.get("dep_airport"))
    if live:
        sources.append({"name": "AeroDataBox (live flight schedule & status)", "type": "live"})

    dep_iata = (live and live["departure"]["iata"]) or req.get("dep_airport")
    arr_iata = (live and live["arrival"]["iata"]) or req.get("arr_airport")
    dep_time = (live and live["departure"]["scheduled_time"]) or req.get("dep_time")
    if not dep_iata or not dep_time:
        raise JourneyInputError(
            "We couldn't find live data for this flight. Please enter the departure "
            "airport and departure time."
        )

    dep_ref, arr_ref = airports.lookup(dep_iata), airports.lookup(arr_iata)
    dep_leg = live["departure"] if live else {}
    arr_leg = live["arrival"] if live else {}
    dep_country = dep_leg.get("country") or dep_ref["country"]
    arr_country = arr_leg.get("country") or arr_ref["country"]
    airline = (live and live.get("airline_iata")) or req.get("airline") or req["flight_number"][:2]

    flight = {
        "number": flight_data.normalize_flight_number(req["flight_number"]),
        "airline": (live and live.get("airline")) or airline,
        "status": live.get("status") if live else None,
        "aircraft": live.get("aircraft") if live else None,
        "date": req["date"],
        "departure": {
            "airport": dep_iata,
            "name": dep_leg.get("name") or dep_ref["name"],
            "scheduled_time": dep_time,
            "revised_time": dep_leg.get("revised_time"),
            "terminal": dep_leg.get("terminal"),
            "gate": dep_leg.get("gate"),
            "check_in_desk": dep_leg.get("check_in_desk"),
        },
        "arrival": {
            "airport": arr_iata,
            "name": arr_leg.get("name") or arr_ref["name"],
            "scheduled_time": arr_leg.get("scheduled_time"),
        },
        "passport_control": airports.needs_passport_control(dep_country, arr_country),
        "live_data": bool(live),
    }

    delay = None
    if arr_iata:
        try:
            p = predict_delay(flight["number"], dep_iata, arr_iata, airline, req["date"], dep_time)
            delay = {"probabilities": p["probabilities"], "risk_tier": p["risk_tier"]}
            sources.append({"name": "AeroPredict delay model", "type": "estimate"})
        except Exception:
            log.exception("Delay prediction failed")

    wx = {}
    for role, icao in (("departure", dep_leg.get("icao") or dep_ref["icao"]),
                       ("arrival", arr_leg.get("icao") or arr_ref["icao"])):
        metar = weather.get_metar(icao)
        if metar:
            wx[role] = metar
    if wx:
        sources.append({"name": "aviationweather.gov (current METAR)", "type": "live"})

    facts = {
        "flight": flight,
        "delay_prediction": delay,
        "weather": wx,
        "passenger": _passenger(req),
        "security_wait_estimate_min": _security_wait(dep_iata, dep_time, req),
    }
    sources.append({"name": "Typical airport & airline procedures", "type": "estimate"})
    return facts, sources


def _passenger(req: dict) -> dict:
    return {
        "starting_point": req.get("starting_point"),
        "transport_mode": req.get("transport_mode", "car"),
        "travel_minutes": req.get("travel_minutes"),
        "checked_bags": req.get("checked_bags", 0),
        "online_check_in": req.get("online_check_in", False),
        "cabin": req.get("cabin", "economy"),
        "loyalty_tier": req.get("loyalty_tier", "none"),
        "fast_track": req.get("fast_track", False),
        "preferences": req.get("preferences", []),
        "notes": req.get("notes"),
    }


def _security_wait(airport: str, dep_time: str, req: dict) -> int:
    """Typical queue time; airports rarely publish live figures."""
    base = 8 + 22 * airports.congestion(airport)
    hour = int(dep_time[:2])
    # Most passengers reach security 2-3 hours before departure
    queue_hour = (hour - 2) % 24
    if 5 <= queue_hour <= 9 or 16 <= queue_hour <= 20:
        base *= 1.4
    if req.get("fast_track") or req.get("cabin") in ("business", "first"):
        base = max(5, base * 0.35)
    return int(round(base))


# ─────────────────────────────────────────────
#  Rule-based schedule (also the skeleton Claude refines)
# ─────────────────────────────────────────────
def _baseline_schedule(facts: dict) -> dict:
    f, p = facts["flight"], facts["passenger"]
    intl = f["passport_control"]
    dep = datetime.strptime(f"{f['date']} {f['departure']['scheduled_time']}", "%Y-%m-%d %H:%M")

    premium = p["cabin"] in ("business", "first") or p["fast_track"]
    needs_desk = p["checked_bags"] > 0 or not p["online_check_in"]
    before = 180 if intl else 120
    if premium:
        before -= 30
    if not needs_desk:
        before -= 30
    before = max(before, 90 if intl else 60)

    travel = p["travel_minutes"] or 45
    arrive = dep - timedelta(minutes=before)
    return {
        "leave_by": arrive - timedelta(minutes=travel),
        "arrive_airport_by": arrive,
        "check_in_closes": dep - timedelta(minutes=60 if intl else 45),
        "boarding_starts": dep - timedelta(minutes=45 if intl else 35),
        "be_at_gate_by": dep - timedelta(minutes=50 if intl else 40),
        "gate_closes": dep - timedelta(minutes=20),
        "departure": dep,
        "travel_minutes": travel,
        "travel_is_estimate": p["travel_minutes"] is None,
        "needs_check_in_desk": needs_desk,
        "minutes_before_departure": before,
    }


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _rule_based_plan(facts: dict, b: dict) -> dict:
    f, p = facts["flight"], facts["passenger"]
    dep = f["departure"]
    terminal = f"Terminal {dep['terminal']}" if dep["terminal"] else "your departure terminal"
    gate = f"Gate {dep['gate']}" if dep["gate"] else "your gate"
    security = facts["security_wait_estimate_min"]

    steps = []
    t = b["arrive_airport_by"]

    def add(time, title, details, location, minutes, category, estimate=True):
        steps.append({"time": _hm(time), "title": title, "details": details,
                      "location": location, "duration_minutes": minutes,
                      "category": category, "is_estimate": estimate})

    add(b["leave_by"], "Leave for the airport",
        f"Allow about {b['travel_minutes']} min by {p['transport_mode'].replace('_', ' ')}.",
        p["starting_point"] or "Your starting point", b["travel_minutes"], "travel",
        b["travel_is_estimate"])
    add(t, f"Arrive at {terminal}", "Head to the departures level.", terminal, 5, "arrival")
    t += timedelta(minutes=5)

    if b["needs_check_in_desk"]:
        desk = f"desk {dep['check_in_desk']}" if dep["check_in_desk"] else "your airline's check-in area"
        what = "Drop your bags" if p["checked_bags"] else "Check in"
        add(t, f"{what} at {desk}",
            f"Check-in usually closes at {_hm(b['check_in_closes'])}.", desk, 20,
            "bag_drop" if p["checked_bags"] else "check_in")
        t += timedelta(minutes=20)

    lane = "the fast-track lane" if p["fast_track"] or p["cabin"] in ("business", "first") else "security"
    add(t, f"Go through {lane}", f"Typical wait around {security} min at this time of day.",
        terminal, security, "security")
    t += timedelta(minutes=security)

    if f["passport_control"]:
        add(t, "Passport control", "Have your passport and boarding pass ready.", terminal, 15,
            "passport_control")
        t += timedelta(minutes=15)

    walk_start = b["be_at_gate_by"] - timedelta(minutes=15)
    free = int((walk_start - t).total_seconds() // 60)
    if free >= 15:
        prefs = p["preferences"]
        if "lounge" in prefs or p["cabin"] in ("business", "first") or p["loyalty_tier"] in ("gold", "platinum"):
            title, cat = "Relax in the lounge", "lounge"
        elif "dining" in prefs or free >= 45:
            title, cat = "Time for a meal", "dining"
        else:
            title, cat = "Free time", "free_time"
        add(t, title, f"About {free} min before you should head to the gate.", "Airside", free, cat)

    gate_tip = "" if dep["gate"] else "Find your gate number on the departure screens. "
    add(walk_start, f"Walk to {gate}", gate_tip + "Allow extra time at large airports; gates can be far.",
        gate, 15, "walk_to_gate")
    add(b["boarding_starts"], "Boarding starts", "Typical start time; your airline may differ.",
        gate, 25, "boarding")
    add(b["gate_closes"], "Gate closes", "You will not be able to board after this time.",
        gate, 0, "gate_closes")
    add(b["departure"], f"Flight {f['number']} departs", "", gate, 0, "departure", False)

    warnings = []
    if facts["delay_prediction"] and facts["delay_prediction"]["risk_tier"] in ("HIGH", "CRITICAL"):
        warnings.append("This flight has a higher than usual delay risk. Check its status before you leave.")
    for role, wx in facts["weather"].items():
        if wx["delay_impact"] in ("moderate", "high"):
            warnings.append(f"Poor weather at the {role} airport may cause delays.")

    return {
        "summary": (f"Leave by {_hm(b['leave_by'])} and reach the airport by "
                    f"{_hm(b['arrive_airport_by'])} for your {_hm(b['departure'])} departure."),
        "leave_by": _hm(b["leave_by"]),
        "arrive_airport_by": _hm(b["arrive_airport_by"]),
        "timeline": steps,
        "tips": ["Check in online to save time at the airport."] if not p["online_check_in"] else [],
        "warnings": warnings,
    }


# ─────────────────────────────────────────────
#  Claude-personalized plan
# ─────────────────────────────────────────────
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "leave_by": {"type": "string", "description": "HH:MM local time"},
        "arrive_airport_by": {"type": "string", "description": "HH:MM local time"},
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "time": {"type": "string", "description": "HH:MM local time"},
                    "title": {"type": "string"},
                    "details": {"type": "string"},
                    "location": {"type": "string"},
                    "duration_minutes": {"type": "integer"},
                    "category": {"type": "string", "enum": STEP_CATEGORIES},
                    "is_estimate": {"type": "boolean"},
                },
                "required": ["time", "title", "details", "location",
                             "duration_minutes", "category", "is_estimate"],
                "additionalProperties": False,
            },
        },
        "tips": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "leave_by", "arrive_airport_by", "timeline", "tips", "warnings"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are AeroPredict's airport concierge. You plan a passenger's journey \
from their starting point to the departure gate as a clear, minute-by-minute timeline.

Rules:
- Use only the facts provided. Never invent a terminal, gate, check-in desk, flight time or \
weather condition. If the gate or terminal is unknown, tell the passenger to check the \
departure screens.
- Do not name specific restaurants, shops or lounges; describe the type of place instead.
- Start from the baseline schedule. You may adjust it for the passenger's preferences, \
weather, delay risk and transport mode, but never schedule arrival at the airport later \
than the baseline, and never place any step after the gate closing time except the departure.
- If travel_minutes is null, estimate travel time from the starting point and transport mode, \
and mark that step is_estimate=true.
- Mark every time that depends on typical procedures or estimates as is_estimate=true.
- All times are HH:MM in the departure airport's local time, in chronological order.
- Keep each detail to one or two short sentences. Tips must be practical and specific to this trip.
- Put delay or weather concerns in warnings. Leave warnings empty if there are none."""


def _claude_plan(facts: dict, b: dict, req: dict) -> dict:
    baseline = {k: (_hm(v) if isinstance(v, datetime) else v) for k, v in b.items()}
    language = LANGUAGES.get(req.get("language", "en"), "English")
    prompt = (
        "Plan this passenger's airport journey.\n\n"
        f"<facts>\n{json.dumps(facts, indent=2, default=str)}\n</facts>\n\n"
        f"<baseline_schedule>\n{json.dumps(baseline, indent=2)}\n</baseline_schedule>\n\n"
        f"Write all text fields in {language}. Keep times as HH:MM."
    )
    plan = claude_client.generate_json(SYSTEM_PROMPT, prompt, PLAN_SCHEMA)
    _check_times(plan)
    return plan


def _check_times(plan: dict) -> None:
    times = [plan["leave_by"], plan["arrive_airport_by"]] + [s["time"] for s in plan["timeline"]]
    for value in times:
        try:
            datetime.strptime(value, "%H:%M")
        except (TypeError, ValueError):
            raise claude_client.ClaudeUnavailable(f"Claude returned an invalid time: {value!r}")
    if not plan["timeline"]:
        raise claude_client.ClaudeUnavailable("Claude returned an empty timeline")
