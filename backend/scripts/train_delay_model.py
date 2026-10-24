"""
Train the AeroPredict delay model on real US flight records.

Data (all free):
- US Bureau of Transportation Statistics "Reporting Carrier On-Time
  Performance" monthly files:
  https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_YYYY_M.zip
- Optional hourly airport weather from Iowa State University's IEM ASOS
  archive (one CSV per airport, UTC times), e.g.
  https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=ORD&data=sknt&data=gust&data=vsby&data=skyc1&data=skyl1&data=wxcodes&...

    python scripts/train_delay_model.py --flights DIR [DIR ...] [--weather DIR] [--sample 0.1]

The two most recent months are held out as a test set (never seen in
training), so scores show how the model does on future flights. Weather is
hidden on half the training flights because live forecasts only cover the
next day or so; the test is scored both with and without weather. The final
model is then retrained on all months and saved to models/.
"""
import argparse
import gzip
import json
import sys
import time
import zipfile
from datetime import datetime, timezone
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


# ── Flights ──────────────────────────────────────────────────────────────────

def load_flights(folders: list[Path], sample: float) -> pd.DataFrame:
    frames = []
    for folder in folders:
        for path in sorted(folder.glob("*.zip")):
            with zipfile.ZipFile(path) as z:
                name = next(n for n in z.namelist() if n.endswith(".csv"))
                with z.open(name) as f:
                    df = pd.read_csv(f, usecols=COLUMNS, low_memory=False)
            if sample < 1:
                df = df.sample(frac=sample, random_state=0)
            frames.append(df)
            print(f"  {path.name}: {len(df):,} flights")
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["Origin", "Dest", "CRSDepTime", "Reporting_Airline"])
    df["label"] = np.where(df["Cancelled"] == 1, 2,
                           np.where((df["ArrDel15"] == 1) | (df["Diverted"] == 1), 1, 0)).astype(np.int8)
    df["period"] = df["Year"] * 12 + df["Month"]
    df["dep_hour"] = (df["CRSDepTime"] // 100).clip(0, 23).astype(np.int8)
    df["arr_hour"] = ((df["CRSArrTime"].fillna(df["CRSDepTime"]) // 100) % 24).astype(np.int8)
    df["carrier"] = df["Reporting_Airline"].astype(str)
    df["flight_no"] = df["Flight_Number_Reporting_Airline"].astype(int).astype(str)
    df["delay_min"] = np.where(df["label"] == 1, df["ArrDelay"], np.nan)
    df["date"] = pd.to_datetime(df["FlightDate"])
    # Local scheduled departure and arrival, as whole hours (arrival may be the next day)
    df["dep_local"] = df["date"] + pd.to_timedelta(df["dep_hour"], unit="h")
    arr = df["date"] + pd.to_timedelta(df["arr_hour"], unit="h")
    df["arr_local"] = arr.where(df["arr_hour"] >= df["dep_hour"], arr + pd.Timedelta(days=1))
    return df.drop(columns=["FlightDate", "Flight_Number_Reporting_Airline", "Reporting_Airline"])


# ── Weather ──────────────────────────────────────────────────────────────────

def load_weather(folder: Path) -> pd.DataFrame:
    """Hourly weather per airport and local hour, worst conditions within ±1 hour."""
    frames = []
    for path in sorted(folder.glob("*.csv")):
        station = path.stem
        tz = AIRPORT_TZ.get(station)
        if not tz:
            continue
        w = pd.read_csv(path, na_values=["M", "T"], low_memory=False)
        if w.empty:
            continue
        t = pd.to_datetime(w["valid"], format="%Y-%m-%d %H:%M", errors="coerce", utc=True)
        w, t = w[t.notna()].reset_index(drop=True), t[t.notna()].reset_index(drop=True)
        t = t.dt.tz_convert(tz).dt.tz_localize(None).dt.floor("h")
        wx = w["wxcodes"].fillna("").astype(str).str.upper()
        ceiling = np.where(w["skyc1"].isin(["BKN", "OVC", "VV"]), w["skyl1"], NO_CEILING_FT)
        hourly = pd.DataFrame({
            "airport": station, "hour": t,
            "vis": pd.to_numeric(w["vsby"], errors="coerce").clip(upper=10),
            "wind": pd.to_numeric(w["sknt"], errors="coerce"),
            "gust": pd.to_numeric(w["gust"], errors="coerce").fillna(pd.to_numeric(w["sknt"], errors="coerce")),
            "ceiling": pd.to_numeric(pd.Series(ceiling), errors="coerce").fillna(NO_CEILING_FT),
            "ts": wx.str.contains("TS").astype(np.int8),
            "snow": wx.str.contains(r"SN|SG|PL|GS").astype(np.int8),
            "rain": wx.str.contains(r"RA|DZ|SH").astype(np.int8),
            "fog": wx.str.contains(r"FG|BR|HZ").astype(np.int8),
            "freezing": wx.str.contains("FZ").astype(np.int8),
        })
        # Each observation counts for the hour before, the hour itself and the hour after
        spread = pd.concat([hourly.assign(hour=hourly["hour"] + pd.Timedelta(hours=s)) for s in (-1, 0, 1)])
        frames.append(spread.groupby(["airport", "hour"]).agg(
            vis=("vis", "min"), wind=("wind", "max"), gust=("gust", "max"), ceiling=("ceiling", "min"),
            ts=("ts", "max"), snow=("snow", "max"), rain=("rain", "max"), fog=("fog", "max"),
            freezing=("freezing", "max")).reset_index())
        print(f"  weather {station}: {len(hourly):,} observations")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def attach_weather(df: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    for f in WX_FEATURES:
        df[f] = np.nan
    if weather.empty:
        return df
    for side, airport_col, time_col in (("o", "Origin", "dep_local"), ("d", "Dest", "arr_local")):
        w = weather.rename(columns={f: f"{side}_{f}" for f in WX_FIELDS})
        cols = [f"{side}_{f}" for f in WX_FIELDS]
        merged = df[[airport_col, time_col]].merge(
            w, how="left", left_on=[airport_col, time_col], right_on=["airport", "hour"])
        df[cols] = merged[cols].to_numpy()
    return df


# ── History tables ───────────────────────────────────────────────────────────

def key_columns(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "flight": df["carrier"] + df["flight_no"] + "|" + df["Origin"],
        "route_carrier": df["carrier"] + "|" + df["Origin"] + "|" + df["Dest"],
        "route": df["Origin"] + "|" + df["Dest"],
        "origin_hour": df["Origin"] + "|" + df["dep_hour"].astype(str),
        "dest_hour": df["Dest"] + "|" + df["arr_hour"].astype(str),
        "carrier": df["carrier"],
        "origin": df["Origin"],
        "dest": df["Dest"],
    }


def stats_tables(df: pd.DataFrame, keys: dict) -> dict:
    """Per-key counts, delay and cancel rates, and median delay when delayed."""
    tables = {}
    is_delay = (df["label"] == 1).astype(np.int32)
    is_cancel = (df["label"] == 2).astype(np.int32)
    for name in STAT_KEYS:
        g = pd.DataFrame({"k": keys[name], "d": is_delay, "c": is_cancel, "m": df["delay_min"]})
        agg = g.groupby("k").agg(n=("d", "size"), d=("d", "sum"), c=("c", "sum"), m=("m", "median"))
        tables[name] = {k: [int(n), int(d), int(c), None if pd.isna(m) else int(m)]
                        for k, n, d, c, m in zip(agg.index, agg["n"], agg["d"], agg["c"], agg["m"])}
    return tables


def route_table(df: pd.DataFrame) -> dict:
    """Route -> [distance in miles, typical hours from departure to local arrival]."""
    r = pd.DataFrame({"k": df["Origin"] + "|" + df["Dest"], "dist": df["Distance"],
                      "off": (df["arr_hour"] - df["dep_hour"]) % 24})
    agg = r.groupby("k").agg(dist=("dist", "median"), off=("off", "median"))
    return {k: [int(d), int(round(o))] for k, d, o in zip(agg.index, agg["dist"], agg["off"])}


def monthly_summary(df: pd.DataFrame) -> list[dict]:
    """Share of flights delayed and cancelled in each calendar month (most recent year)."""
    recent = df[df["period"] > df["period"].max() - 12]
    g = recent.groupby("Month")["label"]
    return [{"month": int(m), "flights": int(len(s)),
             "delayed_pct": round(float((s == 1).mean() * 100), 1),
             "cancelled_pct": round(float((s == 2).mean() * 100), 2)} for m, s in g]


def history_features(df: pd.DataFrame, keys: dict, prior: dict, tables: dict | None) -> dict:
    """
    History-rate features. With tables (evaluation/serving), look keys up.
    Without (training rows), use each key's history excluding the row's own
    month, so a flight never 'sees' its own outcome.
    """
    out = {}
    is_delay = (df["label"] == 1).astype(np.int32)
    is_cancel = (df["label"] == 2).astype(np.int32)
    for name in STAT_KEYS:
        if tables is not None:
            t = pd.DataFrame.from_dict(tables[name], orient="index", columns=["n", "d", "c", "m"])
            j = pd.DataFrame({"k": keys[name]}).join(t, on="k")
            n, d, c = j["n"].fillna(0), j["d"].fillna(0), j["c"].fillna(0)
        else:
            g = pd.DataFrame({"k": keys[name], "p": df["period"], "d": is_delay, "c": is_cancel})
            tot = g.groupby("k")[["d", "c"]].agg(["sum", "count"])
            tot.columns = ["d_sum", "n", "c_sum", "_"]
            mon = g.groupby(["k", "p"])[["d", "c"]].agg(["sum", "count"])
            mon.columns = ["d_m", "n_m", "c_m", "_m"]
            j = g[["k", "p"]].join(tot, on="k").join(mon, on=["k", "p"])
            n, d, c = j["n"] - j["n_m"], j["d_sum"] - j["d_m"], j["c_sum"] - j["c_m"]
        out[f"{name}_n"] = np.log1p(n.to_numpy())
        out[f"{name}_delay"] = ((d + SMOOTHING * prior["delay"]) / (n + SMOOTHING)).to_numpy()
        out[f"{name}_cancel"] = ((c + SMOOTHING * prior["cancel"]) / (n + SMOOTHING)).to_numpy()
    return out


def matrix(df: pd.DataFrame, prior: dict, tables: dict | None, hide_weather: float = 0.0,
           seed: int = 0) -> pd.DataFrame:
    keys = key_columns(df)
    dates = df["date"].dt.date
    holiday = dates.map({d: holiday_distance(d) for d in dates.unique()})
    X = pd.DataFrame({
        "month": df["Month"].to_numpy(), "day_of_week": df["DayOfWeek"].to_numpy(),
        "dep_hour": df["dep_hour"].to_numpy(), "arr_hour": df["arr_hour"].to_numpy(),
        "distance": df["Distance"].to_numpy(), "day_of_month": df["DayofMonth"].to_numpy(),
        "holiday_days": holiday.to_numpy(),
        **history_features(df, keys, prior, tables),
        **{f: df[f].to_numpy() for f in WX_FEATURES},
    })
    X = X[FEATURES].astype(np.float32)  # halves memory on ~14M rows
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
    args = ap.parse_args()
    started = time.time()

    print("Loading flights...")
    df = load_flights(args.flights, args.sample)
    weather = pd.DataFrame()
    if args.weather:
        print("Loading weather...")
        weather = load_weather(args.weather)
    df = attach_weather(df, weather)
    print(f"{len(df):,} flights; weather found for {df['o_vis'].notna().mean():.0%} of departures")

    periods = sorted(df["period"].unique())
    test_p, val_p = periods[-2:], periods[-3]
    train_df = df[df["period"] < val_p]
    val_df = df[df["period"] == val_p].sample(n=min(200_000, int((df["period"] == val_p).sum())), random_state=1)
    test_df = df[df["period"].isin(test_p)].sample(n=min(300_000, int(df["period"].isin(test_p).sum())), random_state=2)
    print(f"Train {len(train_df):,} | validation {len(val_df):,} | test {len(test_df):,} (held-out months)")

    # 1) Honest evaluation: history tables and model only see the earlier months
    prior = priors(train_df)
    tables = stats_tables(train_df, key_columns(train_df))
    X_train = matrix(train_df, prior, None, hide_weather=WEATHER_HIDDEN_SHARE)
    X_val = matrix(val_df, prior, tables, hide_weather=WEATHER_HIDDEN_SHARE, seed=1)
    dtrain = xgb.DMatrix(X_train, label=train_df["label"].to_numpy())
    del X_train
    booster = xgb.train(PARAMS, dtrain, num_boost_round=1500,
                        evals=[(xgb.DMatrix(X_val, label=val_df["label"].to_numpy()), "val")],
                        early_stopping_rounds=40, verbose_eval=100)
    rounds = booster.best_iteration + 1
    del dtrain

    class_rates = np.bincount(train_df["label"], minlength=3) / len(train_df)
    y = test_df["label"].to_numpy()
    X_test = matrix(test_df, prior, tables)
    metrics = {
        "test_flights": int(len(y)),
        "baseline_accuracy_always_on_time": round(float((y == 0).mean()), 4),
        "without_weather": evaluate(booster, X_test.assign(**{f: np.nan for f in WX_FEATURES}), y, class_rates, rounds),
    }
    has_wx = X_test["o_vis"].notna().to_numpy()
    if has_wx.any():
        metrics["with_weather"] = evaluate(booster, X_test[has_wx], y[has_wx], class_rates, rounds)
        metrics["with_weather"]["flights"] = int(has_wx.sum())
    print(json.dumps(metrics, indent=2))

    # 2) Final model on every month, same number of trees
    print(f"Retraining on all {len(df):,} flights with {rounds} trees...")
    final_prior = priors(df)
    final_tables = stats_tables(df, key_columns(df))
    del booster, X_test, X_val
    dfinal = xgb.DMatrix(matrix(df, final_prior, None, hide_weather=WEATHER_HIDDEN_SHARE), label=df["label"].to_numpy())
    final = xgb.train(PARAMS, dfinal, num_boost_round=rounds)
    del dfinal
    final_prior["median_delay"] = int(df["delay_min"].median())

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    final.save_model(MODEL_FILE)
    with gzip.open(STATS_FILE, "wt", encoding="utf-8") as f:
        json.dump({"prior": final_prior, "tables": final_tables, "routes": route_table(df)},
                  f, separators=(",", ":"))
    wo = metrics["without_weather"]
    meta = {
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "data_source": "US Bureau of Transportation Statistics, Reporting Carrier On-Time Performance",
        "weather_source": "Iowa Environmental Mesonet ASOS archive" if not weather.empty else None,
        "data_months": [ym(p) for p in periods],
        "n_training": int(len(df)),
        "test_months": [ym(p) for p in test_p],
        "trees": int(rounds),
        "class_rates": {"on_time": round(float(class_rates[0]), 4), "delayed": round(float(class_rates[1]), 4),
                        "cancelled": round(float(class_rates[2]), 4)},
        "monthly": monthly_summary(df),
        # Headline numbers are the cautious ones: predictions made without a weather forecast
        "accuracy": wo["accuracy"], "log_loss": wo["log_loss"], "auc_delay_or_cancel": wo["auc_delay_or_cancel"],
        "calibration": wo["calibration"],
        **metrics,
    }
    META_FILE.write_text(json.dumps(meta, indent=2))
    print(f"Saved model to {MODEL_DIR} in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
