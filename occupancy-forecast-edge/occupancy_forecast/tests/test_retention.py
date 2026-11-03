"""A learned stay prior must never become a resident schedule override."""
import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor

from occupancy_forecast import config, near_term
from .conftest import settings


def history(values=(1., 1., 1.)):
    config.configure(settings(timezone="UTC"))
    return pd.DataFrame({
        "subject": ["alice"] * 3,
        "time": pd.to_datetime(["2026-04-02T10:00Z", "2026-04-09T10:00Z", "2026-04-16T10:00Z"]),
        "latest_home": [1.] * 3, "y_1.0h": values,
    })


def query(**changes):
    row = dict(subject="alice", time=pd.Timestamp("2026-04-23T10:00Z"),
               latest_home=1., state_now=1., dir_away=0., distance_delta_15m=0.)
    row.update(changes)
    return pd.DataFrame([row])


def test_three_observed_matching_days_soften_a_departure_probability():
    table = near_term.fit_retention(history(), 60, before="2026-04-17T00:00Z")
    p = near_term.apply_retention(table, query(), np.array([.6]))
    # Three completed days contribute at half weight; the original probability
    # contributes one pseudo-day: .6 + .5 * (1 - .6) * 3/4 = .75.
    assert p == pytest.approx([.75])


@pytest.mark.parametrize("values", [(1., 1., np.nan), (1., 0., 1.)])
def test_sparse_or_conflicting_history_does_not_assert_a_stay(values):
    table = near_term.fit_retention(history(values), 60, before="2026-04-17T00:00Z")
    assert near_term.apply_retention(table, query(), np.array([.6])) == pytest.approx([.6])


def test_unfinished_future_labels_do_not_supply_a_third_day():
    table = near_term.fit_retention(history(), 60, before="2026-04-16T10:30Z")
    assert near_term.apply_retention(table, query(), np.array([.6])) == pytest.approx([.6])


@pytest.mark.parametrize("changes", [
    {"subject": "bob"}, {"time": pd.Timestamp("2026-04-24T10:00Z")},
    {"time": pd.Timestamp("2026-04-23T12:00Z")},
    {"latest_home": 0.}, {"latest_home": np.nan},
    {"dir_away": 1.}, {"distance_delta_15m": .06},
])
def test_other_residents_times_and_live_departure_evidence_bypass_prior(changes):
    table = near_term.fit_retention(history(), 60, before="2026-04-17T00:00Z")
    assert near_term.apply_retention(table, query(**changes), np.array([.6])) == pytest.approx([.6])


def test_calendar_uses_the_configured_local_weekday():
    frame = history()
    config.configure(settings(timezone="America/Chicago"))
    frame["time"] = pd.to_datetime(["2026-04-03T03:00Z", "2026-04-10T03:00Z", "2026-04-17T03:00Z"])
    table = near_term.fit_retention(frame, 60, before="2026-04-18T00:00Z")
    # Friday 03:00 UTC is Thursday 22:00 in Chicago.
    assert near_term.apply_retention(table, query(time=pd.Timestamp("2026-04-24T03:00Z")), np.array([.6])) == pytest.approx([.75])


def test_retention_must_win_repeated_folds_and_the_existing_gate():
    scored = pd.DataFrame({"p": [.6] * 12, "retention_p": [.8] * 12,
                           "actual": [1.] * 12, "baseline_instant_persistence": [0.] * 12,
                           "fold": [0] * 4 + [1] * 4 + [2] * 4})
    assert near_term.qualify(scored)["retention"]
    scored.loc[scored.fold < 2, "retention_p"] = .5
    assert not near_term.qualify(scored)["retention"]
    scored["baseline_instant_persistence"] = 1.
    assert not near_term.qualify(scored)["ship"]


def test_qualified_serving_applies_the_prior_only_to_the_requested_lead(monkeypatch):
    table = near_term.fit_retention(history(), 60, before="2026-04-17T00:00Z")
    rows = query()
    model = DummyRegressor(strategy="constant", constant=-.4).fit(rows[["state_now"]], [-.4])
    monkeypatch.setattr(near_term, "build", lambda *args, **kwargs: rows)
    artifact = {"identity": near_term.identity(), "version": near_term.VERSION,
                "metrics": {"60": {"alice": {"ship": True, "retention": True}}},
                "models": {60: {"model": model, "columns": ["state_now"], "retention": table}}}
    result = near_term.forecast(artifact, None, rows.time.iloc[0])["alice"]
    assert next(p for p in result["points"] if p["minutes"] == 60)["p"] == pytest.approx(.75)
    assert next(p for p in result["points"] if p["minutes"] == 120)["p"] is None


def test_previous_artifact_keeps_serving_while_a_new_model_is_prepared(tmp_path):
    import pickle
    artifact = {"identity": near_term.identity(version="near-term-1"), "version": "near-term-1",
                "metrics": {}, "models": {}}
    (tmp_path / "near_term.pkl").write_bytes(pickle.dumps(artifact))
    assert near_term.load(tmp_path) is not None
    config.configure(settings(people=["person.someone_else"]))
    assert near_term.load(tmp_path) is None


def test_near_only_training_preserves_other_models_and_their_training_time(tmp_path, monkeypatch):
    import pickle
    from fastapi import Response
    from occupancy_forecast import server
    hourly, eta, routine = {1: {"qualified": True}}, {"alice": {}}, {"alice": {}}
    for key, value in [("models", hourly), ("eta_models", eta), ("out_routine", routine),
                       ("last_train", "2026-04-20T00:00:00Z")]:
        monkeypatch.setitem(server._state, key, value)
    monkeypatch.setattr(server, "_too_little_history", lambda: None)
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    def fit(source, path):
        summary = {"version": near_term.VERSION, "trained_at": "2026-04-23T00:00:00Z",
                   "duration_s": 1., "metrics": {}}
        (path / "near_term.pkl").write_bytes(pickle.dumps({**summary, "identity": near_term.identity(), "models": {}}))
        return summary
    monkeypatch.setattr(near_term, "train_all", fit)
    result = server.run_train(Response(), near_only=True)
    assert result["near_term"]["version"] == near_term.VERSION
    assert server.api_near_term_metrics()["trained_at"] == "2026-04-23T00:00:00Z"
    assert server._state["models"] is hourly
    assert server._state["eta_models"] is eta
    assert server._state["out_routine"] is routine
    assert server._state["last_train"] == "2026-04-20T00:00:00Z"
    assert not server._train_lock.locked()
