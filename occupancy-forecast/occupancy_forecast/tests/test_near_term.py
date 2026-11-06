import pickle

import numpy as np
import pandas as pd
import pytest

from occupancy_forecast import config, features, near_term, train
from occupancy_forecast.sources.ha import StoreSource
from occupancy_forecast.sources.store import HistoryStore, _ms
from .conftest import settings


def source_with_history(tmp_path, start="2026-04-01T00:00Z", days=33):
    config.configure(settings(house_entity=None))
    store = HistoryStore(tmp_path / "history.db")
    times = pd.date_range(start, periods=days * 48 + 1, freq="30min")
    rows = [(s.entity_id, _ms(t.isoformat()), "not_home" if 8 <= t.hour < 18 else "home")
            for s in config.PEOPLE for t in times]
    rows += [("sensor.home_alice_distance", _ms(t.isoformat()), str(i * 10))
             for i, t in enumerate(times)]
    store.append(rows)
    return StoreSource(store, None)


def test_distance_offsets_are_elapsed_time_on_five_minute_grid():
    slots = pd.date_range("2026-05-01T01:00Z", periods=13, freq="5min")
    samples = pd.date_range("2026-05-01T00:00Z", periods=25, freq="5min")
    class Source:
        def numeric(self, *args):
            return [(t.isoformat(), i * 1000.) for i, t in enumerate(samples)]
        def seeded_states(self, *args):
            return []
    frame = pd.DataFrame(index=slots)
    features._add_proximity(frame, Source(), config.subject("alice"), "", None, slots)
    assert frame.distance_delta_30m.tolist() == [6.] * 13
    assert frame.distance_delta_60m.tolist() == [12.] * 13


def test_near_features_do_not_change_global_hourly_resolution(tmp_path):
    source = source_with_history(tmp_path, days=2)
    near_term.build(source, "2026-04-01T00:00Z", "2026-04-02T12:03:17Z", align_end=True)
    assert config.GRID_MINUTES == 30
    assert config.HORIZONS_H == tuple(range(1, 49))
    table = features.build(source, "2026-04-01T00:00Z", "2026-04-02T12:03:17Z")
    assert table.time.max() == pd.Timestamp("2026-04-02T12:00Z")


def test_issue_anchored_near_features_are_prefix_invariant(tmp_path):
    source = source_with_history(tmp_path)
    issue = pd.Timestamp("2026-05-02T12:03:17Z")
    end = issue + pd.Timedelta(days=1)
    full = near_term.build(source, "2026-04-01T00:00Z", end.isoformat(), align_end=True)
    prefix = near_term.build(source, "2026-04-01T00:00Z", issue.isoformat(), align_end=True)
    columns = sorted(set(c for h in near_term.HORIZONS for c in train.features_for(h))
                     | set(near_term.EXTRA))
    a = full.loc[full.time == issue].set_index("subject", drop=False)
    b = prefix.loc[prefix.time == issue].set_index("subject", drop=False)
    pd.testing.assert_frame_equal(a[columns], b[columns])
    assert b.home_frac.isna().all()


def test_overlapping_truth_and_unknown_intervals():
    events = [("2026-05-01T00:00Z", "home"), ("2026-05-01T00:03Z", "not_home"),
              ("2026-05-01T00:08Z", "unknown")]
    slots = pd.date_range("2026-05-01T00:00Z", periods=5, freq="1min")
    actual = near_term.window_truth(events, slots)
    assert actual[:4] == pytest.approx([.6, .4, .2, 0.])
    assert np.isnan(actual[4])


def test_gate_is_per_subject_and_keeps_strong_persistence():
    good = pd.DataFrame({"p": [.1, .9] * 200, "actual": [0., 1.] * 200,
                         "baseline_instant_persistence": [.5] * 400,
                         "fold": [0] * 200 + [1] * 200})
    assert near_term.gate(good)["ship"]
    good["baseline_instant_persistence"] = good.actual
    assert not near_term.gate(good)["ship"]
    assert not near_term.gate(pd.DataFrame())["ship"]


