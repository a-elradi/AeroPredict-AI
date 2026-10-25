"""
Build route and airline punctuality history from official national data,
for flights the US data doesn't cover:

- United Kingdom: CAA punctuality statistics, every airline on every route
  to and from 25 UK airports, by direction, monthly
  https://www.caa.co.uk/data-and-analysis/uk-aviation-market/flight-punctuality/
- Brazil: ANAC "Voo Regular Ativo" (VRA), every scheduled flight to, from and
  within Brazil with scheduled and actual times
  https://sistemas.anac.gov.br/dadosabertos/

Names are matched to IATA codes using OurAirports (airports, countries) and
OpenFlights (airlines). Output: data/route_stats.json.gz with three tables:
  flight         "AIRLINE+NUMBER|ORIGIN"   (Brazil only: flight-level data)
  route_airline  "AIRLINE|ORIGIN|DEST"
  route          "ORIGIN|DEST"
each entry [flights, late_15min, cancelled, typical_delay_when_late_min].

    python scripts/build_route_stats.py [--months 12]
"""
import argparse
import csv
import gzip
import io
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import requests

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
HEADERS = {"User-Agent": "Mozilla/5.0 (AeroPredict route statistics; github.com/a-elradi/AeroPredict-AI)"}
MIN_FLIGHTS = 10

OURAIRPORTS = "https://davidmegginson.github.io/ourairports-data/"
OPENFLIGHTS_AIRLINES = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
CAA_YEAR_PAGE = "https://www.caa.co.uk/data-and-analysis/uk-aviation-market/flight-punctuality/uk-flight-punctuality-statistics/{year}"
ANAC_VRA = ("https://sistemas.anac.gov.br/dadosabertos/Voos%20e%20opera%C3%A7%C3%B5es%20a%C3%A9reas/"
            "Voo%20Regular%20Ativo%20%28VRA%29/")
ANAC_MONTHS = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
               "Setembro", "Outubro", "Novembro", "Dezembro"]

