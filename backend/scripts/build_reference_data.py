"""
Build the reference data files the website loads at startup:

- data/airports_world.json: every airport with scheduled passenger service
  (IATA -> ICAO, name, country, latitude, longitude, time zone), from OurAirports
  (public domain): https://davidmegginson.github.io/ourairports-data/airports.csv
- data/acsa_punctuality.json: on-time performance (on/off-block within 15
  minutes) of each home airline at South Africa's 9 ACSA airports, year to date
  and latest month: https://www.airports.co.za/business/statistics/on-time-performance
- data/eurocontrol_punctuality.json: official departure and arrival
  punctuality (share within 15 minutes of schedule) for ~110 European
  airports and their countries, year to date and for the last 7 days, from
  EUROCONTROL: https://ansperformance.eu/punctuality/

    python scripts/build_reference_data.py

Needs openpyxl (EUROCONTROL spreadsheet) and timezonefinder (airport time
zones); only this script uses them.
Run weekly by .github/workflows/reference-data.yml.
"""
import csv
import html
import io
import json
import re
import sys
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
ACSA_URL = "https://www.airports.co.za/business/statistics/on-time-performance"
ACSA_AIRPORTS = {"TAMBO": "JNB", "CAPE TOWN": "CPT", "KING SHAKA": "DUR", "BRAM FISCHER": "BFN",
                 "EAST LONDON": "ELS", "GEORGE": "GRJ", "STUURMAN": "PLZ", "PORT ELIZABETH": "PLZ",
                 "UPINGTON": "UTN", "KIMBERLEY": "KIM"}
ACSA_AIRLINES = {"AIRLINK": "4Z", "CEMAIR": "5Z", "FLYSAFAIR": "FA", "GLOBAL AVIATION": "GE", "LIFT": "GE",
                 "SOUTH AFRICAN AIRWAYS": "SA", "SAFAIR": "FA", "MANGO": "JE"}
EUROCONTROL_URL = "https://www.eurocontrol.int/Economics/Download/Stakeholder_Punctuality.xlsx"
HEADERS = {"User-Agent": "AeroPredict reference data (github.com/a-elradi/AeroPredict-AI)"}

# EUROCONTROL country names -> ISO codes
COUNTRY_CODES = {
    "Albania": "AL", "Armenia": "AM", "Austria": "AT", "Belgium": "BE", "Bosnia And Herzegovina": "BA",
    "Bulgaria": "BG", "Croatia": "HR", "Cyprus": "CY", "Czech Republic": "CZ", "Czechia": "CZ",
    "Denmark": "DK", "Estonia": "EE", "Finland": "FI", "France": "FR", "Georgia": "GE", "Germany": "DE",
    "Greece": "GR", "Hungary": "HU", "Iceland": "IS", "Ireland": "IE", "Israel": "IL", "Italy": "IT",
    "Latvia": "LV", "Lithuania": "LT", "Luxembourg": "LU", "Malta": "MT", "Moldova": "MD",
    "Montenegro": "ME", "Morocco": "MA", "Netherlands": "NL", "North Macedonia": "MK", "Norway": "NO",
    "Poland": "PL", "Portugal": "PT", "Romania": "RO", "Serbia": "RS", "Slovakia": "SK",
    "Slovenia": "SI", "Spain": "ES", "Sweden": "SE", "Switzerland": "CH", "Türkiye": "TR",
    "Turkey": "TR", "Ukraine": "UA", "United Kingdom": "GB",
}


def build_airports() -> dict:
    from timezonefinder import TimezoneFinder  # only needed here
    tf = TimezoneFinder()
    text = requests.get(OURAIRPORTS_URL, headers=HEADERS, timeout=120).text
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        iata = (r["iata_code"] or "").strip().upper()
        if len(iata) != 3 or r["scheduled_service"] != "yes" or r["type"] not in ("large_airport", "medium_airport"):
            continue
        icao = (r.get("icao_code") or r.get("gps_code") or r["ident"]).strip().upper()
        # Prefer the larger airport when two share an IATA code
        if iata in out and r["type"] != "large_airport":
            continue
        lat, lon = float(r["latitude_deg"]), float(r["longitude_deg"])
        out[iata] = [icao, r["name"], r["iso_country"], round(lat, 4), round(lon, 4),
                     tf.timezone_at(lat=lat, lng=lon)]
    return dict(sorted(out.items()))


