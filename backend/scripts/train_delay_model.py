"""
Train the AeroPredict delay model on real US flight records.

Data (all free):
- US Bureau of Transportation Statistics "Reporting Carrier On-Time
  Performance" monthly files:
  https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_YYYY_M.zip
- Optional hourly airport weather from Iowa State University's IEM ASOS
  archive (one CSV per airport named <IATA>.csv, UTC times), e.g.
  https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=ORD&data=sknt&data=gust&data=vsby&data=skyc1&data=skyl1&data=wxcodes&...

    python scripts/train_delay_model.py --flights DIR [DIR ...] [--weather DIR] [--sample 0.1] [--out DIR]

The two most recent months are held out as a test set (never seen in
training), so scores show how the model does on future flights. Weather is
hidden on half the training flights because live forecasts only cover the
next day or so; the test is scored both with and without weather. The final
model is then retrained on all months and saved to models/.

Besides US files, flights from other countries can be added in the same format
(files named <country>_YYYY_M.zip, e.g. brazil_2026_7.zip from
scripts/download_training_data.py); test scores are also reported per country.

Lookup keys (flight, route, airport...) are kept as integers while training
so ~14M flights fit in memory; they are turned into the text keys used by
services/delay_model.py only when the history tables are saved.
"""
import argparse
import gzip
import json
import sys
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.delay_model import (  # noqa: E402
    FEATURES, MODEL_DIR, MODEL_FILE, STATS_FILE, META_FILE, STAT_KEYS, SMOOTHING, holiday_distance,
)
from app.services.weather_features import AIRPORT_TZ, NO_CEILING_FT, WX_FEATURES, WX_FIELDS  # noqa: E402

COLUMNS = ["Year", "Month", "DayofMonth", "DayOfWeek", "FlightDate", "Reporting_Airline",
           "Flight_Number_Reporting_Airline", "Origin", "Dest", "CRSDepTime", "CRSArrTime",
           "ArrDelay", "ArrDel15", "Cancelled", "Diverted", "Distance"]
PARAMS = {
    "objective": "multi:softprob", "num_class": 3, "eval_metric": "mlogloss",
    "tree_method": "hist", "max_depth": 9, "eta": 0.05, "subsample": 0.8,
    "colsample_bytree": 0.8, "min_child_weight": 100, "nthread": 4,
}
WEATHER_HIDDEN_SHARE = 0.5
TRAIN_ROWS_SHARE = 0.4  # trees learn from 40% of flights; history uses all of them (saves memory)
EPOCH = np.datetime64("1970-01-01")
SOURCE_COUNTRY = {"brazil": "BR", "korea": "KR"}  # file name prefix -> country; anything else is US BTS data
WORLD = json.loads((Path(__file__).resolve().parents[1] / "data" / "airports_world.json").read_text(encoding="utf-8"))
AIRPORT_COUNTRY: np.ndarray = np.array([], dtype=object)  # country of each airport code, set in main()


class Codes:
    """Integer codes for airports and carriers, shared by all months."""

    def __init__(self):
        self.airports: list[str] = []
        self.carriers: list[str] = []


# ── Flights ──────────────────────────────────────────────────────────────────

