from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd

from engine.algorithms import ThresholdAlgorithm, KMeansAlgorithm, DegenerateWindowError
from engine.discovery import DiscoveredAlgorithm
from engine import runner


def _fake_repo(window_df, *, raise_on_fetch=None):
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    if raise_on_fetch is not None:
        repo.fetch_window.side_effect = raise_on_fetch
    else:
        repo.fetch_window.return_value = window_df
    repo.upsert_measurement_status.return_value = len(window_df) if window_df is not None else 0
    return repo


NOW = datetime(2026, 6, 20, 15, 0, tzinfo=timezone.utc)


def _threshold():
    return DiscoveredAlgorithm(
        algorithm=ThresholdAlgorithm(company="C", device_key="D",
            power_column="phase_a_active_power", threshold_w=1100, smoothing_minutes=10),
        device_id=52)


def test_window_bounds_is_today_in_tz():
    start, end = runner.window_bounds(NOW, "UTC", 0)
    assert start == datetime(2026, 6, 20, 0, 0, tzinfo=timezone.utc)
    assert end == NOW


def test_run_once_classifies_and_upserts():
    df = pd.DataFrame({
        "time": pd.date_range("2026-06-20", periods=3, freq="min", tz="UTC"),
        "phase_a_active_power": [0.0, 5000.0, 5000.0],
    })
    repo = _fake_repo(df)
    res = runner.run_once(repo, conn=MagicMock(), discovered=_threshold(),
                          now=NOW, window_days=0, default_tz="UTC")
    assert res.result == "ok"
    assert res.processed_count == 3
    assert res.updated_count == 3
    # statuses passed to the upsert reflect threshold classification.
    # signature: upsert_measurement_status(conn, device_id, times, statuses, algorithm)
    _, _, _, statuses, algo = repo.upsert_measurement_status.call_args.args
    assert statuses == ["OFF", "LOAD", "LOAD"]
    assert algo == "threshold"
    # device_algo_status updated with last row's status.
    # signature: upsert_device_algo_status(conn, device_id, status, algorithm, last_time, count)
    args = repo.upsert_device_algo_status.call_args.args
    assert args[2] == "LOAD"


def test_run_once_unresolved_device_is_skipped():
    repo = _fake_repo(pd.DataFrame())
    disc = DiscoveredAlgorithm(algorithm=_threshold().algorithm, device_id=None)
    res = runner.run_once(repo, MagicMock(), disc, NOW, 0, "UTC")
    assert res.result == "skipped"
    repo.fetch_window.assert_not_called()


def test_run_once_empty_window_is_skipped():
    repo = _fake_repo(pd.DataFrame(columns=["time", "phase_a_active_power"]))
    res = runner.run_once(repo, MagicMock(), _threshold(), NOW, 0, "UTC")
    assert res.result == "skipped"
    assert res.processed_count == 0
    repo.upsert_measurement_status.assert_not_called()


def test_run_once_degenerate_kmeans_is_skipped():
    df = pd.DataFrame({
        "time": pd.date_range("2026-06-20", periods=4, freq="min", tz="UTC"),
        "total_active_power": [3.0, 3.0, 3.0, 3.0],
    })
    repo = _fake_repo(df)
    disc = DiscoveredAlgorithm(
        algorithm=KMeansAlgorithm(company="C", device_key="D",
            power_column="total_active_power", n_clusters=3), device_id=7)
    res = runner.run_once(repo, MagicMock(), disc, NOW, 0, "UTC")
    assert res.result == "skipped"
    assert "distinct" in (res.error_detail or "")


