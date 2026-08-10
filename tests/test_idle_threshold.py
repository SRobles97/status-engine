from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from engine.algorithms import ThresholdAlgorithm
from engine import intervals as intervals_mod
from engine import runner


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


def test_emit_algo_intervals_collapses_idle_to_load_when_flag_false():
    """With emits_idle=False, IDLE should be remapped to LOAD before build_intervals."""
    df = pd.DataFrame({
        "time": pd.date_range("2026-08-07T11:00", periods=3, freq="h", tz="UTC"),
        "total_current": [0.0, 5.0, 15.0]
    })
    statuses = pd.Series(["OFF", "IDLE", "LOAD"], index=df.index)

    repo = MagicMock()
    repo.fetch_threshold_minutes.return_value = 15.0
    repo.device_company_id.return_value = 13
    repo.get_or_create_unassigned_classification.return_value = 7

    conn = MagicMock()
    with patch.object(runner.reporting_mod, "refresh_daily_facts"), \
         patch.object(runner.reporting_mod, "refresh_classification_facts"), \
         patch.object(runner.intervals_mod, "build_intervals") as mock_build:
        mock_build.return_value = []
        runner._emit_algo_intervals(
            repo, conn, write_id=42, df=df, statuses_full=statuses,
            tz_name="UTC", schedules=[], special={}, gap_seconds=300.0,
            source="algo", rule="majority", emits_idle=False
        )

    # Capture what was passed to build_intervals
    statuses_arg = mock_build.call_args.kwargs["statuses"]

    # With emits_idle=False, IDLE should be remapped to LOAD
    assert "IDLE" not in statuses_arg, (
        f"IDLE should be remapped to LOAD, but got: {statuses_arg}")
    assert statuses_arg == ["OFF", "LOAD", "LOAD"], (
        f"Expected ['OFF', 'LOAD', 'LOAD'], got: {statuses_arg}")


def test_emit_algo_intervals_preserves_idle_when_flag_true():
    """With emits_idle=True, IDLE should pass through untouched to build_intervals."""
    df = pd.DataFrame({
        "time": pd.date_range("2026-08-07T11:00", periods=3, freq="h", tz="UTC"),
        "total_current": [0.0, 5.0, 15.0]
    })
    statuses = pd.Series(["OFF", "IDLE", "LOAD"], index=df.index)

    repo = MagicMock()
    repo.fetch_threshold_minutes.return_value = 15.0
    repo.device_company_id.return_value = 13
    repo.get_or_create_unassigned_classification.return_value = 7

    conn = MagicMock()
    with patch.object(runner.reporting_mod, "refresh_daily_facts"), \
         patch.object(runner.reporting_mod, "refresh_classification_facts"), \
         patch.object(runner.intervals_mod, "build_intervals") as mock_build:
        mock_build.return_value = []
        runner._emit_algo_intervals(
            repo, conn, write_id=42, df=df, statuses_full=statuses,
            tz_name="UTC", schedules=[], special={}, gap_seconds=300.0,
            source="algo", rule="majority", emits_idle=True
        )

    # Capture what was passed to build_intervals
    statuses_arg = mock_build.call_args.kwargs["statuses"]

    # With emits_idle=True, IDLE should pass through untouched
    assert "IDLE" in statuses_arg, (
        f"IDLE should be preserved, but got: {statuses_arg}")
    assert statuses_arg == ["OFF", "IDLE", "LOAD"], (
        f"Expected ['OFF', 'IDLE', 'LOAD'], got: {statuses_arg}")
