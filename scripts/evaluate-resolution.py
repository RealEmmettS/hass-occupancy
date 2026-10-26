"""Compare occupancy resolutions offline, with identical origins and 5-minute truth.

This is an exploratory chronological replay, not a fresh holdout after earlier
experiments have inspected these dates. Keep all inputs and outputs private.
No live configuration, model artifacts, or Home Assistant state is changed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "occupancy-forecast-edge"))
from occupancy_forecast import config, features, train
from occupancy_forecast.sources.ha import StoreSource
from occupancy_forecast.sources.store import HistoryStore

WIDTHS = (30, 15, 10, 5)
HORIZONS = (0.5, 1, 3, 6)


def fit_predict(tr, te, h):
    estimator = Pipeline([
        ("encode", train._encoder()),
        ("model", HistGradientBoostingRegressor(
            max_iter=120, learning_rate=.05, max_leaf_nodes=7,
            min_samples_leaf=75, l2_regularization=10., random_state=0,
            early_stopping=False)),
    ])
    columns = train.features_for(h)
    estimator.fit(tr[columns], tr[f"y_{h}h"] - tr.state_now)
    return np.clip(estimator.predict(te[columns]) + te.state_now, 0., 1.)


def paired_interval(part, width, rng):
    a = part.loc[part.width == width].set_index(["subject", "time", "horizon"])
    b = part.loc[part.width == 30].set_index(["subject", "time", "horizon"])
    diff = ((a.p - a.actual) ** 2 - (b.p - b.actual) ** 2).dropna()
    daily = diff.groupby(diff.index.get_level_values("time").floor("D")).agg(["sum", "count"])
    samples = rng.integers(0, len(daily), size=(5000, len(daily)))
    means = daily["sum"].to_numpy()[samples].sum(axis=1) / daily["count"].to_numpy()[samples].sum(axis=1)
    return np.quantile(means, [.025, .975]).tolist()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--end", required=True)
    parser.add_argument("--first-test", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    config.configure(config.Settings.from_json(args.config.read_text()))
    config.HORIZONS_H = HORIZONS
    source = StoreSource(HistoryStore(args.history), None)
    frames, timings = {}, {}
    for width in WIDTHS:
        config.GRID_MINUTES = width
        config.SLOTS_PER_DAY = 1440 // width
        started = time.perf_counter()
        frame = features.build(source, stop=args.end)
        # Equal training origins isolate resolution, not sixfold duplicate rows.
        frames[width] = frame.loc[frame.time.dt.minute % 30 == 0].set_index(["subject", "time"], drop=False)
        timings[str(width)] = round(time.perf_counter() - started, 2)
        print(f"Built {width}-minute representation in {timings[str(width)]}s", flush=True)

    # Same completed five-minute outcome for EVERY representation.
    truth = frames[5]
    records = []
    first, end = pd.Timestamp(args.first_test), pd.Timestamp(args.end)
    for h in HORIZONS:
        target = f"y_{h}h"
        eligible = truth.dropna(subset=[target, "state_now"]).index
        for frame in frames.values():
            eligible = eligible.intersection(frame.dropna(subset=[target, "state_now"]).index)
        folds = pd.date_range(first, end, freq="7D", inclusive="left")
        for fold, start in enumerate(folds):
            stop = min(start + pd.Timedelta(days=7), end)
            ref = truth.loc[eligible]
            embargo = pd.Timedelta(hours=h) + pd.Timedelta(minutes=30)
            tr_idx = ref.loc[ref.time + embargo < start].index
            te_idx = ref.loc[(ref.time >= start) & (ref.time + embargo <= stop)].index
            if not len(tr_idx) or not len(te_idx):
                continue
            for width, frame in frames.items():
                config.GRID_MINUTES = width
                config.SLOTS_PER_DAY = 1440 // width
                tr, te = frame.loc[tr_idx].reset_index(drop=True), frame.loc[te_idx].reset_index(drop=True)
                part = te[["subject", "time", "state_now"]].copy()
                part["p"] = fit_predict(tr, te, h)
                part["actual"] = (truth.loc[te_idx, target].to_numpy() >= .5).astype(float)
                part["width"], part["horizon"], part["fold"] = width, h, fold
                records.append(part)
            print(f"Scored +{h}h fold {fold}: {len(tr_idx)} train / {len(te_idx)} test", flush=True)

    out = pd.concat(records, ignore_index=True)
    out["error"] = (out.p - out.actual) ** 2
    out["persistence_error"] = (out.state_now - out.actual) ** 2
    out["transition"] = (out.state_now >= .5) != (out.actual >= .5)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(args.output.with_suffix(".parquet"), index=False)
    summaries = []
    for (width, h), part in out.groupby(["width", "horizon"]):
        summaries.append({"width": int(width), "horizon": float(h), "n": len(part),
                          "brier": float(part.error.mean()),
                          "persistence_brier": float(part.persistence_error.mean()),
                          "transitions": int(part.transition.sum()),
                          "transition_brier": float(part.loc[part.transition, "error"].mean())})
    subject = out.groupby(["width", "horizon", "subject"]).error.agg(["mean", "count"]).reset_index()
    intervals = []
    rng = np.random.default_rng(20261026)
    for h, part in out.groupby("horizon"):
        for width in WIDTHS[1:]:
            intervals.append({"horizon": float(h), "width": width,
                              "new_minus_30_ci95": paired_interval(part, width, rng)})
    payload = {"method": "Exploratory chronological replay; identical half-hour origins, common five-minute truth, existing fixed model settings; not physical ground truth or a new independent holdout",
               "first_test": args.first_test, "end": args.end, "build_seconds": timings,
               "scores": summaries, "per_subject": subject.to_dict("records"),
               "paired_day_intervals": intervals}
    args.output.write_text(json.dumps(payload, indent=2))
    print(pd.DataFrame(summaries).pivot(index="width", columns="horizon", values="brier").round(5).to_string())


if __name__ == "__main__":
    main()
