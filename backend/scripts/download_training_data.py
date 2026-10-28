"""
Download the data the delay model is trained on:
- the latest MONTHS months of US BTS on-time records that have been published
- hourly weather for the airports in weather_features.AIRPORT_TZ, same period
- Brazil: every flight from ANAC's VRA files over the same months, written in the
  same format as the US files (flights/brazil_YYYY_M.zip), plus weather for
  Brazil's busier airports (--brazil-months 0 to skip)

    python scripts/download_training_data.py OUT_DIR [--months 24] [--brazil-months 24]

Creates OUT_DIR/flights/*.zip and OUT_DIR/weather/<IATA>.csv.
Used by .github/workflows/retrain-model.yml.
"""
import argparse
import csv
import io
import json
import math
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.weather_features import AIRPORT_TZ  # noqa: E402

BTS_URL = ("https://transtats.bts.gov/PREZIP/"
           "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{y}_{m}.zip")
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
HEADERS = {"User-Agent": "AeroPredict model training (github.com/a-elradi/AeroPredict-AI)"}
BTS_COLUMNS = ["Year", "Month", "DayofMonth", "DayOfWeek", "FlightDate", "Reporting_Airline",
               "Flight_Number_Reporting_Airline", "Origin", "Dest", "CRSDepTime", "CRSArrTime",
               "ArrDelay", "ArrDel15", "Cancelled", "Diverted", "Distance"]
BRAZIL_WEATHER_MIN_FLIGHTS = 3000  # airports with at least this many flights in the data get weather
WORLD = json.loads((Path(__file__).resolve().parents[1] / "data" / "airports_world.json").read_text(encoding="utf-8"))


def miles(a: str, b: str) -> float | None:
    if a not in WORLD or b not in WORLD:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (WORLD[a][3], WORLD[a][4], WORLD[b][3], WORLD[b][4]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(2 * 3959 * math.asin(math.sqrt(h)))


def brazil_month(ref, y: int, m: int) -> list[list]:
    """One month of ANAC VRA flights as rows in the BTS file layout (times are scheduled local times)."""
    import build_route_stats as brs  # shares the airline/airport matching with the route statistics
    fmt = "%Y-%m-%d %H:%M:%S"
    text = brs.get(brs.anac_url(y, m)).content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('"ICAO Empresa'))
    rows, cancelled, operated = [], defaultdict(list), Counter()
    for r in csv.DictReader(lines[start:], delimiter=";"):
        airline = ref.airline_by_icao.get(r["ICAO Empresa Aérea"])
        origin = ref.icao_to_iata.get(r["ICAO Aeródromo Origem"])
        dest = ref.icao_to_iata.get(r["ICAO Aeródromo Destino"])
        number = r["Número Voo"]
        if not (airline and origin and dest and number.isdigit()):
            continue
        try:
            dep = datetime.strptime(r["Partida Prevista"][:19], fmt)
            arr = datetime.strptime(r["Chegada Prevista"][:19], fmt)
        except ValueError:
            continue
        dist = miles(origin, dest)
        if dist is None:
            continue
        base = [dep.year, dep.month, dep.day, dep.isoweekday(), dep.strftime("%Y-%m-%d"), airline, int(number),
                origin, dest, dep.hour * 100 + dep.minute, arr.hour * 100 + arr.minute]
        key = (airline, number, origin, dest)
        status = r["Situação Voo"]
        if status == "CANCELADO":
            cancelled[key].append(base + ["", 0, 1, 0, dist])
            continue
        if status != "REALIZADO" or not r["Chegada Real"]:
            continue
        try:
            delay = (datetime.strptime(r["Chegada Real"][:19], fmt) - arr).total_seconds() / 60
        except ValueError:
            continue
        rows.append(base + [round(delay), int(delay >= 15), 0, 0, dist])
        operated[key] += 1
    # Only cancellations of flights that did operate that month count (the files also hold
    # schedule placeholders that never flew), at most as many as operated
    for key, items in cancelled.items():
        rows += items[:operated[key]]
    return rows


