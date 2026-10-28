"""
How accurate are predictions for flights outside the US data?

Builds route history from all but the last months of Brazil's flight-by-flight
ANAC data, then predicts every flight in the held-out months with the live
prediction code and compares with what actually happened (on time vs 15+ min
late or cancelled). Two comparisons: the old behaviour for these flights (no
route data: a general estimate) and simply guessing the overall average.

    python scripts/backtest_route_history.py [--months 12] [--test 2] [--sample 40000]
    python scripts/backtest_route_history.py --files TRAINING_DATA/flights --prefix korea
        (any country's monthly files from scripts/download_training_data.py; reports only,
         the calibration is fitted on Brazil)

It also fits a calibration (logistic on the log-odds) on a random half of the
held-out flights and scores it on the other half; the live code applies it to
route-history predictions.

Writes models/route_history_backtest.json and models/route_history_calibration.json.
"""
import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_route_stats as brs  # noqa: E402
from app.services import delay_model, route_history  # noqa: E402

OUT = ROOT.parent / "models" / "route_history_backtest.json"
CALIBRATION = ROOT.parent / "models" / "route_history_calibration.json"


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def fit_calibration(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    """a, b so that sigmoid(a + b * logit(p)) matches outcomes (Newton's method)."""
    x = np.column_stack([np.ones(len(p)), logit(p)])
    w = np.array([0.0, 1.0])
    for _ in range(50):
        q = 1 / (1 + np.exp(-x @ w))
        grad = x.T @ (y - q)
        hess = -(x.T * (q * (1 - q))) @ x
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return float(w[0]), float(w[1])


def auc(y: np.ndarray, p: np.ndarray) -> float:
    order = np.argsort(p)
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    # average ranks for ties
    for value in np.unique(p):
        idx = p == value
        if idx.sum() > 1:
            ranks[idx] = ranks[idx].mean()
    pos = y.sum()
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * (len(y) - pos)))


