"""A separately qualified two-hour forecast of five-minute presence windows.

The hourly forecast is unchanged. Fits use half-hour origins; chronological
validation uses every five-minute phase and gates each subject and lead against
the strongest baseline. Issued revisions are retained, including abstentions.
"""
from __future__ import annotations

import hashlib
import json
import pickle
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from . import baseline, config, eta, evaluate, features, log, train

MINUTES = (15, 30, 45, 60, 75, 90, 105, 120)
HORIZONS = tuple(m / 60 for m in MINUTES)
WINDOW = 5
VERSION = "near-term-1"
EXTRA = ("latest_home", "distance_delta_5m", "distance_delta_15m", "closing_kmh_15m")
_log = log.get(__name__)


def identity() -> str:
    payload = {"version": VERSION, "subjects": [asdict(s) for s in config.SUBJECTS],
               "zones": [asdict(z) for z in config.ZONES], "timezone": config.TIMEZONE,
               "units": config.SETTINGS.units if config.SETTINGS else {},
               "country": config.HOLIDAY_COUNTRY}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def add_movement(frame, source, start, stop):
    """Identical causal movement inputs for replay, fitting and live inference."""
    for subject in config.SUBJECTS:
        selected = frame.subject == subject.slug
        slots = pd.DatetimeIndex(frame.loc[selected, "time"])
        events = features.presence_events(source, subject, start, stop)
        pairs = [(t, np.nan if v is None or str(v).strip().lower() in
                  ("", "unknown", "unavailable") else float(v == config.HOME_STATE))
                 for t, v in events]
        frame.loc[selected, "latest_home"] = features.numeric_on_grid(pairs, slots, None)
        if not subject.is_person:
            continue
        trace = eta.traces(source, subject.slug, start, stop)
        if trace.empty:
            continue
        travel = eta.feature_frame(trace)
        distance = [(t.isoformat(), v) for t, v in trace.distance_km.items()]
        here = features.numeric_on_grid(distance, slots, config.DISTANCE_STALE_MIN)
        for minutes in (5, 15):
            before = features.numeric_on_grid(distance, slots - pd.Timedelta(minutes=minutes),
                                              config.DISTANCE_STALE_MIN)
            frame.loc[selected, f"distance_delta_{minutes}m"] = here - before
        speed = [(t.isoformat(), v) for t, v in travel.closing_kmh.items()]
        frame.loc[selected, "closing_kmh_15m"] = features.numeric_on_grid(speed, slots, 15)
    for column in EXTRA:
        if column not in frame:
            frame[column] = np.nan
    # A stale recorder cannot supply even the latest state as a feature.
    frame.loc[frame.state_now.isna(), list(EXTRA)] = np.nan
    return frame


def build(source, start=None, stop=None, *, align_end=False):
    start = start or features.history_start(source)
    frame = features.build(source, start, stop, resolution_minutes=WINDOW,
                           horizons=HORIZONS, align_end=align_end, min_target_coverage=1.)
    return add_movement(frame, source, start, stop)


def gate(scored: pd.DataFrame) -> dict:
    """One subject/lead: an honest abstention is preferable to a weak model."""
    if scored.empty:
        return {"ship": False, "reason": "Not enough completed validation history",
                "n": 0, "folds": 0}
    errors = {c: float(((scored[c] - scored.actual) ** 2).mean())
              for c in scored if c.startswith("baseline_")}
    best = min(errors, key=errors.get)
    brier = float(((scored.p - scored.actual) ** 2).mean())
    reference = errors[best]
    skill = 100 * (1 - brier / reference) if reference > 0 else None
    wins = sum(float(((f.p - f.actual) ** 2).mean()) <
               float(((f[best] - f.actual) ** 2).mean())
               for _, f in scored.groupby("fold"))
    folds = scored.fold.nunique()
    ship = bool(folds >= 2 and skill is not None and
                skill >= train.min_ship_skill_pct(folds) and
                train.fold_record_allows(wins, folds))
    return {"ship": ship, "reason": None if ship else "Validation gain below publication threshold",
            "n": len(scored), "folds": int(folds), "folds_won": int(wins),
            "brier": brier, "best_baseline": best.removeprefix("baseline_"),
            "baseline_brier": reference, "skill_pct": skill,
            "required_skill_pct": train.min_ship_skill_pct(folds)}


