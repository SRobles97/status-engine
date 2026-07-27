from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import List, Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from engine.schedule import build_daily_blocks, resolve_schedules_for_date


def remap_idle_to_load(statuses: pd.Series) -> pd.Series:
    """IDLE collapses to LOAD; device_state_intervals only knows LOAD/OFF."""
    return statuses.replace("IDLE", "LOAD")


@dataclass
class Run:
    state: str
    start: datetime          # UTC tz-aware, inclusive
    end: Optional[datetime]  # UTC tz-aware, exclusive; None => open (LOAD only)


def collapse_runs(
    times: Sequence[datetime], statuses: Sequence[str], gap_seconds: float
) -> List[Run]:
    """Collapse per-sample LOAD/OFF labels into contiguous runs.

    A run spans [first sample, next run's first sample). A gap larger than
    gap_seconds closes the current run at the last sample before the gap and
    starts a fresh run. The trailing run stays open (end=None) only when LOAD.
    """
    n = len(times)
    if n == 0:
        return []
    runs: List[Run] = []
    run_state = statuses[0]
    run_start = times[0]
    for i in range(1, n):
        gap = (times[i] - times[i - 1]).total_seconds() > gap_seconds
        if gap:
            runs.append(Run(run_state, run_start, times[i - 1]))
            run_state = statuses[i]
            run_start = times[i]
        elif statuses[i] != run_state:
            runs.append(Run(run_state, run_start, times[i]))
            run_state = statuses[i]
            run_start = times[i]
    # trailing run: LOAD stays open; OFF closes at the last sample
    last_end = None if run_state == "LOAD" else times[n - 1]
    runs.append(Run(run_state, run_start, last_end))
    return runs


def _local_midnights_between(start: datetime, end: datetime, tz) -> List[datetime]:
    """UTC instants of local midnights strictly inside (start, end)."""
    start_local = start.astimezone(tz)
    first_midnight_local = datetime.combine(
        start_local.date() + timedelta(days=1), time(0, 0), tzinfo=tz
    )
    out: List[datetime] = []
    m = first_midnight_local
    while True:
        m_utc = m.astimezone(start.tzinfo)
        if m_utc >= end:
            break
        out.append(m_utc)
        m = m + timedelta(days=1)
    return out


def split_runs_at_midnight(runs: List[Run], tz_name: str) -> List[Run]:
    """Split each closed run at local-midnight boundaries so every run belongs
    to a single local day. Open runs (end=None) are returned unchanged.

    Known go-forward limitation: an open (trailing) LOAD run is passed through
    unchanged, so with STATUS_WINDOW_DAYS=0 a prior day's trailing-open LOAD is
    not revisited after midnight rollover. Deployments wanting the prior day's open
    interval closed should set STATUS_WINDOW_DAYS=1.
    """
    tz = ZoneInfo(tz_name)
    out: List[Run] = []
    for r in runs:
        if r.end is None:
            out.append(r)
            continue
        cuts = _local_midnights_between(r.start, r.end, tz)
        if not cuts:
            out.append(r)
            continue
        prev = r.start
        for c in cuts:
            out.append(Run(r.state, prev, c))
            prev = c
        out.append(Run(r.state, prev, r.end))
    return out


def _merge_blocks(blocks):
    """Return a sorted, non-overlapping union of (start, end) blocks.

    Blocks whose ranges overlap or touch (next.start <= current.end) are merged
    into a single block whose end is the maximum of the two ends.
    """
    if not blocks:
        return []
    sorted_blocks = sorted(blocks, key=lambda b: b[0])
    merged = [sorted_blocks[0]]
    for bs, be in sorted_blocks[1:]:
        cur_start, cur_end = merged[-1]
        if bs <= cur_end:  # overlap or adjacent: extend
            merged[-1] = (cur_start, max(cur_end, be))
        else:
            merged.append((bs, be))
    return merged


def schedule_blocks_utc(start, end, tz_name, schedules, special):
    """Merged non-overlapping union of work blocks (UTC tz-aware) overlapping [start, end).

    Iterates every local day in [start, end), collects all raw blocks from every
    active shift_type via resolve_schedules_for_date / build_daily_blocks, then
    merges overlapping or adjacent blocks so the result is a sorted, non-overlapping
    list. This prevents double-counting in compute_on_schedule and overlapping OFF
    pieces in slice_off_to_schedule when multiple shift-types share hours (e.g. an
    overnight tail plus the next day's early shift, or two shift-types with
    overlapping workHours).
    """
    tz = ZoneInfo(tz_name)
    start_local_day = start.astimezone(tz).date()
    end_local_day = end.astimezone(tz).date()
    blocks = []
    d = start_local_day
    while d <= end_local_day:
        for day_schedules in resolve_schedules_for_date(schedules, d):
            for bs, be in build_daily_blocks(day_schedules, d, special):
                blocks.append((bs.replace(tzinfo=tz).astimezone(start.tzinfo),
                               be.replace(tzinfo=tz).astimezone(start.tzinfo)))
        d = d + timedelta(days=1)
    return _merge_blocks(blocks)