UK_AIRPORTS = {
    "ABERDEEN": "ABZ", "BELFAST CITY (GEORGE BEST)": "BHD", "BELFAST INTERNATIONAL": "BFS",
    "BIRMINGHAM": "BHX", "BOURNEMOUTH": "BOH", "BRISTOL": "BRS", "CARDIFF WALES": "CWL",
    "EAST MIDLANDS INTERNATIONAL": "EMA", "EDINBURGH": "EDI", "EXETER": "EXT", "GATWICK": "LGW",
    "GLASGOW": "GLA", "HEATHROW": "LHR", "ISLE OF MAN": "IOM", "JERSEY": "JER", "LEEDS BRADFORD": "LBA",
    "LIVERPOOL (JOHN LENNON)": "LPL", "LONDON CITY": "LCY", "LUTON": "LTN", "MANCHESTER": "MAN",
    "NEWCASTLE": "NCL", "SOUTHAMPTON": "SOU", "SOUTHEND": "SEN", "STANSTED": "STN",
    "TEESSIDE INTERNATIONAL AIRPORT": "MME",
}
# CAA destination names that are ambiguous between airports of the same city
DEST_OVERRIDES = {
    ("TR", "ISTANBUL"): "IST", ("AE", "DUBAI"): "DXB", ("RU", "MOSCOW"): "SVO", ("TH", "BANGKOK"): "BKK",
    ("JP", "TOKYO"): "HND", ("US", "NEW YORK"): "JFK", ("US", "CHICAGO"): "ORD", ("US", "WASHINGTON"): "IAD",
    ("US", "HOUSTON"): "IAH", ("CN", "SHANGHAI"): "PVG", ("CN", "BEIJING"): "PEK", ("IT", "ROME"): "FCO",
    ("IT", "MILAN"): "MXP", ("FR", "PARIS"): "CDG", ("SE", "STOCKHOLM"): "ARN", ("BR", "SAO PAULO"): "GRU",
    ("BR", "RIO DE JANEIRO"): "GIG", ("AR", "BUENOS AIRES"): "EZE", ("CA", "TORONTO"): "YYZ",
    ("CA", "MONTREAL"): "YUL", ("KR", "SEOUL"): "ICN", ("ID", "JAKARTA"): "CGK", ("DE", "BERLIN"): "BER",
}
DEST_NAME_OVERRIDES = {
    "NEW YORK (JF KENNEDY)": "JFK", "TENERIFE (SURREINA SOFIA)": "TFS", "TENERIFE (NORTE LOS RODEOS)": "TFN",
    "DOHA HAMAD": "DOH", "OPORTO (PORTUGAL)": "OPO", "ARRECIFE": "ACE", "BERGAMO": "BGY",
    "LONDON (HEATHROW)": "LHR", "LONDON (GATWICK)": "LGW", "LONDON (STANSTED)": "STN", "LONDON (LUTON)": "LTN",
    "LONDON CITY": "LCY", "LAS PALMAS": "LPA", "SHARM EL SHEIKH (OPHIRA)": "SSH",
    "HONG KONG (CHEK LAP KOK)": "HKG", "WROCLAW": "WRO", "BALE MULHOUSE": "BSL", "ABU DHABI INTERNATIONAL": "AUH",
    "ENFIDHA - HAMMAMET INTL": "NBE", "CITY OF DERRY (EGLINTON)": "LDY", "ZAKINTHOS": "ZTH", "SALONIKA": "SKG",
    "CASABLANCA MOHAMED V": "CMN", "MONTREAL (DORVAL)": "YUL", "BANGKOK SUVARNABHUMI": "BKK",
    "BARI (PALESE)": "BRI", "CHISINAU INTERNATIONAL": "RMO", "ILHA DO SAL C.VERDE": "SID", "SEOUL (INCHEON)": "ICN",
    "KERRY COUNTY": "KIR", "BELGRADE": "BEG", "HANOVER": "HAJ", "TIVAT": "TIV", "MIKONOS": "JMK",
    "EAST MIDLANDS INTERNATIONAL": "EMA", "BOA VISTA (RABIL)": "BVC", "LODZ LUBLINEK": "LCJ", "HALIFAX INT": "YHZ",
    "SZCZECIN (GOLENOW)": "SZZ", "MALE INTERNATIONAL": "MLE",
}
COUNTRY_ALIASES = {"IRISH REPUBLIC": "IE", "IRELAND": "IE", "SLOVAK REPUBLIC": "SK", "REPUBLIC OF SOUTH AFRICA": "ZA","TURKIYE": "TR", "TURKEY": "TR", "UNITED STATES OF AMERICA": "US", "USA": "US",
                   "RUSSIA": "RU", "RUSSIAN FEDERATION": "RU", "SOUTH KOREA": "KR", "KOREA REPUBLIC OF": "KR",
                   "CZECH REPUBLIC": "CZ", "CZECHIA": "CZ", "IRAN": "IR", "UAE": "AE",
                   "UNITED ARAB EMIRATES": "AE", "VIETNAM": "VN", "VIET NAM": "VN", "IVORY COAST": "CI",
                   "MACEDONIA": "MK", "NORTH MACEDONIA": "MK", "MOLDOVA": "MD", "TANZANIA": "TZ",
                   "CHANNEL ISLANDS": "JE", "BOSNIA AND HERZEGOVINA": "BA", "HONG KONG": "HK"}
