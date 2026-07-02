from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

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
