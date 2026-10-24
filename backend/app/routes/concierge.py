import logging
import os
import re
from datetime import datetime

from flask import Blueprint, jsonify, request

from ..services import baggage, claude_client, flight_data, journey_planner as jp, passenger_rights, wayfinding
from ..utils.rate_limit import rate_limit

log = logging.getLogger(__name__)

concierge_bp = Blueprint("concierge", __name__)

JOURNEY_PLAN_LIMIT = int(os.environ.get("JOURNEY_PLAN_LIMIT_PER_HOUR", "10"))

_FLIGHT_NO = re.compile(r"^[A-Z0-9]{2}\d{1,4}[A-Z]?$")
_IATA = re.compile(r"^[A-Z]{3}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class _Invalid(ValueError):
    pass


def _parse_journey_request(data: dict) -> dict:
    if not isinstance(data, dict):
        raise _Invalid("Request body must be a JSON object.")

    req = {}
    number = flight_data.normalize_flight_number(str(data.get("flight_number", "")))
    if not _FLIGHT_NO.match(number):
        raise _Invalid("Please enter a valid flight number, e.g. TK1 or BA2041.")
    req["flight_number"] = number

    date = str(data.get("date", ""))
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise _Invalid("Please enter the travel date as YYYY-MM-DD.")
    req["date"] = date

    for field in ("dep_airport", "arr_airport"):
        value = str(data.get(field) or "").strip().upper()
        if value:
            if not _IATA.match(value):
                raise _Invalid(f"{field} must be a 3-letter IATA airport code.")
            req[field] = value

    dep_time = str(data.get("dep_time") or "").strip()
    if dep_time:
        if not _HHMM.match(dep_time):
            raise _Invalid("Departure time must be HH:MM (24-hour).")
        req["dep_time"] = dep_time

    airline = str(data.get("airline") or "").strip().upper()
    if airline:
        req["airline"] = airline[:3]

    req["starting_point"] = str(data.get("starting_point") or "").strip()[:120] or None
    req["notes"] = str(data.get("notes") or "").strip()[:300] or None

    def choice(field, options, default):
        value = data.get(field) or default
        if value not in options:
            raise _Invalid(f"{field} must be one of: {', '.join(options)}.")
        return value

    req["transport_mode"] = choice("transport_mode", jp.TRANSPORT_MODES, "car")
    req["cabin"] = choice("cabin", jp.CABINS, "economy")
    req["loyalty_tier"] = choice("loyalty_tier", jp.LOYALTY_TIERS, "none")
    req["language"] = choice("language", list(jp.LANGUAGES), "en")

    travel = data.get("travel_minutes")
    if travel not in (None, ""):
        try:
            travel = int(travel)
        except (TypeError, ValueError):
            raise _Invalid("travel_minutes must be a number.")
        if not 0 < travel <= 600:
            raise _Invalid("travel_minutes must be between 1 and 600.")
        req["travel_minutes"] = travel

    try:
        req["checked_bags"] = max(0, min(int(data.get("checked_bags") or 0), 10))
    except (TypeError, ValueError):
        raise _Invalid("checked_bags must be a number.")
    req["online_check_in"] = bool(data.get("online_check_in"))
    req["fast_track"] = bool(data.get("fast_track"))

    prefs = data.get("preferences") or []
    if not isinstance(prefs, list) or any(p not in jp.PREFERENCES for p in prefs):
        raise _Invalid(f"preferences may only contain: {', '.join(jp.PREFERENCES)}.")
    req["preferences"] = list(dict.fromkeys(prefs))
    return req


@concierge_bp.route("/concierge/journey-plan", methods=["POST"])
@rate_limit("journey-plan", max_calls=JOURNEY_PLAN_LIMIT, per_seconds=3600)
def journey_plan():
    try:
        req = _parse_journey_request(request.get_json(silent=True))
        result = jp.build_plan(req)
    except (_Invalid, jp.JourneyInputError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        log.exception("Journey plan failed")
        return jsonify({"error": "Could not build your journey plan. Please try again."}), 500
    return jsonify({"success": True, "data": result})


@concierge_bp.route("/concierge/wayfinding", methods=["GET"])
@rate_limit("wayfinding", max_calls=300, per_seconds=3600)
def wayfinding_view():
    airport = request.args.get("airport", "").strip().upper()
    if not _IATA.match(airport):
        return jsonify({"error": "Please enter a 3-letter airport code, e.g. IST."}), 400
    gate = request.args.get("gate", "").strip()[:8] or None
    requested = [c for c in request.args.get("categories", "").split(",") if c]
    categories = requested or ["coffee", "dining", "toilets", "atm", "prayer_room", "lounge"]
    unknown = [c for c in categories if c not in wayfinding.CATEGORIES]
    if unknown:
        return jsonify({"error": f"Unknown categories: {', '.join(unknown)}. "
                                 f"Choose from: {', '.join(wayfinding.CATEGORIES)}."}), 400
    try:
        result = wayfinding.find_nearby(airport, gate, categories)
    except wayfinding.MapLoading:
        resp = jsonify({
            "loading": True,
            "retry_after_seconds": 5,
            "message": f"Loading the {airport} airport map for the first time. This can take up to a minute.",
        })
        resp.status_code = 202
        resp.headers["Retry-After"] = "5"
        return resp
    except wayfinding.AirportNotFound:
        return jsonify({"error": f"We don't have map data for {airport} yet."}), 404
    except ConnectionError:
        return jsonify({"error": "The airport map service is busy. Please try again in a minute."}), 503
    except Exception:
        log.exception("Wayfinding failed")
        return jsonify({"error": "Could not load the airport map. Please try again."}), 500
    return jsonify({"success": True, "data": result})


@concierge_bp.route("/concierge/baggage-risk", methods=["POST"])
@rate_limit("baggage-risk", max_calls=60, per_seconds=3600)
def baggage_risk():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    route = [str(a).strip().upper() for a in data.get("route") or []]
    if not 2 <= len(route) <= 5 or not all(_IATA.match(a) for a in route):
        return jsonify({"error": "Enter 2 to 5 airport codes in travel order, e.g. IST, FRA, JFK."}), 400
    if any(a == b for a, b in zip(route, route[1:])):
        return jsonify({"error": "The same airport can't appear twice in a row."}), 400

    raw = data.get("connections") or []
    if not isinstance(raw, list) or len(raw) != len(route) - 2:
        return jsonify({"error": "Add one connection entry for each stop between your first and last airport."}), 400
    connections = []
    for c in raw:
        if not isinstance(c, dict) or c.get("duration") not in baggage.CONNECTION_FACTORS:
            return jsonify({"error": "Connection duration must be one of: "
                                     f"{', '.join(baggage.CONNECTION_FACTORS)}."}), 400
        connections.append({"duration": c["duration"],
                            "airline_change": bool(c.get("airline_change")),
                            "terminal_change": bool(c.get("terminal_change"))})

    try:
        bags = int(data.get("checked_bags", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "checked_bags must be a number."}), 400
    if not 1 <= bags <= 10:
        return jsonify({"error": "checked_bags must be between 1 and 10."}), 400

    date = data.get("date") or None
    if date:
        try:
            datetime.strptime(str(date), "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Please enter the travel date as YYYY-MM-DD."}), 400

    return jsonify({"success": True, "data": baggage.assess(route, connections, bags, date)})


@concierge_bp.route("/concierge/rights", methods=["POST"])
@rate_limit("rights", max_calls=60, per_seconds=3600)
def rights():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    origin = str(data.get("origin") or "").strip().upper()
    destination = str(data.get("destination") or "").strip().upper()
    if not _IATA.match(origin) or not _IATA.match(destination) or origin == destination:
        return jsonify({"error": "Enter your departure and final destination airport codes, e.g. IST and LHR."}), 400
    airline = str(data.get("airline") or "").strip().upper()[:2]
    disruption = data.get("disruption")
    if disruption not in passenger_rights.DISRUPTIONS:
        return jsonify({"error": f"disruption must be one of: {', '.join(passenger_rights.DISRUPTIONS)}."}), 400
    cause = data.get("cause") or "unknown"
    if cause not in passenger_rights.CAUSES:
        return jsonify({"error": f"cause must be one of: {', '.join(passenger_rights.CAUSES)}."}), 400
    try:
        delay_hours = float(data.get("delay_hours") or 0)
        notice = data.get("notice_days")
        notice_days = None if notice in (None, "") else int(notice)
    except (TypeError, ValueError):
        return jsonify({"error": "delay_hours and notice_days must be numbers."}), 400
    if not 0 <= delay_hours <= 72:
        return jsonify({"error": "delay_hours must be between 0 and 72."}), 400
    if notice_days is not None and not 0 <= notice_days <= 365:
        return jsonify({"error": "notice_days must be between 0 and 365."}), 400
    result = passenger_rights.assess(origin, destination, airline, disruption,
                                     delay_hours, cause, notice_days)
    result["claim_partner"] = passenger_rights.claim_partner(result, origin, destination, airline)
    return jsonify({"success": True, "data": result})


@concierge_bp.route("/concierge/status", methods=["GET"])
def concierge_status():
    """Which data sources are switched on (no secrets are returned)."""
    return jsonify({
        "claude_ai": claude_client.is_configured(),
        "live_flight_data": flight_data.is_configured(),
        "weather": True,
        "journey_plan_limit_per_hour": JOURNEY_PLAN_LIMIT,
        "claim_partner": bool(os.environ.get("CLAIM_PARTNER_URL")),
        "map_errors": dict(list(wayfinding.last_errors.items())[-10:]),
    })
