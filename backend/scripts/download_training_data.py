"""
Download the data the delay model is trained on:
- the latest MONTHS months of US BTS on-time records that have been published
- hourly weather for the airports in weather_features.AIRPORT_TZ, same period

    python scripts/download_training_data.py OUT_DIR [--months 24]

Creates OUT_DIR/flights/*.zip and OUT_DIR/weather/<IATA>.csv.
Used by .github/workflows/retrain-model.yml.
"""
import argparse
import sys
import time
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.weather_features import AIRPORT_TZ  # noqa: E402

BTS_URL = ("https://transtats.bts.gov/PREZIP/"
           "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{y}_{m}.zip")
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
HEADERS = {"User-Agent": "AeroPredict model training (github.com/a-elradi/AeroPredict-AI)"}


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

    first_y, first_m = months[-1]
    end = date(last_y + (last_m == 12), last_m % 12 + 1, 1)  # first day after the last month
    for station in sorted(AIRPORT_TZ):
        dest = weather / f"{station}.csv"
        if dest.exists():
            continue
        params = [("station", station), *[("data", d) for d in
                  ("tmpf", "sknt", "gust", "vsby", "skyc1", "skyl1", "wxcodes", "p01i")],
                  ("year1", first_y), ("month1", first_m), ("day1", 1),
                  ("year2", end.year), ("month2", end.month), ("day2", 1),
                  ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"), ("missing", "M"),
                  ("trace", "T"), ("direct", "no"), ("report_type", 3)]
        download(IEM_URL, dest, params=params)
        print(f"  weather {station}: {sum(1 for _ in open(dest)) - 1:,} observations", flush=True)
        time.sleep(2)  # be polite to the free archive


if __name__ == "__main__":
    main()