AIRLINE_OVERRIDES = {"THY TURKISH AIRLINES": "TK", "EASYJET UK": "U2", "EASYJET EUROPE": "EC",
                     "EASYJET SWITZERLAND": "DS", "JET2COM": "LS", "TUI AIRWAYS": "BY", "WIZZ AIR UK": "W9",
                     "RYANAIR UK": "RK", "LOGANAIR": "LM", "BRITISH AIRWAYS": "BA", "VIRGIN ATLANTIC AIRWAYS": "VS",
                     "EMIRATES": "EK", "QATAR AIRWAYS": "QR", "ETIHAD AIRWAYS": "EY", "PEGASUS AIRLINES": "PC",
                     "AER LINGUS": "EI", "RYANAIR": "FR", "KLM ROYAL DUTCH AIRLINES": "KL", "DEUTSCHE LUFTHANSA": "LH",
                     "SWISS INTERNATIONAL AIR LINES": "LX", "AIR FRANCE": "AF", "SAUDI ARABIAN AIRLINES": "SV",
                     "EGYPTAIR": "MS", "AMERICAN AIRLINES": "AA", "UNITED AIRLINES": "UA", "DELTA AIR LINES": "DL",
                     "SINGAPORE AIRLINES": "SQ", "CATHAY PACIFIC AIRWAYS": "CX", "AIR INDIA": "AI",
                     "SUNEXPRESS": "XQ", "AJET": "VF", "EUROWINGS": "EW", "BLUE ISLANDS": "SI", "AURIGNY AIR SERVICES": "GR",
                     "KLM": "KL", "SWISS AIRLINES": "LX", "EMERALD AIRLINES UK": "EI", "NORWEGIAN AIR SWEDEN": "DY",
                     "NORWEGIAN AIR SHUTTLE": "DY", "IBERIA": "IB", "DELTA AIRLINES": "DL", "ITALIA TRASPOTO AEREO": "AZ",
                     "EGYPT AIR": "MS", "AJET HAVA TASIMACILIGI ANONIM SIRKETI": "VF", "CHINA SOUTHERN": "CZ",
                     "WIDEROE FLYVESELSKAP A S": "WF", "SCANDINAVIAN AIRLINES IRELAND": "SK",
                     "SCANDINAVIAN AIRLINES SYSTEM": "SK", "EL AL": "LY", "GULF AIR": "GF", "KM MALTA AIRLINES": "KM",
                     "SAS": "SK", "WIZZ AIR MALTA": "W4", "AIR PORTUGAL": "TP", "EMERALD AIRLINES IRELAND": "EI",
                     "VIRGIN ATLANTIC INTERNATIONAL": "VS", "MALAYSIAN AIRLINES SYSTEM MAS": "MH", "WEST JET AIRLINES": "WS",
                     "AIR PEACE": "P4", "CORENDON AIRLINES": "XC", "CONDOR": "DE", "TAM LINHAS AEREAS": "LA",
                     "AVIANCA COLOMBIA": "AV", "RWANDAIR": "WB", "RIYADH AIR": "RX", "PAKISTAN INTL AIRLINES": "PK",
                     # Subsidiaries that fly under their parent's flight numbers
                     "BA CITYFLYER": "BA", "BA EUROFLYER BRITISH AIRWAYS": "BA", "KLM CITYHOPPER": "KL",
                     "LUFTHANSA CITY AIRLINES": "LH", "LUFTHANSA CITYLINE": "LH", "EUROWINGS LUFTVERKEHRS": "EW",
                     "TRANSAVIA": "HV", "ARKIA": "IZ", "UZBEKISTAN AIRLINES": "HY", "NOUVELAIR TUNISIE": "BJ",
                     "ENTER AIR": "E4", "CORENDON AIRLINES EUROPE": "XR", "TIANJIN AIRLINES": "GS",
                     "BEIJING CAPITAL AIRLINES": "JD", "NORSE ATLANTIC UK": "Z0"}
# ICAO airline codes whose IATA code in OpenFlights is out of date or missing
ICAO_OVERRIDES = {"TAM": "LA", "JAT": "JA", "DWI": "DM", "ITY": "AZ"}
NOISE = {"PLC", "LTD", "LIMITED", "SA", "AG", "AB", "AS", "ASA", "INC", "CORPORATION", "CORP", "COMPANY", "CO",
         "SPA", "GMBH", "BV", "NV", "LLC", "SRL", "SAS", "KFT", "THE", "DBA"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    return " ".join(w for w in re.sub(r"[^A-Z0-9 ]", " ", s).split() if w not in NOISE)


def get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=300, **kw)
    r.raise_for_status()
    return r


# ── Reference data ───────────────────────────────────────────────────────────

