"""
Collect yesterday's flights from open airport feeds that only keep a short
history, so the route statistics can cover a full year:

- Norway: Avinor flight data, every flight at Avinor's airports over the last
  ~36 hours (free to use; label "Flight data from Avinor", www.avinor.no)
  https://partner.avinor.no/en/services/flight-data/
- Hong Kong: Airport Authority Hong Kong flight records (keeps 91 days)
- Taiwan: Taoyuan International Airport open flight data (today and the next
  two days only, so it runs just before midnight Taiwan time). Its actual times
  look like take-off/landing times, so it's collected raw and not used in the
  route statistics until it has been calibrated.
- Netherlands: Schiphol Public Flight API v4 (free: the application's Client ID
  in SCHIPHOL_APP_ID and Client Secret in SCHIPHOL_APP_KEY, from
  https://developer.schiphol.nl; exchanged for an OAuth token on each run)
- Sweden: Swedavia FlightInfo API, Sweden's 10 state airports (free key:
  SWEDAVIA_KEY, from https://apideveloper.swedavia.se)
Sources whose key isn't set are skipped. The first run of each also saves one
raw response to data/collected/<source>/sample.json so the parsing can be checked.

Records are stored by month in data/collected/<source>/<YYYY-MM>.json.gz as
{key: [date, airline, number, origin, dest, delay_min, cancelled]} and read by
scripts/build_route_stats.py. Run daily by .github/workflows/collect-flights.yml.

    python scripts/collect_flights.py [source ...]   (default: avinor hongkong)
"""
import csv
import io
import gzip
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
COLLECTED = DATA_DIR / "collected"
HEADERS = {"User-Agent": "Mozilla/5.0 (AeroPredict flight collector; github.com/a-elradi/AeroPredict-AI)"}
AVINOR_FEED = "https://asrv.avinor.no/XmlFeed/v1.0"
HKG_PAST = "https://www.hongkongairport.com/flightinfo-rest/rest/flights/past"
TAOYUAN_FEED = "https://www.taoyuan-airport.com/uploads/flightx/a_flight_v4.txt"
TAIWAN_UTC_OFFSET = timedelta(hours=8)
SCHIPHOL_TOKEN = "https://api.auth.schiphol.nl/oauth/token"
SCHIPHOL_AUDIENCE = "https://api.schiphol.nl/public"
SCHIPHOL_FLIGHTS = "https://api.schiphol.nl/public/public-flights/v4/flights"
SWEDAVIA_FLIGHTS = "https://api.swedavia.se/flightinfo/v2/{airport}/{direction}/{day}"
SWEDAVIA_AIRPORTS = ["ARN", "GOT", "BMA", "MMX", "LLA", "UME", "OSD", "VBY", "KRN", "RNB"]


class KeyMissing(Exception):
    pass


