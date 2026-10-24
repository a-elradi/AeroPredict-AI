"""
Train the AeroPredict delay model on real US flight records.

Data: US Bureau of Transportation Statistics "Reporting Carrier On-Time
Performance" monthly files (free):
https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_YYYY_M.zip

    python scripts/train_delay_model.py /path/to/folder/with/zips [sample_fraction]

The two most recent months are held out as a test set (the model never sees
them), so the reported scores show how it does on future flights. The final
model is then retrained on all months and saved to models/.
"""
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
    FEATURES, MODEL_DIR, MODEL_FILE, STATS_FILE, META_FILE, STAT_KEYS, SMOOTHING, build_features,
)

COLUMNS = ["Year", "Month", "DayofMonth", "DayOfWeek", "Reporting_Airline",
           "Flight_Number_Reporting_Airline", "Origin", "Dest", "CRSDepTime", "CRSArrTime",
           "ArrDelay", "ArrDel15", "Cancelled", "Diverted", "Distance"]
PARAMS = {
    "objective": "multi:softprob", "num_class": 3, "eval_metric": "mlogloss",
    "tree_method": "hist", "max_depth": 8, "eta": 0.08, "subsample": 0.8,
    "colsample_bytree": 0.8, "min_child_weight": 50, "nthread": 4,
}


SAMPLE = 1.0