class Ref:
    def __init__(self):
        rows = list(csv.DictReader(io.StringIO(get(OURAIRPORTS + "airports.csv").text)))
        self.icao_to_iata, self.by_country, self.country_of = {}, defaultdict(list), {}
        for r in rows:
            iata = (r["iata_code"] or "").strip().upper()
            if len(iata) != 3 or r["type"] not in ("large_airport", "medium_airport", "small_airport"):
                continue
            icao = (r.get("icao_code") or r.get("gps_code") or r["ident"]).strip().upper()
            self.icao_to_iata[icao] = iata
            self.country_of.setdefault(iata, r["iso_country"])
            if r["scheduled_service"] == "yes":
                self.by_country[r["iso_country"]].append(
                    (iata, norm(r["name"]), norm(r["municipality"]), r["type"]))
        self.countries = {norm(r["name"]): r["code"] for r in csv.DictReader(io.StringIO(get(OURAIRPORTS + "countries.csv").text))}
        self.countries.update(COUNTRY_ALIASES)

        self.airline_by_name, self.airline_by_icao = {}, {}
        for r in csv.reader(io.StringIO(get(OPENFLIGHTS_AIRLINES).text)):
            if len(r) < 8 or r[7] != "Y" or not re.fullmatch(r"[A-Z0-9]{2}", r[3] or ""):
                continue
            self.airline_by_name.setdefault(norm(r[1]), r[3])
            if re.fullmatch(r"[A-Z]{3}", r[4] or ""):
                self.airline_by_icao.setdefault(r[4], r[3])
        self.airline_by_icao.update(ICAO_OVERRIDES)

    def country(self, name: str) -> str | None:
        return self.countries.get(norm(name))

    def destination(self, name: str, country_name: str) -> str | None:
        if name.strip().upper() in DEST_NAME_OVERRIDES:
            return DEST_NAME_OVERRIDES[name.strip().upper()]
        # e.g. "PORTUGAL(EXCLUDING MADEIRA)", "SPAIN(CANARY ISLANDS)"
        iso = self.country(re.sub(r"\(.*\)", "", country_name))
        if not iso:
            return None
        m = re.match(r"^(.*?)\s*\((.*)\)\s*$", name)
        city, hint = (norm(m.group(1)), norm(m.group(2))) if m else (norm(name), "")
        if (iso, city) in DEST_OVERRIDES and not hint:
            return DEST_OVERRIDES[(iso, city)]
        # The UK, Channel Islands and Isle of Man are separate countries in OurAirports
        pool = [a for c in ({"GB", "JE", "GG", "IM"} if iso == "GB" else {iso}) for a in self.by_country.get(c, [])]
        cands = [a for a in pool if city and (city in a[2] or city in a[1])]
        if hint == norm(re.sub(r"\(.*\)", "", country_name)):
            hint = ""  # "OPORTO (PORTUGAL)": the hint is just the country
        if hint:
            if re.fullmatch(r"[A-Z]{3}", hint) and any(a[0] == hint for a in cands):
                return hint
            first = hint.split()[0]
            cands = [a for a in cands if first in a[1] or first in a[2]] or \
                    [a for a in pool if first in a[1]]
        if not cands:
            return None
        rank = {"large_airport": 0, "medium_airport": 1, "small_airport": 2}
        cands.sort(key=lambda a: (rank[a[3]], not a[1].startswith(city), len(a[1])))
        return cands[0][0]

    def airline(self, name: str) -> str | None:
        n = norm(name)
        if n in AIRLINE_OVERRIDES:
            return AIRLINE_OVERRIDES[n]
        if n in self.airline_by_name:
            return self.airline_by_name[n]
        best = None
        for known, code in self.airline_by_name.items():
            if len(known) >= 5 and n.startswith(known + " "):
                if best is None or len(known) > len(best[0]):
                    best = (known, code)
        return best[1] if best else None


# ── Accumulation ─────────────────────────────────────────────────────────────

