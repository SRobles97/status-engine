from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from engine.algorithms import DegenerateWindowError
from engine.discovery import DiscoveredAlgorithm
from engine.schedule import on_schedule_mask
from engine.smoothing import smooth_statuses


@dataclass
class RunResult:
    device_id: Optional[int]
    algorithm: str
    processed_count: int
    updated_count: int
    result: str
    error_detail: Optional[str] = None


def window_bounds(now: datetime, tz_name: str, window_days: int) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    local_now = now.astimezone(tz)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=window_days)
    return start, now


def run_once(repo, conn, discovered: DiscoveredAlgorithm, now: datetime,
             window_days: int, default_tz: str, on_schedule_only: bool = False) -> RunResult:
    algo = discovered.algorithm
    if discovered.device_id is None:
        return RunResult(None, algo.name, 0, 0, "skipped",
                         f"unresolved device {algo.company}/{algo.device_key}")

    tz_name = repo.device_timezone(conn, discovered.device_id) or default_tz
    start, end = window_bounds(now, tz_name, window_days)
    extra = ([algo.guard_column]
             if algo.guard_column and algo.guard_column != algo.power_column else None)
    df = repo.fetch_window(conn, discovered.device_id, algo.power_column, start, end,
                           extra_columns=extra)
    if df.empty:
        return RunResult(discovered.device_id, algo.name, 0, 0, "skipped", "empty window")

    # Restrict to the device's work hours, matching the production worker's coverage.
    # A device with no configured schedule is classified in full (no filtering).
    if on_schedule_only:
        schedules, special = repo.fetch_device_schedules(conn, discovered.device_id)
        if schedules:
            mask = on_schedule_mask(list(df["time"]), tz_name, schedules, special)
            df = df[mask]
            if df.empty:
                return RunResult(discovered.device_id, algo.name, 0, 0,
                                 "skipped", "no on-schedule measurements")

    try:
        statuses = algo.classify(df)
    except DegenerateWindowError as e:
        return RunResult(discovered.device_id, algo.name, len(df), 0, "skipped", str(e))

    statuses = smooth_statuses(statuses, df["time"], algo.smoothing_minutes)

    # Data-quality guard as a hard floor AFTER smoothing: a row drawing essentially no
    # current (guard_column < guard_min) cannot be LOAD/IDLE, even if smoothing bridged
    # it. Rejects sensor glitches and stops smoothing from labelling true stoppages LOAD.
    if algo.guard_column:
        statuses = statuses.mask(df[algo.guard_column].astype(float) < algo.guard_min, "OFF")

    times = list(df["time"])
    updated = repo.upsert_measurement_status(
        conn, discovered.device_id, times, list(statuses), algo.name)
    repo.upsert_device_algo_status(
        conn, discovered.device_id, statuses.iloc[-1], algo.name, times[-1], len(df))
    return RunResult(discovered.device_id, algo.name, len(df), updated, "ok")


def run_all(repo, conn, discovered_list, now: datetime, window_days: int,
            default_tz: str, on_schedule_only: bool = False) -> list[RunResult]:
    results: list[RunResult] = []
    for discovered in discovered_list:
        algo = discovered.algorithm
        started = time.monotonic()
        try:
            res = run_once(repo, conn, discovered, now, window_days, default_tz,
                           on_schedule_only=on_schedule_only)
        except Exception as e:  # FR-08: log and continue
            res = RunResult(discovered.device_id, algo.name, 0, 0, "error", str(e))
        duration_ms = int((time.monotonic() - started) * 1000)
        repo.insert_run_log(
            conn, company=algo.company, device_key=algo.device_key,
            device_id=discovered.device_id, algorithm=algo.name,
            processed_count=res.processed_count, updated_count=res.updated_count,
            result=res.result, error_detail=res.error_detail, duration_ms=duration_ms)
        results.append(res)
    return results
