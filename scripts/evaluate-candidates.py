"""Offline chronological model comparison. Never writes to Home Assistant.

Keep configuration, data and output outside the repository. First run validation;
freeze one candidate before running holdout. Features must come from the same
snapshot. The legacy table is optional and is used only to fit the old model;
all candidates are scored on causal inputs available at the issue time.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "occupancy-forecast-edge"))
from occupancy_forecast import baseline, config, evaluate, features, train

HORIZONS = (1, 3, 6, 12, 24, 48)
# Small, declared search. No random validation or automatic early stopping on
# correlated rows. Candidate selection sees only the earlier validation period.
CANDIDATES = {
    "causal_current": dict(iterations=200, leaves=15, leaf=50, l2=1., binary=False),
    "regularized": dict(iterations=120, leaves=7, leaf=75, l2=10., binary=False),
    "regularized_binary": dict(iterations=120, leaves=7, leaf=75, l2=10., binary=True),
    "medium_binary": dict(iterations=160, leaves=15, leaf=75, l2=10., binary=True),
    "recent_binary": dict(iterations=120, leaves=7, leaf=75, l2=10., binary=True, half_life=21),
}


def fit_predict(tr, te, h, params):
    model = Pipeline([
        ("encode", train._encoder()),
        ("model", HistGradientBoostingRegressor(
            max_iter=params["iterations"], learning_rate=.05,
            max_leaf_nodes=params["leaves"], min_samples_leaf=params["leaf"],
            l2_regularization=params["l2"], random_state=0, early_stopping=False)),
    ])
    y = tr[f"y_{h}h"]
    if params["binary"]:
        y = (y >= evaluate.HOME_THRESHOLD).astype(float)
    kwargs = {}
    if params.get("half_life"):
        age = (tr.time.max() - tr.time).dt.total_seconds() / 86400
        kwargs["model__sample_weight"] = np.exp2(-age / params["half_life"]).to_numpy()
    model.fit(tr[train.features_for(h)], y - tr.state_now, **kwargs)
    return np.clip(model.predict(te[train.features_for(h)]) + te.state_now.to_numpy(), 0, 1)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--legacy-features", type=Path)
    parser.add_argument("--cutoff", required=True, help="First holdout origin, ISO UTC")
    parser.add_argument("--end", required=True, help="Last observed target end, ISO UTC")
    parser.add_argument("--phase", choices=("validation", "holdout"), default="validation")
    parser.add_argument("--candidate", choices=tuple(CANDIDATES))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "holdout" and not args.candidate:
        parser.error("Freeze --candidate before opening the holdout")
    config.configure(config.Settings.from_json(args.config.read_text()))
    wide = pd.read_parquet(args.features).set_index(["subject", "time"], drop=False)
    legacy = pd.read_parquet(args.legacy_features).set_index(["subject", "time"], drop=False) if args.legacy_features else None
    cutoff, end = pd.Timestamp(args.cutoff), pd.Timestamp(args.end)
    candidates = ({"causal_current": CANDIDATES["causal_current"],
                   args.candidate: CANDIDATES[args.candidate]} if args.candidate else CANDIDATES)
    starts = [cutoff - pd.Timedelta(days=d) for d in (21, 14, 7)] if args.phase == "validation" else [cutoff]
    records = []
    for h in HORIZONS:
        target = f"y_{h}h"
        frame = wide.dropna(subset=["state_now", "coverage", target])
        for fold, start in enumerate(starts):
            stop = min(start + pd.Timedelta(days=7), cutoff) if args.phase == "validation" else end
            tr = frame.loc[frame.time + evaluate.embargo_for(h) < start].reset_index(drop=True)
            # No label straddles the reserved period or the observed snapshot.
            te = frame.loc[(frame.time >= start) & (frame.time < stop)
                           & (frame.time + evaluate.embargo_for(h) <= stop)].reset_index(drop=True)
            if tr.empty or te.empty:
                continue
            preds = {name: fit_predict(tr, te, h, params) for name, params in candidates.items()}
            if legacy is not None and h < 24:
                old = legacy.loc[(legacy.time + evaluate.embargo_for(h) < start)].dropna(subset=["state_now", "coverage", target]).reset_index(drop=True)
                # The old live path used the 5-minute state, but raw distance
                # units. Match that input contract without future transitions.
                old_test = te.copy()
                for subject in config.PEOPLE:
                    mask = old_test.subject == subject.slug
                    scale = config.distance_scale(subject.distance_entity or "")
                    old_test.loc[mask, ["distance_km", "distance_delta_30m", "distance_delta_60m"]] /= scale
                # Legacy +24/+48 used unfinished-slot lags and are excluded:
                # reconstructing those from completed history would leak.
                preds["legacy"] = fit_predict(old, old_test, h, CANDIDATES["causal_current"])
            for name, predictor in baseline.predictors(h).items():
                preds["baseline_" + name] = predictor(tr, te)
            preds["same_as_now"] = te.state_now.to_numpy()
            for name, p in preds.items():
                part = te[["subject", "time", target]].copy()
                part = part.rename(columns={target: "actual"})
                part["p"] = p
                part["candidate"], part["horizon"], part["fold"] = name, h, fold
                part["state_now"] = te.state_now.to_numpy()
                records.append(part)
            print(f"{args.phase}: +{h}h fold {fold}, {len(tr)} training / {len(te)} test rows", flush=True)
    out = pd.concat(records, ignore_index=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(args.output.with_suffix(".parquet"), index=False)
    summary = []
    for (candidate, h), part in out.groupby(["candidate", "horizon"]):
        summary.append({"candidate": candidate, "horizon": int(h), **asdict(evaluate.score(part.actual, part.p))})
    payload = {"phase": args.phase, "cutoff": str(cutoff), "end": str(end),
               "candidate": args.candidate, "definitions": "Brier on completed binary occupancy targets; lower is better; no random splitting",
               "scores": summary}
    args.output.write_text(json.dumps(payload, indent=2))
    print(pd.DataFrame(summary).pivot(index="candidate", columns="horizon", values="brier").round(5).to_string())


if __name__ == "__main__":
    main()