def scores(y: np.ndarray, p: np.ndarray) -> dict:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return {"auc": round(auc(y, p), 3), "brier": round(float(np.mean((p - y) ** 2)), 4),
            "log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4)}


def calibration(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    out = []
    for i in range(bins):
        idx = (p >= edges[i]) & (p <= edges[i + 1]) if i == bins - 1 else (p >= edges[i]) & (p < edges[i + 1])
        if idx.sum():
            out.append({"predicted": round(float(p[idx].mean()), 3), "actual": round(float(y[idx].mean()), 3),
                        "flights": int(idx.sum())})
    return out


def predict_all(flights: list) -> tuple[np.ndarray, list]:
    probs, methods = [], []
    for airline, number, origin, dest, _, sched_dep, _ in flights:
        r = delay_model.predict(airline, number or "", origin, dest, datetime.fromisoformat(sched_dep))
        probs.append(r["proba"][1] + r["proba"][2])
        rh = r.get("route_history")
        methods.append(rh["level"] if rh else r["method"])
    return np.array(probs), methods


def load_files(folder: Path, prefix: str) -> list[tuple[tuple[int, int], list]]:
    """Monthly training files (US file layout) -> [((y, m), [flight tuples like brazil_month's])]."""
    import csv
    import io
    import zipfile
    out = []
    for path in sorted(folder.glob(f"{prefix}_*.zip"), key=lambda p: tuple(int(x) for x in p.stem.split("_")[1:])):
        y, m = (int(x) for x in path.stem.split("_")[1:])
        rows = []
        with zipfile.ZipFile(path) as z:
            reader = csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8"))
            for r in reader:
                t = int(r["CRSDepTime"])
                sched = f"{r['FlightDate']} {t // 100:02d}:{t % 100:02d}:00"
                delay = None if r["Cancelled"] == "1" else float(r["ArrDelay"] or 0)
                if r["ArrDel15"] == "1" and delay is not None:
                    delay = max(delay, 16.0)
                rows.append((r["Reporting_Airline"], r["Flight_Number_Reporting_Airline"], r["Origin"], r["Dest"],
                             False, sched, delay))
        out.append(((y, m), rows))
    return out


def use_history(data: dict) -> None:
    route_history._load = lambda: data
    route_history._airport_average.cache_clear()
    delay_model._load()["calibration"] = None  # measure the uncorrected predictions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--test", type=int, default=2)
    ap.add_argument("--sample", type=int, default=40000)
    ap.add_argument("--files", type=Path, help="training-data folder with <prefix>_YYYY_M.zip files")
    ap.add_argument("--prefix", default="brazil")
    args = ap.parse_args()

    stats, unmatched, test = brs.Stats(), defaultdict(int), []
    stats.source = args.prefix
    if args.files:
        months = load_files(args.files, args.prefix)[-args.months:]
        periods = [p for p, _ in months]
        train_periods, test_periods = periods[:-args.test], periods[-args.test:]
        for (y, m), rows in months:
            if (y, m) in train_periods:
                brs.add_brazil(stats, [r for r in rows if r[6] is not None], [r for r in rows if r[6] is None])
            else:
                test += [r for r in rows if r[0] and r[5]]
    else:
        ref = brs.Ref()
        periods = brs.month_list(args.months, brs.latest_anac_month())
        train_periods, test_periods = periods[:-args.test], periods[-args.test:]
        for y, m in periods:
            flown, cancelled = brs.brazil_month(ref, y, m, unmatched)
            if (y, m) in train_periods:
                brs.add_brazil(stats, flown, cancelled)
            else:
                test += [f for f in flown + cancelled if f[0] and f[5]]
            print(f"  Brazil {y}-{m:02d}: {'train' if (y, m) in train_periods else 'test'}", flush=True)

    random.seed(42)
    test = random.sample(test, min(args.sample, len(test)))
    y = np.array([1 if f[6] is None or f[6] > 15 else 0 for f in test])
    fmt = lambda p: f"{p[0][0]}-{p[0][1]:02d} to {p[-1][0]}-{p[-1][1]:02d}"
    history = {"sources": {args.prefix: {"name": f"{args.prefix} official flight records", "period": fmt(train_periods)}},
               **stats.export(), "airline_sources": {a: sorted(s) for a, s in stats.airline_sources.items()}}

    use_history(history)
    p_new, methods = predict_all(test)
    use_history({})
    p_old, _ = predict_all(test)
    p_avg = np.full(len(y), y.mean())

    rng = np.random.default_rng(0)
    half = rng.random(len(y)) < 0.5
    a, b = fit_calibration(y[half], p_new[half])
    p_cal = 1 / (1 + np.exp(-(a + b * logit(p_new))))
    if not args.files:  # the live calibration is fitted on Brazil only
        CALIBRATION.write_text(json.dumps({"a": round(a, 4), "b": round(b, 4), "fitted_on": "Brazil " + fmt(test_periods),
                                           "flights": int(half.sum())}, indent=1))

    by_level = {}
    for level in sorted(set(methods)):
        idx = np.array([m == level for m in methods])
        if idx.sum() >= 200:
            by_level[level] = {"flights": int(idx.sum()), "actual_late": round(float(y[idx].mean()), 3),
                               **scores(y[idx], p_new[idx])}
    report = {
        "country": args.prefix.capitalize(), "source": "official flight records, every flight",
        "history_months": fmt(train_periods),
        "test_months": fmt(test_periods), "test_flights": int(len(y)), "actual_late_share": round(float(y.mean()), 3),
        "with_route_history": scores(y, p_new), "before_route_history": scores(y, p_old),
        "overall_average_guess": scores(y, p_avg), "by_data_level": by_level,
        "calibration": calibration(y, p_new),
        "recalibration": {"a": round(a, 4), "b": round(b, 4),
                          "held_out_half_before": scores(y[~half], p_new[~half]),
                          "held_out_half_after": scores(y[~half], p_cal[~half]),
                          "calibration_after": calibration(y[~half], p_cal[~half])},
        "distinct_predictions_before": int(len(np.unique(np.round(p_old, 3)))),
        "distinct_predictions_with": int(len(np.unique(np.round(p_new, 3)))),
    }
    (OUT if not args.files else OUT.with_name(f"route_history_backtest_{args.prefix}.json")).write_text(
        json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
