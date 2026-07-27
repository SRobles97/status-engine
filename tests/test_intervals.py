from datetime import datetime, timezone, date
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


# One schedule version: Mon-Sun 08:00-16:00, no breaks.
_SCHED = [(date(2026, 1, 1), None, "day",
           {k: {"workHours": {"start": "08:00", "end": "16:00"}}
            for k in ["monday", "tuesday", "wednesday", "thursday",
                      "friday", "saturday", "sunday"]})]


def _utc(h, m=0):
    return datetime(2026, 6, 22, h, m, tzinfo=timezone.utc)  # 2026-06-22 is a Monday


def test_compute_on_schedule_fully_inside():
    on, ratio, ok = intervals.compute_on_schedule(
        _utc(9), _utc(10), "UTC", _SCHED, {}, "majority")
    assert on == 3600 and ratio == 1.0 and ok is True


def test_compute_on_schedule_partial_majority():
    # 07:30-08:30 -> 30 min inside (08:00-08:30)
    on, ratio, ok = intervals.compute_on_schedule(
        _utc(7, 30), _utc(8, 30), "UTC", _SCHED, {}, "majority")
    assert on == 1800 and ok is True  # exactly half -> majority true (2*1800 >= 3600)


def test_compute_on_schedule_outside():
    on, ratio, ok = intervals.compute_on_schedule(
        _utc(18), _utc(19), "UTC", _SCHED, {}, "majority")
    assert on == 0 and ratio == 0.0 and ok is False


def test_slice_off_to_schedule_clips_to_blocks():
    # OFF 07:00-12:00 -> clipped to 08:00-12:00
    pieces = intervals.slice_off_to_schedule(
        _utc(7), _utc(12), "UTC", _SCHED, {})
    assert pieces == [(_utc(8), _utc(12))]


def test_slice_off_no_schedule_returns_empty():
    pieces = intervals.slice_off_to_schedule(_utc(9), _utc(10), "UTC", [], {})
    assert pieces == []


def test_build_intervals_load_and_off_with_schedule():
    # samples every 30 min from 07:00 to 12:00 Monday, LOAD until 09:00 then OFF
    times, labels = [], []
    h = 7
    while h <= 12:
        times.append(_utc(h))
        labels.append("LOAD" if h < 9 else "OFF")
        h += 1  # hourly samples
    rows = intervals.build_intervals(
        device_id=99, times=times, statuses=labels, tz_name="UTC",
        schedules=_SCHED, special={}, allowed_minutes=15, gap_seconds=7200,
        source="algo", rule="majority")
    loads = [r for r in rows if r.state == "LOAD"]
    offs = [r for r in rows if r.state == "OFF"]
    assert loads and offs
    # LOAD interval carries device+source
    assert loads[0].device_id == 99 and loads[0].source == "algo"
    # OFF is clipped to <=16:00 and starts no earlier than 08:00 (all inside here)
    assert all(o.on_schedule for o in offs)
    # OFF over 15 min allowed threshold -> not allowed
    assert all(o.is_allowed is False for o in offs)


def test_build_intervals_no_schedule_no_off():
    times = [_utc(9), _utc(10), _utc(11)]
    labels = ["OFF", "OFF", "OFF"]
    rows = intervals.build_intervals(
        device_id=1, times=times, statuses=labels, tz_name="UTC",
        schedules=[], special={}, allowed_minutes=15, gap_seconds=7200,
        source="algo", rule="majority")
    assert rows == []  # no schedule -> no OFF intervals, and no LOAD present


def test_build_intervals_short_off_is_allowed():
    # 10-minute OFF inside schedule, allowed_minutes=15 -> is_allowed True
    times = [_utc(9, 0), _utc(9, 10), _utc(9, 15)]
    labels = ["OFF", "OFF", "LOAD"]
    rows = intervals.build_intervals(
        device_id=1, times=times, statuses=labels, tz_name="UTC",
        schedules=_SCHED, special={}, allowed_minutes=15, gap_seconds=7200,
        source="algo", rule="majority")
    offs = [r for r in rows if r.state == "OFF"]
    assert offs and offs[0].is_allowed is True


# ---------------------------------------------------------------------------
# Merge-blocks tests: two shift-types with overlapping workHours (08:00-16:00
# and 10:00-18:00) on the same Monday → raw blocks overlap 10:00-16:00.
# schedule_blocks_utc must return the merged non-overlapping union [08:00,18:00).
# ---------------------------------------------------------------------------

_SCHED_OVERLAP = [
    (date(2026, 1, 1), None, "day",
     {k: {"workHours": {"start": "08:00", "end": "16:00"}}
      for k in ["monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday"]}),
    (date(2026, 1, 1), None, "night",
     {k: {"workHours": {"start": "10:00", "end": "18:00"}}
      for k in ["monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday"]}),
]


