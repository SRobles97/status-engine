from unittest.mock import MagicMock, patch

import pandas as pd
from engine import repository


def _conn_with_cursor():
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def test_resolve_device_id_returns_value():
    conn, cur = _conn_with_cursor()
    cur.fetchone.return_value = (52,)
    assert repository.resolve_device_id(conn, "F1") == 52
    sql, params = cur.execute.call_args.args
    assert "device_key = %s" in sql
    assert "c.name" not in sql  # company is not part of resolution
    assert params == ("F1",)


def test_resolve_device_id_none_when_missing():
    conn, cur = _conn_with_cursor()
    cur.fetchone.return_value = None
    assert repository.resolve_device_id(conn, "ghost") is None


def test_fetch_window_builds_whitelisted_query():
    conn, cur = _conn_with_cursor()
    cur.fetchall.return_value = [
        (pd.Timestamp("2026-06-20T12:00Z"), 1500.0),
    ]
    cur.description = [("time",), ("phase_a_active_power",)]
    df = repository.fetch_window(conn, 52, "phase_a_active_power",
                                 pd.Timestamp("2026-06-20T00:00:00Z"), pd.Timestamp("2026-06-21T00:00:00Z"))
    assert list(df.columns) == ["time", "phase_a_active_power"]
    assert len(df) == 1
    sql = cur.execute.call_args.args[0]
    assert "phase_a_active_power" in sql and "device_id = %s" in sql


def test_fetch_window_rejects_non_whitelisted_column():
    conn, _ = _conn_with_cursor()
    import pytest
    with pytest.raises(ValueError):
        repository.fetch_window(conn, 52, "drop_table", None, None)


def test_upsert_measurement_status_uses_execute_values():
    conn, cur = _conn_with_cursor()
    with patch("engine.repository.execute_values") as ev:
        n = repository.upsert_measurement_status(
            conn, 52, [pd.Timestamp("2026-06-20T12:00Z")], ["LOAD"], "threshold")
    assert n == 1
    template_sql = ev.call_args.args[1]
    assert "measurement_status" in template_sql
    assert "ON CONFLICT (device_id, time) DO UPDATE" in template_sql
    rows = ev.call_args.args[2]
    assert rows == [(52, pd.Timestamp("2026-06-20T12:00Z"), "LOAD", "threshold")]


def test_upsert_device_algo_status_upserts_pk():
    conn, cur = _conn_with_cursor()
    repository.upsert_device_algo_status(
        conn, 52, "LOAD", "threshold", pd.Timestamp("2026-06-20T12:00Z"), 100)
    sql, params = cur.execute.call_args.args
    assert "device_algo_status" in sql
    assert "ON CONFLICT (device_id) DO UPDATE" in sql
    assert params[0] == 52 and params[1] == "LOAD"


def test_insert_run_log_writes_all_fields():
    conn, cur = _conn_with_cursor()
    repository.insert_run_log(conn, company="C", device_key="D", device_id=52,
                              algorithm="threshold", processed_count=10, updated_count=10,
                              result="ok", error_detail=None, duration_ms=5)
    sql, params = cur.execute.call_args.args
    assert "status_run_log" in sql
    assert params == ("C", "D", 52, "threshold", 10, 10, "ok", None, 5)


def test_try_advisory_lock_returns_bool():
    conn, cur = _conn_with_cursor()
    cur.fetchone.return_value = (True,)
    assert repository.try_advisory_lock(conn, 9_400_000_000) is True
    sql, params = cur.execute.call_args.args
    assert "pg_try_advisory_lock" in sql and params == (9_400_000_000,)


def test_fetch_device_schedules_returns_versions_and_merged_special():
    from datetime import date
    conn, cur = _conn_with_cursor()
    cur.fetchall.side_effect = [
        [(date(2026, 2, 26), date(2026, 4, 30), "day", {"monday": {}}),
         (date(2026, 5, 1), None, "day", {"tuesday": {}})],
        [({"2026-05-01": {"workHours": None}},), ({"2026-05-21": {"workHours": None}},)],
    ]
    scheds, special = repository.fetch_device_schedules(conn, 42)
    assert len(scheds) == 2
    assert scheds[0][2] == "day" and scheds[1][1] is None
    assert special == {"2026-05-01": {"workHours": None}, "2026-05-21": {"workHours": None}}


def test_fetch_window_includes_extra_columns():
    conn, cur = _conn_with_cursor()
    cur.fetchall.return_value = [(pd.Timestamp("2026-06-20T12:00:00Z"), 1500.0, 12.0)]
    df = repository.fetch_window(conn, 42, "phase_a_active_power",
                                pd.Timestamp("2026-06-20T00:00:00Z"),
                                pd.Timestamp("2026-06-21T00:00:00Z"),
                                extra_columns=["total_current"])
    assert list(df.columns) == ["time", "phase_a_active_power", "total_current"]
    assert "total_current" in cur.execute.call_args.args[0]


def test_fetch_window_rejects_non_whitelisted_extra():
    import pytest
    conn, _ = _conn_with_cursor()
    with pytest.raises(ValueError):
        repository.fetch_window(conn, 42, "phase_a_active_power", None, None,
                                extra_columns=["evil"])