def download_brazil(flights: Path, months: list[tuple[int, int]]) -> Counter:
    """Writes flights/brazil_YYYY_M.zip; returns flights per airport (to choose weather stations)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_route_stats as brs
    ref = brs.Ref()
    counts = Counter()
    for y, m in sorted(months):
        dest = flights / f"brazil_{y}_{m}.zip"
        if not dest.exists():
            try:
                rows = brazil_month(ref, y, m)
            except requests.HTTPError as e:  # not published yet
                print(f"  Brazil {y}-{m:02d}: skipped ({e})", flush=True)
                continue
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(BTS_COLUMNS)
            w.writerows(rows)
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr(f"brazil_{y}_{m}.csv", buf.getvalue())
        with zipfile.ZipFile(dest) as z:
            reader = csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8"))
            next(reader)
            n = 0
            for row in reader:
                counts[row[7]] += 1
                counts[row[8]] += 1
                n += 1
        print(f"  {dest.name}: {n:,} flights", flush=True)
    return counts


def months_back(start: date, n: int) -> list[tuple[int, int]]:
    y, m = start.year, start.month
    out = []
    for _ in range(n):
        out.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def latest_published_month() -> tuple[int, int]:
    """BTS publishes with a lag of a few months; find the newest file that exists."""
    for y, m in months_back(date.today(), 8):
        r = requests.head(BTS_URL.format(y=y, m=m), headers=HEADERS, timeout=60)
        if r.status_code == 200:
            return y, m
    raise SystemExit("No BTS month found in the last 8 months")


def download(url: str, dest: Path, params: dict | None = None, attempts: int = 4) -> None:
    for attempt in range(attempts):
        try:
            with requests.get(url, params=params, headers=HEADERS, stream=True, timeout=900) as r:
                r.raise_for_status()
                tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.rename(dest)
                return
        except requests.RequestException as e:
            print(f"  retry {attempt + 1} for {dest.name}: {e}", flush=True)
            time.sleep(10 * (attempt + 1))
    raise SystemExit(f"Could not download {url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--brazil-months", type=int, default=24)
    args = ap.parse_args()

    flights, weather = args.out / "flights", args.out / "weather"
    flights.mkdir(parents=True, exist_ok=True)
    weather.mkdir(parents=True, exist_ok=True)

    last_y, last_m = latest_published_month()
    months = months_back(date(last_y, last_m, 1), args.months)
    print(f"Flights: {months[-1][0]}-{months[-1][1]:02d} to {last_y}-{last_m:02d}", flush=True)
    for y, m in sorted(months):
        dest = flights / f"otp_{y}_{m}.zip"
        if not dest.exists():
            download(BTS_URL.format(y=y, m=m), dest)
        print(f"  {dest.name}: {dest.stat().st_size // 1_000_000} MB", flush=True)

    stations = {iata: iata for iata in AIRPORT_TZ}  # US: the archive uses the 3-letter code
    if args.brazil_months:
        print("Brazil flights...", flush=True)
        counts = download_brazil(flights, months[:args.brazil_months])
        # Elsewhere the archive uses ICAO codes (SBGR); files are still saved under the IATA code
        stations.update({iata: WORLD[iata][0] for iata, n in counts.items()
                         if n >= BRAZIL_WEATHER_MIN_FLIGHTS and iata in WORLD and iata not in stations})

    first_y, first_m = months[-1]
    end = date(last_y + (last_m == 12), last_m % 12 + 1, 1)  # first day after the last month
    for iata, station in sorted(stations.items()):
        dest = weather / f"{iata}.csv"
        if dest.exists():
            continue
        params = [("station", station), *[("data", d) for d in
                  ("tmpf", "sknt", "gust", "vsby", "skyc1", "skyl1", "wxcodes", "p01i")],
                  ("year1", first_y), ("month1", first_m), ("day1", 1),
                  ("year2", end.year), ("month2", end.month), ("day2", 1),
                  ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"), ("missing", "M"),
                  ("trace", "T"), ("direct", "no"), ("report_type", 3)]
        download(IEM_URL, dest, params=params)
        print(f"  weather {iata}: {sum(1 for _ in open(dest)) - 1:,} observations", flush=True)
        time.sleep(2)  # be polite to the free archive


if __name__ == "__main__":
    main()
