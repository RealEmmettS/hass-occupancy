# Occupancy Test Journal

An automatic, read-only observation journal. It does not subscribe to commands,
publish MQTT controls, or call Home Assistant services.

## Setup

1. Install and start the forecast add-on, with MQTT configured.
2. Set `forecast_addon` to its full Supervisor slug, including the repository
   prefix (for example, `1234abcd_occupancy_forecast`). The journal uses internal
   add-on DNS; it does not expose a network port.
3. Configure `residents` as objects with `name`, `entity_id`, and `forecast`
   fields. Include known untracked residents with `forecast: false` so coverage
   is visible. This is display/evaluation metadata, not a person-creation service.
4. Add optional climate and distance sensor IDs to `extra_entities`.
5. Start the journal and enable **Show in sidebar**. The panel has recent values
   and newline-delimited JSON export links. Home Assistant authenticates access.

Example configuration (fictional entities):

```yaml
retention_days: 90
forecast_addon: 1234abcd_occupancy_forecast
residents:
  - name: Resident A
    entity_id: person.resident_a
    forecast: true
  - name: Resident B
    entity_id: person.resident_b
    forecast: false
extra_entities:
  - climate.example_zone
  - sensor.home_resident_a_distance
```

## What is retained

- `forecast`: the forecast API's published snapshot, including `predicted_at`,
  each subject's `observed_at`, hourly curve, and conditional arrival estimate.
  Changed snapshots are appended, never overwritten.
- `observations`: a sample every 60 seconds of person/tracker readings, optional
  climate/distance readings, and Velair state sensors. Only allowlisted
  attributes are retained. GPS coordinates and user IDs are excluded.
- `collection_health`: source error and recovery transitions. An error does not
  create a fabricated occupancy reading. A stopped source remains visibly stale.

Data lives in the add-on's `/data/journal.sqlite3` in compressed SQLite rows,
with WAL transactions and per-operation closed connections. Capture times and
source timestamps use UTC. The panel formats dates in the browser's timezone.
The configurable retention window defaults to 90 days (30–365 allowed). Older
records are pruned; SQLite reuses freed pages, so file size need not shrink.
There is no cloud upload. Back up the add-on's data using Home Assistant backups.

This is sampled history, not a lossless message bus: revisions or brief trips
between one-minute samples can be missed. The capture timestamp is separate
from the model's issue timestamp. Check that a prediction was captured before
the outcome when evaluating it. Never evaluate unobserved periods as away.

The forecast add-on's native **Was it right?** chart retains its own 30-day
hourly forecast comparison. The journal supplements that with revisions, ETA,
cooling context and exports; it does not replace its training metrics.

## Operation and removal

Boot mode `auto` starts collection after host restarts. Enable the add-on
watchdog to restart a stalled journal worker. Dependency failures are recorded
and retried rather than causing a restart loop. `/health` exposes only worker
freshness and error categories; all other routes require the Supervisor ingress.

Stopping the add-on pauses collection and preserves history. Uninstalling it
removes its data: export or back up first. Its code and data are separate from
the forecast add-on, so either can be stopped without affecting device control.

Run the journal's standard-library tests with:

```sh
python -m unittest discover -s occupancy-journal/tests -v
```
