"""
AeroPredict — Delay and cancellation rights
Works out which passenger-rights rules cover a disrupted flight, what cash
compensation may be owed, what care the airline must provide, and what to
ask for at the desk. Amounts follow the published regulations; whether a
claim succeeds still depends on the cause and the airline's evidence.
"""
import math

from . import airports

EU_EEA_CH = {"AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR",
             "HR", "HU", "IE", "IS", "IT", "LI", "LT", "LU", "LV", "MT", "NL", "NO", "PL",
             "PT", "RO", "SE", "SI", "SK"}

# Airline (IATA code) -> country of licence, for rules that depend on the carrier
AIRLINE_COUNTRY = {
    # EU / EEA / Switzerland
    "LH": "DE", "EW": "DE", "DE": "DE", "AF": "FR", "KL": "NL", "IB": "ES", "VY": "ES",
    "UX": "ES", "FR": "IE", "EI": "IE", "SK": "SE", "AY": "FI", "OS": "AT", "LX": "CH",
    "TP": "PT", "AZ": "IT", "LO": "PL", "A3": "GR", "W6": "HU", "SN": "BE", "DY": "NO",
    "BT": "LV", "OU": "HR", "RO": "RO", "EC": "AT",
    # United Kingdom
    "BA": "GB", "VS": "GB", "U2": "GB", "LS": "GB",
    # Türkiye
    "TK": "TR", "PC": "TR", "XQ": "TR", "VF": "TR",
    # North America
    "AA": "US", "UA": "US", "DL": "US", "WN": "US", "B6": "US", "AS": "US", "NK": "US",
    "F9": "US", "AC": "CA", "WS": "CA", "PD": "CA", "TS": "CA",
    # Others (no passenger-rights law of their own applied here)
    "EK": "AE", "EY": "AE", "FZ": "AE", "QR": "QA", "SV": "SA", "GF": "BH", "WY": "OM",
    "MS": "EG", "SQ": "SG", "CX": "HK", "NH": "JP", "JL": "JP", "KE": "KR", "OZ": "KR",
    "QF": "AU", "AI": "IN", "6E": "IN", "TG": "TH", "MH": "MY", "LA": "CL", "ET": "ET",
}

DISRUPTIONS = ["delay", "cancellation", "denied_boarding"]
CAUSES = ["airline", "weather", "strike_external", "air_traffic_control", "unknown"]
EXTRAORDINARY = {"weather", "strike_external", "air_traffic_control"}


def _center(iata: str):
    b = airports.BOUNDS.get(iata)
    if not b:
        return None
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def distance_km(a: str, b: str) -> int | None:
    pa, pb = _center(a), _center(b)
    if not pa or not pb:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (*pa, *pb))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return int(round(2 * 6371 * math.asin(math.sqrt(h))))


def _band(distance: int | None, intra_eu: bool) -> str:
    if distance is None:
        return "unknown"
    if distance <= 1500:
        return "short"
    if intra_eu or distance <= 3500:
        return "medium"
    return "long"


EU_AMOUNTS = {"short": 250, "medium": 400, "long": 600}
UK_AMOUNTS = {"short": 220, "medium": 350, "long": 520}
EU_DELAY_FOR_CARE_H = {"short": 2, "medium": 3, "long": 4}