def test_training_excludes_partial_derived_household_without_erasing_person_data():
    config.configure(settings(house_entity=None))
    times = pd.date_range("2026-05-01T00:00Z", periods=3, freq="30min")
    frame = pd.DataFrame([
        {"subject": subject, "time": time, "state_now": current,
         "latest_home": current, "y_1.0h": target}
        for subject, values in {
            "alice": [(1., 1.), (1., 1.), (0., 0.)],
            "bob": [(np.nan, 0.), (0., np.nan), (0., 0.)],
            "house": [(1., 1.), (1., 1.), (0., 0.)],
        }.items()
        for time, (current, target) in zip(times, values)
    ])
    original = frame.copy(deep=True)
    eligible = near_term.training_rows(frame)
    assert eligible.loc[eligible.subject == "house", "time"].tolist() == list(times[1:])
    target_rows = near_term.training_rows(frame, "y_1.0h")
    assert target_rows.loc[target_rows.subject == "house", "time"].tolist() == [times[2]]
    assert len(target_rows.loc[target_rows.subject == "alice"]) == 3
    pd.testing.assert_frame_equal(frame, original)


def test_direct_household_sensor_does_not_require_every_person_observation():
    frame = pd.DataFrame([{"subject": "house", "time": pd.Timestamp("2026-05-01T00:00Z"),
                           "state_now": 1., "latest_home": 1., "y_1.0h": 0.}])
    assert len(near_term.training_rows(frame, "y_1.0h")) == 1


def test_new_resident_gets_own_warmup_and_folds_in_an_old_household():
    older = pd.date_range("2026-01-01T00:00Z", periods=70 * 48, freq="30min")
    newer = pd.date_range("2026-03-01T00:00Z", periods=10 * 48, freq="30min")
    frame = pd.concat([pd.DataFrame({"subject": "alice", "time": older}),
                       pd.DataFrame({"subject": "bob", "time": newer})], ignore_index=True)
    windows = near_term.validation_folds(frame)
    assert len(windows["bob"]) == 2
    assert windows["bob"][0].test_start == pd.Timestamp("2026-03-06T00:00Z")
    assert windows["bob"][1].test_start == pd.Timestamp("2026-03-08T00:00Z")
    assert windows["alice"][0].test_start == pd.Timestamp("2026-02-15T00:00Z")
    # Replicating phases must not manufacture more independent validation days.
    phased = pd.concat([frame.assign(time=frame.time + pd.Timedelta(minutes=m))
                        for m in range(0, 30, 5)], ignore_index=True)
    assert [f.test_start for f in near_term.validation_folds(phased)["bob"]] == [
        pd.Timestamp("2026-03-06T00:00Z"), pd.Timestamp("2026-03-08T00:00Z")]


def test_previous_near_term_artifact_survives_code_update_but_not_membership_change():
    artifact = {"version": "near-term-2", "identity": near_term.identity(version="near-term-2")}
    assert near_term.compatible(artifact)
    config.configure(settings(people=["person.alice", "person.bob", "person.charlie"]))
    assert not near_term.compatible(artifact)


def test_store_retains_withheld_revisions_and_ignores_retries(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    a = _ms("2026-05-01T00:00Z")
    rows = [("alice", a, a + 900000, 15, None, 1., near_term.VERSION),
            ("alice", a + 60000, a + 960000, 15, .8, 1., near_term.VERSION)]
    store.append_near_term(rows)
    store.append_near_term(rows)
    records = store.near_term_forecasts("alice", 15, "2026-05-01T00:00Z", "2026-05-02T00:00Z")
    assert len(records) == 2 and records[0]["p"] is None
    store.prune_near_term("2026-05-01T00:01Z")
    assert len(store.near_term_forecasts("alice", 15, "2026-05-01T00:00Z", "2026-05-02T00:00Z")) == 1


def test_artifact_identity_invalidation_and_unknown_serving(tmp_path, monkeypatch):
    artifact = {"identity": near_term.identity(), "metrics": {}, "models": {}}
    (tmp_path / "near_term.pkl").write_bytes(pickle.dumps(artifact))
    assert near_term.load(tmp_path) is not None
    config.configure(settings(people=["person.other"]))
    assert near_term.load(tmp_path) is None
    at = pd.Timestamp("2026-05-01T00:00Z")
    result = near_term.forecast(artifact, None, at)
    assert all(p["p"] is None for s in result.values() for p in s["points"])
    assert result[config.HOUSE_SLUG]["points"][0]["target_at"] == (at + pd.Timedelta(minutes=15)).isoformat()


def test_inference_failure_isolated(monkeypatch):
    artifact = {"identity": near_term.identity()}
    monkeypatch.setattr(near_term, "build", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("unavailable")))
    result = near_term.forecast(artifact, None)
    assert all(p["p"] is None and p["reason"] == "Near-term calculation unavailable"
               for s in result.values() for p in s["points"])