def _overlap_seconds(a_start, a_end, b_start, b_end) -> float:
    lo = max(a_start, b_start)
    hi = min(a_end, b_end)
    return max(0.0, (hi - lo).total_seconds())


def compute_on_schedule(start, end, tz_name, schedules, special, rule):
    """Return (on_seconds:int, ratio:float, on_schedule:bool) for [start, end)."""
    total = (end - start).total_seconds()
    blocks = schedule_blocks_utc(start, end, tz_name, schedules, special)
    on = sum(_overlap_seconds(start, end, bs, be) for bs, be in blocks)
    on_i = int(round(on))
    ratio = (on / total) if total > 0 else 0.0
    if rule == "strict":
        ok = total > 0 and on_i >= int(round(total))
    elif rule == "any":
        ok = on_i > 0
    else:  # majority
        ok = on_i * 2 >= int(round(total)) and on_i > 0
    return on_i, ratio, ok


def slice_off_to_schedule(start, end, tz_name, schedules, special):
    """Clip an OFF interval to the parts inside work blocks. Empty if no schedule."""
    blocks = schedule_blocks_utc(start, end, tz_name, schedules, special)
    pieces = []
    for bs, be in sorted(blocks):
        lo = max(start, bs)
        hi = min(end, be)
        if hi > lo:
            pieces.append((lo, hi))
    return pieces


@dataclass
class IntervalRow:
    device_id: int
    source: str
    state: str
    start_time: datetime
    end_time: Optional[datetime]
    measurement_count: int
    is_allowed: bool
    on_schedule_seconds: int
    on_schedule_ratio: float
    on_schedule: bool
    on_schedule_rule: str


def _count_samples(times, start, end) -> int:
    if end is None:
        return sum(1 for t in times if t >= start)
    return sum(1 for t in times if start <= t < end)


def build_intervals(device_id, times, statuses, tz_name, schedules, special,
                    allowed_minutes, gap_seconds, source, rule):
    times = list(times)
    runs = collapse_runs(times, list(statuses), gap_seconds)
    runs = split_runs_at_midnight(runs, tz_name)
    last_sample = times[-1] if times else None
    rows: List[IntervalRow] = []
    for r in runs:
        if r.state == "LOAD":
            # An open (still running) LOAD run has no end yet, so score its
            # schedule overlap against the last sample. Without this,
            # on_schedule_seconds stays 0 for as long as the machine keeps
            # running: refresh_daily_facts extrapolates the interval's DURATION
            # but reads on_schedule_seconds as stored, so
            # load_minutes_on_schedule — what the dashboard card and the reports
            # show for an 'algoritmo' device — would sit at 0 and then jump when
            # the run finally closes. The umbral worker gives its open intervals
            # the same `end or now()` treatment.
            #
            # The cap is the last sample rather than wall clock: a device that
            # stopped reporting must stop accruing worked time.
            probe_end = r.end if r.end is not None else last_sample
            on_s, ratio, ok = (0, 0.0, False)
            if probe_end is not None and probe_end > r.start:
                on_s, ratio, ok = compute_on_schedule(
                    r.start, probe_end, tz_name, schedules, special, rule)
            rows.append(IntervalRow(
                device_id=device_id, source=source, state="LOAD",
                start_time=r.start, end_time=r.end,
                measurement_count=_count_samples(times, r.start, r.end),
                is_allowed=False, on_schedule_seconds=on_s,
                on_schedule_ratio=ratio, on_schedule=ok, on_schedule_rule=rule))
        else:  # OFF -> closed, sliced to schedule
            if r.end is None:
                continue  # defensive: collapse_runs never leaves OFF open
            for piece_start, piece_end in slice_off_to_schedule(
                    r.start, r.end, tz_name, schedules, special):
                on_s, ratio, ok = compute_on_schedule(
                    piece_start, piece_end, tz_name, schedules, special, rule)
                dur_min = (piece_end - piece_start).total_seconds() / 60.0
                is_allowed = (allowed_minutes is not None
                              and dur_min <= allowed_minutes)
                rows.append(IntervalRow(
                    device_id=device_id, source=source, state="OFF",
                    start_time=piece_start, end_time=piece_end,
                    measurement_count=_count_samples(times, piece_start, piece_end),
                    is_allowed=is_allowed, on_schedule_seconds=on_s,
                    on_schedule_ratio=ratio, on_schedule=ok, on_schedule_rule=rule))
    return rows
