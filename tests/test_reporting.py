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


def test_refresh_daily_facts_writes_idle_columns():
    conn, cur = _conn_with_cursor()
    reporting.refresh_daily_facts(conn, 52, date(2026, 6, 22), "America/Santiago", "algo")
    sql, _ = cur.execute.call_args.args
    assert "idle_minutes" in sql
    assert "idle_minutes_on_schedule" in sql
    assert "idle_minutes_off_schedule" in sql
    assert "idle_interval_count" in sql
    assert "i.state='IDLE'" in sql.replace(" ", "")


def test_blank_facts_zero_the_idle_columns():
    # Un día especial sin trabajo llega a _upsert_blank_facts a través de la
    # función pública, igual que test_no_work_special_day_blank_facts.
    conn, cur = _conn_with_cursor()
    special = {"2026-06-22": {"workHours": None}}
    reporting.refresh_daily_facts(conn, 52, date(2026, 6, 22), "America/Santiago",
                                  "algo", special_days=special)
    sql, _ = cur.execute.call_args.args
    assert "idle_minutes=0" in sql.replace(" ", "")
    assert "idle_interval_count=0" in sql.replace(" ", "")


def _split_top_level(text):
    """Split on commas at paren-depth 0 so 'CASE WHEN ... THEN a, b ELSE c END'
    style expressions containing commas inside parens aren't torn apart."""
    items, current, depth = [], [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _insert_column_list(sql, table="device_daily_facts"):
    marker = f"INSERT INTO {table} ("
    start = sql.index(marker) + len(marker)
    end = sql.index(")", start)
    return _split_top_level(sql[start:end])


def _select_expression_list(sql):
    end = sql.index("FROM intervals i")
    # The outer SELECT (not the CTE's inner one) is the last SELECT before
    # "FROM intervals i".
    start = sql.rindex("SELECT", 0, end) + len("SELECT")
    return _split_top_level(sql[start:end])


def _values_tuple_list(sql):
    start = sql.index("VALUES (") + len("VALUES (")
    end = sql.index(")\n", start)
    return _split_top_level(sql[start:end])


def test_insert_columns_and_select_list_stay_aligned():
    # Guarda contra el riesgo del brief: si la lista de columnas del INSERT y
    # la lista del SELECT se desalinean por posición, los minutos de un
    # estado terminan escritos silenciosamente en la columna de otro.
    conn, cur = _conn_with_cursor()
    reporting.refresh_daily_facts(conn, 52, date(2026, 6, 22), "America/Santiago", "algo")
    sql, _ = cur.execute.call_args.args
    cols = _insert_column_list(sql)
    selects = _select_expression_list(sql)
    assert len(cols) == len(selects)

    idx = cols.index("idle_minutes")
    assert "state='IDLE'" in selects[idx].replace(" ", "")
    assert "on_sched" not in selects[idx]

    idx = cols.index("idle_minutes_on_schedule")
    assert "state='IDLE'" in selects[idx].replace(" ", "")
    assert "on_sched/60.0" in selects[idx].replace(" ", "")

    idx = cols.index("idle_minutes_off_schedule")
    assert "state='IDLE'" in selects[idx].replace(" ", "")
    assert "on_sched)/60.0" in selects[idx].replace(" ", "")

    idx = cols.index("idle_interval_count")
    assert "state='IDLE'" in selects[idx].replace(" ", "")
    assert "SUM(CASE" in selects[idx]


def test_blank_facts_insert_columns_and_values_stay_aligned():
    # Mismo riesgo estructural pero para _upsert_blank_facts, el camino de día
    # especial: nadie lo ejerce contra una base de datos real, solo por lectura.
    conn, cur = _conn_with_cursor()
    special = {"2026-06-22": {"workHours": None}}
    reporting.refresh_daily_facts(conn, 52, date(2026, 6, 22), "America/Santiago",
                                  "algo", special_days=special)
    sql, _ = cur.execute.call_args.args
    cols = _insert_column_list(sql)
    values = _values_tuple_list(sql)
    assert len(cols) == len(values)
    assert values[cols.index("idle_minutes")] == "0"
    assert values[cols.index("idle_minutes_on_schedule")] == "0"
    assert values[cols.index("idle_minutes_off_schedule")] == "0"
    assert values[cols.index("idle_interval_count")] == "0"
    assert values[cols.index("computed_at")] == "now()"


def _class_insert_sql():
    conn, cur = _conn_with_cursor()
    reporting.refresh_classification_facts(
        conn, 52, date(2026, 6, 22), "America/Santiago", unassigned_id=7, source="algo")
    return cur.execute.call_args_list[1].args[0]


def test_classification_facts_include_open_intervals():
    sql = _class_insert_sql()
    assert "end_time IS NOT NULL" not in sql
    assert "duration_seconds IS NOT NULL" not in sql
    assert "COALESCE(i.duration_seconds, EXTRACT(EPOCH FROM (now() - i.start_time)))" in sql
    assert "COALESCE(i.on_schedule_seconds, 0)" in sql


def test_classification_facts_closed_interval_behaviour_unchanged():
    sql = _class_insert_sql()
    assert "COALESCE(i.duration_seconds," in sql
    assert "i.dur >= 0" in sql
    assert "SUM(i.dur / 60.0)" in sql and "SUM((i.dur - i.on_sched) / 60.0)" in sql
    assert "i.state = 'OFF'" in sql and "i.source = %(source)s" in sql