def assess(origin: str, destination: str, airline: str, disruption: str,
           delay_hours: float, cause: str, notice_days: int | None) -> dict:
    o_country = airports.lookup(origin)["country"]
    d_country = airports.lookup(destination)["country"]
    carrier = AIRLINE_COUNTRY.get(airline)
    distance = distance_km(origin, destination)
    intra_eu = o_country in EU_EEA_CH and d_country in EU_EEA_CH
    band = _band(distance, intra_eu)
    extraordinary = cause in EXTRAORDINARY

    regimes = []

    # EU261: departing the EU/EEA/CH, or arriving there on an EU/EEA/CH airline
    if o_country in EU_EEA_CH or (d_country in EU_EEA_CH and carrier in EU_EEA_CH):
        regimes.append(_eu_style("EU261", "EU Regulation 261/2004", "€", EU_AMOUNTS,
                                 band, disruption, delay_hours, extraordinary, notice_days))
    # UK261: departing the UK, or arriving in the UK on a UK or EU airline
    if o_country == "GB" or (d_country == "GB" and (carrier == "GB" or carrier in EU_EEA_CH)):
        regimes.append(_eu_style("UK261", "UK Regulation 261 (UK261)", "£", UK_AMOUNTS,
                                 band, disruption, delay_hours, extraordinary, notice_days))
    # Türkiye SHY-YOLCU: departing Türkiye, or arriving there on a Turkish airline
    if o_country == "TR" or (d_country == "TR" and carrier == "TR"):
        regimes.append(_turkey(o_country == d_country == "TR", band, disruption,
                               delay_hours, extraordinary, notice_days))
    # US DOT refund rule: flights to, from or within the US
    if "US" in (o_country, d_country):
        regimes.append(_us(o_country == d_country == "US", disruption, delay_hours))
    # Canada APPR: flights to, from or within Canada
    if "CA" in (o_country, d_country):
        regimes.append(_canada(disruption, delay_hours, cause))
    if not regimes:
        regimes.append(_montreal_only())

    best = max(regimes, key=lambda r: r["compensation"]["amount"] or 0)
    return {
        "route": {"origin": origin, "destination": destination, "distance_km": distance},
        "airline": airline,
        "disruption": disruption,
        "regimes": regimes,
        "headline": best["compensation"],
        "ask_at_the_desk": _desk_script(disruption, delay_hours, regimes),
        "rebooking_options": _rebooking_options(disruption, delay_hours),
        "keep_evidence": [
            "Your boarding pass and booking confirmation.",
            "Photos of the departure board showing the delay or cancellation.",
            "The reason the airline gives you, in writing if possible (ask for it by email).",
            "Receipts for meals, transport and hotels you pay for yourself.",
            "The actual time you arrived at your final destination (when the doors opened).",
        ],
        "note": ("This is general guidance, not legal advice. Airlines can refuse compensation "
                 "for extraordinary circumstances such as severe weather, air traffic control "
                 "restrictions or security risks, but must still provide care."),
    }


def _comp(amount, currency, text, eligible):
    return {"amount": amount if eligible else 0, "currency": currency,
            "eligible": eligible, "explanation": text}


def _eu_style(code, name, cur, amounts, band, disruption, delay_h, extraordinary, notice):
    if band == "unknown":
        amount_text = f"{cur}{amounts['short']}–{cur}{amounts['long']} depending on distance"
        amount = amounts["short"]
    else:
        amount = amounts[band]
        amount_text = f"{cur}{amount}"

    if disruption == "delay":
        eligible = delay_h >= 3 and not extraordinary
        why = (f"{amount_text} if you reach your final destination 3 or more hours late"
               + ("" if not extraordinary else ", unless caused by extraordinary circumstances (as here)")
               + ".")
    elif disruption == "cancellation":
        late_notice = notice is None or notice < 14
        eligible = late_notice and not extraordinary
        why = (f"{amount_text} if you were told less than 14 days before departure"
               + (", unless caused by extraordinary circumstances (as here)" if extraordinary else "")
               + ". It can be halved if the replacement flight gets you there close to the original time.")
    else:
        eligible = True
        why = f"{amount_text} if you were denied boarding against your will on a confirmed booking."

    care_after = EU_DELAY_FOR_CARE_H.get(band, 2)
    care = [f"Meals and refreshments once you have waited {care_after}+ hours.",
            "A hotel and transport to it if you have to stay overnight.",
            "Two phone calls or emails (usually given as access to communication)."]
    choices = ["Rebooking on the next available flight to your destination (including other airlines when needed).",
               "Rebooking at a later date of your choice."]
    if disruption != "delay" or delay_h >= 5:
        choices.append("A full refund instead, plus a flight back to your origin if you are mid-journey.")
    return {"code": code, "name": name,
            "compensation": _comp(amount, cur, why, eligible),
            "care": care, "choices": choices,
            "claim": "Claim from the operating airline in writing. Deadlines depend on the country (often 2–6 years)."}


def _turkey(domestic, band, disruption, delay_h, extraordinary, notice):
    if domestic:
        amount, text_amount = 100, "€100 (domestic flight)"
    elif band == "unknown":
        amount, text_amount = 250, "€250–€600 depending on distance"
    else:
        amount = EU_AMOUNTS[band]
        text_amount = f"€{amount} (paid in Turkish lira at the day's rate)"

    if disruption == "cancellation":
        eligible = (notice is None or notice < 14) and not extraordinary
        why = f"{text_amount} if the flight was cancelled less than 14 days before departure"
        why += ", unless caused by extraordinary circumstances (as here)." if extraordinary else "."
    elif disruption == "denied_boarding":
        eligible, why = True, f"{text_amount} if you were denied boarding against your will."
    else:
        eligible = False
        why = ("SHY-YOLCU gives care and refund rights for long delays rather than cash "
               "compensation. If the delay leads to a cancellation, compensation can apply.")
    care = ["Meals and refreshments during long waits.",
            "A hotel and transport to it if you have to stay overnight.",
            "Free communication (phone calls or email)."]
    choices = ["Rebooking on the next suitable flight.", "Rebooking at a later date of your choice."]
    if disruption != "delay" or delay_h >= 5:
        choices.append("A full refund instead.")
    return {"code": "SHY-YOLCU", "name": "Türkiye Passenger Rights Regulation (SHY-YOLCU)",
            "compensation": _comp(amount, "€", why, eligible), "care": care, "choices": choices,
            "claim": "Claim from the airline first. If it doesn't answer, complain to the Turkish DGCA (SHGM)."}


