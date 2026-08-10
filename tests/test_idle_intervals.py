from datetime import datetime, timezone, time, date

from engine import intervals


def _t(minute):
    return datetime(2026, 7, 30, 12, minute, tzinfo=timezone.utc)


# Misma forma que `_SCHED` en test_intervals.py: resolve_schedules_for_date
# espera una 4-tupla (valid_from, valid_to, shift_type, {weekday: cfg}), no un
# dict. Turno abierto todos los días, así nada se recorta por horario.
_ALL_DAYS = ["monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday"]
SCHEDULES = [(date(2026, 1, 1), None, "day",
              {k: {"workHours": {"start": "00:00", "end": "23:59"}}
               for k in _ALL_DAYS})]


def test_trailing_idle_run_stays_open():
    runs = intervals.collapse_runs([_t(0), _t(1), _t(2)],
                                   ["OFF", "IDLE", "IDLE"], gap_seconds=300)
    assert runs[-1].state == "IDLE" and runs[-1].end is None


def test_trailing_off_run_still_closes():
    runs = intervals.collapse_runs([_t(0), _t(1)], ["LOAD", "OFF"], gap_seconds=300)
    assert runs[-1].state == "OFF" and runs[-1].end == _t(1)


def test_idle_interval_is_stored_whole_and_scored():
    rows = intervals.build_intervals(
        device_id=1, times=[_t(0), _t(1), _t(2), _t(3)],
        statuses=["IDLE", "IDLE", "LOAD", "LOAD"],
        tz_name="America/Santiago", schedules=SCHEDULES, special={},
        allowed_minutes=None, gap_seconds=300, source="algo", rule="majority")
    idle = [r for r in rows if r.state == "IDLE"]
    assert len(idle) == 1
    assert idle[0].start_time == _t(0) and idle[0].end_time == _t(2)
    assert idle[0].on_schedule_seconds == 120
    assert idle[0].is_allowed is False


def test_open_trailing_idle_run_is_scored_against_last_sample():
    # Sin muestra siguiente que la cierre, el IDLE final debe quedar abierto
    # (end_time=None) y su on_schedule_seconds debe puntuarse contra la
    # última muestra en vez de quedar en 0 — el mismo mecanismo abierto que
    # ya cubre LOAD, y el que dos incidentes en producción demostraron que
    # importa vigilar de cerca.
    rows = intervals.build_intervals(
        device_id=1, times=[_t(0), _t(1), _t(2)],
        statuses=["IDLE", "IDLE", "IDLE"],
        tz_name="America/Santiago", schedules=SCHEDULES, special={},
        allowed_minutes=None, gap_seconds=300, source="algo", rule="majority")
    assert len(rows) == 1
    assert rows[0].state == "IDLE"
    assert rows[0].end_time is None
    assert rows[0].on_schedule_seconds == 120


def test_idle_is_not_sliced_to_the_schedule():
    # Outside every work block: OFF would be dropped, IDLE must survive whole
    # Sin ningún weekday configurado no hay bloques de trabajo en ningún día.
    empty_schedule = [(date(2026, 1, 1), None, "day", {})]
    rows = intervals.build_intervals(
        device_id=1, times=[_t(0), _t(1), _t(2)],
        statuses=["IDLE", "IDLE", "OFF"],
        tz_name="America/Santiago", schedules=empty_schedule, special={},
        allowed_minutes=None, gap_seconds=300, source="algo", rule="majority")
    assert [r.state for r in rows] == ["IDLE"]
    assert rows[0].on_schedule_seconds == 0
