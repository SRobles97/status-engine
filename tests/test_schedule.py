from datetime import date, datetime, timezone

from engine.schedule import (
    build_daily_blocks,
    resolve_schedules_for_date,
    on_schedule_mask,
)

# F1-like weekly schedule: Mon–Thu 08:00–17:50 with two breaks, Fri 08:00–14:00.
WEEK = {
    "monday": {"workHours": {"start": "08:00", "end": "17:50"},
               "breaks": [{"start": "09:30", "durationMinutes": 15},
                          {"start": "12:55", "durationMinutes": 65}]},
    "tuesday": {"workHours": {"start": "08:00", "end": "17:50"},
                "breaks": [{"start": "09:30", "durationMinutes": 15}]},
    "friday": {"workHours": {"start": "08:00", "end": "14:00"},
               "breaks": [{"start": "10:30", "durationMinutes": 15}]},
}


def _hm(blocks):
    return [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in blocks]


def test_weekday_blocks_subtract_breaks():
    # 2026-06-22 is a Monday.
    blocks = build_daily_blocks(WEEK, date(2026, 6, 22))
    assert _hm(blocks) == [("08:00", "09:30"), ("09:45", "12:55"), ("14:00", "17:50")]


def test_friday_single_break():
    # 2026-06-26 is a Friday.
    blocks = build_daily_blocks(WEEK, date(2026, 6, 26))
    assert _hm(blocks) == [("08:00", "10:30"), ("10:45", "14:00")]


def test_unconfigured_weekday_is_empty():
    # Saturday not present in WEEK → no work windows.
    assert build_daily_blocks(WEEK, date(2026, 6, 27)) == []


def test_special_day_holiday_overrides_to_no_work():
    special = {"2026-06-22": {"name": "Feriado", "workHours": None, "breaks": None}}
    assert build_daily_blocks(WEEK, date(2026, 6, 22), special) == []


def test_special_day_can_override_hours():
    special = {"2026-06-22": {"workHours": {"start": "09:00", "end": "11:00"}}}
    assert _hm(build_daily_blocks(WEEK, date(2026, 6, 22), special)) == [("09:00", "11:00")]


def test_overnight_block_splits_across_midnight():
    night = {"monday": {"workHours": {"start": "22:00", "end": "06:00"}}}
    mon = build_daily_blocks(night, date(2026, 6, 22))       # Monday
    tue = build_daily_blocks(night, date(2026, 6, 23))       # Tuesday tail
    assert _hm(mon) == [("22:00", "00:00")]
    assert _hm(tue) == [("00:00", "06:00")]


def test_resolve_picks_active_version_else_most_recent():
    scheds = [
        (date(2026, 2, 26), date(2026, 4, 30), "day", {"a": 1}),
        (date(2026, 5, 1), None, "day", {"b": 2}),
    ]
    assert resolve_schedules_for_date(scheds, date(2026, 6, 1)) == [{"b": 2}]
    assert resolve_schedules_for_date(scheds, date(2026, 3, 15)) == [{"a": 1}]
    # Before any schedule exists there is nothing active or expired → empty.
    assert resolve_schedules_for_date(scheds, date(2026, 1, 1)) == []
    # After the last schedule expires → fall back to most recent expired version.
    expired = [(date(2026, 2, 1), date(2026, 3, 1), "day", {"a": 1})]
    assert resolve_schedules_for_date(expired, date(2026, 6, 1)) == [{"a": 1}]


def test_on_schedule_mask_uses_device_timezone():
    # All weekdays 08:00–17:00, no breaks → weekday-independent window.
    flat = {d: {"workHours": {"start": "08:00", "end": "17:00"}}
            for d in ("monday", "tuesday", "wednesday", "thursday",
                      "friday", "saturday", "sunday")}
    scheds = [(date(2026, 1, 1), None, "day", flat)]
    tz = "America/Santiago"  # June = UTC-4, so 08:00 local = 12:00 UTC
    times = [
        datetime(2026, 6, 22, 11, 30, tzinfo=timezone.utc),  # 07:30 local → OFF
        datetime(2026, 6, 22, 12, 30, tzinfo=timezone.utc),  # 08:30 local → ON
        datetime(2026, 6, 22, 21, 30, tzinfo=timezone.utc),  # 17:30 local → OFF
    ]
    assert on_schedule_mask(times, tz, scheds, {}) == [False, True, False]
