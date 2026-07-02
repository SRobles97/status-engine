from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import List, Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd


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
    to a single local day. Open runs (end=None) are returned unchanged."""
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
