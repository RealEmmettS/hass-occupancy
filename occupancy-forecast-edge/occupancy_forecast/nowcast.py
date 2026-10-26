"""Refresh causal origin features at the actual prediction time.

Training and serving share the trailing five-minute presence/zone integration,
distance units, and direction features. Targets and historical lags stay on the
half-hour grid. Explicit unknown presence clears the forecast rather than
retaining a previously known state.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config, features

# The window the origin state is measured over, ending at `now`.
#
# Five minutes is a window length, not a tuned dwell threshold: it rejects
# anything under 2.5 minutes because such a blip cannot reach half the window,
# and it admits a real change 2.5 minutes in. Shorter would start passing the
# jitter; longer would give back the responsiveness this exists to buy.
WINDOW_MINUTES = config.ORIGIN_WINDOW_MINUTES

# How far back to read events. Only needs to cover the window plus enough slack
# for `seeded_states` to find a prior value, which it does by seeding anyway.
LOOKBACK_MINUTES = 6 * 60


def presence_fraction(events: list[tuple[str, str]], at: pd.Timestamp,
                      window_min: int = WINDOW_MINUTES) -> float | None:
    """Fraction of the `window_min` minutes ENDING at `at` spent at home.

    Same definition and the same integration as `home_frac`, over a shorter
    window -- see `features.slot_fraction`, whose `minutes` argument exists for
    this caller alone.
    """
    if not events:
        return None
    if window_min == config.ORIGIN_WINDOW_MINUTES:
        out = features.trailing_fraction(events, pd.DatetimeIndex([at]), config.HOME_STATE)
    else:
        window = pd.DatetimeIndex([at - pd.Timedelta(minutes=window_min)], tz="UTC")
        out = features.slot_fraction(events, window, config.HOME_STATE, window_min)
    value = out["frac"].iloc[0]
    return None if pd.isna(value) else float(value)


def apply(rows: pd.DataFrame, source, at: pd.Timestamp) -> pd.DataFrame:
    """Refresh the origin block while retaining target calendars and lags.

    A failed optional source keeps its grid value. An explicit unknown presence
    becomes NaN and therefore cannot produce a curve or an arrival estimate.
    """
    if rows.empty:
        return rows

    start = (at - pd.Timedelta(minutes=LOOKBACK_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stop = at.strftime("%Y-%m-%dT%H:%M:%SZ")
    index = pd.DatetimeIndex([at], tz="UTC")

    fractions: dict[str, float] = {}
    coverages: dict[str, float] = {}
    minutes: dict[str, float] = {}
    origin_frames: dict[str, pd.DataFrame] = {}
    for subject in config.SUBJECTS:
        try:
            events = features.presence_events(source, subject, start, stop)
        except Exception:  # noqa: BLE001
            # A source that cannot answer for one subject must not cost the
            # others their nowcast, nor the whole cycle its forecast.
            continue
        if not events:
            continue
        origin = features.trailing_fraction(events, index, config.HOME_STATE)
        fractions[subject.slug] = float(origin["frac"].iloc[0])
        coverages[subject.slug] = float(origin["coverage"].iloc[0])
        held = features.minutes_in_state(events, index)[0]
        if not np.isnan(held):
            minutes[subject.slug] = float(held)
        # Use the same helpers and windows as training. Refresh distance and
        # direction while travelling instead of retaining a half-hour-old fix.
        recent = pd.DatetimeIndex([at - pd.Timedelta(hours=1),
                                   at - pd.Timedelta(minutes=30), at])
        fresh = pd.DataFrame(index=recent)
        try:
            features._add_proximity(fresh, source, subject, start, stop, recent)
        except Exception:  # independent optional source
            pass
        if subject.is_person:
            features._add_zones(fresh, events, recent)
        origin_frames[subject.slug] = fresh.iloc[[-1]]

    if not fractions:
        return rows

    out = rows.copy()
    updated = out["subject"].isin(fractions)
    out.loc[updated, "current_at"] = at.isoformat()
    out.loc[updated, "state_now"] = out.loc[updated, "subject"].map(fractions)
    out.loc[updated, "coverage"] = out.loc[updated, "subject"].map(coverages)

    held = out["subject"].map(minutes)
    out["minutes_in_state"] = held.where(held.notna(), out["minutes_in_state"])

    # Keep the row internally consistent. Without this `state_now` says "just
    # left" while `other_alice` still reports where they were half an hour ago,
    # and the cross-subject features contradict the origin they sit beside.
    for slug, fraction in fractions.items():
        column = f"other_{slug}"
        if column in out.columns:
            out.loc[out["subject"] != slug, column] = fraction

    for slug, fresh in origin_frames.items():
        mask = out["subject"] == slug
        for column in fresh.columns:
            value = fresh[column].iloc[0]
            if column in out:
                out.loc[mask, column] = value

    # Household zone features are the same union used by features.build.
    for column in features.zone_columns():
        if column in out:
            members = out.loc[out["subject"].isin([s.slug for s in config.PEOPLE]), column]
            if len(members) and members.notna().any():
                out.loc[out["subject"] == config.HOUSE_SLUG, column] = members.max()

    return out
