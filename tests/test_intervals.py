from datetime import datetime, timezone, timedelta
import pandas as pd
from engine import intervals


def _t(minute):
    return datetime(2026, 6, 20, 12, minute, tzinfo=timezone.utc)


def test_remap_idle_to_load():
    s = pd.Series(["OFF", "IDLE", "LOAD", "IDLE"])
    out = intervals.remap_idle_to_load(s)
    assert list(out) == ["OFF", "LOAD", "LOAD", "LOAD"]


def test_collapse_simple_alternation():
    times = [_t(0), _t(1), _t(2), _t(3)]
    labels = ["LOAD", "LOAD", "OFF", "OFF"]
    runs = intervals.collapse_runs(times, labels, gap_seconds=300)
    assert len(runs) == 2
    assert runs[0].state == "LOAD" and runs[0].start == _t(0) and runs[0].end == _t(2)
    # trailing OFF run is closed at the last sample
    assert runs[1].state == "OFF" and runs[1].start == _t(2) and runs[1].end == _t(3)


def test_collapse_trailing_load_left_open():
    times = [_t(0), _t(1), _t(2)]
    labels = ["OFF", "LOAD", "LOAD"]
    runs = intervals.collapse_runs(times, labels, gap_seconds=300)
    assert runs[-1].state == "LOAD" and runs[-1].end is None


def test_collapse_gap_breaks_run():
    # 10-minute gap between sample 1 and 2 exceeds gap_seconds=300 (5 min)
    times = [_t(0), _t(1), _t(12), _t(13)]
    labels = ["LOAD", "LOAD", "LOAD", "LOAD"]
    runs = intervals.collapse_runs(times, labels, gap_seconds=300)
    assert len(runs) == 2
    assert runs[0].state == "LOAD" and runs[0].start == _t(0) and runs[0].end == _t(1)
    assert runs[1].state == "LOAD" and runs[1].start == _t(12) and runs[1].end is None


def test_collapse_empty():
    assert intervals.collapse_runs([], [], gap_seconds=300) == []


def test_split_no_crossing_is_noop():
    r = intervals.Run("LOAD", _t(0), _t(30))
    out = intervals.split_runs_at_midnight([r], "UTC")
    assert out == [r]


def test_split_closed_run_crossing_midnight_utc():
    # 23:30 -> 00:30 next day, tz=UTC
    start = datetime(2026, 6, 20, 23, 30, tzinfo=timezone.utc)
    end = datetime(2026, 6, 21, 0, 30, tzinfo=timezone.utc)
    out = intervals.split_runs_at_midnight([intervals.Run("OFF", start, end)], "UTC")
    midnight = datetime(2026, 6, 21, 0, 0, tzinfo=timezone.utc)
    assert len(out) == 2
    assert out[0].start == start and out[0].end == midnight
    assert out[1].start == midnight and out[1].end == end


def test_split_open_run_keeps_final_segment_open():
    start = datetime(2026, 6, 20, 23, 30, tzinfo=timezone.utc)
    # open LOAD run that has crossed into the next day; "now" is implied by later samples
    out = intervals.split_runs_at_midnight([intervals.Run("LOAD", start, None)], "UTC")
    # cannot split an open run with no end -> returned unchanged (open, single day at tail)
    assert out == [intervals.Run("LOAD", start, None)]
