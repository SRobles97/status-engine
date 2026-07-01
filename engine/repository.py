from __future__ import annotations

from typing import Optional

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from engine.algorithms import ALLOWED_POWER_COLUMNS


def connect(dsn: str):
    return psycopg2.connect(dsn)


def resolve_device_id(conn, device_key: str) -> Optional[int]:
    # device_key is globally unique in this deployment, so the <Company> folder
    # in the algorithms tree is purely organizational and is not used to resolve.
    sql = (
        "SELECT d.id FROM devices d "
        "WHERE d.device_key = %s AND d.is_active = true"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (device_key,))
        row = cur.fetchone()
    return int(row[0]) if row else None


def fetch_device_schedules(conn, device_id: int):
    """Return (schedule_versions, merged_special_days) for a device.

    schedule_versions: list of (valid_from, valid_to, shift_type, day_schedules).
    merged_special_days: special_days from all versions merged into one dict.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT valid_from, valid_to, COALESCE(shift_type, 'day'), day_schedules "
            "FROM device_schedules WHERE device_id = %s ORDER BY valid_from, shift_type",
            (device_id,),
        )
        schedules = [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]
        cur.execute(
            "SELECT special_days FROM device_schedules WHERE device_id = %s",
            (device_id,),
        )
        special: dict = {}
        for (sd,) in cur.fetchall():
            if isinstance(sd, dict):
                special.update(sd)
    return schedules, special


def device_timezone(conn, device_id: int) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT timezone FROM devices WHERE id = %s", (device_id,))
        row = cur.fetchone()
    return row[0] if row else None


def fetch_window(conn, device_id: int, power_column: str, start, end,
                 extra_columns=None) -> pd.DataFrame:
    if power_column not in ALLOWED_POWER_COLUMNS:
        raise ValueError(f"invalid power_column: {power_column!r}")
    extras = list(extra_columns or [])
    for col in extras:
        if col not in ALLOWED_POWER_COLUMNS:
            raise ValueError(f"invalid extra column: {col!r}")
    select_cols = ["time", power_column] + extras
    sql = (
        f"SELECT {', '.join(select_cols)} FROM power_measurements "
        "WHERE device_id = %s AND time >= %s AND time < %s "
        f"AND {power_column} IS NOT NULL ORDER BY time ASC"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (device_id, start, end))
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=select_cols)


def upsert_measurement_status(
    conn, device_id: int, times: list, statuses: list[str], algorithm: str
) -> int:
    rows = [(device_id, t, s, algorithm) for t, s in zip(times, statuses)]
    if not rows:
        return 0
    sql = (
        "INSERT INTO measurement_status (device_id, time, status, algorithm) VALUES %s "
        "ON CONFLICT (device_id, time) DO UPDATE "
        "SET status = EXCLUDED.status, algorithm = EXCLUDED.algorithm, computed_at = now()"
    )
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    return len(rows)


def upsert_device_algo_status(
    conn, device_id: int, status: str, algorithm: str, last_time, count: int
) -> None:
    sql = (
        "INSERT INTO device_algo_status "
        "(device_id, status, algorithm, last_measurement_time, measurement_count, computed_at) "
        "VALUES (%s, %s, %s, %s, %s, now()) "
        "ON CONFLICT (device_id) DO UPDATE SET status = EXCLUDED.status, "
        "algorithm = EXCLUDED.algorithm, last_measurement_time = EXCLUDED.last_measurement_time, "
        "measurement_count = EXCLUDED.measurement_count, computed_at = now()"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (device_id, status, algorithm, last_time, count))


def insert_run_log(conn, *, company, device_key, device_id, algorithm,
                   processed_count, updated_count, result, error_detail, duration_ms) -> None:
    sql = (
        "INSERT INTO status_run_log "
        "(company, device_key, device_id, algorithm, processed_count, updated_count, "
        "result, error_detail, duration_ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (company, device_key, device_id, algorithm, processed_count,
                          updated_count, result, error_detail, duration_ms))


def try_advisory_lock(conn, key: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        row = cur.fetchone()
    return bool(row[0])
