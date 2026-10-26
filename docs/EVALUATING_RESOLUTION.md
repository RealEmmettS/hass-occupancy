# Evaluating finer occupancy intervals

Three time scales answer different questions:

- The collector normally runs every five minutes and can react to person events.
- The current occupancy target is the home fraction of a 30-minute interval.
- The published curve has whole-hour horizons; its crossing is not an exact
  arrival or departure timestamp. The separate ETA model uses raw travel events.

Changing a target interval does not by itself improve transition timing. A smaller
interval can improve scores on unchanged-state rows while worsening the less common
arrival/departure rows. Evaluate both, and never interpret a five-minute grid as a
promise of five-minute prediction accuracy.

## Offline experiments

`scripts/evaluate-resolution.py` compares 30-, 15-, 10-, and 5-minute
representations with fixed model settings. Every arm uses the same half-hour
origins and the same completed five-minute observations for scoring. Training
sample counts are held equal, so denser grids do not masquerade as additional
independent history. The output includes transition scores, per-subject scores,
build cost, and paired day-block uncertainty.

`scripts/evaluate-near-term.py` prototypes forecasts from five minutes to two hours
ahead using five-minute targets. A predefined movement arm adds the latest presence
state, five- and fifteen-minute distance changes, and recent closing speed. It is
compared with both the existing feature set and the existing baseline ladder, plus
instantaneous persistence. A moving person is not assumed to be coming home.

Both scripts accept `--config`, `--history`, `--first-test`, `--end`, and `--output`.
Pass private paths outside this repository. Neither script writes model artifacts
to Home Assistant, changes its configuration, or publishes forecasts. They adjust
modelling constants only within their standalone process; do not import their
`main` functions into the concurrent live server.

These are chronological retrospective experiments. If earlier work has already
inspected their dates, they are not a new untouched holdout. Keep the configured
arms and outcome definition fixed, report adverse results as well as aggregate
gains, and collect prospective evidence before claiming a live improvement.

## Criteria for a near-term extension

Stable 0.1.6 implements the separate head described in
[Near-term occupancy](NEAR_TERM.md). `scripts/qualify-near-term.py` exercises its
production gate and serving path. Target windows must be fully known, and each
subject/lead is gated independently. Production validation includes all five-minute
phases, while fits still use half-hour origins.

Distance changes must always use actual elapsed 30/60 minutes. A positional
one-row difference means five minutes on a five-minute grid, and confounds a
resolution comparison. The feature builder now uses timestamp offsets at every
resolution. Older experimental outputs from before this correction are superseded.

Keep the longer-range forecast separate. A potential near-term extension should
train on finer targets and use its own explicitly labelled horizon and target
duration, origin timestamp, and matched baseline records. Gate each horizon and
inspect each subject; a good aggregate score must not hide an unreliable combined
occupancy signal. Unknown observations must remain unknown.

Use windows for prospective arrival/departure estimates and score missed events,
false alarms, event coverage, and timing error against raw timestamped transitions.
Current offline occupancy probabilities do not establish an event-time error in
minutes. Denser live serving also needs tests at off-grid issue times, bounded
runtime on the Pi, and journal retention of the additional forecast field.

No phone reporting permission, tracker identity, publication threshold, or live
resolution is changed by these experiments.
