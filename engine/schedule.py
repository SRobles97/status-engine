"""Device work-schedule resolution, ported to match the production intervals/KPI
worker (specs/timescale-playground). Decides whether a measurement timestamp falls
within a device's work hours, honouring breaks, special-day holidays, multiple
effective-dated schedule versions, multiple shift_types, and overnight shifts.

All schedule strings ("08:00") are in the device's local timezone; measurement
timestamps are UTC-aware and converted to that timezone before matching.
Bounds are [start, end): start inclusive, end exclusive. `extra_hours` is ignored,
matching the production worker.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

DAY_KEYS = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]

Block = Tuple[datetime, datetime]


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def _get_work_hours(cfg: Optional[Dict[str, Any]]) -> Optional[Tuple[time, time]]:
    if not cfg:
        return None
    wh = cfg.get("workHours")
    if not isinstance(wh, dict) or not wh.get("start") or not wh.get("end"):
        return None
    return _parse_hhmm(wh["start"]), _parse_hhmm(wh["end"])


def _effective_day_cfg(
    day_schedules: Dict[str, Any], special_days: Optional[Dict[str, Any]], d: date
) -> Optional[Dict[str, Any]]:
    """Day config for a date: a configured special day overrides the weekday."""
    if special_days:
        special = special_days.get(d.isoformat())
        if special is not None:
            return special
    return day_schedules.get(DAY_KEYS[d.weekday()])


def _today_work_block(cfg: Optional[Dict[str, Any]], local_day: date) -> Optional[Block]:
    hours = _get_work_hours(cfg)
    if hours is None:
        return None
    work_start, work_end = hours
    start_dt = datetime.combine(local_day, work_start)
    end_dt = datetime.combine(local_day, work_end)
    if end_dt <= start_dt:  # overnight: clip to midnight of next day
        end_dt = datetime.combine(local_day + timedelta(days=1), time(0, 0))
    return (start_dt, end_dt)


def _overnight_tail_block(
    day_schedules: Dict[str, Any], local_day: date, special_days: Optional[Dict[str, Any]]
) -> Optional[Block]:
    """If the previous day has an overnight shift, return its [00:00, end) tail today."""
    prev_day = local_day - timedelta(days=1)
    hours = _get_work_hours(_effective_day_cfg(day_schedules, special_days, prev_day))
    if hours is None:
        return None
    prev_start, prev_end = hours
    if prev_end > prev_start:  # not overnight
        return None
    tail_start = datetime.combine(local_day, time(0, 0))
    tail_end = datetime.combine(local_day, prev_end)
    return (tail_start, tail_end) if tail_end > tail_start else None


def _parse_breaks_list(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    breaks_raw = cfg.get("breaks")
    if isinstance(breaks_raw, list):
        return [b for b in breaks_raw
                if isinstance(b, dict) and b.get("start") and b.get("durationMinutes")]
    br = cfg.get("break")
    if isinstance(br, dict) and br.get("start") and br.get("durationMinutes"):
        return [br]
    return []


def _subtract_break_from_blocks(blocks: List[Block], brk: Dict[str, Any], local_day: date) -> List[Block]:
    bstart_dt = datetime.combine(local_day, _parse_hhmm(brk["start"]))
    bend_dt = bstart_dt + timedelta(minutes=int(brk["durationMinutes"]))
    result: List[Block] = []
    for a, b in blocks:
        if bend_dt <= a or bstart_dt >= b:
            result.append((a, b))
        else:
            if a < bstart_dt:
                result.append((a, bstart_dt))
            if bend_dt < b:
                result.append((bend_dt, b))
    return result


def build_daily_blocks(
    day_schedules: Dict[str, Any], local_day: date, special_days: Optional[Dict[str, Any]] = None
) -> List[Block]:
    """[start, end) work blocks (naive local time) for local_day, breaks subtracted.

    A special day overrides the weekday (its workHours, or no work if it has none).
    Overnight shifts (end <= start) contribute a tail on the following day.
    """
    blocks: List[Block] = []

    tail = _overnight_tail_block(day_schedules, local_day, special_days)
    if tail:
        blocks.append(tail)

    cfg = _effective_day_cfg(day_schedules, special_days, local_day)
    today_block = _today_work_block(cfg, local_day)
    if today_block:
        blocks.append(today_block)

    if not blocks:
        return []

    if cfg:
        for brk in _parse_breaks_list(cfg):
            blocks = _subtract_break_from_blocks(blocks, brk, local_day)

    return [(a, b) for (a, b) in blocks if b > a]


def resolve_schedules_for_date(
    schedules: Sequence[Tuple[date, Optional[date], str, Dict[str, Any]]], ref_date: date
) -> List[Dict[str, Any]]:
    """All day_schedules covering ref_date (one per shift_type), else most-recent expired."""
    active: Dict[str, Dict[str, Any]] = {}
    for valid_from, valid_to, shift_type, day_schedules in schedules:
        if valid_from <= ref_date and (valid_to is None or valid_to >= ref_date):
            active.setdefault(shift_type, day_schedules)
    if active:
        return list(active.values())

    best: Dict[str, Tuple[date, Dict[str, Any]]] = {}
    for valid_from, valid_to, shift_type, day_schedules in schedules:
        if valid_to is not None and valid_to < ref_date:
            if shift_type not in best or valid_to > best[shift_type][0]:
                best[shift_type] = (valid_to, day_schedules)
    return [ds for _, ds in best.values()] if best else []


def on_schedule_mask(
    times: Sequence[datetime],
    tz_name: str,
    schedules: Sequence[Tuple[date, Optional[date], str, Dict[str, Any]]],
    special_days: Optional[Dict[str, Any]],
) -> List[bool]:
    """Per-timestamp on-schedule flags. `times` are tz-aware (UTC); converted to tz_name."""
    tz = ZoneInfo(tz_name)
    day_cache: Dict[date, List[Block]] = {}

    def blocks_for(local_day: date) -> List[Block]:
        if local_day not in day_cache:
            union: List[Block] = []
            for day_schedules in resolve_schedules_for_date(schedules, local_day):
                for bs, be in build_daily_blocks(day_schedules, local_day, special_days):
                    union.append((bs.replace(tzinfo=tz), be.replace(tzinfo=tz)))
            day_cache[local_day] = union
        return day_cache[local_day]

    mask: List[bool] = []
    for t in times:
        t_local = t.astimezone(tz)
        mask.append(any(bs <= t_local < be for bs, be in blocks_for(t_local.date())))
    return mask