def test_run_all_continues_after_device_error_and_logs_each():
    good = _threshold()
    bad = DiscoveredAlgorithm(algorithm=_threshold().algorithm, device_id=99)
    df = pd.DataFrame({
        "time": pd.date_range("2026-06-20", periods=1, freq="min", tz="UTC"),
        "phase_a_active_power": [5000.0],
    })
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    # bad device (id 99) raises during fetch; good device (id 52) succeeds
    def fetch(conn, device_id, col, start, end, extra_columns=None):
        if device_id == 99:
            raise RuntimeError("boom")
        return df
    repo.fetch_window.side_effect = fetch
    repo.upsert_measurement_status.return_value = 1

    results = runner.run_all(repo, MagicMock(), [bad, good], NOW, 0, "UTC")
    by_result = sorted(r.result for r in results)
    assert by_result == ["error", "ok"]
    # every device produced a run-log row (FR-09)
    assert repo.insert_run_log.call_count == 2
    err = next(r for r in results if r.result == "error")
    assert "boom" in (err.error_detail or "")


def _flat_schedule():
    from datetime import date
    flat = {d: {"workHours": {"start": "08:00", "end": "17:00"}}
            for d in ("monday", "tuesday", "wednesday", "thursday",
                      "friday", "saturday", "sunday")}
    return [(date(2026, 1, 1), None, "day", flat)]


def _df_three_times():
    # 11:30Z=07:30 local (OFF), 12:30Z=08:30 local (ON), 21:30Z=17:30 local (OFF)
    return pd.DataFrame({
        "time": [datetime(2026, 6, 22, 11, 30, tzinfo=timezone.utc),
                 datetime(2026, 6, 22, 12, 30, tzinfo=timezone.utc),
                 datetime(2026, 6, 22, 21, 30, tzinfo=timezone.utc)],
        "phase_a_active_power": [5000.0, 5000.0, 5000.0],
    })


def test_run_once_filters_off_schedule_when_enabled():
    repo = MagicMock()
    repo.device_timezone.return_value = "America/Santiago"
    repo.fetch_window.return_value = _df_three_times()
    repo.fetch_device_schedules.return_value = (_flat_schedule(), {})
    repo.upsert_measurement_status.return_value = 1
    res = runner.run_once(repo, MagicMock(), _threshold(), NOW, 0, "America/Santiago",
                          on_schedule_only=True)
    assert res.result == "ok"
    assert res.processed_count == 1            # only the on-schedule row survived
    _, _, times, statuses, _ = repo.upsert_measurement_status.call_args.args
    assert times == [datetime(2026, 6, 22, 12, 30, tzinfo=timezone.utc)]
    assert statuses == ["LOAD"]


def test_run_once_no_schedule_classifies_all_even_when_enabled():
    repo = MagicMock()
    repo.device_timezone.return_value = "America/Santiago"
    repo.fetch_window.return_value = _df_three_times()
    repo.fetch_device_schedules.return_value = ([], {})   # device has no schedule
    repo.upsert_measurement_status.return_value = 3
    res = runner.run_once(repo, MagicMock(), _threshold(), NOW, 0, "America/Santiago",
                          on_schedule_only=True)
    assert res.result == "ok"
    assert res.processed_count == 3


def test_run_once_toggle_off_skips_schedule_fetch():
    repo = MagicMock()
    repo.device_timezone.return_value = "America/Santiago"
    repo.fetch_window.return_value = _df_three_times()
    repo.upsert_measurement_status.return_value = 3
    res = runner.run_once(repo, MagicMock(), _threshold(), NOW, 0, "America/Santiago",
                          on_schedule_only=False)
    assert res.processed_count == 3
    repo.fetch_device_schedules.assert_not_called()


