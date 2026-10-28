"""
AeroPredict ML Engine
=====================
Turns the delay model (services/delay_model.py, trained on a year of real
US flight records from the Bureau of Transportation Statistics) into the
prediction shown to passengers: on-time / delayed / cancelled
probabilities, a risk tier, typical delay length and the history behind it.

US records cover flights to, from and within the United States. For other
routes the model has no flight history and gives a general estimate, which
the response says plainly.
"""
import json
from datetime import datetime, timezone

from . import airports, delay_model, live_status

META_PATH = delay_model.META_FILE

MIN_HISTORY = 20  # flights needed before we quote a track record

US_CARRIER_NAMES = {
    "AA": "American", "AS": "Alaska", "B6": "JetBlue", "DL": "Delta", "F9": "Frontier",
    "G4": "Allegiant", "HA": "Hawaiian", "NK": "Spirit", "UA": "United", "WN": "Southwest",
    "MQ": "Envoy", "OH": "PSA", "OO": "SkyWest", "YX": "Republic", "9E": "Endeavor",
    "QX": "Horizon", "ZW": "Air Wisconsin", "C5": "CommuteAir", "G7": "GoJet", "PT": "Piedmont",
    "EV": "ExpressJet", "YV": "Mesa", "MX": "Breeze", "XP": "Avelo", "SY": "Sun Country",
}


def train_model(force: bool = False) -> dict:
    """The model is trained offline with scripts/train_delay_model.py; this returns its report."""
    return json.loads(META_PATH.read_text()) if META_PATH.exists() else {}


def _rate(entry, prior: float, idx: int) -> float:
    n = entry[0]
    return (entry[idx] + delay_model.SMOOTHING * prior) / (n + delay_model.SMOOTHING)


def predict(flight_number: str, dep_airport: str, arr_airport: str,
            airline: str, date_str: str, dep_time: str) -> dict:
    dt = datetime.strptime(f"{date_str} {dep_time}", "%Y-%m-%d %H:%M")
    dep_ap, arr_ap = dep_airport.upper().strip(), arr_airport.upper().strip()
    number = flight_number.upper().replace(" ", "")
    carrier = (airline or number[:2]).upper().strip()[:2]
    flight_no = number[2:] if number[:2] == carrier else number.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    flight_no = str(int(flight_no)) if flight_no.isdigit() else flight_no

    r = delay_model.predict(carrier, flight_no, dep_ap, arr_ap, dt)
    live_proba, live_notes, live_delay = live_status.apply(r["proba"], dep_ap, arr_ap, _to_utc(dt, dep_ap))
    p_on, p_delay, p_cancel = live_proba
    hist, prior = r["history"], r["prior"]
    disrupted = p_delay + p_cancel

    if p_cancel >= 0.10 or disrupted >= 0.45:
        risk_tier, risk_color = "CRITICAL", "#FF4D4D"
    elif disrupted >= 0.32:
        risk_tier, risk_color = "HIGH", "#FF8C00"
    elif disrupted >= 0.22:
        risk_tier, risk_color = "MODERATE", "#FFD700"
    else:
        risk_tier, risk_color = "LOW", "#00C851"

    rh = r.get("route_history")
    expected = rh["typical_delay"] if rh and rh.get("typical_delay") else _expected_delay(hist, prior)
    if live_delay:
        expected = max(expected, live_delay)
    severity = "Minor" if expected < 30 else "Moderate" if expected < 60 else "Severe"

    return {
        "flight_number": number,
        "dep_airport": dep_ap,
        "arr_airport": arr_ap,
        "airline": carrier,
        "departure": dt.strftime("%Y-%m-%d %H:%M"),
        "probabilities": {
            "on_time": round(p_on * 100, 1),
            "delayed": round(p_delay * 100, 1),
            "cancelled": round(p_cancel * 100, 1),
        },
        "risk_tier": risk_tier,
        "risk_color": risk_color,
        "expected_delay_minutes": expected,
        "delay_severity": severity,
        "data_basis": _with_live(_data_basis(hist, r["meta"], r.get("weather_used", False),
                                             r.get("method"), r.get("airport_shares", []),
                                             rh, carrier, number, dep_ap, arr_ap, r.get("airline_record")),
                                 live_notes),
        "factors": _combine_factors(
            [{"icon": "🚦", "label": "FAA live status", "value": n, "level": "high"} for n in live_notes]
            + _weather_factors(r.get("weather"), dep_ap, arr_ap),
            _route_history_factors(rh or r.get("airline_record"), carrier, number, dep_ap, arr_ap)
            + _airport_factors(r.get("airport_shares", [])),
            _explain_factors(hist, prior, carrier, number, dep_ap, arr_ap, dt)),
    }