class Stats:
    def __init__(self):
        self.t = {"flight": defaultdict(lambda: [0, 0.0, 0, 0.0]),
                  "route_airline": defaultdict(lambda: [0, 0.0, 0, 0.0]),
                  "route": defaultdict(lambda: [0, 0.0, 0, 0.0])}

    def add(self, table, key, flights, late, cancelled, late_minutes):
        e = self.t[table][key]
        e[0] += flights
        e[1] += late
        e[2] += cancelled
        e[3] += late_minutes

    def add_all(self, airline, number, origin, dest, flights, late, cancelled, late_minutes):
        if number and airline:
            self.add("flight", f"{airline}{number}|{origin}", flights, late, cancelled, late_minutes)
        if airline:
            self.add("route_airline", f"{airline}|{origin}|{dest}", flights, late, cancelled, late_minutes)
        self.add("route", f"{origin}|{dest}", flights, late, cancelled, late_minutes)

    def export(self) -> dict:
        out = {}
        for name, table in self.t.items():
            out[name] = {k: [n, round(late), c, round(lm / late) if late >= 1 else None]
                         for k, (n, late, c, lm) in table.items() if n + c >= MIN_FLIGHTS}
        return out


def month_list(months: int, latest: tuple[int, int]) -> list[tuple[int, int]]:
    y, m = latest
    out = []
    for _ in range(months):
        out.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return sorted(out)


# ── United Kingdom (CAA) ─────────────────────────────────────────────────────

UK_LATE_BUCKETS = [("flights_between_16_and_30_minutes_late_percent", 23),
                   ("flights_between_31_and_60_minutes_late_percent", 45),
                   ("flights_between_61_and_120_minutes_late_percent", 90),
                   ("flights_between_121_and_180_minutes_late_percent", 150),
                   ("flights_between_181_and_360_minutes_late_percent", 270),
                   ("flights_more_than_360_minutes_late_percent", 420)]


def uk_files(months: int) -> list[tuple[str, str]]:
    found = {}
    for year in (date.today().year, date.today().year - 1, date.today().year - 2):
        try:
            html = get(CAA_YEAR_PAGE.format(year=year)).text
        except requests.RequestException:
            continue
        for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
            text = " ".join(re.sub("<[^>]+>", "", text).split())
            m = re.match(r"(\d{6}) Punctuality Statistics Full Analysis Arrival Departure", text)
            if m:
                found[m.group(1)] = "https://www.caa.co.uk" + href if href.startswith("/") else href
    periods = sorted(found)[-months:]
    return [(p, found[p]) for p in periods]


def build_uk(ref: Ref, stats: Stats, months: int) -> dict:
    files = uk_files(months)
    unmatched_dest, unmatched_airline, airline_flights = defaultdict(int), defaultdict(int), defaultdict(int)
    flights_total = 0
    for period, url in files:
        rows = csv.DictReader(io.StringIO(get(url).content.decode("utf-8-sig", errors="replace")))
        for r in rows:
            if r["scheduled_charter"] != "S":
                continue
            uk = UK_AIRPORTS.get(r["reporting_airport"])
            other = ref.destination(r["origin_destination"], r["origin_destination_country"])
            n, cancelled = int(float(r["number_flights_matched"] or 0)), int(float(r["number_flights_cancelled"] or 0))
            if not uk or not other or n + cancelled == 0:
                if not other:
                    unmatched_dest[f"{r['origin_destination']} ({r['origin_destination_country']})"] += n
                continue
            if ref.country_of.get(other) == "BR":
                continue  # Brazil's flight-level data already covers these flights
            airline = ref.airline(r["airline_name"])
            airline_flights[r["airline_name"]] += n
            if not airline:
                unmatched_airline[r["airline_name"]] += n
            late_pct = sum(float(r[k] or 0) for k, _ in UK_LATE_BUCKETS)
            late = n * late_pct / 100
            late_minutes = n * sum(float(r[k] or 0) * mid for k, mid in UK_LATE_BUCKETS) / 100
            origin, dest = (uk, other) if r["arrival_departure"] == "D" else (other, uk)
            stats.add_all(airline, None, origin, dest, n, late, cancelled, late_minutes)
            flights_total += n
        print(f"  UK {period}: done", flush=True)
    top = sorted(airline_flights.items(), key=lambda kv: -kv[1])[:45]
    print("  UK airline matches: " + "; ".join(f"{name}={ref.airline(name)}" for name, _ in top), flush=True)
    report_unmatched("UK destinations", unmatched_dest)
    report_unmatched("UK airlines", unmatched_airline)
    return {"name": "UK Civil Aviation Authority punctuality statistics",
            "period": f"{files[0][0][:4]}-{files[0][0][4:]} to {files[-1][0][:4]}-{files[-1][0][4:]}" if files else None,
            "flights": flights_total}


