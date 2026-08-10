import pandas as pd
from engine.algorithms import ThresholdAlgorithm
from engine import intervals as intervals_mod


def test_threshold_algorithm_does_not_emit_idle_by_default():
    algo = ThresholdAlgorithm(
        company="X", device_key="k", power_column="total_current", threshold_w=10.0
    )
    assert algo.emits_idle is False


def test_remap_applied_only_when_emits_idle_is_false():
    statuses = pd.Series(["OFF", "IDLE", "LOAD"])
    assert list(intervals_mod.remap_idle_to_load(statuses)) == ["OFF", "LOAD", "LOAD"]
    # emits_idle=True must pass the series through untouched
    assert list(statuses) == ["OFF", "IDLE", "LOAD"]
