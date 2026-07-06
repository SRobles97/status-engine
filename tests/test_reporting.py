from datetime import date
from unittest.mock import MagicMock
from engine import reporting


def _conn_with_cursor():
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def test_refresh_daily_facts_filters_and_writes_algo_source():
    conn, cur = _conn_with_cursor()
    reporting.refresh_daily_facts(conn, 52, date(2026, 6, 22), "America/Santiago", "algo")
    sql, params = cur.execute.call_args.args
    assert "device_daily_facts" in sql
    assert "i.source = %(source)s" in sql
    assert "ON CONFLICT (device_id, day, source)" in sql
    assert params["source"] == "algo" and params["device_id"] == 52


def test_refresh_classification_facts_deletes_and_writes_algo_source():
    conn, cur = _conn_with_cursor()
    reporting.refresh_classification_facts(
        conn, 52, date(2026, 6, 22), "America/Santiago", unassigned_id=7, source="algo")
    delete_sql = cur.execute.call_args_list[0].args[0]
    assert "DELETE FROM device_daily_classification_facts" in delete_sql
    assert "source = %s" in delete_sql
    insert_sql = cur.execute.call_args_list[1].args[0]
    assert "i.source = %(source)s" in insert_sql and "'OFF'" in insert_sql


def test_no_work_special_day_blank_facts():
    conn, cur = _conn_with_cursor()
    special = {"2026-06-22": {"workHours": None}}
    reporting.refresh_daily_facts(conn, 52, date(2026, 6, 22), "America/Santiago",
                                  "algo", special_days=special)
    sql, params = cur.execute.call_args.args
    assert "VALUES" in sql and "%(source)s" in sql  # blank row parameterized by source
    assert params["source"] == "algo"               # tagged algo