# ── Brazil (ANAC VRA) ────────────────────────────────────────────────────────

def anac_url(y: int, m: int) -> str:
    folder = quote(f"{m:02d} - {ANAC_MONTHS[m - 1]}")
    return f"{ANAC_VRA}{y}/{folder}/VRA_{y}{m}.csv"


def latest_anac_month() -> tuple[int, int]:
    y, m = date.today().year, date.today().month
    for _ in range(8):
        if requests.head(anac_url(y, m), headers=HEADERS, timeout=60).status_code == 200:
            return y, m
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    raise SystemExit("No recent ANAC VRA file found")


def build_brazil(ref: Ref, stats: Stats, months: int) -> dict:
    periods = month_list(months, latest_anac_month())
    unmatched = defaultdict(int)
    flights_total = 0
    fmt = "%Y-%m-%d %H:%M:%S"
    for y, m in periods:
        text = get(anac_url(y, m)).content.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith('"ICAO Empresa'))
        for r in csv.DictReader(lines[start:], delimiter=";"):
            status = r["Situação Voo"]
            airline = ref.airline_by_icao.get(r["ICAO Empresa Aérea"])
            origin = ref.icao_to_iata.get(r["ICAO Aeródromo Origem"])
            dest = ref.icao_to_iata.get(r["ICAO Aeródromo Destino"])
            if not origin or not dest:
                unmatched[f"{r['ICAO Aeródromo Origem']}/{r['ICAO Aeródromo Destino']}"] += 1
                continue
            number = str(int(r["Número Voo"])) if r["Número Voo"].isdigit() else None
            if status == "CANCELADO":
                stats.add_all(airline, number, origin, dest, 0, 0, 1, 0)
                continue
            if status != "REALIZADO" or not r["Chegada Prevista"] or not r["Chegada Real"]:
                continue
            try:
                # Some airlines' times carry fractions of a second ("04:25:00.100000000")
                delay = (datetime.strptime(r["Chegada Real"][:19], fmt)
                         - datetime.strptime(r["Chegada Prevista"][:19], fmt)).total_seconds() / 60
            except ValueError:
                continue
            late = delay > 15
            stats.add_all(airline, number, origin, dest, 1, int(late), 0, delay if late else 0)
            flights_total += 1
        print(f"  Brazil {y}-{m:02d}: done", flush=True)
    report_unmatched("Brazil airports", unmatched)
    return {"name": "ANAC Brazil, Voo Regular Ativo (VRA)",
            "period": f"{periods[0][0]}-{periods[0][1]:02d} to {periods[-1][0]}-{periods[-1][1]:02d}",
            "flights": flights_total}


def report_unmatched(what: str, counts: dict) -> None:
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:15]
    if top:
        print(f"  unmatched {what} (by flights): " + "; ".join(f"{k}={v}" for k, v in top), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    args = ap.parse_args()
    print("Loading reference lists...", flush=True)
    ref = Ref()
    stats = Stats()
    sources = {}
    for key, builder in (("uk", build_uk), ("brazil", build_brazil)):
        try:
            sources[key] = builder(ref, stats, args.months)
        except Exception as e:  # one country failing shouldn't block the others
            print(f"  {key} failed: {e}", flush=True)
    tables = stats.export()
    if not sources or len(tables["route"]) < 500:
        sys.exit("Too little route data; not trusting this build")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(DATA_DIR / "route_stats.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"sources": sources, **tables}, f, separators=(",", ":"))
    print("route_stats.json.gz: " + ", ".join(f"{k} {len(v):,}" for k, v in tables.items()) +
          " | " + ", ".join(f"{k}: {v['flights']:,} flights {v['period']}" for k, v in sources.items()))


if __name__ == "__main__":
    main()