def _us(domestic, disruption, delay_h):
    threshold = 3 if domestic else 6
    refund = disruption == "cancellation" or delay_h >= threshold
    choices = ["Rebooking on the airline's next available flight at no extra cost."]
    if refund:
        choices.append("An automatic cash refund (not a voucher) if you decline the new flight. "
                       "Airlines must pay it within 7 business days for card payments.")
    else:
        choices.append(f"A cash refund once the delay reaches {threshold} hours "
                       f"({'domestic' if domestic else 'international'} flights) and you choose not to travel.")
    return {"code": "US DOT", "name": "US Department of Transportation refund rules",
            "compensation": _comp(0, "$",
                                  "US law doesn't require cash compensation for delays or cancellations. "
                                  "Many airlines still give meal vouchers or hotels when the cause is within "
                                  "their control: check the airline's customer service plan.", False),
            "care": ["Depends on the airline's customer service plan (see the DOT dashboard at flightrights.gov)."],
            "choices": choices,
            "claim": "Ask the airline for the refund. If it refuses, file a complaint with the US DOT."}


def _canada(disruption, delay_h, cause):
    controlled = cause == "airline"
    if delay_h >= 9:
        amount = 1000
    elif delay_h >= 6:
        amount = 700
    elif delay_h >= 3:
        amount = 400
    else:
        amount = 0
    eligible = controlled and amount > 0
    why = ("Large airlines owe CA$400 (3–6 h late), CA$700 (6–9 h) or CA$1,000 (9+ h) when the "
           "delay or cancellation is within their control and not safety-related.")
    return {"code": "APPR", "name": "Canada Air Passenger Protection Regulations",
            "compensation": _comp(amount, "CA$", why, eligible),
            "care": ["Food, drink and communication after a 2-hour wait.",
                     "A hotel and transport if you have to stay overnight."],
            "choices": ["Rebooking on the next available flight, including on another airline after a long wait.",
                        "A refund if the new flight doesn't meet your travel needs."],
            "claim": "Claim from the airline within one year. If it doesn't resolve it, complain to the Canadian Transportation Agency."}


def _montreal_only():
    return {"code": "Montreal", "name": "Montreal Convention",
            "compensation": _comp(0, "", "No fixed compensation applies to this route, but the airline "
                                  "must pay proven costs caused by a delay unless it took all reasonable "
                                  "measures to avoid it.", False),
            "care": ["Depends on the airline's own policy. Ask for meal and hotel vouchers."],
            "choices": ["Rebooking on the next available flight.", "A refund if you no longer want to travel."],
            "claim": "Send the airline a written claim with receipts for your extra costs."}


def _desk_script(disruption, delay_h, regimes) -> list[str]:
    codes = ", ".join(r["code"] for r in regimes if r["code"] != "Montreal")
    lines = ["Please rebook me on the next available flight to my final destination, "
             "including on a partner airline if that's sooner."]
    if disruption != "delay" or delay_h >= 2:
        lines.append("Can I have meal vouchers while I wait?")
    if disruption == "cancellation" or delay_h >= 6:
        lines.append("If I have to stay overnight, please book me a hotel and transport.")
    lines.append("Can you tell me the reason for the disruption and send it to me by email?")
    if codes:
        lines.append(f"I'd like to note that I'm covered by {codes}.")
    return lines


def _rebooking_options(disruption, delay_h) -> list[dict]:
    options = [
        {"option": "Next flight on the same airline",
         "best_for": "Simplest. Your bags and onward connections are usually moved for you.",
         "tip": "Ask at the desk and call the airline at the same time. Whoever answers first wins."},
        {"option": "Partner airline (same alliance)",
         "best_for": "When your airline's next flight is many hours away.",
         "tip": "Name a specific partner flight you found. Agents act faster when you do."},
        {"option": "Nearby alternative airport",
         "best_for": "Big cities with several airports (London, Istanbul, New York, Paris).",
         "tip": "Ask the airline to cover ground transport to the other airport."},
    ]
    if disruption == "cancellation" or delay_h >= 5:
        options.append({"option": "Refund and book yourself",
                        "best_for": "When your trip no longer makes sense or other airlines are much sooner.",
                        "tip": "Take the refund in cash or to your card, not as a voucher, unless the voucher is worth more."})
    return options
