"""Generate synthetic requests, fit Poisson regression, and evaluate a later holdout."""

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from random import Random

from fixtures.alandi import ZONES

IST = timezone(timedelta(hours=5, minutes=30))
START = datetime(2025, 1, 6, tzinfo=IST)
SEED = 241125
WEEKS = 12
TRAIN_WEEKS = 10
MODEL_FILE = Path(__file__).with_name("model.json")


def feature_ids(zone, hour, weekday):
    return (0, 1 + zone, 4 + hour, 28 + weekday)


def poisson(mu, rng):
    limit, draw, count = math.exp(-mu), rng.random(), 0
    while draw > limit:
        count += 1
        draw *= rng.random()
    return count


def generate_history(hours):
    rng = Random(SEED)
    for hour in hours:
        for zone, name in enumerate(ZONES):
            commute = int(7 <= hour.hour <= 10 or 16 <= hour.hour <= 19)
            weekday = hour.weekday() < 5
            evening = int(18 <= hour.hour < 23)
            mu = math.exp((-1.77, -.82, -1.31)[zone] + .57 * commute + .88 * weekday + .29 * evening)
            for _ in range(poisson(mu, rng)):
                yield name, (hour + timedelta(seconds=rng.randrange(3600))).astimezone(UTC)


def rows_from_history(hours, history):
    counts = Counter((zone, when.astimezone(IST).replace(minute=0, second=0, microsecond=0))
                     for zone, when in history)
    return [
        (feature_ids(i, hour.hour, hour.weekday()), counts[zone, hour])
        for hour in hours for i, zone in enumerate(ZONES)
    ]


def fit(rows):
    weights = [0.0] * 35
    for _ in range(650):
        gradient = [0.0] * len(weights)
        for ids, count in rows:
            error = math.exp(sum(weights[i] for i in ids)) - count
            for i in ids:
                gradient[i] += error
        for i, value in enumerate(gradient):
            penalty = .001 * weights[i] if i else 0
            weights[i] -= 1.1 * (value / len(rows) + penalty)
    return [round(weight, 8) for weight in weights]


def evaluate(training, holdout, weights):
    hourly = defaultdict(list)
    for ids, count in training:
        hourly[ids[1:3]].append(count)
    average = {key: sum(values) / len(values) for key, values in hourly.items()}
    mae = sum(abs(count - math.exp(sum(weights[i] for i in ids)))
              for ids, count in holdout) / len(holdout)
    baseline = sum(abs(count - average[ids[1:3]]) for ids, count in holdout) / len(holdout)
    return mae, baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=MODEL_FILE)
    parser.add_argument("--history", type=Path, help="Optional JSONL export of the synthetic incidents")
    args = parser.parse_args()
    hours = [START + timedelta(hours=i) for i in range(WEEKS * 7 * 24)]
    history = list(generate_history(hours))
    if args.history:
        with args.history.open("w", encoding="utf-8") as file:
            for zone, timestamp in history:
                file.write(json.dumps({"zone": zone, "requested_at_utc": timestamp.isoformat()}) + "\n")
    rows = rows_from_history(hours, history)
    cut = TRAIN_WEEKS * 7 * 24 * len(ZONES)
    training, holdout = rows[:cut], rows[cut:]
    weights = fit(training)
    mae, baseline = evaluate(training, holdout, weights)
    artifact = {
        "version": "alandi-poisson-1", "provenance": "trained on synthetic data",
        "seed": SEED, "timezone": "Asia/Kolkata", "zones": ZONES, "weights": weights,
        "training_start": START.isoformat(), "holdout_start": (START + timedelta(weeks=TRAIN_WEEKS)).isoformat(),
        "holdout_end": (START + timedelta(weeks=WEEKS)).isoformat(),
        "training_rows": len(training), "holdout_rows": len(holdout),
        "synthetic_incidents": len(history), "zero_count_rows": sum(count == 0 for _, count in rows),
        "baseline": "Training-only historical average for each zone and hour of day",
        "mae": round(mae, 6), "baseline_mae": round(baseline, 6),
    }
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"{artifact['version']} · seed {SEED} · {len(history)} synthetic incidents")
    print(f"{len(training)} training rows · {len(holdout)} later holdout rows · {artifact['zero_count_rows']} zero-count rows")
    print(f"Poisson MAE {mae:.6f} · historical-average MAE {baseline:.6f}")


if __name__ == "__main__":
    main()
