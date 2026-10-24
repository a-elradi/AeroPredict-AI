"""
AeroPredict — delay model trained on real US flight records (BTS).
See scripts/train_delay_model.py for training and evaluation.

Features are historical rates for the flight number, route, airline and
airports (smoothed toward the overall average when there is little
history), plus month, weekday, scheduled hours and distance.
"""
import gzip
import json
import math
import threading
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import airports
from .weather_features import WX_FEATURES, empty as empty_weather

MODEL_DIR = Path(__file__).resolve().parents[3] / "models"
MODEL_FILE = MODEL_DIR / "delay_model.ubj"
STATS_FILE = MODEL_DIR / "delay_stats.json.gz"
META_FILE = MODEL_DIR / "model_meta.json"

STAT_KEYS = ["flight", "route_carrier", "route", "origin_hour", "dest_hour", "carrier", "origin", "dest"]
SMOOTHING = 50  # flights of "average" history blended into every rate
FEATURES = (["month", "day_of_week", "dep_hour", "arr_hour", "distance", "day_of_month", "holiday_days"]
            + [f"{k}_{s}" for k in STAT_KEYS for s in ("n", "delay", "cancel")]
            + WX_FEATURES)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1) if n > 0 else _last_weekday(year, month, weekday)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


@lru_cache(maxsize=16)
def us_holidays(year: int) -> tuple[date, ...]:
    """US holidays that move a lot of air travellers."""
    return (
        date(year, 1, 1), _nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0), date(year, 6, 19), date(year, 7, 4),
        _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 11, 3, 4), date(year, 12, 25),
    )


def holiday_distance(d: date) -> int:
    """Days to the nearest major holiday, capped at 15."""
    days = [abs((h - d).days) for y in (d.year - 1, d.year, d.year + 1) for h in us_holidays(y)]
    return min(min(days), 15)

_lock = threading.Lock()
_loaded: dict | None = None


def stat_keys(carrier: str, flight_no: str, origin: str, dest: str, dep_hour: int, arr_hour: int) -> dict:
    return {
        "flight": f"{carrier}{flight_no}|{origin}",
        "route_carrier": f"{carrier}|{origin}|{dest}",
        "route": f"{origin}|{dest}",
        "origin_hour": f"{origin}|{dep_hour}",
        "dest_hour": f"{dest}|{arr_hour}",
        "carrier": carrier,
        "origin": origin,
        "dest": dest,
    }


def build_features(tables: dict, prior: dict, *, carrier: str, flight_no: str, origin: str, dest: str,
                   flight_date: date, dep_hour: int, arr_hour: int, distance: float,
                   weather: dict | None = None) -> dict:
    """weather: WX_FEATURES values, or None/NaN when no forecast covers the flight."""
    row = {"month": flight_date.month, "day_of_week": flight_date.isoweekday(), "dep_hour": dep_hour,
           "arr_hour": arr_hour, "distance": distance, "day_of_month": flight_date.day,
           "holiday_days": holiday_distance(flight_date)}
    row.update(weather or empty_weather())
    for name, key in stat_keys(carrier, flight_no, origin, dest, dep_hour, arr_hour).items():
        n, d, c, _ = tables[name].get(key, (0, 0, 0, None))
        row[f"{name}_n"] = math.log1p(n)
        row[f"{name}_delay"] = (d + SMOOTHING * prior["delay"]) / (n + SMOOTHING)
        row[f"{name}_cancel"] = (c + SMOOTHING * prior["cancel"]) / (n + SMOOTHING)
    return row


def is_available() -> bool:
    return MODEL_FILE.exists() and STATS_FILE.exists()


def _load() -> dict:
    global _loaded
    with _lock:
        if _loaded is None:
            import xgboost as xgb
            booster = xgb.Booster()
            booster.load_model(str(MODEL_FILE))
            with gzip.open(STATS_FILE, "rt", encoding="utf-8") as f:
                stats = json.load(f)
            meta = json.loads(META_FILE.read_text()) if META_FILE.exists() else {}
            _loaded = {"booster": booster, "tables": stats["tables"], "prior": stats["prior"],
                       "routes": stats.get("routes", {}), "meta": meta}
    return _loaded


def _route_info(m: dict, origin: str, dest: str) -> tuple[float, int | None]:
    """Distance in miles and typical hours from departure to (local) arrival."""
    info = m["routes"].get(f"{origin}|{dest}")
    if info:
        return float(info[0]), int(info[1])
    a, b = airports.BOUNDS.get(origin), airports.BOUNDS.get(dest)
    if a and b:
        lat1, lon1 = math.radians((a[0] + a[2]) / 2), math.radians((a[1] + a[3]) / 2)
        lat2, lon2 = math.radians((b[0] + b[2]) / 2), math.radians((b[1] + b[3]) / 2)
        h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        return 2 * 3959 * math.asin(math.sqrt(h)), None
    return 1000.0, None


def predict(carrier: str, flight_no: str, origin: str, dest: str, dep_local: datetime) -> dict:
    """dep_local: scheduled departure in the origin airport's local time."""
    import xgboost as xgb
    from .weather_features import live_features
    m = _load()
    tables, prior = m["tables"], m["prior"]
    distance, offset = _route_info(m, origin, dest)
    if offset is None:
        offset = int(round(distance / 450 + 0.5))  # rough flight time, ignores time zones
    dep_hour = dep_local.hour
    arr_hour = (dep_hour + offset) % 24

    names = m["booster"].feature_names or FEATURES
    weather, weather_used = ({}, False)
    if any(n in WX_FEATURES for n in names):
        weather, weather_used = live_features(origin, dest, dep_local, offset)
    row = build_features(tables, prior, carrier=carrier, flight_no=flight_no, origin=origin, dest=dest,
                         flight_date=dep_local.date(), dep_hour=dep_hour, arr_hour=arr_hour,
                         distance=distance, weather=weather or None)
    x = np.array([[row[f] for f in names]], dtype=np.float32)
    proba = m["booster"].predict(xgb.DMatrix(x, feature_names=names))[0]

    keys = stat_keys(carrier, flight_no, origin, dest, dep_hour, arr_hour)
    history = {name: tables[name].get(key) for name, key in keys.items()}
    return {"proba": [float(p) for p in proba], "history": history, "prior": prior,
            "distance_miles": distance, "meta": m["meta"],
            "weather_used": weather_used, "weather": weather if weather_used else None}