def load(folder: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(folder.glob("*.zip")):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".csv"))
            with z.open(name) as f:
                df = pd.read_csv(f, usecols=COLUMNS, low_memory=False)
        frames.append(df)
        print(f"  {path.name}: {len(df):,} flights")
    df = pd.concat(frames, ignore_index=True)
    if SAMPLE < 1:
        df = df.sample(frac=SAMPLE, random_state=0)  # quick trial runs only
    df = df.dropna(subset=["Origin", "Dest", "CRSDepTime", "Reporting_Airline"])
    df["label"] = np.where(df["Cancelled"] == 1, 2,
                           np.where((df["ArrDel15"] == 1) | (df["Diverted"] == 1), 1, 0))
    df["period"] = df["Year"] * 12 + df["Month"]
    df["dep_hour"] = (df["CRSDepTime"] // 100).clip(0, 23).astype(int)
    df["arr_hour"] = (df["CRSArrTime"].fillna(df["CRSDepTime"]) // 100).clip(0, 23).astype(int) % 24
    df["carrier"] = df["Reporting_Airline"].astype(str)
    df["flight_no"] = df["Flight_Number_Reporting_Airline"].astype(int).astype(str)
    df["delay_min"] = np.where(df["label"] == 1, df["ArrDelay"], np.nan)
    return df


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


def monthly_summary(df: pd.DataFrame) -> list[dict]:
    """Share of flights delayed and cancelled in each calendar month."""
    g = df.groupby("Month")["label"]
    return [{"month": int(m), "flights": int(len(s)),
             "delayed_pct": round(float((s == 1).mean() * 100), 1),
             "cancelled_pct": round(float((s == 2).mean() * 100), 2)} for m, s in g]


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


def out_of_month_stats(df: pd.DataFrame, keys: dict, prior: dict) -> pd.DataFrame:
    """For training rows: key stats computed without the row's own month (no leakage)."""
    out = {}
    is_delay = (df["label"] == 1).astype(np.int32)
    is_cancel = (df["label"] == 2).astype(np.int32)
    for name in STAT_KEYS:
        g = pd.DataFrame({"k": keys[name], "p": df["period"], "d": is_delay, "c": is_cancel})
        tot = g.groupby("k")[["d", "c"]].agg(["sum", "count"])
        tot.columns = ["d_sum", "n", "c_sum", "n2"]
        mon = g.groupby(["k", "p"])[["d", "c"]].agg(["sum", "count"])
        mon.columns = ["d_sum_m", "n_m", "c_sum_m", "n2_m"]
        j = g[["k", "p"]].join(tot, on="k").join(mon, on=["k", "p"])
        n = j["n"] - j["n_m"]
        out[f"{name}_n"] = np.log1p(n.values)
        out[f"{name}_delay"] = ((j["d_sum"] - j["d_sum_m"] + SMOOTHING * prior["delay"]) / (n + SMOOTHING)).values
        out[f"{name}_cancel"] = ((j["c_sum"] - j["c_sum_m"] + SMOOTHING * prior["cancel"]) / (n + SMOOTHING)).values
    return pd.DataFrame(out, index=df.index)


def matrix(df: pd.DataFrame, stat_feats: pd.DataFrame) -> pd.DataFrame:
    base = pd.DataFrame({
        "month": df["Month"].values, "day_of_week": df["DayOfWeek"].values,
        "dep_hour": df["dep_hour"].values, "arr_hour": df["arr_hour"].values,
        "distance": df["Distance"].values,
    }, index=df.index)
    return pd.concat([base, stat_feats], axis=1)[FEATURES]


def train(df: pd.DataFrame, rounds: int | None = None, evals_df: pd.DataFrame | None = None):
    keys = key_columns(df)
    prior = {"delay": float((df["label"] == 1).mean()), "cancel": float((df["label"] == 2).mean())}
    X = matrix(df, out_of_month_stats(df, keys, prior))
    dtrain = xgb.DMatrix(X, label=df["label"].values)
    watch = [(dtrain, "train")]
    if evals_df is not None:
        dval = xgb.DMatrix(evals_df[0], label=evals_df[1])
        watch.append((dval, "val"))
    booster = xgb.train(PARAMS, dtrain, num_boost_round=rounds or 600, evals=watch,
                        early_stopping_rounds=30 if evals_df is not None else None, verbose_eval=50)
    return booster, stats_tables(df, keys), prior


def features_with_tables(df: pd.DataFrame, tables: dict, prior: dict) -> pd.DataFrame:
    rows = [build_features(
        tables, prior, carrier=r.carrier, flight_no=r.flight_no, origin=r.Origin, dest=r.Dest,
        month=int(r.Month), day_of_week=int(r.DayOfWeek), dep_hour=int(r.dep_hour),
        arr_hour=int(r.arr_hour), distance=float(r.Distance))
        for r in df.itertuples(index=False)]
    return pd.DataFrame(rows)[FEATURES]


def main():
    global SAMPLE
    folder = Path(sys.argv[1])
    if len(sys.argv) > 2:
        SAMPLE = float(sys.argv[2])
    started = time.time()
    print("Loading flights...")
    df = load(folder)
    periods = sorted(df["period"].unique())
    test_p, val_p = periods[-2:], periods[-3]
    train_df = df[df["period"] < val_p]
    val_df = df[df["period"] == val_p].sample(n=min(200_000, (df["period"] == val_p).sum()), random_state=1)
    test_df = df[df["period"].isin(test_p)].sample(n=min(300_000, df["period"].isin(test_p).sum()), random_state=2)
    print(f"Train {len(train_df):,} | validation {len(val_df):,} | test {len(test_df):,} (held-out months)")

    # 1) Honest evaluation: model and stats only see the earlier months
    keys = key_columns(train_df)
    prior = {"delay": float((train_df["label"] == 1).mean()), "cancel": float((train_df["label"] == 2).mean())}
    tables = stats_tables(train_df, keys)
    X_val = features_with_tables(val_df, tables, prior)
    booster, _, _ = train(train_df, evals_df=(X_val, val_df["label"].values))
    best_rounds = booster.best_iteration + 1
    X_test = features_with_tables(test_df, tables, prior)
    proba = booster.predict(xgb.DMatrix(X_test), iteration_range=(0, best_rounds))
    y = test_df["label"].values

    class_rates = np.bincount(train_df["label"], minlength=3) / len(train_df)
    baseline = np.tile(class_rates, (len(y), 1))
    disrupted = (y > 0).astype(int)
    p_disrupted = proba[:, 1] + proba[:, 2]
    metrics = {
        "test_flights": int(len(y)),
        "accuracy": round(float(accuracy_score(y, proba.argmax(1))), 4),
        "baseline_accuracy_always_on_time": round(float((y == 0).mean()), 4),
        "log_loss": round(float(log_loss(y, proba, labels=[0, 1, 2])), 4),
        "baseline_log_loss": round(float(log_loss(y, baseline, labels=[0, 1, 2])), 4),
        "auc_delay_or_cancel": round(float(roc_auc_score(disrupted, p_disrupted)), 4),
        "calibration": [],
    }
    bins = np.clip((p_disrupted * 10).astype(int), 0, 9)
    for b in range(10):
        m = bins == b
        if m.sum() >= 500:
            metrics["calibration"].append({
                "predicted": f"{b * 10}-{b * 10 + 10}%", "flights": int(m.sum()),
                "actual_disrupted_pct": round(float(disrupted[m].mean() * 100), 1)})
    print(json.dumps(metrics, indent=2))

    # 2) Final model on every month, same number of trees
    print(f"Retraining on all {len(df):,} flights with {best_rounds} trees...")
    final, final_tables, final_prior = train(df, rounds=best_rounds)
    final_prior["median_delay"] = int(df["delay_min"].median())

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    final.save_model(MODEL_FILE)
    with gzip.open(STATS_FILE, "wt", encoding="utf-8") as f:
        json.dump({"prior": final_prior, "tables": final_tables, "routes": route_table(df)},
                  f, separators=(",", ":"))
    meta = {
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "data_source": "US Bureau of Transportation Statistics, Reporting Carrier On-Time Performance",
        "data_months": [f"{p // 12 if p % 12 else p // 12 - 1}-{(p % 12) or 12:02d}" for p in periods],
        "n_training": int(len(df)),
        "test_months": [f"{p // 12 if p % 12 else p // 12 - 1}-{(p % 12) or 12:02d}" for p in test_p],
        "trees": int(best_rounds),
        "class_rates": {"on_time": round(float(class_rates[0]), 4), "delayed": round(float(class_rates[1]), 4),
                        "cancelled": round(float(class_rates[2]), 4)},
        "monthly": monthly_summary(df),
        **metrics,
    }
    META_FILE.write_text(json.dumps(meta, indent=2))
    print(f"Saved model to {MODEL_DIR} in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
