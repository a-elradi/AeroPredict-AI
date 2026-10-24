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
from pathlib import Path

import numpy as np

from . import airports

MODEL_DIR = Path(__file__).resolve().parents[3] / "models"
MODEL_FILE = MODEL_DIR / "delay_model.ubj"
STATS_FILE = MODEL_DIR / "delay_stats.json.gz"
META_FILE = MODEL_DIR / "model_meta.json"

STAT_KEYS = ["flight", "route_carrier", "route", "origin_hour", "dest_hour", "carrier", "origin", "dest"]
SMOOTHING = 50  # flights of "average" history blended into every rate
FEATURES = (["month", "day_of_week", "dep_hour", "arr_hour", "distance"]
            + [f"{k}_{s}" for k in STAT_KEYS for s in ("n", "delay", "cancel")])

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
                   month: int, day_of_week: int, dep_hour: int, arr_hour: int, distance: float) -> dict:
    """day_of_week: 1 = Monday ... 7 = Sunday (BTS convention)."""
    row = {"month": month, "day_of_week": day_of_week, "dep_hour": dep_hour,
           "arr_hour": arr_hour, "distance": distance}
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


def predict(carrier: str, flight_no: str, origin: str, dest: str, month: int,
            day_of_week: int, dep_hour: int) -> dict:
    import xgboost as xgb
    m = _load()
    tables, prior = m["tables"], m["prior"]
    distance, offset = _route_info(m, origin, dest)
    if offset is None:
        offset = int(round(distance / 450 + 0.5))  # rough flight time, ignores time zones
    arr_hour = (dep_hour + offset) % 24

    row = build_features(tables, prior, carrier=carrier, flight_no=flight_no, origin=origin, dest=dest,
                         month=month, day_of_week=day_of_week, dep_hour=dep_hour,
                         arr_hour=arr_hour, distance=distance)
    x = np.array([[row[f] for f in FEATURES]], dtype=np.float32)
    proba = m["booster"].predict(xgb.DMatrix(x, feature_names=FEATURES))[0]

    keys = stat_keys(carrier, flight_no, origin, dest, dep_hour, arr_hour)
    history = {name: tables[name].get(key) for name, key in keys.items()}
    return {"proba": [float(p) for p in proba], "history": history, "prior": prior,
            "distance_miles": distance, "meta": m["meta"]}
