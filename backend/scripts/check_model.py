"""
Quality check for a newly trained delay model, run before it is published.

    python scripts/check_model.py NEW_MODEL_DIR [--current models/model_meta.json]

Exits with an error (so the retraining workflow stops and the live model is
kept) unless the new model, on its held-out test months:
- clearly beats simply predicting the average rates (log loss),
- ranks risky flights above safe ones well enough (AUC),
- gives odds that match what actually happened (calibration),
- was trained on enough flights, and
- is not much worse than the model currently live.
"""
import argparse
import json
import sys
from pathlib import Path

MIN_LOG_LOSS_GAIN = 0.03      # vs. always predicting the average rates
MIN_AUC = 0.65
MAX_AUC_DROP = 0.02           # vs. the live model (test months differ, so allow some noise)
MAX_CALIBRATION_GAP = 12.0    # percentage points, for buckets with enough flights
MIN_BUCKET_FLIGHTS = 5000
MIN_TRAINING_FLIGHTS = 8_000_000


def check(new: dict, current: dict | None) -> list[str]:
    problems = []
    wo = new["without_weather"]
    if wo["log_loss"] > wo["baseline_log_loss"] - MIN_LOG_LOSS_GAIN:
        problems.append(f"log loss {wo['log_loss']} is not clearly better than the baseline {wo['baseline_log_loss']}")
    if wo["auc_delay_or_cancel"] < MIN_AUC:
        problems.append(f"AUC {wo['auc_delay_or_cancel']} is below {MIN_AUC}")
    for b in wo["calibration"]:
        if b["flights"] < MIN_BUCKET_FLIGHTS:
            continue
        low, high = (float(x) for x in b["predicted"].rstrip("%").split("-"))
        gap = abs(b["actual_disrupted_pct"] - (low + high) / 2)
        if gap > MAX_CALIBRATION_GAP:
            problems.append(f"flights predicted {b['predicted']} were disrupted {b['actual_disrupted_pct']}% of the time")
    if new["n_training"] < MIN_TRAINING_FLIGHTS:
        problems.append(f"only {new['n_training']:,} training flights")
    if current and current.get("auc_delay_or_cancel"):
        if wo["auc_delay_or_cancel"] < current["auc_delay_or_cancel"] - MAX_AUC_DROP:
            problems.append(f"AUC {wo['auc_delay_or_cancel']} is much lower than the live model's "
                            f"{current['auc_delay_or_cancel']}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("new_dir", type=Path)
    ap.add_argument("--current", type=Path)
    args = ap.parse_args()
    new = json.loads((args.new_dir / "model_meta.json").read_text())
    current = json.loads(args.current.read_text()) if args.current and args.current.exists() else None

    wo = new["without_weather"]
    print(f"Trained on {new['n_training']:,} flights ({new['data_months'][0]} to {new['data_months'][-1]})")
    print(f"Test months {', '.join(new['test_months'])}: AUC {wo['auc_delay_or_cancel']}, "
          f"log loss {wo['log_loss']} (baseline {wo['baseline_log_loss']})")
    if current:
        print(f"Live model: AUC {current.get('auc_delay_or_cancel')}, log loss {current.get('log_loss')}, "
              f"test months {', '.join(current.get('test_months', []))}")
    problems = check(new, current)
    for p in problems:
        print(f"FAILED: {p}")
    if problems:
        sys.exit(1)
    print("PASSED: the new model can be published")


if __name__ == "__main__":
    main()
