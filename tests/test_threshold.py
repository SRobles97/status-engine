import pandas as pd
import pytest
from engine.algorithms import ThresholdAlgorithm


def _df(values):
    return pd.DataFrame({
        "time": pd.date_range("2026-06-20", periods=len(values), freq="min", tz="UTC"),
        "phase_a_active_power": values,
    })


def test_threshold_above_is_load_below_is_off():
    algo = ThresholdAlgorithm(company="C", device_key="D",
                              power_column="phase_a_active_power", threshold_w=1100)
    out = algo.classify(_df([0.0, 1100.0, 1100.1, 5000.0]))
    assert out.tolist() == ["OFF", "OFF", "LOAD", "LOAD"]  # strictly greater than


def test_threshold_name_and_invalid_column():
    assert ThresholdAlgorithm("C", "D", "total_active_power", 10).name == "threshold"
    with pytest.raises(ValueError):
        ThresholdAlgorithm("C", "D", "not_a_column", 10)


def test_threshold_accepts_and_validates_guard_column():
    a = ThresholdAlgorithm(company="C", device_key="D", power_column="phase_a_active_power",
                           threshold_w=1100, guard_column="total_current", guard_min=1.0)
    assert a.guard_column == "total_current" and a.guard_min == 1.0
    with pytest.raises(ValueError):
        ThresholdAlgorithm(company="C", device_key="D", power_column="phase_a_active_power",
                           threshold_w=1100, guard_column="bogus")
