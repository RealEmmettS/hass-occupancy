"""Regression checks for the information available at forecast issue time."""
import numpy as np
import pandas as pd
import pytest

from occupancy_forecast import config, features, nowcast, predict, train
from occupancy_forecast.sources.ha import StoreSource
from occupancy_forecast.sources.store import HistoryStore, _ms
from .conftest import settings


def test_every_served_feature_is_invariant_to_future_observations(tmp_path):
    config.configure(settings(house_entity=None))
    start = pd.Timestamp("2026-04-01T00:00:00Z")
    issue = start + pd.Timedelta(days=32, hours=8)
    end = issue + pd.Timedelta(days=3)
    store = HistoryStore(tmp_path / "history.db")
    times = pd.date_range(start - pd.Timedelta(hours=1), end, freq="30min")
    store.append([(person, int(t.timestamp() * 1000),
                   "not_home" if 8 <= t.hour < 18 else "home")
                  for person in [s.entity_id for s in config.PEOPLE] for t in times])
    source = StoreSource(store, None)
    full = features.build(source, start.isoformat(), end.isoformat())
    # Store reads are end-exclusive; include events stamped exactly at issue.
    prefix = features.build(source, start.isoformat(), (issue + pd.Timedelta(seconds=1)).isoformat())
    columns = sorted(set(c for h in config.HORIZONS_H for c in train.features_for(h)))
    a = full[full.time == issue].set_index("subject")
    b = prefix[prefix.time == issue].set_index("subject")
    columns = [c for c in columns if c != "subject"]
    pd.testing.assert_frame_equal(a[columns], b[columns])
    assert prefix[prefix.time == issue].home_frac.isna().all()
    store._db.close()


def test_trailing_windows_do_not_integrate_the_gaps_or_future():
    slots = pd.date_range("2026-05-01T00:00Z", periods=3, freq="30min")
    events = [("2026-04-30T23:00Z", "home"),
              ("2026-05-01T00:10Z", "not_home"),
              ("2026-05-01T00:29Z", "home"),
              ("2026-05-01T01:01Z", "not_home")]
    result = features.trailing_fraction(events, slots, "home")
    assert result.frac.tolist() == pytest.approx([1, .2, 1])
    assert result.coverage.tolist() == [1, 1, 1]


@pytest.mark.parametrize("unit,reading", [("m", 1000), ("ft", 1000 / .3048),
                                           ("km", 1), ("mi", 1 / 1.609344)])
def test_proximity_is_normalized_before_training_and_nowcasting(unit, reading):
    config.configure(settings(units={"sensor.home_alice_distance": unit}))
    slots = pd.date_range("2026-05-01T00:00Z", periods=3, freq="30min")
    class Source:
        def numeric(self, entity, start, stop):
            return [(t.isoformat(), reading) for t in slots]
        def seeded_states(self, *args, **kwargs):
            return []
    frame = pd.DataFrame(index=slots)
    features._add_proximity(frame, Source(), config.subject("alice"),
                            slots[0].isoformat(), slots[-1].isoformat(), slots)
    assert frame.distance_km.tolist() == pytest.approx([1, 1, 1])


def test_explicit_unknown_clears_current_and_all_predictions():
    at = pd.Timestamp("2026-05-01T12:00Z")
    assert nowcast.presence_fraction([("2026-05-01T11:00Z", "home"),
                                       (at.isoformat(), "unknown")], at) is None
    rows = pd.DataFrame([{"subject": "alice", "time": at, "state_now": np.nan}])
    result, = predict.predict_rows({}, rows, etas={"alice": 20})
    assert result["current"] is None
    assert result["curve"] == {}
    assert result["eta_minutes"] is None


def test_old_forecast_schema_migrates_without_inventing_issue_times(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE forecasts(subject TEXT, target_ts INTEGER, horizon_h INTEGER, p REAL, PRIMARY KEY(subject,target_ts,horizon_h))")
        db.execute("INSERT INTO forecasts VALUES('alice', 1000, 1, .8)")
    store = HistoryStore(path)
    context = store.forecast_context("alice", 1, "1970-01-01T00:00:00Z", "1970-01-02T00:00:00Z")
    assert context[1000]["issued_ts"] is None
    store._db.close()