def train_all(source, models_dir: Path, *, stop=None) -> dict:
    started = time.monotonic()
    stop = stop or pd.Timestamp.now(tz="UTC").isoformat()
    frame = build(source, stop=stop)
    eligible = frame.dropna(subset=["state_now", "latest_home"])
    origins = eligible.loc[eligible.time.dt.minute % 30 == 0]
    # Same existing fold geometry, counted in half-hour origins, not sixfold
    # inflated by the finer validation grid.
    geometry = evaluate.fold_geometry(origins.time)
    folds = evaluate.calendar_folds(origins.time,
                                    embargo=pd.Timedelta(minutes=max(MINUTES) + 30),
                                    **geometry)
    models, metrics = {}, {}
    for minutes, horizon in zip(MINUTES, HORIZONS):
        target = f"y_{horizon}h"
        available = eligible.dropna(subset=[target])
        columns = train.features_for(horizon) + list(EXTRA)
        records = []
        for fold in folds:
            embargo = pd.Timedelta(minutes=minutes + 30)
            tr = available.loc[(available.time + embargo < fold.test_start)
                               & (available.time.dt.minute % 30 == 0)]
            te = available.loc[(available.time >= fold.test_start)
                               & (available.time + pd.Timedelta(minutes=minutes + WINDOW)
                                  <= min(fold.test_stop, pd.Timestamp(stop)))]
            if len(tr) < geometry["min_test_rows"] or te.empty:
                continue
            model = train._dedicated_estimator()
            model.fit(tr[columns], tr[target] - tr.state_now)
            part = te[["subject", "time"]].copy()
            part["p"] = np.clip(model.predict(te[columns]) + te.state_now, 0., 1.)
            part["actual"] = (te[target] >= .5).astype(float)
            part["fold"] = fold.index
            # The conventional half-hour calendar baseline remains a strong
            # comparator at every phase; row-local lag baselines use 5m truth.
            for name, predictor in baseline.predictors(horizon).items():
                part["baseline_" + name] = predictor(tr, te)
            part["baseline_instant_persistence"] = te.latest_home
            records.append(part)
        scored = pd.concat(records, ignore_index=True) if records else pd.DataFrame()
        metrics[str(minutes)] = {
            s: gate(scored.loc[scored.subject == s] if not scored.empty else scored)
            for s in config.all_slugs()}
        if any(m["ship"] for m in metrics[str(minutes)].values()):
            fit = available.loc[available.time.dt.minute % 30 == 0]
            model = train._dedicated_estimator()
            model.fit(fit[columns], fit[target] - fit.state_now)
            models[minutes] = {"model": model, "columns": columns}
        _log.info("near-term +%dm: %d/%d subjects qualify", minutes,
                  sum(m["ship"] for m in metrics[str(minutes)].values()), len(config.SUBJECTS))
    summary = {"version": VERSION, "trained_at": stop, "metrics": metrics,
               "duration_s": round(time.monotonic() - started, 1),
               "validation": "chronological; half-hour fits; all five-minute phases"}
    artifact = {**summary, "identity": identity(), "models": models}
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / "near_term.pkl"
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(pickle.dumps(artifact))
    temporary.replace(path)
    return summary


def load(models_dir: Path) -> dict | None:
    path = models_dir / "near_term.pkl"
    if not path.exists():
        return None
    try:
        artifact = pickle.loads(path.read_bytes())
        return artifact if artifact.get("identity") == identity() else None
    except Exception:
        _log.warning("near-term artifact unavailable", exc_info=True)
        return None


def empty(at, reason="Waiting for near-term training") -> dict:
    return {"issued_at": at.isoformat(), "window_minutes": WINDOW,
            "model_version": VERSION, "current": None,
            "points": [{"minutes": m, "target_at": (at + pd.Timedelta(minutes=m)).isoformat(),
                        "p": None, "reason": reason} for m in MINUTES]}


