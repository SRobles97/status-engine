from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional


def is_no_work_special_day(special_days: Optional[Dict[str, Any]], d: date) -> bool:
    if not special_days:
        return False
    entry = special_days.get(d.isoformat())
    if not isinstance(entry, dict):
        return entry is not None
    wh = entry.get("workHours")
    if not wh or not isinstance(wh, dict):
        return True
    return "start" not in wh or "end" not in wh


_BLANK_FACTS_SQL = """
    INSERT INTO device_daily_facts (
      device_id, day, source,
      total_minutes,
      load_minutes, load_minutes_on_schedule, load_minutes_off_schedule,
      off_minutes, off_minutes_on_schedule, off_minutes_off_schedule,
      allowed_off_minutes, allowed_off_minutes_on_schedule, allowed_off_minutes_off_schedule,
      idle_minutes, idle_minutes_on_schedule, idle_minutes_off_schedule,
      interval_count, load_interval_count, off_interval_count, allowed_off_interval_count,
      idle_interval_count,
      computed_at
    )
    VALUES (%(device_id)s, %(day)s, %(source)s, 0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0, now())
    ON CONFLICT (device_id, day, source) DO UPDATE SET
      total_minutes=0,
      load_minutes=0, load_minutes_on_schedule=0, load_minutes_off_schedule=0,
      off_minutes=0, off_minutes_on_schedule=0, off_minutes_off_schedule=0,
      allowed_off_minutes=0, allowed_off_minutes_on_schedule=0, allowed_off_minutes_off_schedule=0,
      idle_minutes=0, idle_minutes_on_schedule=0, idle_minutes_off_schedule=0,
      interval_count=0, load_interval_count=0, off_interval_count=0, allowed_off_interval_count=0,
      idle_interval_count=0,
      computed_at=now();
    """


def _upsert_blank_facts(conn, device_id, day, source) -> None:
    with conn.cursor() as cur:
        cur.execute(_BLANK_FACTS_SQL, {"device_id": device_id, "day": day, "source": source})


_FACTS_SQL = """
    INSERT INTO device_daily_facts (
      device_id, day, source,
      total_minutes,
      load_minutes, load_minutes_on_schedule, load_minutes_off_schedule,
      off_minutes, off_minutes_on_schedule, off_minutes_off_schedule,
      allowed_off_minutes, allowed_off_minutes_on_schedule, allowed_off_minutes_off_schedule,
      idle_minutes, idle_minutes_on_schedule, idle_minutes_off_schedule,
      interval_count, load_interval_count, off_interval_count, allowed_off_interval_count,
      idle_interval_count,
      computed_at
    )
    WITH intervals AS (
      SELECT
        i.device_id,
        i.state,
        COALESCE(i.duration_seconds, EXTRACT(EPOCH FROM (now() - i.start_time)))::real AS dur,
        COALESCE(i.on_schedule_seconds, 0)::real AS on_sched,
        i.is_allowed
      FROM device_state_intervals i
      WHERE i.device_id = %(device_id)s
        AND i.source = %(source)s
        AND (i.start_time AT TIME ZONE %(tz)s)::date = %(day)s::date
    )
    SELECT
      i.device_id, %(day)s::date, %(source)s,
      COALESCE(SUM(i.dur / 60.0), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='LOAD' THEN i.dur/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='LOAD' THEN i.on_sched/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='LOAD' THEN (i.dur - i.on_sched)/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='OFF' THEN i.dur/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='OFF' THEN i.on_sched/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='OFF' THEN (i.dur - i.on_sched)/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='OFF' AND i.is_allowed THEN i.dur/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='OFF' AND i.is_allowed THEN i.on_sched/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='OFF' AND i.is_allowed THEN (i.dur - i.on_sched)/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='IDLE' THEN i.dur/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='IDLE' THEN i.on_sched/60.0 ELSE 0 END), 0)::real,
      COALESCE(SUM(CASE WHEN i.state='IDLE' THEN (i.dur - i.on_sched)/60.0 ELSE 0 END), 0)::real,
      COUNT(*)::int,
      SUM(CASE WHEN i.state='LOAD' THEN 1 ELSE 0 END)::int,
      SUM(CASE WHEN i.state='OFF'  THEN 1 ELSE 0 END)::int,
      SUM(CASE WHEN i.state='OFF' AND i.is_allowed THEN 1 ELSE 0 END)::int,
      SUM(CASE WHEN i.state='IDLE' THEN 1 ELSE 0 END)::int,
      now()
    FROM intervals i
    GROUP BY i.device_id
    ON CONFLICT (device_id, day, source) DO UPDATE SET
      total_minutes                    = EXCLUDED.total_minutes,
      load_minutes                     = EXCLUDED.load_minutes,
      load_minutes_on_schedule         = EXCLUDED.load_minutes_on_schedule,
      load_minutes_off_schedule        = EXCLUDED.load_minutes_off_schedule,
      off_minutes                      = EXCLUDED.off_minutes,
      off_minutes_on_schedule          = EXCLUDED.off_minutes_on_schedule,
      off_minutes_off_schedule         = EXCLUDED.off_minutes_off_schedule,
      allowed_off_minutes              = EXCLUDED.allowed_off_minutes,
      allowed_off_minutes_on_schedule  = EXCLUDED.allowed_off_minutes_on_schedule,
      allowed_off_minutes_off_schedule = EXCLUDED.allowed_off_minutes_off_schedule,
      idle_minutes                     = EXCLUDED.idle_minutes,
      idle_minutes_on_schedule         = EXCLUDED.idle_minutes_on_schedule,
      idle_minutes_off_schedule        = EXCLUDED.idle_minutes_off_schedule,
      interval_count                   = EXCLUDED.interval_count,
      load_interval_count              = EXCLUDED.load_interval_count,
      off_interval_count               = EXCLUDED.off_interval_count,
      allowed_off_interval_count       = EXCLUDED.allowed_off_interval_count,
      idle_interval_count              = EXCLUDED.idle_interval_count,
      computed_at                      = EXCLUDED.computed_at;
    """


