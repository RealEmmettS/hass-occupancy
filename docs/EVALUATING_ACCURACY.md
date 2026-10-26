# Evaluating occupancy accuracy

Treat tracker presence as an observed proxy. It is not independently verified
physical occupancy. The combined subject covers only the selected residents.

## Two separate measurements

1. **Issued forecasts:** join a prediction captured before its target with the
   later, completed half-hour observation. Include the publication rate and the
   number of observations that could be scored. Compare with the current-state
   prediction captured at the same issue time, on exactly the same rows.
2. **Candidate evaluation:** split by date, train only on earlier data, and leave
   the newest period untouched until a candidate is selected. A label ending
   after a training cutoff cannot enter training. Never randomly split adjacent
   time slots, and never report training residuals as forecast accuracy.

The built-in verification chart preserves the last published revision for each
subject, horizon and target slot. Starting with model 0.5.0 it also stores the
issue timestamp, current-state baseline and model version. Actual lead time can
be up to half an hour shorter than the named horizon because predictions are
refreshed within a half-hour origin slot. Legacy rows remain visible but do not
receive invented issue timestamps or baseline values. The separate journal
retains sampled revisions and supports an audit using the first captured
revision per origin, instead. These are different measurements.

## Feature contract

- `home_frac` is the observed fraction of the **forward** half-hour target.
- Current presence, zone and direction features use the **preceding** five
  minutes, in both training and serving. Latest explicit unknown presence
  invalidates the origin rather than being interpreted as away.
- Distance sensors are converted from their declared units to kilometres before
  fitting or serving. ETA models carry a distance-unit schema marker.
- Target-relative historical slots must finish before issue time. At +24 hours,
  the target's one-day lag is still the unfinished origin slot, so it is excluded.
- Cross-person current features follow the same causal origin definition.
- Prefix-invariance tests compare every served input with and without future
  observations. Completed labels and coverage are tested separately.

## Reproducible candidate comparison

Build a private feature snapshot using the current code and keep its configuration
and database outside Git. `scripts/evaluate-candidates.py` compares a declared,
small set of regularized histogram-boosting candidates on three earlier weekly
windows, with a horizon-specific label embargo. It records row-level predictions
and aggregate scores. For example, with paths and dates for your own installation:

```text
python scripts/evaluate-candidates.py --config PRIVATE_CONFIG --features PRIVATE_PARQUET --cutoff HOLDOUT_START --end SNAPSHOT_END --phase validation --output PRIVATE_VALIDATION_JSON
python scripts/evaluate-candidates.py --config PRIVATE_CONFIG --features PRIVATE_PARQUET --cutoff HOLDOUT_START --end SNAPSHOT_END --phase holdout --candidate regularized --output PRIVATE_HOLDOUT_JSON
```

Freeze the candidate before running the second command. It measures dedicated
models at +1, +3, +6, +12, +24 and +48 hours; it does not replay the historical
deployment or independently validate every choice made by the production
dedicated/pooled publication gate. That gate retains rolling-origin evaluation
against its baseline ladder and is not weakened to populate the chart.

Report Brier score (lower is better), calibration, publication coverage and
per-person/transition results. Aggregate by day when estimating uncertainty;
adjacent slots and household members are correlated. A small holdout cannot
establish accuracy for seasonal changes, holidays or future schedule changes.

The dedicated model uses 120 iterations, seven leaves, a 75-sample minimum leaf
and L2 regularization of 10. Random internal early stopping is disabled; the
explicit temporal folds provide validation. Smaller capacity is suitable for a
short archive, but future changes still require an independent time holdout.

## Arrival ETA

ETA is conditional on evidence of movement toward home. It has its own sample
and temporal validation requirements. Empty ETA output means those requirements
were not met, not an arrival at zero minutes. Median and 90th-percentile errors
come from held-out fold predictions. Do not reduce the gate just to show a value.

References: [probability calibration and Brier scoring](https://scikit-learn.org/stable/modules/calibration.html)
and [histogram gradient boosting parameters](https://scikit-learn.org/1.7/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).

## Concurrent history access

The API, collector and prediction worker share one SQLite connection. Cross-thread
access is serialized through a reentrant lock, including cursor consumption and
commit. A regression test overlaps eight exact-record readers with a collector.
The pre-fix synthetic reproduction returned incorrect row counts and query errors;
the fixed version preserves the expected rows. This addresses the class of
connection reuse problem described in [CPython issue 118172](https://github.com/python/cpython/issues/118172).