def test_run_once_guard_forces_off_when_guard_below_min():
    algo = ThresholdAlgorithm(company="C", device_key="D",
                              power_column="phase_a_active_power", threshold_w=1100,
                              guard_column="total_current", guard_min=1.0)
    disc = DiscoveredAlgorithm(algorithm=algo, device_id=42)
    df = pd.DataFrame({
        "time": pd.date_range("2026-06-22T12:00", periods=3, freq="min", tz="UTC"),
        "phase_a_active_power": [5000.0, 10838.0, 0.0],  # row1 = glitch (power up, current 0)
        "total_current": [12.0, 0.0, 0.0],
    })
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    repo.fetch_window.return_value = df
    repo.upsert_measurement_status.return_value = 3
    res = runner.run_once(repo, MagicMock(), disc, NOW, 0, "UTC", on_schedule_only=False)
    assert res.result == "ok"
    _, _, _, statuses, _ = repo.upsert_measurement_status.call_args.args
    assert statuses == ["LOAD", "OFF", "OFF"]  # glitch forced OFF despite power > 1100
    assert repo.fetch_window.call_args.kwargs.get("extra_columns") == ["total_current"]


def test_guard_overrides_smoothing():
    # smoothing would bridge the middle dip into LOAD, but current<min must keep it OFF
    algo = ThresholdAlgorithm(company="C", device_key="D",
                              power_column="phase_a_active_power", threshold_w=1100,
                              smoothing_minutes=10,
                              guard_column="total_current", guard_min=1.0)
    disc = DiscoveredAlgorithm(algorithm=algo, device_id=42)
    df = pd.DataFrame({
        "time": pd.date_range("2026-06-22T12:00", periods=3, freq="min", tz="UTC"),
        "phase_a_active_power": [5000.0, 0.0, 5000.0],   # dip in the middle
        "total_current": [12.0, 0.09, 12.0],             # middle row: machine truly off
    })
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    repo.fetch_window.return_value = df
    repo.upsert_measurement_status.return_value = 3
    runner.run_once(repo, MagicMock(), disc, NOW, 0, "UTC", on_schedule_only=False)
    _, _, _, statuses, _ = repo.upsert_measurement_status.call_args.args
    assert statuses == ["LOAD", "OFF", "LOAD"]  # guard beats smoothing on the dip


def _algo():
    return ThresholdAlgorithm(company="C", device_key="D",
                              power_column="phase_a_active_power", threshold_w=1100,
                              smoothing_minutes=0)


def test_run_once_emits_intervals_when_enabled():
    # full window: OFF then LOAD, UTC, no schedule -> LOAD-only intervals
    df = pd.DataFrame({
        "time": pd.date_range("2026-06-22T12:00", periods=3, freq="h", tz="UTC"),
        "phase_a_active_power": [0.0, 5000.0, 6000.0]})
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    repo.fetch_window.return_value = df
    repo.fetch_device_schedules.return_value = ([], {})   # no schedule
    repo.fetch_threshold_minutes.return_value = 15.0
    repo.device_company_id.return_value = 3
    repo.get_or_create_unassigned_classification.return_value = 7
    repo.upsert_measurement_status.return_value = 3
    disc = DiscoveredAlgorithm(algorithm=_algo(), device_id=42)
    res = runner.run_once(repo, MagicMock(), disc, NOW, 0, "UTC",
                          on_schedule_only=False, emit_intervals=True,
                          gap_seconds=7200, interval_source="algo",
                          on_schedule_rule="majority")
    assert res.result == "ok"
    # delete-then-insert happened for the touched day
    assert repo.delete_algo_intervals_for_day.called
    assert repo.insert_intervals.called
    inserted = repo.insert_intervals.call_args.args[1]
    assert all(r.source == "algo" for r in inserted)
    assert repo.refresh_daily_facts.called


def test_run_once_no_intervals_when_disabled():
    df = pd.DataFrame({
        "time": pd.date_range("2026-06-22T12:00", periods=2, freq="h", tz="UTC"),
        "phase_a_active_power": [0.0, 5000.0]})
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    repo.fetch_window.return_value = df
    repo.upsert_measurement_status.return_value = 2
    disc = DiscoveredAlgorithm(algorithm=_algo(), device_id=42)
    res = runner.run_once(repo, MagicMock(), disc, NOW, 0, "UTC",
                          on_schedule_only=False, emit_intervals=False)
    assert res.result == "ok"
    assert not repo.insert_intervals.called