def refresh_daily_facts(conn, device_id, day, tz, source, special_days=None) -> None:
    if is_no_work_special_day(special_days, day):
        _upsert_blank_facts(conn, device_id, day, source)
        return
    with conn.cursor() as cur:
        cur.execute(_FACTS_SQL, {"device_id": device_id, "day": day, "tz": tz, "source": source})


def refresh_classification_facts(conn, device_id, day, tz, unassigned_id, source,
                                 special_days=None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM device_daily_classification_facts "
            "WHERE device_id = %s AND day = %s AND source = %s;",
            (device_id, day, source),
        )
    if is_no_work_special_day(special_days, day):
        return
    sql = """
    INSERT INTO device_daily_classification_facts (
      device_id, day, source, classification_id, state,
      minutes, minutes_on_schedule, minutes_off_schedule,
      interval_count, computed_at
    )
    -- Open intervals (end_time / duration_seconds NULL) count with their elapsed time,
    -- exactly as the totals writer (device_daily_facts) does; otherwise the OFF total
    -- includes an ongoing stop while this breakdown drops it. Known minor skew:
    -- on_schedule_seconds is a snapshot from the last KPI run (~60s) while dur uses
    -- now(), so minutes_off_schedule can be overstated by a few seconds for an
    -- in-shift open interval (self-correcting; same property as the totals).
    -- Non-negative guard is a WHERE on the computed duration (rows with a negative
    -- duration are dropped, as before), not GREATEST, so they are not counted either.
    WITH intervals AS (
      SELECT
        i.device_id, i.classification_id,
        COALESCE(i.duration_seconds, EXTRACT(EPOCH FROM (now() - i.start_time)))::real AS dur,
        COALESCE(i.on_schedule_seconds, 0)::real AS on_sched
      FROM device_state_intervals i
      WHERE i.device_id = %(device_id)s AND i.source = %(source)s AND i.state = 'OFF'
        AND (i.start_time AT TIME ZONE %(tz)s)::date = %(day)s::date
    )
    SELECT
      i.device_id, %(day)s::date, %(source)s,
      COALESCE(i.classification_id, %(unassigned_id)s)::bigint, 'OFF'::text,
      SUM(i.dur / 60.0)::real,
      SUM(i.on_sched / 60.0)::real,
      SUM((i.dur - i.on_sched) / 60.0)::real,
      COUNT(*)::int, now()
    FROM intervals i
    WHERE i.dur >= 0
    GROUP BY i.device_id, COALESCE(i.classification_id, %(unassigned_id)s);
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"device_id": device_id, "day": day, "tz": tz,
                          "unassigned_id": unassigned_id, "source": source})