def _record_source(dep: str, arr: str) -> str:
    """Whose flight records the model's history for this route comes from."""
    countries = {airports.lookup(dep)["country"], airports.lookup(arr)["country"]}
    if "BR" in countries:
        return "Brazil's official flight records, ANAC"
    if "KR" in countries:
        return "Korea's official flight records, Airportal"
    return "US flight records, BTS"


def _to_utc(dep_local: datetime, airport: str):
    """Departure time in UTC, from the airport's time zone; None if the zone is unknown."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    tz = airports.lookup(airport).get("tz")
    if not tz:
        return None
    try:
        return dep_local.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
    except ZoneInfoNotFoundError:
        return None


def _with_live(basis: dict, notes: list) -> dict:
    if notes:
        basis["note"] += " Includes live FAA airport status: " + "; ".join(notes) + "."
        basis["live_status"] = notes
    return basis


def _expected_delay(hist: dict, prior: dict) -> int:
    """Typical arrival delay when this flight (or route) is late, in minutes."""
    for name in ("flight", "route_carrier", "route", "origin_hour", "origin"):
        e = hist.get(name)
        if e and e[1] >= 10 and e[3] is not None:
            return int(e[3])
    return int(prior.get("median_delay", 45))


def _weather_factors(wx: dict | None, dep: str, arr: str) -> list[dict]:
    """Forecast conditions worth telling the passenger about (flights in the next ~day)."""
    if not wx:
        return []
    out = []
    for side, airport in (("o", dep), ("d", arr)):
        def v(k):
            x = wx.get(f"{side}_{k}")
            return None if x is None or x != x else x  # NaN check
        problems = []
        if v("ts"):
            problems.append(("thunderstorms", "high"))
        if v("snow") or v("freezing"):
            problems.append(("snow or freezing precipitation", "high"))
        if v("vis") is not None and v("vis") < 3:
            problems.append((f"low visibility ({v('vis'):g} mi)", "high" if v("vis") < 1 else "medium"))
        if v("ceiling") is not None and v("ceiling") < 1000:
            problems.append((f"low cloud ({int(v('ceiling'))} ft)", "medium"))
        if v("gust") is not None and v("gust") >= 30:
            problems.append((f"strong gusts ({int(v('gust'))} kt)", "medium"))
        for text, level in problems:
            out.append({"icon": "⛈", "label": f"Forecast at {airport}", "value": text, "level": level})
        if not problems and v("vis") is not None:
            out.append({"icon": "🌤", "label": f"Forecast at {airport}", "value": "No significant weather", "level": "low"})
    return out


def _combine_factors(weather: list, airport: list, history: list) -> list[dict]:
    if airport:  # airport punctuality replaces the "no history" placeholder
        history = [f for f in history if f["label"] != "No flight history for this route"]
    return weather + airport + history


def _route_history_label(rh: dict, carrier: str, number: str, dep: str, arr: str) -> str:
    return {"flight": f"{number} track record",
            "route_airline": f"{carrier} on {dep} → {arr}",
            "route": f"All airlines on {dep} → {arr}",
            "airline_country": f"{carrier} domestic flights ({rh.get('country')})",
            "country": f"All domestic flights ({rh.get('country')})",
            "airline": f"{carrier} on all reported routes"}[rh["level"]]


def _route_history_factors(rh: dict | None, carrier, number, dep, arr) -> list[dict]:
    if not rh:
        return []
    on_time = 100 - rh["disrupted_share"] * 100
    late = rh["disrupted_share"]
    level = "high" if late > 0.30 else "low" if late < 0.18 else "medium"
    return [{"icon": "✈", "label": _route_history_label(rh, carrier, number, dep, arr),
             "value": f"{on_time:.0f}% on time ({rh['flights']:,} flights)", "level": level}]


def _airport_factors(shares: list[dict]) -> list[dict]:
    """Official airport punctuality used for routes without their own history."""
    out = []
    for a in shares:
        what = "departures" if a["side"] == "origin" else "arrivals"
        if a["source"] == "flight records":
            late = 100 - a["on_time_pct"]
            out.append({"icon": "🛫", "label": f"{a['airport']} {what} (flight records)",
                        "value": f"{a['on_time_pct']:.0f}% on time ({a['flights']:,} flights)",
                        "level": "high" if late > 30 else "low" if late < 18 else "medium"})
            continue
        if a["source"] not in ("EUROCONTROL", "ACSA"):
            continue
        where = {"airport": a["airport"], "airport_airline": f"{a.get('airline')} at {a['airport']}"}.get(
            a.get("level"), f"{a['airport']} (country average)")
        if a["source"] == "ACSA":  # one figure for departures and arrivals
            label, value = f"{where} (ACSA)", f"{a['on_time_pct']:.0f}% on time this year"
            if a.get("month_pct") is not None:
                value += f", {a['month_pct']:.0f}% in {a.get('month')}"
        else:
            label, value = f"{where} {what} (EUROCONTROL)", f"{a['on_time_pct']:.0f}% on time this year"
            if a.get("on_time_pct_7d") is not None:
                value += f", {a['on_time_pct_7d']:.0f}% last 7 days"
        late = 100 - a["on_time_pct"]
        level = "high" if late > 30 else "low" if late < 18 else "medium"
        out.append({"icon": "🇪🇺" if a["source"] == "EUROCONTROL" else "🛫", "label": label, "value": value,
                    "level": level})
    return out


def _data_basis(hist: dict, meta: dict, weather_used: bool = False,
                method: str | None = None, shares: list | None = None, rh: dict | None = None,
                carrier: str = "", number: str = "", dep: str = "", arr: str = "",
                airline_rec: dict | None = None) -> dict:
    months = meta.get("data_months") or []
    period = f"{months[0]} to {months[-1]}" if months else "the past year"
    flight = hist.get("flight")
    route = hist.get("route")
    if method == "route_history" and rh:
        what = {"flight": f"this flight's {rh['flights']:,} departures",
                "route_airline": f"{rh['flights']:,} {carrier} flights from {dep} to {arr}",
                "route": f"{rh['flights']:,} flights by all airlines from {dep} to {arr}",
                "airline_country": f"{rh['flights']:,} {carrier} domestic flights across the country",
                "country": f"{rh['flights']:,} domestic flights by all airlines in the country",
                "airline": f"{rh['flights']:,} {carrier} flights on the routes we have data for"}[rh["level"]]
        src = rh["source"]
        level = "high" if rh["level"] == "flight" else "medium"
        note = (f"Based on {what} ({src.get('name')}, {src.get('period')}), adjusted for time of day, "
                f"season and holidays.")
        if rh["level"] == "route":
            note += " Your airline's own record on this route isn't available."
        elif rh["level"] == "airline_country":
            level = "airport"
            note += " This route isn't reported on its own, so this uses the airline's national record."
        elif rh["level"] == "country":
            level = "low"
            note += " Neither this route nor this airline is reported on its own, so this is the national average."
        elif rh["level"] == "airline":
            level = "low"
            note += (" There's no data for this route or these airports, so this is the airline's "
                     "overall punctuality, which can differ from route to route.")
    elif method == "airport_punctuality":
        used = ", ".join(f"{a['airport']} ({a['on_time_pct']:.0f}% on time)" for a in shares or [])
        level, note = "airport", (f"No history for this exact flight or route. Based on official airport "
                                  f"punctuality: {used}, adjusted for time of day, season and holidays. "
                                  + ("Airport figures marked as flight records come from flights to and from "
                                     "that airport in other countries' official data. "
                                     if any(a["source"] == "flight records" for a in shares or []) else "")
                                  + (f"Adjusted for {carrier}'s own punctuality ({airline_rec['flights']:,} flights, "
                                     f"{airline_rec['source']['name']})." if airline_rec else
                                     "Airline-specific performance isn't included."))
    elif flight and flight[0] >= MIN_HISTORY:
        level, note = "high", (f"Based on this flight's {flight[0]} departures ({_record_source(dep, arr)}, {period}), "
                               f"plus route, airline and airport history.")
    elif route and route[0] >= MIN_HISTORY:
        level, note = "medium", (f"Based on {route[0]:,} flights on this route ({_record_source(dep, arr)}, {period}). "
                                 f"This flight number has little history.")
    elif hist.get("carrier") or hist.get("origin") or hist.get("dest"):
        level, note = "low", "Little history for this route. Based mainly on airline and airport records."
    else:
        level, note = "general", ("We don't have punctuality data for this flight, its route or these "
                                  "airports yet (we cover flights in the US, UK, Brazil, Australia, South Korea, "
                                  "Hong Kong, Norway and Israel, Japanese and South African airlines, and "
                                  "about 1,500 airports worldwide). "
                                  "This is only a general estimate from time of day and season, so the "
                                  "numbers will look similar for many flights. Treat it as a rough guide.")
    if weather_used:
        note += " Includes the current airport weather forecast."
    return {"confidence": level, "note": note, "weather_forecast_used": weather_used,
            "source": meta.get("data_source", "US Bureau of Transportation Statistics"),
            "flights_in_model": meta.get("n_training")}


def _pct_on_time(e) -> float:
    return (e[0] - e[1] - e[2]) / e[0] * 100


def _level(e, prior: dict) -> str:
    rate = (e[1] + e[2]) / e[0]
    base = prior["delay"] + prior["cancel"]
    return "high" if rate > base * 1.25 else "low" if rate < base * 0.8 else "medium"


def _explain_factors(hist, prior, carrier, number, dep, arr, dt) -> list[dict]:
    factors = []
    f = hist.get("flight")
    if f and f[0] >= MIN_HISTORY:
        factors.append({"icon": "✈", "label": f"{number} track record",
                        "value": f"{_pct_on_time(f):.0f}% on time ({f[0]} flights)", "level": _level(f, prior)})
    rt = hist.get("route")
    if rt and rt[0] >= MIN_HISTORY:
        factors.append({"icon": "🛫", "label": f"{dep} → {arr} route",
                        "value": f"{_pct_on_time(rt):.0f}% on time", "level": _level(rt, prior)})
    c = hist.get("carrier")
    if c and c[0] >= MIN_HISTORY:
        name = US_CARRIER_NAMES.get(carrier, carrier)
        factors.append({"icon": "📊", "label": f"{name} overall",
                        "value": f"{_pct_on_time(c):.0f}% on time", "level": _level(c, prior)})
    oh = hist.get("origin_hour")
    if oh and oh[0] >= MIN_HISTORY:
        factors.append({"icon": "⏰", "label": f"{dep} departures around {dt.hour:02d}:00",
                        "value": f"{_pct_on_time(oh):.0f}% on time", "level": _level(oh, prior)})
    if not factors:
        factors.append({"icon": "ℹ", "label": "No flight history for this route",
                        "value": "General estimate", "level": "medium"})
    if dt.hour >= 17:
        factors.append({"icon": "🌆", "label": "Evening departure",
                        "value": "Delays build up during the day", "level": "medium"})
    elif dt.hour < 9:
        factors.append({"icon": "🌅", "label": "Early departure",
                        "value": "Usually the most punctual time", "level": "low"})
    return factors
