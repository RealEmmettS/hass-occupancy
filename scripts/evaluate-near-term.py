"""Exploratory five-minute near-term replay with recent movement features.

Predefined control and movement arms, fixed model settings, weekly chronological
folds and a conservative embargo. No serving or publication gate is altered.
All observations and results must remain outside the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "occupancy-forecast-edge"))
from occupancy_forecast import baseline, config, eta, features, train
from occupancy_forecast.sources.ha import StoreSource
from occupancy_forecast.sources.store import HistoryStore

MINUTES = (5, 15, 30, 45, 60, 90, 120)
EXTRA = ("latest_home", "distance_delta_5m", "distance_delta_15m", "closing_kmh_15m")


def add_movement(frame, source, start, stop):
    for subject in config.SUBJECTS:
        selected = frame.subject == subject.slug
        slots = pd.DatetimeIndex(frame.loc[selected, "time"])
        events = features.presence_events(source, subject, start, stop)
        pairs = [(t, np.nan if v in (None, "unknown", "unavailable")
                  else float(v == config.HOME_STATE)) for t, v in events]
        frame.loc[selected, "latest_home"] = features.numeric_on_grid(pairs, slots, None)
        if not subject.is_person:
            continue
        trace = eta.traces(source, subject.slug, start, stop)
        if trace.empty:
            continue
        travel = eta.feature_frame(trace)
        distance = [(t.isoformat(), v) for t, v in trace.distance_km.items()]
        here = features.numeric_on_grid(distance, slots, None)
        for minutes in (5, 15):
            before = features.numeric_on_grid(distance, slots - pd.Timedelta(minutes=minutes), None)
            frame.loc[selected, f"distance_delta_{minutes}m"] = here - before
        speed = [(t.isoformat(), v) for t, v in travel.closing_kmh.items()]
        frame.loc[selected, "closing_kmh_15m"] = features.numeric_on_grid(speed, slots, 15)
    for column in EXTRA:
        if column not in frame:
            frame[column] = np.nan
    return frame


def main():
    parser = argparse.ArgumentParser(__doc__)
    for arg in ("config", "history", "output"):
        parser.add_argument("--" + arg, required=True, type=Path)
    parser.add_argument("--end", required=True)
    parser.add_argument("--first-test", required=True)
    args = parser.parse_args()
    config.configure(config.Settings.from_json(args.config.read_text()))
    config.GRID_MINUTES, config.SLOTS_PER_DAY = 5, 288
    config.HORIZONS_H = tuple(m / 60 for m in MINUTES)
    source = StoreSource(HistoryStore(args.history), None)
    start = features.history_start(source)
    frame = add_movement(features.build(source, stop=args.end), source, start, args.end)
    # Do not manufacture extra independent examples from more frequent rows.
    frame = frame.loc[frame.time.dt.minute % 30 == 0].reset_index(drop=True)
    records = []
    end = pd.Timestamp(args.end)
    for minutes in MINUTES:
        h = minutes / 60
        target = f"y_{h}h"
        available = frame.dropna(subset=["state_now", target])
        for fold, cutoff in enumerate(pd.date_range(args.first_test, end, freq="7D", inclusive="left")):
            stop = min(cutoff + pd.Timedelta(days=7), end)
            embargo = pd.Timedelta(minutes=minutes + 30)
            tr = available.loc[available.time + embargo < cutoff]
            te = available.loc[(available.time >= cutoff) & (available.time + embargo <= stop)]
            if tr.empty or te.empty:
                continue
            references = {name: predictor(tr, te)
                          for name, predictor in baseline.predictors(h).items()}
            for arm in ("control", "movement"):
                columns = train.features_for(h) + (list(EXTRA) if arm == "movement" else [])
                model = Pipeline([
                    ("encode", train._encoder()),
                    ("model", HistGradientBoostingRegressor(
                        max_iter=120, learning_rate=.05, max_leaf_nodes=7,
                        min_samples_leaf=75, l2_regularization=10., random_state=0,
                        early_stopping=False))])
                model.fit(tr[columns], tr[target] - tr.state_now)
                part = te[["subject", "time", "state_now", "latest_home"]].copy()
                part["p"] = np.clip(model.predict(te[columns]) + te.state_now, 0., 1.)
                part["actual"] = (te[target] >= .5).astype(float)
                for name, prediction in references.items():
                    part["baseline_" + name] = prediction
                part["arm"], part["minutes"], part["fold"] = arm, minutes, fold
                records.append(part)
            print(f"Scored +{minutes}m fold {fold}: {len(tr)} / {len(te)} rows", flush=True)
    out = pd.concat(records, ignore_index=True)
    out["error"] = (out.p - out.actual) ** 2
    out["persistence_error"] = (out.state_now - out.actual) ** 2
    out["instant_persistence_error"] = (out.latest_home - out.actual) ** 2
    out["transition"] = (out.state_now >= .5) != (out.actual >= .5)
    out["arrival"] = out.transition & (out.actual >= .5)
    out["departure"] = out.transition & (out.actual < .5)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(args.output.with_suffix(".parquet"), index=False)
    summary = []
    for (arm, minutes), part in out.groupby(["arm", "minutes"]):
        reference_errors = {c.removeprefix("baseline_"): float(((part[c] - part.actual) ** 2).mean())
                            for c in part if c.startswith("baseline_")}
        reference_errors["instant_persistence"] = float(part.instant_persistence_error.mean())
        best = min(reference_errors, key=reference_errors.get)
        summary.append({"arm": arm, "minutes": int(minutes), "n": len(part),
                        "brier": float(part.error.mean()),
                        "best_baseline": best, "best_baseline_brier": reference_errors[best],
                        "skill_vs_best_pct": 100 * (1 - float(part.error.mean()) / reference_errors[best]),
                        "persistence_brier": float(part.persistence_error.mean()),
                        "instant_persistence_brier": float(part.instant_persistence_error.mean()),
                        **{kind + "_brier": float(part.loc[part[kind], "error"].mean())
                           for kind in ("transition", "arrival", "departure")},
                        "transitions": int(part.transition.sum())})
    args.output.write_text(json.dumps({"method": "Exploratory retrospective replay, not a new untouched holdout; equal half-hour origins and five-minute targets", "scores": summary}, indent=2))
    print(pd.DataFrame(summary).pivot(index="arm", columns="minutes", values="brier").round(5).to_string())


if __name__ == "__main__":
    main()
