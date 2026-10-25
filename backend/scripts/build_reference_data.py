"""
Build the reference data files the website loads at startup:

- data/airports_world.json: every airport with scheduled passenger service
  (IATA -> ICAO, name, country, latitude, longitude), from OurAirports
  (public domain): https://davidmegginson.github.io/ourairports-data/airports.csv
- data/eurocontrol_punctuality.json: official departure and arrival
  punctuality (share within 15 minutes of schedule) for ~110 European
  airports and their countries, year to date and for the last 7 days, from
  EUROCONTROL: https://ansperformance.eu/punctuality/

    python scripts/build_reference_data.py

Needs openpyxl to read the EUROCONTROL spreadsheet (only for this script).
Run weekly by .github/workflows/reference-data.yml.
"""
import csv
import io
import json
import sys
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
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
        out[iata] = [icao, r["name"], r["iso_country"], round(float(r["latitude_deg"]), 4),
                     round(float(r["longitude_deg"]), 4)]
    return dict(sorted(out.items()))


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
    euro = build_eurocontrol()
    (DATA_DIR / "eurocontrol_punctuality.json").write_text(json.dumps(euro, ensure_ascii=False, indent=0),
                                                           encoding="utf-8")
    print(f"eurocontrol_punctuality.json: {len(euro['airports'])} airports, {len(euro['countries'])} countries, "
          f"data to {euro['last_data_day']}")
    if len(airports) < 2000 or len(euro["airports"]) < 50:
        sys.exit("Unexpectedly little data; not trusting this download")


if __name__ == "__main__":
    main()
