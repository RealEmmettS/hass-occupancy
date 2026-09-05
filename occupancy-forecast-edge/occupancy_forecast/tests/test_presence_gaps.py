import numpy as np
import pandas as pd

from occupancy_forecast import config, features
from occupancy_forecast.sources.ha import StoreSource
from occupancy_forecast.sources.store import HistoryStore, _ms


def test_unknown_interval_is_not_away_or_carried_home():
    slots = pd.date_range("2026-01-01", periods=3, freq="30min", tz="UTC")
    events = [(slots[0].isoformat(), "home"), (slots[1].isoformat(), "unknown"),
              (slots[2].isoformat(), "not_home")]
    result = features.slot_fraction(events, slots, "home")
    assert result["frac"].iloc[0] == 1
    assert np.isnan(result["frac"].iloc[1])
    assert result["coverage"].iloc[1] == 0
    assert result["frac"].iloc[2] == 0


def test_house_is_unknown_when_away_person_has_unobserved_housemate(tmp_path):
    config.configure(config.Settings(people=["person.alice", "person.bob"], timezone="UTC"))
    store = HistoryStore(tmp_path / "history.db")
    try:
        store.append([("person.alice", _ms("2026-01-01T00:00:00Z"), "not_home"),
                      ("person.bob", _ms("2026-01-01T00:00:00Z"), "unknown"),
                      ("person.alice", _ms("2026-01-01T00:30:00Z"), "home")])
        events = features.presence_events(StoreSource(store, None), config.SUBJECTS[-1], "2026-01-01T00:00:00Z", None)
        assert events[0][1] == "unknown"
        assert events[-1][1] == "home"
    finally:
        store._db.close()


def test_archive_age_does_not_count_untracked_person_days(tmp_path):
    config.configure(config.Settings(people=["person.alice", "person.bob"], timezone="UTC"))
    store = HistoryStore(tmp_path / "history.db")
    try:
        stamps = pd.date_range("2026-01-01", "2026-01-13", freq="30min", tz="UTC")
        rows = [(p, int(t.timestamp() * 1000), "unknown" if p == "person.bob" and t.day < 12 else "home")
                for p in ["person.alice", "person.bob"] for t in stamps]
        store.append(rows)
        days = features.usable_history_days(StoreSource(store, None), "2026-01-13T00:00:00Z")
        assert store.span()["days"] == 12
        # The end-exclusive liveness window may withhold its final slot.
        assert 0.95 <= days <= 1
    finally:
        store._db.close()
