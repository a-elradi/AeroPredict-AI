"""
Build route and airline punctuality history from official national data,
for flights the US data doesn't cover:

- United Kingdom: CAA punctuality statistics, every airline on every route
  to and from 25 UK airports, by direction, monthly
  https://www.caa.co.uk/data-and-analysis/uk-aviation-market/flight-punctuality/
- Brazil: ANAC "Voo Regular Ativo" (VRA), every scheduled flight to, from and
  within Brazil with scheduled and actual times
  https://sistemas.anac.gov.br/dadosabertos/
- Australia: BITRE domestic on-time performance by route and airline on
  competitive routes (via data.gov.au)
- South Korea: Airportal (MOLIT), every flight at Korea's 15 airports with
  scheduled and actual times and the airport's delay flag
  https://www.airportal.go.kr/
- Hong Kong: Airport Authority Hong Kong flight records, every flight over
  the last ~90 days (the feed's limit), via DATA.GOV.HK
- Norway: Avinor flight data, every flight at Norway's airports, saved daily by
  scripts/collect_flights.py (the feed keeps ~36 hours); Hong Kong older than
  the feed's 90 days comes from the same collector
- Israel: Israel Airports Authority flight board for Ben Gurion, saved daily
  by scripts/collect_flights.py (data.gov.il)
- Japan: MLIT on-time performance of the 10 main Japanese airlines on
  domestic routes, latest fiscal year (airline level only)
  https://www.mlit.go.jp/koku/15_bf_000727.html

Names are matched to IATA codes using OurAirports (airports, countries) and
OpenFlights (airlines). Output: data/route_stats.json.gz with four tables:
  airline_country "AIRLINE|COUNTRY"         (an airline's national domestic record: Australia, Brazil, Korea, Japan)
  airline         "AIRLINE"                 (an airline's record on every route above, all countries)
  flight         "AIRLINE+NUMBER|ORIGIN"   (Brazil, Korea, Hong Kong: flight-level data)
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
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

import collect_flights

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
    "SZCZECIN (GOLENOW)": "SZZ", "MALE INTERNATIONAL": "MLE", "CARDIFF WALES": "CWL", "TROMSOE": "TOS",
    "MAURITIUS": "MRU", "MINNEAPOLIS-ST PAUL": "MSP", "NASHVILLE METROPOLITAN": "BNA",
    "BAKU (HEYDER ALIYEV INT'L)": "GYD", "ANTIGUA": "ANU", "PODGORICA": "TGD", "BERMUDA": "BDA",
    "ANCONA": "AOI", "AARHUS (TIRSTRUP)": "AAR", "GRENADA": "GND", "GRAND CAYMAN": "GCM",
    "TARBES-LOURDES INTERNATIONAL": "LDE", "SHENZHEN (HUANGTIAN)": "SZX",
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
                     "BEIJING CAPITAL AIRLINES": "JD", "NORSE ATLANTIC UK": "Z0",
                     "VOLOTEA": "V7", "FLY PLAY HF": "OG", "HISKY": "H4", "TITAN AIRWAYS": "ZT"}
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
        self.source = None  # set by main() before each country's builder
        self.airline_sources = defaultdict(set)
        self.t = {"airline": defaultdict(lambda: [0, 0.0, 0, 0.0]),
                  "airline_country": defaultdict(lambda: [0, 0.0, 0, 0.0]),
                  "flight": defaultdict(lambda: [0, 0.0, 0, 0.0]),
                  "route_airline": defaultdict(lambda: [0, 0.0, 0, 0.0]),
                  "route": defaultdict(lambda: [0, 0.0, 0, 0.0])}

    def add(self, table, key, flights, late, cancelled, late_minutes):
        e = self.t[table][key]
        e[0] += flights
        e[1] += late
        e[2] += cancelled
        e[3] += late_minutes
        if table == "route_airline":  # every airline's overall record, across all countries
            airline = key.split("|")[0]
            self.add("airline", airline, flights, late, cancelled, late_minutes)
            self.airline_sources[airline].add(self.source)

    def add_all(self, airline, number, origin, dest, flights, late, cancelled, late_minutes):
        if number and airline:
            self.add("flight", f"{airline}{number}|{origin}", flights, late, cancelled, late_minutes)
        if airline:
            self.add("route_airline", f"{airline}|{origin}|{dest}", flights, late, cancelled, late_minutes)
        self.add("route", f"{origin}|{dest}", flights, late, cancelled, late_minutes)

    def export(self) -> dict:
        out = {}
        for name, table in self.t.items():
            out[name] = {k: [n, round(late), c, round(lm / late) if late >= 1 and lm > 0 else None]
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
            if ref.country_of.get(other) in ("BR", "KR"):
                continue  # Brazil's and Korea's flight-level data already cover these flights
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


# ── Australia (BITRE via data.gov.au) ────────────────────────────────────────

AUSTRALIA_CSV = ("https://data.gov.au/data/dataset/29128ebd-dbaa-4ff5-8b86-d9f30de56452/resource/"
                 "cf663ed1-0c5e-497f-aea9-e74bfda9cf44/download/otp_time_series_web.csv")
AU_AIRLINES = {"Jetstar": "JQ", "Qantas": "QF", "QantasLink": "QF", "Virgin Australia": "VA",
               "Virgin Australia Regional Airlines": "VA", "Regional Express": "ZL", "Rex Airlines": "ZL",
               "Tigerair Australia": "TT", "Bonza": "AB"}
AU_CITIES = {"Sydney": "SYD", "Melbourne": "MEL", "Brisbane": "BNE", "Adelaide": "ADL", "Perth": "PER",
             "Canberra": "CBR", "Gold Coast": "OOL", "Cairns": "CNS", "Hobart": "HBA", "Darwin": "DRW",
             "Townsville": "TSV", "Launceston": "LST", "Sunshine Coast": "MCY", "Mackay": "MKY",
             "Rockhampton": "ROK", "Ballina": "BNK", "Coffs Harbour": "CFS", "Newcastle": "NTL",
             "Albury": "ABX", "Alice Springs": "ASP", "Kalgoorlie": "KGI", "Karratha": "KTA",
             "Port Hedland": "PHE", "Proserpine": "PPP", "Hamilton Island": "HTI", "Wagga Wagga": "WGA",
             "Mildura": "MQL", "Gladstone": "GLT", "Emerald": "EMD", "Broome": "BME", "Ayers Rock": "AYQ",
             "Port Lincoln": "PLO", "Dubbo": "DBO", "Armidale": "ARM", "Avalon": "AVV", "Mount Isa": "ISA",
             "Bundaberg": "BDB", "Hervey Bay": "HVB", "Mount Gambier": "MGB", "Devonport": "DPO",
             "Burnie": "BWT", "Geraldton": "GET", "Newman": "ZNE", "Paraburdoo": "PBO", "Exmouth": "LEA",
             "Learmonth": "LEA", "Port Macquarie": "PQQ", "Tamworth": "TMW", "Toowoomba": "WTB", "Orange": "OAG"}


def build_australia(ref: Ref, stats: Stats, months: int) -> dict:
    rows = list(csv.DictReader(io.StringIO(get(AUSTRALIA_CSV).content.decode("utf-8-sig", errors="replace"))))
    latest = max((int(r["Year"]), int(r["Month_Num"])) for r in rows if r.get("Year"))
    keep = set(month_list(months, latest))
    unmatched = defaultdict(int)
    flights_total = 0
    for r in rows:
        if not r.get("Year") or (int(r["Year"]), int(r["Month_Num"])) not in keep:
            continue
        flown, cancelled = int(r["Sectors_Flown"] or 0), int(r["Cancellations"] or 0)
        if r["Departing_Port"] == "All Ports":
            # Each airline's national record: a fallback for domestic routes not listed one by one
            airline = AU_AIRLINES.get(r["Airline"])
            if airline:
                stats.add("airline_country", f"{airline}|AU", flown, int(r["Arrivals_Delayed"] or 0), cancelled, 0)
            continue
        origin, dest = AU_CITIES.get(r["Departing_Port"]), AU_CITIES.get(r["Arriving_Port"])
        if not origin or not dest:
            unmatched[f"{r['Departing_Port']}/{r['Arriving_Port']}"] += flown
            continue
        late = int(r["Arrivals_Delayed"] or 0)  # arrived 15+ minutes after schedule
        if r["Airline"] == "All Airlines":
            stats.add("route", f"{origin}|{dest}", flown, late, cancelled, 0)
            flights_total += flown
        else:
            airline = AU_AIRLINES.get(r["Airline"])
            if airline:
                stats.add("route_airline", f"{airline}|{origin}|{dest}", flown, late, cancelled, 0)
            else:
                unmatched[r["Airline"]] += flown
    report_unmatched("Australia", unmatched)
    first = min(keep)
    return {"name": "BITRE Australia, domestic airline on-time performance",
            "period": f"{first[0]}-{first[1]:02d} to {latest[0]}-{latest[1]:02d}", "flights": flights_total}


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
        operated, cancelled = defaultdict(int), defaultdict(int)
        for r in csv.DictReader(lines[start:], delimiter=";"):
            status = r["Situação Voo"]
            airline = ref.airline_by_icao.get(r["ICAO Empresa Aérea"])
            origin = ref.icao_to_iata.get(r["ICAO Aeródromo Origem"])
            dest = ref.icao_to_iata.get(r["ICAO Aeródromo Destino"])
            if not origin or not dest:
                unmatched[f"{r['ICAO Aeródromo Origem']}/{r['ICAO Aeródromo Destino']}"] += 1
                continue
            number = str(int(r["Número Voo"])) if r["Número Voo"].isdigit() else None
            domestic = airline and ref.country_of.get(origin) == ref.country_of.get(dest) == "BR"
            if status == "CANCELADO":
                cancelled[(airline, number, origin, dest, domestic)] += 1
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
            if domestic:
                stats.add("airline_country", f"{airline}|BR", 1, int(late), 0, delay if late else 0)
            operated[(airline, number, origin, dest)] += 1
            flights_total += 1
        # The data also lists schedule placeholders that never flew (hundreds of 'cancelled'
        # flights on routes with one real flight a day), so only cancellations of flights that
        # did operate that month count, and at most as many as operated.
        for (airline, number, origin, dest, domestic), c in cancelled.items():
            c = min(c, operated.get((airline, number, origin, dest), 0))
            if c:
                stats.add_all(airline, number, origin, dest, 0, 0, c, 0)
                if domestic:
                    stats.add("airline_country", f"{airline}|BR", 0, 0, c, 0)
        print(f"  Brazil {y}-{m:02d}: done", flush=True)
    report_unmatched("Brazil airports", unmatched)
    return {"name": "ANAC Brazil, Voo Regular Ativo (VRA)",
            "period": f"{periods[0][0]}-{periods[0][1]:02d} to {periods[-1][0]}-{periods[-1][1]:02d}",
            "flights": flights_total}


# ── South Korea (Airportal, MOLIT) ───────────────────────────────────────────

AIRPORTAL = "https://www.airportal.go.kr"
KR_AIRPORTS = ["RKSI", "RKSS", "RKPC", "RKPK", "RKTU", "RKTN", "RKJB", "RKNY", "RKJJ", "RKJY",
               "RKPU", "RKTH", "RKPS", "RKNW", "RKJK"]
KR_STATUS_CANCELLED = "취소"
KR_STATUS_LATE = {"지연", "회항"}  # delayed, diverted
KR_TAXI_MINUTES = 15  # actual departure times are take-off times; on-time flights take off ~15 min after schedule


def airportal(path: str, body: dict) -> requests.Response:
    headers = {**HEADERS, "Content-Type": "application/json;charset=UTF-8", "request-call": "web-rest"}
    for attempt in range(8):  # the site often drops connections
        try:
            r = requests.post(AIRPORTAL + path, json=body, headers=headers, timeout=300)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 7:
                raise
            time.sleep(min(5 * 2 ** attempt, 120))


def _minutes(hhmm: str) -> int | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(hhmm or "").strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def build_korea(ref: Ref, stats: Stats, months: int) -> dict:
    """
    Every passenger flight at Korea's 15 airports. Departures use the departure delay; arrivals
    from abroad use the arrival delay (domestic arrivals are already counted as departures).
    The recorded times are take-off and landing times, so "late" is the airport's own delay
    flag rather than a comparison of times.
    """
    import pandas as pd
    today = date.today()
    periods = month_list(months, (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12))
    kr_iata = {ref.icao_to_iata[i] for i in KR_AIRPORTS if i in ref.icao_to_iata}
    names = {}  # Korean airport name -> IATA, learned from the daily feed (it carries both)
    pending = []  # rows whose airport name hasn't been seen in the feed yet
    failures = 0
    unmatched = defaultdict(int)
    flights_total = 0

    def record(context, here, other_iata, flight, sched, actual, status):
        nonlocal flights_total
        if context == "arrival" and other_iata in kr_iata:
            return
        origin, dest = (here, other_iata) if context == "departure" else (other_iata, here)
        fm = re.fullmatch(r"([A-Z0-9]{2})(\d{1,4})[A-Z]?", flight.strip())
        airline, number = (fm.group(1), str(int(fm.group(2)))) if fm else (None, None)
        domestic = airline and other_iata in kr_iata
        if status == KR_STATUS_CANCELLED:
            stats.add_all(airline, number, origin, dest, 0, 0, 1, 0)
            if domestic:
                stats.add("airline_country", f"{airline}|KR", 0, 0, 1, 0)
            return
        s, a = _minutes(sched), _minutes(actual)
        if s is None or a is None:
            return
        delay = (a - s + 720) % 1440 - 720  # flights that cross midnight
        if context == "departure":
            delay -= KR_TAXI_MINUTES
        late = status in KR_STATUS_LATE
        delay = max(delay, 16) if late else delay
        stats.add_all(airline, number, origin, dest, 1, int(late), 0, delay if late else 0)
        if domestic:
            stats.add("airline_country", f"{airline}|KR", 1, int(late), 0, delay if late else 0)
        flights_total += 1
    for y, m in periods:
        last = (date(y + m // 12, m % 12 + 1, 1) - date(y, m, 1)).days
        for icao in KR_AIRPORTS:
            here = ref.icao_to_iata.get(icao)
            for context, key in (("departure", "depAirport"), ("arrival", "arrAirport")):
                try:
                    feed = airportal("/airport/getFlightArrivalDepartureInfo.do",
                                     {"searchContext": context, "ymd": f"{y}{m:02d}15", "loadType": "", key: icao})
                    for f in feed.json().get("content") or []:
                        if f.get("apKr") and f.get("apIata"):
                            names[f["apKr"]] = f["apIata"]
                    raw = airportal("/airport/exportDepArr.do",
                                    {"searchContext": context, "ymd1": f"{y}{m:02d}01", "ymd2": f"{y}{m:02d}{last}",
                                     "loadType": "", key: icao}).content
                except requests.RequestException as e:  # skip this airport-month rather than all of Korea
                    print(f"  Korea {icao} {context} {y}-{m:02d} skipped: {e}", flush=True)
                    failures += 1
                    if failures > 30:
                        raise
                    continue
                if not raw.startswith(b"PK"):
                    continue  # no flights (small airports) or an error page
                df = pd.read_excel(io.BytesIO(raw), dtype=str)
                for r in df.itertuples(index=False):
                    _, _, _, flight, other, _, sched, _, actual, kind, status = r[:11]
                    if kind != "여객" or not isinstance(flight, str):
                        continue
                    if other in names:
                        record(context, here, names[other], flight, sched, actual, status)
                    else:
                        pending.append((context, here, other, flight, sched, actual, status))
        print(f"  Korea {y}-{m:02d}: done ({flights_total:,} flights so far)", flush=True)
    for context, here, other, *rest in pending:
        if other in names:
            record(context, here, names[other], *rest)
        else:
            unmatched[other] += 1
    report_unmatched("Korea airports", unmatched)
    return {"name": "Airportal (Korea Ministry of Land, Infrastructure and Transport), flight records",
            "period": f"{periods[0][0]}-{periods[0][1]:02d} to {periods[-1][0]}-{periods[-1][1]:02d}",
            "flights": flights_total}


# ── Hong Kong (Airport Authority Hong Kong) ─────────────────────────────────

HKG_PAST = "https://www.hongkongairport.com/flightinfo-rest/rest/flights/past"
HKG_DAYS = 90  # the feed keeps the last 91 days


def build_hongkong(ref: Ref, stats: Stats, months: int) -> dict:
    """
    Every passenger flight at Hong Kong over the last ~3 months: departures by pushback time,
    arrivals by on-block time. Flights to the UK, Brazil and Korea are left to those countries' data.
    """
    today = date.today()
    days = [today - timedelta(days=d) for d in range(HKG_DAYS, 0, -1)]
    flights_total = 0
    for day in days:
        for arrival in (False, True):
            resp = get(HKG_PAST, params={"date": day.isoformat(), "lang": "en", "cargo": "false",
                                         "arrival": str(arrival).lower()})
            for block in resp.json():
                for f in block.get("list", []):
                    others = f.get("origin" if arrival else "destination") or []
                    if not others or not f.get("flight"):
                        continue
                    other = others[-1] if arrival else others[0]  # the stop next to Hong Kong
                    if ref.country_of.get(other) in ("GB", "BR", "KR"):
                        continue
                    origin, dest = (other, "HKG") if arrival else ("HKG", other)
                    fm = re.fullmatch(r"([A-Z0-9]{2}) ?(\d{1,4})[A-Z]?", f["flight"][0]["no"].strip())  # operating flight
                    airline, number = (fm.group(1), str(int(fm.group(2)))) if fm else (None, None)
                    status = f.get("status") or ""
                    if status.startswith("Cancelled"):
                        stats.add_all(airline, number, origin, dest, 0, 0, 1, 0)
                        continue
                    m = re.match(r"(?:At gate|Dep) (\d{1,2}):(\d{2})(?: \((\d{2})/(\d{2})/(\d{4})\))?", status)
                    s = _minutes(f.get("time"))
                    if not m or s is None:
                        continue
                    actual_day = date(int(m.group(5)), int(m.group(4)), int(m.group(3))) if m.group(3) else day
                    delay = (actual_day - day).days * 1440 + int(m.group(1)) * 60 + int(m.group(2)) - s
                    late = delay > 15
                    stats.add_all(airline, number, origin, dest, 1, int(late), 0, delay if late else 0)
                    flights_total += 1
    # Older days saved by the daily collector (scripts/collect_flights.py)
    older = [r for r in collect_flights.load("hongkong", months) if r[0] < days[0].isoformat()]
    first = min([r[0] for r in older], default=days[0].isoformat())
    flights_total += add_collected(ref, stats, older, skip_countries=("GB", "BR", "KR"))
    print(f"  Hong Kong: {flights_total:,} flights", flush=True)
    return {"name": "Airport Authority Hong Kong flight records (DATA.GOV.HK)",
            "period": f"{first} to {days[-1]:%Y-%m-%d}", "flights": flights_total}


def add_collected(ref: Ref, stats: Stats, records: list, skip_countries=(), domestic_country: str | None = None) -> int:
    """Records saved by scripts/collect_flights.py: [date, airline, number, origin, dest, delay, cancelled]."""
    n = 0
    for _, airline, number, origin, dest, delay, cancelled in records:
        if ref.country_of.get(origin) in skip_countries or ref.country_of.get(dest) in skip_countries:
            continue
        domestic = domestic_country and airline and \
            ref.country_of.get(origin) == ref.country_of.get(dest) == domestic_country
        if cancelled:
            stats.add_all(airline, number, origin, dest, 0, 0, 1, 0)
            if domestic:
                stats.add("airline_country", f"{airline}|{domestic_country}", 0, 0, 1, 0)
            continue
        if delay is None:
            continue
        late = delay > 15
        stats.add_all(airline, number, origin, dest, 1, int(late), 0, delay if late else 0)
        if domestic:
            stats.add("airline_country", f"{airline}|{domestic_country}", 1, int(late), 0, delay if late else 0)
        n += 1
    return n


# ── Norway (Avinor, collected daily) ─────────────────────────────────────────

def build_norway(ref: Ref, stats: Stats, months: int) -> dict:
    """
    Every flight at Norway's airports, saved daily from Avinor's flight data feed (it only keeps
    ~36 hours). Flights to the UK, Brazil, Korea and Hong Kong are left to those sources.
    """
    records = collect_flights.load("avinor", months)
    if not records:
        raise RuntimeError("no collected Avinor data yet")
    n = add_collected(ref, stats, records, skip_countries=("GB", "BR", "KR", "HK"), domestic_country="NO")
    dates = sorted(r[0] for r in records)
    print(f"  Norway: {n:,} flights", flush=True)
    return {"name": "Flight data from Avinor (www.avinor.no)", "period": f"{dates[0]} to {dates[-1]}",
            "flights": n}


def build_israel(ref: Ref, stats: Stats, months: int) -> dict:
    """Every flight at Tel Aviv Ben Gurion, saved daily from the Israel Airports Authority board."""
    records = collect_flights.load("israel", months)
    if not records:
        raise RuntimeError("no collected Israel data yet")
    n = add_collected(ref, stats, records, skip_countries=("GB", "BR", "KR", "HK", "NO"), domestic_country="IL")
    dates = sorted(r[0] for r in records)
    print(f"  Israel: {n:,} flights", flush=True)
    return {"name": "Israel Airports Authority flight board (data.gov.il)", "period": f"{dates[0]} to {dates[-1]}",
            "flights": n}


def build_netherlands(ref: Ref, stats: Stats, months: int) -> dict:
    """Every passenger flight at Amsterdam Schiphol (pushback / on-block times), saved daily."""
    records = collect_flights.load("schiphol", months)
    if not records:
        raise RuntimeError("no collected Schiphol data yet (needs the free Schiphol API key)")
    n = add_collected(ref, stats, records, skip_countries=("GB", "BR", "KR", "HK", "NO", "IL"))
    dates = sorted(r[0] for r in records)
    print(f"  Netherlands: {n:,} flights", flush=True)
    return {"name": "Schiphol Public Flight API", "period": f"{dates[0]} to {dates[-1]}", "flights": n}


# ── Japan (MLIT Civil Aviation Bureau) ───────────────────────────────────────

MLIT_INDEX = "https://www.mlit.go.jp/koku/15_bf_000727.html"
JP_AIRLINES = {"全日本空輸": "NH", "日本航空": "JL", "日本トランスオーシャン": "NU", "スカイマーク": "BC",
               "AIRDO": "HD", "ソラシドエア": "6J", "スターフライヤー": "7G", "PEACH": "MM",
               "ジェットスター": "GK", "スプリング": "IJ"}


def _jp_airline(cell) -> str | None:
    text = unicodedata.normalize("NFKC", str(cell)).upper() if isinstance(cell, str) else ""
    return next((code for name, code in JP_AIRLINES.items() if text.startswith(name)), None)


def _jp_rates(sheet) -> dict:
    """Year-total on-time rate per airline (first row under each airline is the latest year)."""
    header = next(i for i, row in sheet.iterrows() if "年度計" in row.values)
    col = list(sheet.iloc[header]).index("年度計")
    out = {}
    for _, row in sheet.iloc[header + 1:].iterrows():
        code = _jp_airline(row[1])
        if code and code not in out and isinstance(row[col], (int, float)):
            out[code] = float(row[col])
    return out


def _jp_counts(sheet) -> dict:
    """Year total of the '合計' rows (delays or cancellations by cause) per airline."""
    header = next(i for i, row in sheet.iterrows() if "4月" in row.values)
    months = [j for j, v in enumerate(sheet.iloc[header]) if isinstance(v, str) and re.fullmatch(r"\d{1,2}月", v)]
    out, current = {}, None
    for _, row in sheet.iloc[header + 1:].iterrows():
        current = _jp_airline(row[1]) or current
        if current and row[3] == "合計" and current not in out:
            out[current] = sum(float(re.sub(r"[^\d.]", "", str(row[j])) or 0) for j in months)
    return out


def build_japan(ref: Ref, stats: Stats, months: int) -> dict:
    """
    National on-time record of Japan's 10 main airlines on domestic routes, for the latest
    fiscal year (April-March). MLIT publishes rates and delay/cancellation counts per airline,
    not per route, so this only feeds the airline_country table.
    """
    import pandas as pd
    index = get(MLIT_INDEX).text
    year_page = max(set(re.findall(r'/koku/koku_fr4_\d+\.html', index)))
    page = get("https://www.mlit.go.jp" + year_page).content.decode("utf-8", errors="replace")
    link = next(m.group(1) for m in re.finditer(r'href="([^"]+\.xlsx?)"[^>]*>(.*?)</a>', page)
                if re.sub(r"<[^>]+>", "", m.group(2)).strip() == "特定本邦航空運送事業者")
    book = pd.ExcelFile(io.BytesIO(get("https://www.mlit.go.jp" + link).content))
    sheets = {name: book.parse(name, header=None) for name in book.sheet_names}
    pick = lambda tag: next(df for name, df in sheets.items() if tag in name)
    arr_rate, dep_rate = _jp_rates(pick("（1-1）")), _jp_rates(pick("（1-2）"))
    dep_delayed, cancelled = _jp_counts(pick("（2-2）")), _jp_counts(pick("（3）"))
    flights_total = 0
    for code, rate in arr_rate.items():
        if code not in dep_rate or not dep_delayed.get(code) or dep_rate[code] >= 1:
            continue
        flights = round(dep_delayed[code] / (1 - dep_rate[code]))  # flights = delayed / delayed share
        stats.add("airline_country", f"{code}|JP", flights, round(flights * (1 - rate)), int(cancelled.get(code, 0)), 0)
        flights_total += flights
    fy = re.search(r"(\d{4})年度実績", pick("（1-1）").to_string())
    print(f"  Japan: {len(arr_rate)} airlines, {flights_total:,} flights", flush=True)
    return {"name": "MLIT Japan, airline on-time performance (domestic)",
            "period": f"fiscal year {fy.group(1)}" if fy else "latest fiscal year", "flights": flights_total}


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
    for key, builder in (("uk", build_uk), ("brazil", build_brazil), ("australia", build_australia),
                         ("korea", build_korea), ("hongkong", build_hongkong),
                         ("japan", build_japan), ("norway", build_norway),
                         ("israel", build_israel), ("netherlands", build_netherlands)):
        try:
            stats.source = key
            sources[key] = builder(ref, stats, args.months)
        except Exception as e:  # one country failing shouldn't block the others
            print(f"  {key} failed: {e}", flush=True)
    tables = stats.export()
    if not sources or len(tables["route"]) < 500:
        sys.exit("Too little route data; not trusting this build")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(DATA_DIR / "route_stats.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"sources": sources, **tables,
                   "airline_sources": {a: sorted(s) for a, s in stats.airline_sources.items() if a in tables["airline"]}},
                  f, separators=(",", ":"))
    print("route_stats.json.gz: " + ", ".join(f"{k} {len(v):,}" for k, v in tables.items()) +
          " | " + ", ".join(f"{k}: {v['flights']:,} flights {v['period']}" for k, v in sources.items()))


if __name__ == "__main__":
    main()
