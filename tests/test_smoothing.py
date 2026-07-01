import pandas as pd
from engine.smoothing import smooth_statuses


def _t(n):
    return pd.Series(pd.date_range("2026-06-20", periods=n, freq="min", tz="UTC"))


def test_short_gap_between_loads_is_absorbed():
    statuses = pd.Series(["LOAD", "OFF", "OFF", "OFF", "LOAD"])
    out = smooth_statuses(statuses, _t(5), smoothing_minutes=10)
    assert out.tolist() == ["LOAD", "LOAD", "LOAD", "LOAD", "LOAD"]


def test_long_gap_is_preserved():
    statuses = pd.Series(["LOAD"] + ["OFF"] * 15 + ["LOAD"])
    out = smooth_statuses(statuses, _t(17), smoothing_minutes=10)
    assert out.tolist() == ["LOAD"] + ["OFF"] * 15 + ["LOAD"]


def test_leading_off_and_trailing_gap_untouched():
    statuses = pd.Series(["OFF", "OFF", "LOAD", "OFF", "OFF"])
    out = smooth_statuses(statuses, _t(5), smoothing_minutes=10)
    assert out.tolist() == ["OFF", "OFF", "LOAD", "OFF", "OFF"]


def test_zero_window_returns_unchanged():
    statuses = pd.Series(["LOAD", "OFF", "LOAD"])
    out = smooth_statuses(statuses, _t(3), smoothing_minutes=0)
    assert out.tolist() == ["LOAD", "OFF", "LOAD"]