def get(url: str, headers: dict | None = None, **kw) -> requests.Response:
    for attempt in range(5):
        try:
            r = requests.get(url, headers={**HEADERS, **(headers or {})}, timeout=120, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(5 * 2 ** attempt)


def flight_code(text: str) -> tuple[str | None, str | None]:
    m = re.fullmatch(r"([A-Z0-9]{2}) ?(\d{1,4})[A-Z]?", (text or "").strip())
    return (m.group(1), str(int(m.group(2)))) if m else (None, None)


def norway_airports() -> list[str]:
    world = json.loads((DATA_DIR / "airports_world.json").read_text(encoding="utf-8"))
    return sorted(iata for iata, a in world.items() if a[2] == "NO")


def collect_avinor() -> dict:
    """Departures from every Norwegian airport, plus arrivals from abroad."""
    airports = set(norway_airports())
    out = {}
    for airport in sorted(airports):
        for direction in ("D", "A"):
            try:
                root = ET.fromstring(get(AVINOR_FEED, params={"TimeFrom": 36, "TimeTo": 0, "airport": airport,
                                                              "direction": direction}).content)
            except (requests.RequestException, ET.ParseError) as e:
                print(f"  Avinor {airport} {direction}: {e}", flush=True)
                continue
            for f in root.iter("flight"):
                other, sched = f.findtext("airport"), f.findtext("schedule_time")
                status = f.find("status")
                if not other or not sched or (direction == "A" and other in airports):
                    continue
                airline, number = flight_code(f.findtext("flight_id"))
                origin, dest = (airport, other) if direction == "D" else (other, airport)
                sched_t = datetime.fromisoformat(sched.replace("Z", "+00:00"))
                code = status.get("code") if status is not None else None
                if code == "C":
                    delay, cancelled = None, 1
                elif code in ("D", "A") and status.get("time"):
                    actual = datetime.fromisoformat(status.get("time").replace("Z", "+00:00"))
                    delay, cancelled = round((actual - sched_t).total_seconds() / 60), 0
                else:
                    continue  # not departed/arrived yet
                out[f"{f.get('uniqueID')}|{direction}"] = [sched_t.date().isoformat(), airline, number, origin,
                                                           dest, delay, cancelled]
    return out


def collect_hongkong(day: date) -> dict:
    out = {}
    for arrival in (False, True):
        resp = get(HKG_PAST, params={"date": day.isoformat(), "lang": "en", "cargo": "false",
                                     "arrival": str(arrival).lower()})
        for block in resp.json():
            for f in block.get("list", []):
                others = f.get("origin" if arrival else "destination") or []
                if not others or not f.get("flight"):
                    continue
                other = others[-1] if arrival else others[0]  # the stop next to Hong Kong
                origin, dest = (other, "HKG") if arrival else ("HKG", other)
                no = f["flight"][0]["no"]  # the operating flight; the rest are codeshares
                airline, number = flight_code(no)
                status = f.get("status") or ""
                key = f"{day}|{no}|{'A' if arrival else 'D'}"
                if status.startswith("Cancelled"):
                    out[key] = [day.isoformat(), airline, number, origin, dest, None, 1]
                    continue
                m = re.match(r"(?:At gate|Dep) (\d{1,2}):(\d{2})(?: \((\d{2})/(\d{2})/(\d{4})\))?", status)
                s = re.fullmatch(r"(\d{1,2}):(\d{2})", f.get("time") or "")
                if not m or not s:
                    continue
                actual_day = date(int(m.group(5)), int(m.group(4)), int(m.group(3))) if m.group(3) else day
                delay = ((actual_day - day).days * 1440 + int(m.group(1)) * 60 + int(m.group(2))
                         - int(s.group(1)) * 60 - int(s.group(2)))
                out[key] = [day.isoformat(), airline, number, origin, dest, delay, 0]
    return out


def collect_taoyuan() -> dict:
    """Today's flights at Taipei Taoyuan (Taiwan time) that have departed, arrived or been cancelled."""
    today = (datetime.now(timezone.utc) + TAIWAN_UTC_OFFSET).strftime("%Y/%m/%d")
    text = get(TAOYUAN_FEED).content.decode("big5", errors="replace")
    out, seen = {}, set()
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 14 or r[6] != today:
            continue
        direction, airline, number = r[1], r[2].strip(), r[4].strip()
        other, status = r[10].strip(), r[13].strip().upper()
        flight = (direction, r[7], other, r[8], r[9])  # codeshares repeat the operating flight's row
        if flight in seen or not number.isdigit() or not other:
            continue
        seen.add(flight)
        origin, dest = ("TPE", other) if direction == "D" else (other, "TPE")
        record = [today.replace("/", "-"), airline, str(int(number)), origin, dest]
        key = f"{record[0]}|{airline}{int(number)}|{direction}"
        if "CANCELLED" in status:
            out[key] = record + [None, 1]
        elif ("DEPARTED" in status or "ARRIVED" in status) and r[8].strip() and r[9].strip():
            sched = datetime.strptime(f"{r[6]} {r[7]}", "%Y/%m/%d %H:%M:%S")
            actual = datetime.strptime(f"{r[8]} {r[9]}", "%Y/%m/%d %H:%M:%S")
            out[key] = record + [round((actual - sched).total_seconds() / 60), 0]
    return out


def _key(*names: str) -> list[str]:
    values = [os.environ.get(n, "").strip() for n in names]
    if not all(values):
        raise KeyMissing(" and ".join(names) + " not set")
    return values


def _save_sample(source: str, payload) -> None:
    path = COLLECTED / source / "sample.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1)[:200_000], encoding="utf-8")


def _utc(text: str | None) -> datetime | None:
    if not text:
        return None
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def collect_schiphol(day: date) -> dict:
    """Every flight at Amsterdam Schiphol on one day: pushback and on-block times (gate times)."""
    client_id, client_secret = _key("SCHIPHOL_APP_ID", "SCHIPHOL_APP_KEY")
    token = requests.post(SCHIPHOL_TOKEN, timeout=60, data={
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret,
        "audience": SCHIPHOL_AUDIENCE})
    if not token.ok:  # say why (e.g. wrong secret) rather than retrying
        raise RuntimeError(f"Schiphol sign-in {token.status_code}: {token.text[:300]}")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token.json()['access_token']}"}
    check = requests.get(SCHIPHOL_FLIGHTS, headers={**HEADERS, **headers}, timeout=120,
                         params={"scheduleDate": day.isoformat(), "flightDirection": "D", "page": 0})
    if not check.ok:
        raise RuntimeError(f"Schiphol API {check.status_code}: {check.text[:300]}")
    out = {}
    for direction in ("D", "A"):
        for page in range(200):
            resp = get(SCHIPHOL_FLIGHTS, headers=headers, params={
                "scheduleDate": day.isoformat(), "flightDirection": direction, "page": page,
                "includedelays": "false", "sort": "+scheduleTime"})
            flights = resp.json().get("flights", []) if resp.content else []
            if page == 0:
                _save_sample("schiphol", resp.json() if resp.content else {})
            for f in flights:
                if f.get("serviceType") not in ("J", "C") or f.get("flightName") != f.get("mainFlight"):
                    continue  # passenger flights only; codeshares repeat the operating flight
                route = (f.get("route") or {}).get("destinations") or []
                if not route:
                    continue
                other = route[0] if direction == "D" else route[-1]
                origin, dest = ("AMS", other) if direction == "D" else (other, "AMS")
                airline, number = f.get("prefixIATA"), str(f.get("flightNumber") or "") or None
                states = (f.get("publicFlightState") or {}).get("flightStates") or []
                key = f"{day}|{f.get('flightName')}|{direction}"
                if "CNX" in states:
                    out[key] = [day.isoformat(), airline, number, origin, dest, None, 1]
                    continue
                sched = _utc(f.get("scheduleDateTime"))
                actual = _utc(f.get("actualOffBlockTime") if direction == "D" else f.get("actualOnBlockTime"))
                if sched and actual:
                    out[key] = [day.isoformat(), airline, number, origin, dest,
                                round((actual - sched).total_seconds() / 60), 0]
            if 'rel="next"' not in resp.headers.get("Link", ""):
                break
    return out


