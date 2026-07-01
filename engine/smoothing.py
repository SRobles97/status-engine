from __future__ import annotations

import pandas as pd


def smooth_statuses(
    statuses: pd.Series, times: pd.Series, smoothing_minutes: float
) -> pd.Series:
    s = list(statuses)
    t = list(times)
    if smoothing_minutes <= 0 or not s:
        return pd.Series(s, index=statuses.index)

    started = False
    gap: list[int] = []  # positional indices of the current non-LOAD run
    for i in range(len(s)):
        if s[i] == "LOAD":
            if started and gap:
                duration_min = (t[i] - t[gap[0]]).total_seconds() / 60.0
                if duration_min < smoothing_minutes:
                    for j in gap:
                        s[j] = "LOAD"
            gap = []
            started = True
        elif started:
            gap.append(i)
    return pd.Series(s, index=statuses.index)