def test_unknown_clears_even_a_qualified_near_model(monkeypatch):
    rows = pd.DataFrame([{"subject": "alice", "state_now": np.nan,
                          "latest_home": np.nan, "time": pd.Timestamp("2026-05-01T00:00Z")}])
    monkeypatch.setattr(near_term, "build", lambda *a, **kw: rows)
    class NeverPredict:
        def predict(self, frame):
            raise AssertionError("Unknown presence must not be predicted")
    artifact = {"identity": near_term.identity(),
                "metrics": {str(m): {"alice": {"ship": True}} for m in near_term.MINUTES},
                "models": {m: {"model": NeverPredict(), "columns": []} for m in near_term.MINUTES}}
    result = near_term.forecast(artifact, None)["alice"]
    assert result["current"] is None
    assert all(p["p"] is None and p["reason"] == "Current presence is unknown" for p in result["points"])


def test_near_verification_rejects_unconfigured_subject_and_invalid_lead():
    with pytest.raises(ValueError):
        near_term.verification(None, "unconfigured")
    with pytest.raises(ValueError):
        near_term.verification(None, "alice", minutes=5)


def test_retrain_publication_cannot_be_overwritten_by_older_inflight_cycle(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from occupancy_forecast import server
    first_entered, release_first, second_entered = (threading.Event() for _ in range(3))
    monkeypatch.setitem(server._state, "near_model", None)
    monkeypatch.setitem(server._state, "forecast", [])
    monkeypatch.setattr(server._broker, "client", lambda: None)
    monkeypatch.setattr(server, "_record_forecasts", lambda results: None)
    def run(*args):
        if args[-1] is None:
            first_entered.set()
            assert release_first.wait(5)
            return [{"generation": "old"}]
        second_entered.set()
        return [{"generation": "new"}]
    monkeypatch.setattr(server.predict_mod, "run_cycle", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(server.do_predict)
        assert first_entered.wait(5)
        server._state["near_model"] = {"new": True}
        new = pool.submit(server.do_predict)
        try:
            assert not second_entered.wait(.1)
        finally:
            release_first.set()
        assert old.result(timeout=5)[0]["generation"] == "old"
        assert new.result(timeout=5)[0]["generation"] == "new"
    assert server._state["forecast"] == [{"generation": "new"}]


def test_server_records_withheld_attempts_and_exposes_verification(tmp_path, monkeypatch):
    from occupancy_forecast import server
    source = source_with_history(tmp_path, days=1)
    monkeypatch.setitem(server._state, "source", source)
    at = pd.Timestamp.now(tz="UTC")
    result = {"subject": "alice", "observed_at": at.isoformat(), "curve": {},
              "near_term": near_term.empty(at)}
    server._record_forecasts([result])
    response = server.api_near_term_verification("alice")
    assert response["attempts"] == 1
    assert response["completed"] == response["published"] == response["scored"] == 0
    assert {"brier", "persistence_brier", "skill_pct", "attempts", "completed",
            "published", "scored"} <= response.keys()


def test_verification_excludes_incomplete_and_unknown_targets(tmp_path):
    source = source_with_history(tmp_path, days=1)
    source.store.append([("person.alice", _ms("2026-04-01T06:20:30Z"), "home")])
    issued = _ms("2026-04-01T06:00Z")
    source.store.append_near_term([
        ("alice", issued, issued + 900000, 15, .9, 1., near_term.VERSION),
        ("alice", issued + 60000, issued + 960000, 15, None, 1., near_term.VERSION),
        ("alice", issued + 120000, issued + 1020000, 15, .8, 1., near_term.VERSION)])
    result = near_term.verification(source, "alice", at=pd.Timestamp("2026-04-01T06:21Z"))
    assert result["attempts"] == 3
    assert result["completed"] == 2
    assert result["published"] == result["scored"] == 1
    assert result["brier"] == pytest.approx(.01)
    assert result["persistence_brier"] == 0.