def build_acsa() -> dict:
    page = requests.get(ACSA_URL, headers=HEADERS, timeout=120).text
    text = html.unescape(re.sub(r"<[^>]+>", " | ", re.sub(r"<(script|style).*?</\1>", "", page, flags=re.S)))
    text = re.sub(r"[\s\u200b]+", " ", text)
    month = re.search(r"Performance \|[ |]*([A-Z][a-z]+ 20\d\d)", text)
    year = re.search(r"Year to Date (20\d\d)", text)
    airports = {}
    blocks = re.split(r"([A-Z][A-Za-z.' ]+ Airport) \(Target: \d+%\)", text)
    for name, body in zip(blocks[1::2], blocks[2::2]):
        iata = next((code for key, code in ACSA_AIRPORTS.items() if key in name.upper()), None)
        if not iata:
            continue
        rows = re.findall(r"([A-Za-z][A-Za-z .]+?) [| ]+(\d{1,3}\.\d+)% [| ]+(\d{1,3}\.\d+)%", body)
        entry = {"name": name, "airlines": {}}
        for label, ytd, latest in rows:
            label = label.strip(" |").upper()
            if label == "TOTAL":
                entry["on_time_pct"], entry["on_time_pct_month"] = float(ytd), float(latest)
                continue
            code = next((c for key, c in ACSA_AIRLINES.items() if label.startswith(key)), None)
            if code:
                entry["airlines"][code] = {"on_time_pct": float(ytd), "on_time_pct_month": float(latest)}
        if "on_time_pct" in entry:
            airports[iata] = entry
    return {"source": "Airports Company South Africa (ACSA)",
            "period": f"{year.group(1)} year to date" if year else "year to date",
            "month": month.group(1) if month else None, "airports": airports}


def _pct(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f * 100, 1)


def build_eurocontrol() -> dict:
    import pandas as pd  # openpyxl is needed for read_excel
    raw = requests.get(EUROCONTROL_URL, headers=HEADERS, timeout=120).content
    book = pd.ExcelFile(io.BytesIO(raw))
    apt, cty = book.parse("AIRPORT"), book.parse("COUNTRY")
    airports = {}
    week = {r.WK_APT_CODE: r for r in apt.itertuples() if isinstance(r.WK_APT_CODE, str)}
    for r in apt.itertuples():
        if not isinstance(r.Y2D_APT_CODE, str):
            continue
        w = week.get(r.Y2D_APT_CODE)
        airports[r.Y2D_APT_CODE] = {
            "name": r.Y2D_APT_NAME,
            "dep_on_time_pct": _pct(r.Y2D_DEP_PUN), "arr_on_time_pct": _pct(r.Y2D_ARR_PUN),
            "dep_on_time_pct_7d": _pct(w.WK_DEP_PUN) if w is not None else None,
            "arr_on_time_pct_7d": _pct(w.WK_ARR_PUN) if w is not None else None,
            "operated_pct": _pct(r.Y2D_OPE_SCH),
            "departures": int(r.Y2D_DEP_SCHED) if r.Y2D_DEP_SCHED == r.Y2D_DEP_SCHED else None,
        }
    countries = {}
    for r in cty.itertuples():
        code = COUNTRY_CODES.get(str(r.Y2D_COUNTRY).strip())
        if code:
            countries[code] = {"name": r.Y2D_COUNTRY, "dep_on_time_pct": _pct(r.Y2D_DEP_PUN),
                               "arr_on_time_pct": _pct(r.Y2D_ARR_PUN)}
    last_day = str(apt["LAST_DATA_DAY"].dropna().iloc[0])[:10]
    return {"source": "EUROCONTROL Network Manager via ansperformance.eu",
            "copyright": "© EUROCONTROL. Reproduced with the source acknowledged.",
            "period": f"{last_day[:4]} year to date", "last_data_day": last_day,
            "airports": dict(sorted(airports.items())), "countries": dict(sorted(countries.items()))}


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    airports = build_airports()
    (DATA_DIR / "airports_world.json").write_text(json.dumps(airports, ensure_ascii=False, separators=(",", ":")),
                                                  encoding="utf-8")
    print(f"airports_world.json: {len(airports):,} airports")
    acsa = build_acsa()
    (DATA_DIR / "acsa_punctuality.json").write_text(json.dumps(acsa, indent=0), encoding="utf-8")
    print(f"acsa_punctuality.json: {len(acsa['airports'])} airports ({acsa['month']})")
    euro = build_eurocontrol()
    (DATA_DIR / "eurocontrol_punctuality.json").write_text(json.dumps(euro, ensure_ascii=False, indent=0),
                                                           encoding="utf-8")
    print(f"eurocontrol_punctuality.json: {len(euro['airports'])} airports, {len(euro['countries'])} countries, "
          f"data to {euro['last_data_day']}")
    if len(airports) < 2000 or len(euro["airports"]) < 50:
        sys.exit("Unexpectedly little data; not trusting this download")


if __name__ == "__main__":
    main()