def _read_month(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        with z.open(name) as f:
            return pd.read_csv(f, usecols=COLUMNS, low_memory=False,
                               dtype={"Origin": "category", "Dest": "category", "Reporting_Airline": "category"})


def load_flights(folders: list[Path], sample: float) -> tuple[pd.DataFrame, Codes]:
    paths = sorted(p for folder in folders for p in folder.glob("*.zip"))
    raw, sources = [], []
    for path in paths:
        m = _read_month(path)
        if sample < 1:
            m = m.sample(frac=sample, random_state=0)
        raw.append(m.dropna(subset=["Origin", "Dest", "CRSDepTime", "Reporting_Airline"]))
        sources.append(SOURCE_COUNTRY.get(path.name.split("_")[0], "US"))
        print(f"  {path.name}: {len(m):,} flights", flush=True)

    codes = Codes()
    codes.airports = sorted(set().union(*[set(m["Origin"].cat.categories) | set(m["Dest"].cat.categories) for m in raw]))
    codes.carriers = sorted(set().union(*[set(m["Reporting_Airline"].cat.categories) for m in raw]))
    ap, cr = {a: i for i, a in enumerate(codes.airports)}, {c: i for i, c in enumerate(codes.carriers)}

    frames = []
    for m, src in zip(raw, sources):
        dep_hour = (m["CRSDepTime"] // 100).clip(0, 23).astype(np.int8)
        arr_hour = ((m["CRSArrTime"].fillna(m["CRSDepTime"]) // 100) % 24).astype(np.int8)
        label = np.where(m["Cancelled"] == 1, 2, np.where((m["ArrDel15"] == 1) | (m["Diverted"] == 1), 1, 0))
        day = ((pd.to_datetime(m["FlightDate"]).to_numpy() - EPOCH) // np.timedelta64(1, "D")).astype(np.int32)
        dep_abs = day.astype(np.int64) * 24 + dep_hour.to_numpy()
        arr_abs = day.astype(np.int64) * 24 + arr_hour.to_numpy() + np.where(arr_hour < dep_hour, 24, 0)
        frames.append(pd.DataFrame({
            "Month": m["Month"].astype(np.int8).to_numpy(),
            "DayofMonth": m["DayofMonth"].astype(np.int8).to_numpy(),
            "DayOfWeek": m["DayOfWeek"].astype(np.int8).to_numpy(),
            "period": (m["Year"] * 12 + m["Month"]).astype(np.int32).to_numpy(),
            "day": day,
            "dep_hour": dep_hour.to_numpy(), "arr_hour": arr_hour.to_numpy(),
            "dep_abs": dep_abs, "arr_abs": arr_abs,  # local hours since 1970, for weather joins
            "o": m["Origin"].map(ap).astype(np.int16).to_numpy(),
            "d": m["Dest"].map(ap).astype(np.int16).to_numpy(),
            "c": m["Reporting_Airline"].map(cr).astype(np.int16).to_numpy(),
            "fno": m["Flight_Number_Reporting_Airline"].astype(np.int32).to_numpy(),
            "Distance": m["Distance"].astype(np.float32).to_numpy(),
            "label": label.astype(np.int8),
            "delay_min": np.where(label == 1, m["ArrDelay"], np.nan).astype(np.float32),
            "src": src,
        }))
    del raw
    df = pd.concat(frames, ignore_index=True)
    df["src"] = df["src"].astype("category")
    return df, codes


# ── Weather ──────────────────────────────────────────────────────────────────

def load_weather(folder: Path, codes: Codes) -> pd.DataFrame:
    """Hourly weather per airport and local hour, worst conditions within ±1 hour."""
    ap = {a: i for i, a in enumerate(codes.airports)}
    frames = []
    for path in sorted(folder.glob("*.csv")):
        station = path.stem
        tz = AIRPORT_TZ.get(station) or (WORLD.get(station) or [None] * 6)[5]
        if not tz or station not in ap:
            continue
        w = pd.read_csv(path, na_values=["M", "T"], low_memory=False)
        t = pd.to_datetime(w["valid"], format="%Y-%m-%d %H:%M", errors="coerce", utc=True)
        w, t = w[t.notna()].reset_index(drop=True), t[t.notna()].reset_index(drop=True)
        if w.empty:
            continue
        local = t.dt.tz_convert(tz).dt.tz_localize(None).dt.floor("h")
        hour_abs = ((local.to_numpy() - EPOCH) // np.timedelta64(1, "h")).astype(np.int64)
        wx = w["wxcodes"].fillna("").astype(str).str.upper()
        wind = pd.to_numeric(w["sknt"], errors="coerce")
        hourly = pd.DataFrame({
            "hour": hour_abs,
            "vis": pd.to_numeric(w["vsby"], errors="coerce").clip(upper=10),
            "wind": wind,
            "gust": pd.to_numeric(w["gust"], errors="coerce").fillna(wind),
            "ceiling": pd.to_numeric(pd.Series(np.where(w["skyc1"].isin(["BKN", "OVC", "VV"]), w["skyl1"], NO_CEILING_FT)),
                                     errors="coerce").fillna(NO_CEILING_FT),
            "ts": wx.str.contains("TS").astype(np.int8),
            "snow": wx.str.contains(r"SN|SG|PL|GS").astype(np.int8),
            "rain": wx.str.contains(r"RA|DZ|SH").astype(np.int8),
            "fog": wx.str.contains(r"FG|BR|HZ").astype(np.int8),
            "freezing": wx.str.contains("FZ").astype(np.int8),
        })
        # Each observation counts for the hour before, the hour itself and the hour after
        spread = pd.concat([hourly.assign(hour=hourly["hour"] + s) for s in (-1, 0, 1)])
        agg = spread.groupby("hour").agg(
            vis=("vis", "min"), wind=("wind", "max"), gust=("gust", "max"), ceiling=("ceiling", "min"),
            ts=("ts", "max"), snow=("snow", "max"), rain=("rain", "max"), fog=("fog", "max"),
            freezing=("freezing", "max"))
        agg.index = ap[station] * 10_000_000 + agg.index  # airport + hour in one integer key
        frames.append(agg.astype(np.float32))
        print(f"  weather {station}: {len(hourly):,} observations", flush=True)
    return pd.concat(frames) if frames else pd.DataFrame()


def attach_weather(df: pd.DataFrame, weather: pd.DataFrame) -> None:
    for side, airport, hour in (("o", "o", "dep_abs"), ("d", "d", "arr_abs")):
        if weather.empty:
            for f in WX_FIELDS:
                df[f"{side}_{f}"] = np.float32(np.nan)
            continue
        key = df[airport].astype(np.int64) * 10_000_000 + df[hour]
        idx = weather.index.get_indexer(key)
        found = idx >= 0
        for f in WX_FIELDS:
            col = np.full(len(df), np.nan, dtype=np.float32)
            col[found] = weather[f].to_numpy()[idx[found]]
            df[f"{side}_{f}"] = col


# ── History tables ───────────────────────────────────────────────────────────

def key_columns(df: pd.DataFrame) -> dict[str, np.ndarray]:
    c, f = df["c"].to_numpy(np.int64), df["fno"].to_numpy(np.int64)
    o, d = df["o"].to_numpy(np.int64), df["d"].to_numpy(np.int64)
    return {
        "flight": (c * 10_000 + f) * 1000 + o,
        "route_carrier": (c * 1000 + o) * 1000 + d,
        "route": o * 1000 + d,
        "origin_hour": o * 24 + df["dep_hour"].to_numpy(np.int64),
        "dest_hour": d * 24 + df["arr_hour"].to_numpy(np.int64),
        "carrier": c,
        "origin": o,
        "dest": d,
    }


def decode(name: str, k: int, codes: Codes) -> str:
    """Integer key -> the text key services/delay_model.py looks up."""
    A, C = codes.airports, codes.carriers
    if name == "flight":
        return f"{C[k // 10_000_000]}{k // 1000 % 10_000}|{A[k % 1000]}"
    if name == "route_carrier":
        return f"{C[k // 1_000_000]}|{A[k // 1000 % 1000]}|{A[k % 1000]}"
    if name == "route":
        return f"{A[k // 1000]}|{A[k % 1000]}"
    if name in ("origin_hour", "dest_hour"):
        return f"{A[k // 24]}|{k % 24}"
    if name == "carrier":
        return C[k]
    return A[k]


def stats_frames(df: pd.DataFrame, keys: dict) -> dict[str, pd.DataFrame]:
    """Per-key counts, delays, cancellations and median delay when delayed."""
    is_delay = (df["label"] == 1).to_numpy(np.int32)
    is_cancel = (df["label"] == 2).to_numpy(np.int32)
    out = {}
    for name in STAT_KEYS:
        g = pd.DataFrame({"k": keys[name], "d": is_delay, "c": is_cancel, "m": df["delay_min"].to_numpy()})
        out[name] = g.groupby("k").agg(n=("d", "size"), d=("d", "sum"), c=("c", "sum"), m=("m", "median"))
    return out


def tables_json(frames: dict, codes: Codes) -> dict:
    return {name: {decode(name, int(k), codes): [int(n), int(d), int(c), None if pd.isna(m) else int(m)]
                   for k, n, d, c, m in zip(t.index, t["n"], t["d"], t["c"], t["m"])}
            for name, t in frames.items()}


def route_table(df: pd.DataFrame, codes: Codes) -> dict:
    """Route -> [distance in miles, typical hours from departure to local arrival]."""
    r = pd.DataFrame({"k": key_columns(df)["route"], "dist": df["Distance"].to_numpy(),
                      "off": (df["arr_hour"].astype(np.int16) - df["dep_hour"]) % 24})
    agg = r.groupby("k").agg(dist=("dist", "median"), off=("off", "median"))
    return {decode("route", int(k), codes): [int(d), int(round(o))]
            for k, d, o in zip(agg.index, agg["dist"], agg["off"])}


def monthly_summary(df: pd.DataFrame) -> list[dict]:
    """Share of flights delayed and cancelled in each calendar month (most recent year)."""
    recent = df[df["period"] > df["period"].max() - 12]
    g = recent.groupby("Month")["label"]
    return [{"month": int(m), "flights": int(len(s)),
             "delayed_pct": round(float((s == 1).mean() * 100), 1),
             "cancelled_pct": round(float((s == 2).mean() * 100), 2)} for m, s in g]


def history_features(df: pd.DataFrame, keys: dict, prior: dict, frames: dict | None,
                     source: pd.DataFrame | None = None) -> dict:
    """
    History-rate features for the rows of df.

    With frames (evaluation), each key is looked up. Without (training), the
    history comes from `source` (all training flights, default df) excluding
    the row's own month, so a flight never 'sees' its own outcome.
    """
    out = {}
    if frames is None:
        source = df if source is None else source
        src_keys = key_columns(source)
        src_p = source["period"].to_numpy(np.int64)
        p0 = int(src_p.min())
        is_delay = (source["label"] == 1).to_numpy(np.int32)
        is_cancel = (source["label"] == 2).to_numpy(np.int32)
        row_p = df["period"].to_numpy(np.int64) - p0
    for name in STAT_KEYS:
        k = keys[name]
        if frames is not None:
            t = frames[name]
            idx = t.index.get_indexer(k)
            hit = idx >= 0
            n, d, c = (np.where(hit, t[col].to_numpy()[idx], 0).astype(np.float64) for col in ("n", "d", "c"))
        else:
            sk = src_keys[name]
            g = pd.DataFrame({"k": sk, "km": sk * 1000 + (src_p - p0), "d": is_delay, "c": is_cancel})
            tot = g.groupby("k")[["d", "c"]].agg(["sum", "count"])
            mon = g.groupby("km")[["d", "c"]].agg(["sum", "count"])
            del g
            ti = tot.index.get_indexer(k)
            mi = mon.index.get_indexer(k * 1000 + row_p)
            def pick(table, idx, col):
                v = table[col].to_numpy()
                return np.where(idx >= 0, v[idx], 0).astype(np.float64)
            n = pick(tot, ti, ("d", "count")) - pick(mon, mi, ("d", "count"))
            d = pick(tot, ti, ("d", "sum")) - pick(mon, mi, ("d", "sum"))
            c = pick(tot, ti, ("c", "sum")) - pick(mon, mi, ("c", "sum"))
            del tot, mon
        out[f"{name}_n"] = np.log1p(n).astype(np.float32)
        out[f"{name}_delay"] = ((d + SMOOTHING * prior["delay"]) / (n + SMOOTHING)).astype(np.float32)
        out[f"{name}_cancel"] = ((c + SMOOTHING * prior["cancel"]) / (n + SMOOTHING)).astype(np.float32)
    return out


def matrix(df: pd.DataFrame, prior: dict, frames: dict | None, hide_weather: float = 0.0,
           seed: int = 0, source: pd.DataFrame | None = None) -> pd.DataFrame:
    # Holidays of the departure country, worked out once per (day, country)
    days = df["day"].to_numpy().astype(np.int64)
    country = AIRPORT_COUNTRY[df["o"].to_numpy()] if len(AIRPORT_COUNTRY) else np.full(len(df), "US", dtype=object)
    names, cidx = np.unique(country.astype(str), return_inverse=True)
    pair = days * 1000 + cidx
    uniq = np.unique(pair)
    hol = dict(zip(uniq, (holiday_distance(date.fromordinal(date(1970, 1, 1).toordinal() + int(x // 1000)),
                                           names[int(x % 1000)]) for x in uniq)))
    X = pd.DataFrame({
        "month": df["Month"].to_numpy(np.float32), "day_of_week": df["DayOfWeek"].to_numpy(np.float32),
        "dep_hour": df["dep_hour"].to_numpy(np.float32), "arr_hour": df["arr_hour"].to_numpy(np.float32),
        "distance": df["Distance"].to_numpy(np.float32), "day_of_month": df["DayofMonth"].to_numpy(np.float32),
        "holiday_days": pd.Series(pair).map(hol).to_numpy(np.float32),
        **history_features(df, key_columns(df), prior, frames, source),
        **{f: df[f].to_numpy(np.float32) for f in WX_FEATURES},
    })[FEATURES]
    if hide_weather > 0:
        hide = np.random.default_rng(seed).random(len(X)) < hide_weather
        X.loc[hide, WX_FEATURES] = np.nan
    return X


def priors(df: pd.DataFrame) -> dict:
    return {"delay": float((df["label"] == 1).mean()), "cancel": float((df["label"] == 2).mean())}


def evaluate(booster, X, y, class_rates, rounds) -> dict:
    proba = booster.predict(xgb.DMatrix(X), iteration_range=(0, rounds))
    disrupted = (y > 0).astype(int)
    p_dis = proba[:, 1] + proba[:, 2]
    calibration = []
    bins = np.clip((p_dis * 10).astype(int), 0, 9)
    for b in range(10):
        m = bins == b
        if m.sum() >= 500:
            calibration.append({"predicted": f"{b * 10}-{b * 10 + 10}%", "flights": int(m.sum()),
                                "actual_disrupted_pct": round(float(disrupted[m].mean() * 100), 1)})
    return {
        "accuracy": round(float(accuracy_score(y, proba.argmax(1))), 4),
        "log_loss": round(float(log_loss(y, proba, labels=[0, 1, 2])), 4),
        "baseline_log_loss": round(float(log_loss(y, np.tile(class_rates, (len(y), 1)), labels=[0, 1, 2])), 4),
        "auc_delay_or_cancel": round(float(roc_auc_score(disrupted, p_dis)), 4),
        "mean_predicted_disrupted_pct": round(float(p_dis.mean() * 100), 1),
        "actual_disrupted_pct": round(float(disrupted.mean() * 100), 1),
        "calibration": calibration,
    }


def ym(p: int) -> str:
    return f"{(p - 1) // 12}-{(p - 1) % 12 + 1:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flights", nargs="+", required=True, type=Path)
    ap.add_argument("--weather", type=Path)
    ap.add_argument("--sample", type=float, default=1.0)
    ap.add_argument("--out", type=Path, help="save here instead of models/ (e.g. to check before publishing)")
    args = ap.parse_args()
    global MODEL_DIR, MODEL_FILE, STATS_FILE, META_FILE
    if args.out:
        MODEL_DIR = args.out
        MODEL_FILE, STATS_FILE, META_FILE = (args.out / p.name for p in (MODEL_FILE, STATS_FILE, META_FILE))
    started = time.time()

    print("Loading flights...", flush=True)
    df, codes = load_flights(args.flights, args.sample)
    global AIRPORT_COUNTRY
    AIRPORT_COUNTRY = np.array([(WORLD.get(a) or [None, None, "US"])[2] or "US" for a in codes.airports], dtype=object)
    print("Flights by source: " + ", ".join(f"{k} {v:,}" for k, v in df["src"].value_counts().items()), flush=True)
    weather = pd.DataFrame()
    if args.weather:
        print("Loading weather...", flush=True)
        weather = load_weather(args.weather, codes)
    attach_weather(df, weather)
    del weather
    print(f"{len(df):,} flights; weather found for {np.isfinite(df['o_vis']).mean():.0%} of departures", flush=True)

    # Months with only a sliver of flights (e.g. the first hours of the next month in another
    # country's file) would distort which months are held out for testing
    counts = df["period"].value_counts()
    stray = counts[counts < counts.median() * 0.05].index
    if len(stray):
        print(f"Dropping {int(counts[stray].sum()):,} flights in near-empty months {[ym(p) for p in stray]}", flush=True)
        df = df[~df["period"].isin(stray)].reset_index(drop=True)
    periods = sorted(df["period"].unique())
    test_p, val_p = periods[-2:], periods[-3]
    is_train = (df["period"] < val_p).to_numpy()
    val_df = df[df["period"] == val_p].sample(n=min(200_000, int((df["period"] == val_p).sum())), random_state=1)
    test_df = df[df["period"].isin(test_p)].sample(n=min(300_000, int(df["period"].isin(test_p).sum())), random_state=2)
    train_df = df[is_train]
    print(f"Train {len(train_df):,} | validation {len(val_df):,} | test {len(test_df):,} (held-out months)", flush=True)

    # 1) Honest evaluation: history tables and model only see the earlier months
    prior = priors(train_df)
    frames = stats_frames(train_df, key_columns(train_df))
    class_rates = np.bincount(train_df["label"], minlength=3) / len(train_df)
    fit_rows = train_df.sample(frac=TRAIN_ROWS_SHARE, random_state=3)
    dtrain = xgb.DMatrix(matrix(fit_rows, prior, None, hide_weather=WEATHER_HIDDEN_SHARE, source=train_df),
                         label=fit_rows["label"].to_numpy())
    del train_df, fit_rows
    dval = xgb.DMatrix(matrix(val_df, prior, frames, hide_weather=WEATHER_HIDDEN_SHARE, seed=1),
                       label=val_df["label"].to_numpy())
    print(f"Training (features ready after {time.time() - started:.0f}s)...", flush=True)
    booster = xgb.train(PARAMS, dtrain, num_boost_round=1500, evals=[(dval, "val")],
                        early_stopping_rounds=40, verbose_eval=50)
    rounds = booster.best_iteration + 1
    del dtrain, dval

    y = test_df["label"].to_numpy()
    X_test = matrix(test_df, prior, frames)
    metrics = {
        "test_flights": int(len(y)),
        "baseline_accuracy_always_on_time": round(float((y == 0).mean()), 4),
        "without_weather": evaluate(booster, X_test.assign(**{f: np.nan for f in WX_FEATURES}), y, class_rates, rounds),
    }
    # The same test, per data source (US, and each other country added)
    src = test_df["src"].to_numpy()
    metrics["by_source"] = {}
    for s in sorted(set(src)):
        m = src == s
        if m.sum() >= 2000:
            r = evaluate(booster, X_test[m].assign(**{f: np.nan for f in WX_FEATURES}), y[m], class_rates, rounds)
            metrics["by_source"][str(s)] = {"flights": int(m.sum()), "auc_delay_or_cancel": r["auc_delay_or_cancel"],
                                            "log_loss": r["log_loss"], "baseline_log_loss": r["baseline_log_loss"],
                                            "actual_disrupted_pct": r["actual_disrupted_pct"],
                                            "mean_predicted_disrupted_pct": r["mean_predicted_disrupted_pct"]}
    has_wx = X_test["o_vis"].notna().to_numpy()
    if has_wx.any():
        metrics["with_weather"] = evaluate(booster, X_test[has_wx], y[has_wx], class_rates, rounds)
        metrics["with_weather"]["flights"] = int(has_wx.sum())
    print(json.dumps(metrics, indent=2), flush=True)
    del booster, X_test, frames

    # 2) Final model on every month, same number of trees
    print(f"Retraining on all {len(df):,} flights with {rounds} trees...", flush=True)
    final_prior = priors(df)
    fit_rows = df.sample(frac=TRAIN_ROWS_SHARE, random_state=4)
    dfinal = xgb.DMatrix(matrix(fit_rows, final_prior, None, hide_weather=WEATHER_HIDDEN_SHARE, source=df),
                         label=fit_rows["label"].to_numpy())
    del fit_rows
    final = xgb.train(PARAMS, dfinal, num_boost_round=rounds)
    del dfinal
    final_prior["median_delay"] = int(np.nanmedian(df["delay_min"].to_numpy()))

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    final.save_model(MODEL_FILE)
    with gzip.open(STATS_FILE, "wt", encoding="utf-8") as f:
        json.dump({"prior": final_prior, "tables": tables_json(stats_frames(df, key_columns(df)), codes),
                   "routes": route_table(df, codes)}, f, separators=(",", ":"))
    wo = metrics["without_weather"]
    meta = {
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "data_source": "US Bureau of Transportation Statistics, Reporting Carrier On-Time Performance"
                       + ("; ANAC Brazil (VRA)" if "BR" in set(df["src"]) else "")
                       + ("; Airportal Korea" if "KR" in set(df["src"]) else ""),
        "weather_source": "Iowa Environmental Mesonet ASOS archive" if args.weather else None,
        "data_months": [ym(p) for p in periods],
        "n_training": int(len(df)),
        "test_months": [ym(p) for p in test_p],
        "trees": int(rounds),
        "class_rates": {"on_time": round(float(class_rates[0]), 4), "delayed": round(float(class_rates[1]), 4),
                        "cancelled": round(float(class_rates[2]), 4)},
        "monthly": monthly_summary(df[df["src"] == "US"]),  # the Analytics page's US seasonal chart
        # Headline numbers are the cautious ones: predictions made without a weather forecast
        "accuracy": wo["accuracy"], "log_loss": wo["log_loss"], "auc_delay_or_cancel": wo["auc_delay_or_cancel"],
        "calibration": wo["calibration"],
        **metrics,
    }
    META_FILE.write_text(json.dumps(meta, indent=2))
    print(f"Saved model to {MODEL_DIR} in {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