def forecast(artifact, source, at=None) -> dict[str, dict]:
    at = at if at is not None else pd.Timestamp.now(tz="UTC")
    out = {s: empty(at) for s in config.all_slugs()}
    if not artifact or artifact.get("identity") != identity():
        return out
    try:
        start = (at - pd.Timedelta(days=features.deepest_lookback_days() + 2)).isoformat()
        frame = build(source, start, at.isoformat(), align_end=True)
        rows = frame.groupby("subject", sort=False).tail(1).set_index("subject", drop=False)
        for subject, row in rows.iterrows():
            payload = out[subject]
            valid = np.isfinite(row.state_now) and np.isfinite(row.latest_home)
            payload["current"] = float(row.latest_home) if valid else None
            for point in payload["points"]:
                minutes = point["minutes"]
                metric = artifact["metrics"].get(str(minutes), {}).get(subject, {})
                point["reason"] = ("Current presence is unknown" if not valid else
                                    metric.get("reason") or "No qualified model")
                fitted = artifact["models"].get(minutes)
                if valid and metric.get("ship") and fitted:
                    value = fitted["model"].predict(row.to_frame().T[fitted["columns"]])[0]
                    p = float(np.clip(value + row.state_now, 0., 1.))
                    if np.isfinite(p):
                        point.update(p=p, reason=None)
        return out
    except Exception:
        _log.error("near-term inference failed; hourly forecast remains available", exc_info=True)
        return {s: empty(at, "Near-term calculation unavailable") for s in config.all_slugs()}


def window_truth(events, slots, minutes=WINDOW):
    """Integrate a step trace independently over arbitrarily overlapping windows."""
    if not events or not len(slots):
        return np.full(len(slots), np.nan)
    series = pd.Series([v for _, v in events], index=pd.to_datetime(
        [t for t, _ in events], utc=True, format="ISO8601")).sort_index()
    series = series[~series.index.duplicated(keep="last")]
    stamps = series.index.asi8
    known = (~series.isna() & ~series.astype(str).str.strip().str.lower().isin(
        ["", "unknown", "unavailable"])).to_numpy(dtype=float)
    home = (series == config.HOME_STATE).to_numpy(dtype=float) * known

    def integral(values, points):
        cumulative = np.r_[0., np.cumsum(np.diff(stamps) / 1e9 * values[:-1])]
        positions = np.searchsorted(stamps, points, side="right") - 1
        safe = np.maximum(positions, 0)
        return np.where(positions >= 0, cumulative[safe] +
                        (points - stamps[safe]) / 1e9 * values[safe], 0.)

    left, right = slots.asi8, (slots + pd.Timedelta(minutes=minutes)).asi8
    duration = minutes * 60
    coverage = (integral(known, right) - integral(known, left)) / duration
    frac = (integral(home, right) - integral(home, left)) / duration
    return np.where(coverage >= 1. - 1e-9, frac, np.nan)


def verification(source, subject: str, minutes=15, days=7, *, at=None):
    if subject not in config.all_slugs() or minutes not in MINUTES or not 1 <= days <= 30:
        raise ValueError("Choose a configured subject, a 15-minute lead up to 120, and 1-30 days")
    at = at if at is not None else pd.Timestamp.now(tz="UTC")
    start = (at - pd.Timedelta(days=days)).isoformat()
    store = getattr(source, "store", None)
    records = store.near_term_forecasts(subject, minutes, start, at.isoformat()) if store else []
    completed = [r for r in records if r["target_ts"] + WINDOW * 60000 <= at.timestamp() * 1000]
    slots = pd.to_datetime([r["target_ts"] for r in completed], unit="ms", utc=True)
    selected = next(s for s in config.SUBJECTS if s.slug == subject)
    actual = window_truth(features.presence_events(source, selected, start, at.isoformat()), slots)
    live = features._liveness(source, start, at.isoformat())
    observable = (features.observability(live, slots) &
                  features.observability(live, slots + pd.Timedelta(minutes=WINDOW)))
    p = np.array([r["p"] if r["p"] is not None else np.nan for r in completed])
    current = np.array([r["current"] if r["current"] is not None else np.nan for r in completed])
    valid = observable & np.isfinite(actual) & np.isfinite(p) & np.isfinite(current)
    truth = (actual[valid] >= .5).astype(float)
    error = float(np.mean((p[valid] - truth) ** 2)) if valid.any() else None
    reference = float(np.mean((current[valid] - truth) ** 2)) if valid.any() else None
    return {"subject": subject, "minutes": minutes, "days": days,
            "attempts": len(records), "completed": len(completed),
            "published": sum(r["p"] is not None for r in completed),
            "scored": int(valid.sum()), "brier": error, "persistence_brier": reference,
            "skill_pct": 100 * (1 - error / reference) if reference else None,
            "model_versions": sorted({r["model_version"] for r in completed if r["model_version"]})}
