# Near-term occupancy forecast

Stable 0.1.6 adds a separate next-two-hours view. It predicts whether a person is
home for most of the five-minute window beginning 15, 30, 45, 60, 75, 90, 105 or
120 minutes after issue. The combined subject means at least one selected person
is home. These probabilities do not identify an exact arrival or departure time.

The original hourly curve and its crossing/ETA outputs retain their own models,
targets and qualification. No device control, phone permissions, tracker mappings
or additional dependencies are introduced.

## Qualification

The near-term head uses the same small dedicated estimator, plus latest presence,
5/15-minute distance changes and recent closing speed. Distance features always
use elapsed time and normalized kilometres, independent of grid resolution.
Feature building takes explicit resolution arguments; it never changes global
constants in the running server. Training and inference share the same builder.

Fits use half-hour origins to avoid counting six correlated examples as sixfold
more training history. Rolling chronological validation includes all five-minute
phases. Training labels finish before each test period with a conservative embargo.
Targets containing any unknown interval are excluded. Calendar, persistence and
lag baselines are fitted using only training data; instantaneous persistence is
also a comparator. Each subject and lead must separately beat its best baseline
using the existing effect-size/fold gate, with at least two validation folds.
The early-history 15% skill requirement is retained.

Qualified final models and their metrics are written atomically to
`/data/models/near_term.pkl`. An identity hash covers the feature version, people,
zones, units, timezone and holiday configuration. A mismatch suppresses serving
until retraining. A failed near-term calculation returns explicit missing values
while allowing the hourly forecast to continue. Explicit unknown current presence
also suppresses predictions.

Version 0.1.7 serializes prediction cycles from the scheduler, manual API and
training completion. Model references are selected after acquiring the cycle
lock, preventing an older in-flight calculation from overwriting a fresh model's
forecast. Training itself continues separately and existing artifacts remain valid.

## Live evidence

`GET api/forecast` includes `subjects[].near_term`, with issue time, exact target
times, five-minute window duration, model version and reasons for withheld points.
MQTT attributes include the same object. The existing journal preserves the full
forecast response, so its snapshots automatically retain the additional field.

The additive SQLite `near_term_forecasts` table keeps each issued revision and
withheld attempt for 30 days. Retries cannot overwrite a previous issue. It stores
the latest reported presence used by the same-time persistence comparator.

`GET api/near-term/verification?subject=<slug>&minutes=15&days=7` reports attempts,
completed/published/scored counts, Brier error and the matching persistence score.
Only completed, fully known, observable target windows are scored. Overlapping
windows are integrated separately. Repeated issues are correlated observations,
not independent trials; the panel states this. Live evidence starts with deployment
and is distinct from retrospective qualification.

`GET api/near-term/metrics` supplies training time, duration, validation method,
per-subject/lead qualification, best baseline and skill. These endpoints require
the same ingress access as the existing panel.

## Reproduce privately

Run `scripts/qualify-near-term.py --config <private config> --history <private db>
--end <UTC timestamp> --output <private directory>` with the pinned Python
dependencies. It runs the production training, gate and serving path and writes
summary/model/forecast artifacts. Keep all household inputs and outputs outside
Git. Resolution experiments remain exploratory; dates already inspected are not
an untouched holdout. Prospective tracker-based scoring does not establish
physical presence accuracy or arrival-time accuracy.