def test_schedule_blocks_utc_merged():
    """Test C: returned blocks are sorted and non-overlapping."""
    blocks = intervals.schedule_blocks_utc(
        _utc(7), _utc(19), "UTC", _SCHED_OVERLAP, {})
    # At minimum one block (merged union); each consecutive pair must not overlap
    assert len(blocks) >= 1
    for i in range(len(blocks) - 1):
        assert blocks[i][1] <= blocks[i + 1][0], (
            f"blocks overlap: {blocks[i]} and {blocks[i+1]}")


def test_compute_on_schedule_no_double_count():
    """Test A: LOAD fully inside union must give ratio==1.0, not >1.0."""
    # interval 11:00-12:00 is inside both raw blocks (10:00-16:00 and 08:00-16:00)
    # without merging, on_seconds would be 7200 for a 3600s interval → ratio 2.0
    on, ratio, ok = intervals.compute_on_schedule(
        _utc(11), _utc(12), "UTC", _SCHED_OVERLAP, {}, "majority")
    duration = (_utc(12) - _utc(11)).total_seconds()
    assert on <= duration, f"on_schedule_seconds {on} exceeds duration {duration}"
    assert ratio <= 1.0, f"ratio {ratio} > 1.0 (double-counting)"
    assert ratio == 1.0 and ok is True


def test_slice_off_to_schedule_non_overlapping_pieces():
    """Test B: OFF pieces from overlapping raw blocks must not overlap each other."""
    # OFF spanning the whole merged window [08:00, 18:00); without merge, two
    # overlapping pieces would be returned for the 10:00-16:00 intersection.
    pieces = intervals.slice_off_to_schedule(
        _utc(7), _utc(19), "UTC", _SCHED_OVERLAP, {})
    assert pieces, "expected at least one piece"
    for i in range(len(pieces) - 1):
        assert pieces[i][1] <= pieces[i + 1][0], (
            f"pieces overlap: {pieces[i]} and {pieces[i+1]}")


# ---------------------------------------------------------------------------
# Open (still running) LOAD interval: on-schedule seconds must accrue live.
#
# The trailing LOAD run is stored open (end_time NULL) and rewritten every tick.
# `reporting.refresh_daily_facts` extrapolates its DURATION (now() - start_time)
# but reads `on_schedule_seconds` as stored — so leaving it at 0 makes
# `load_minutes_on_schedule` (what the dashboard card and the reports show for
# an `algoritmo` device) stay at 0 for as long as the machine keeps running,
# then jump once the run closes. The umbral worker avoids this by computing the
# interval's schedule overlap against `end or now()`
# (workers/jobs/job_schedule_kpi.py). The engine must match: use the last
# sample as the open run's provisional end.
# ---------------------------------------------------------------------------


def _open_load_row(times):
    rows = intervals.build_intervals(
        device_id=7, times=times, statuses=["LOAD"] * len(times), tz_name="UTC",
        schedules=_SCHED, special={}, allowed_minutes=15, gap_seconds=7200,
        source="algo", rule="majority")
    loads = [r for r in rows if r.state == "LOAD"]
    assert len(loads) == 1
    return loads[0]


def test_open_load_accrues_on_schedule_seconds_up_to_last_sample():
    # Running since 09:00, last sample 10:00, all inside the 08:00-16:00 shift.
    row = _open_load_row([_utc(9), _utc(9, 30), _utc(10)])

    assert row.end_time is None, "trailing LOAD must stay open"
    assert row.on_schedule_seconds == 3600
    assert row.on_schedule_ratio == 1.0
    assert row.on_schedule is True


def test_open_load_counts_only_the_in_shift_part():
    # Running since 07:00 (before the shift), last sample 09:00 → 1 h inside.
    row = _open_load_row([_utc(7), _utc(8), _utc(9)])

    assert row.on_schedule_seconds == 3600
    assert row.on_schedule_ratio == 0.5


def test_open_load_outside_the_shift_stays_zero():
    # Running since 18:00, after the 08:00-16:00 shift → nothing on schedule.
    row = _open_load_row([_utc(18), _utc(19)])

    assert row.on_schedule_seconds == 0
    assert row.on_schedule_ratio == 0.0
    assert row.on_schedule is False


def test_open_load_with_a_single_sample_has_no_duration_yet():
    row = _open_load_row([_utc(9)])

    assert row.on_schedule_seconds == 0
    assert row.on_schedule is False


def test_open_load_is_not_extrapolated_past_the_last_sample():
    """A device that stopped reporting must not keep accruing worked time.

    Mirrors the umbral worker capping the open interval at `last_power_time`:
    the schedule overlap stops at the last sample, not at wall-clock now.
    """
    row = _open_load_row([_utc(9), _utc(10)])

    assert row.on_schedule_seconds == 3600  # 09:00→10:00, not 09:00→now