def collect_swedavia(day: date) -> dict:
    """Departures from Sweden's 10 state airports, plus arrivals from abroad."""
    (key,) = _key("SWEDAVIA_KEY")
    headers = {"Accept": "application/json", "Ocp-Apim-Subscription-Key": key, "Cache-Control": "no-cache"}
    out = {}
    for airport in SWEDAVIA_AIRPORTS:
        for direction in ("departures", "arrivals"):
            resp = get(SWEDAVIA_FLIGHTS.format(airport=airport, direction=direction, day=day.isoformat()),
                       headers=headers)
            data = resp.json()
            _save_sample("swedavia", data)
            for f in data.get("flights", []):
                dep = direction == "departures"
                other = f.get("arrivalAirportIata" if dep else "departureAirportIata")
                if not other or (not dep and other in SWEDAVIA_AIRPORTS):
                    continue
                origin, dest = (airport, other) if dep else (other, airport)
                airline = (f.get("airlineOperator") or {}).get("iata")
                fm = re.fullmatch(r"[A-Z0-9]{2}\s*0*(\d{1,4})[A-Z]?", f.get("flightId") or "")
                number = fm.group(1) if fm else None
                times = f.get("departureTime" if dep else "arrivalTime") or {}
                status = str((f.get("locationAndStatus") or {}).get("flightLegStatus") or "").upper()
                rec = f"{day}|{f.get('flightId')}|{airport}|{direction[0].upper()}"
                if status in ("CAN", "CANCELLED", "CANCELED"):
                    out[rec] = [day.isoformat(), airline, number, origin, dest, None, 1]
                    continue
                sched, actual = _utc(times.get("scheduledUtc")), _utc(times.get("actualUtc"))
                if sched and actual:
                    out[rec] = [day.isoformat(), airline, number, origin, dest,
                                round((actual - sched).total_seconds() / 60), 0]
    return out


def save(source: str, records: dict) -> int:
    """Merge into the month files; newer records replace older ones (status updates)."""
    by_month = {}
    for key, rec in records.items():
        by_month.setdefault(rec[0][:7], {})[key] = rec
    added = 0
    for month, recs in by_month.items():
        path = COLLECTED / source / f"{month}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.load(gzip.open(path, "rt", encoding="utf-8")) if path.exists() else {}
        added += len(set(recs) - set(existing))
        existing.update(recs)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(dict(sorted(existing.items())), f, separators=(",", ":"))
    return added


def load(source: str, months: int) -> list[list]:
    """The last `months` months of collected records for one source."""
    folder = COLLECTED / source
    if not folder.exists():
        return []
    files = sorted(folder.glob("*.json.gz"))[-months:]
    return [rec for p in files for rec in json.load(gzip.open(p, "rt", encoding="utf-8")).values()]


SOURCES = {"avinor": collect_avinor,
           "hongkong": lambda: collect_hongkong(date.today() - timedelta(days=1)),
           "taoyuan": collect_taoyuan,
           "schiphol": lambda: collect_schiphol(date.today() - timedelta(days=1)),
           "swedavia": lambda: collect_swedavia(date.today() - timedelta(days=1))}


def main():
    names = sys.argv[1:] or ["avinor", "hongkong"]
    failed, skipped = [], []
    for source in names:
        collect = SOURCES[source]
        try:
            records = collect()
            print(f"{source}: {len(records):,} flights, {save(source, records):,} new", flush=True)
        except KeyMissing as e:
            print(f"{source} skipped: {e}", flush=True)
            skipped.append(source)
        except Exception as e:  # one source failing shouldn't block the others
            print(f"{source} failed: {e}", flush=True)
            failed.append(source)
    if failed and len(failed) + len(skipped) == len(names):
        sys.exit("All sources failed")


if __name__ == "__main__":
    main()
