import logging
import os
import re
from datetime import datetime

from flask import Blueprint, jsonify, request

from ..services import claude_client, flight_data, journey_planner as jp, wayfinding
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


@concierge_bp.route("/concierge/status", methods=["GET"])
def concierge_status():
    """Which data sources are switched on (no secrets are returned)."""
    return jsonify({
        "claude_ai": claude_client.is_configured(),
        "live_flight_data": flight_data.is_configured(),
        "weather": True,
        "journey_plan_limit_per_hour": JOURNEY_PLAN_LIMIT,
    })
