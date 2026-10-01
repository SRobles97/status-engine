from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

import main as main_mod
from engine.config import Settings
from tools import backfill


def _spec_dir(tmp_path):
    d = tmp_path / "Co"
    d.mkdir()
    (d / "Dev.py").write_text(
        "from engine.algorithms import ThresholdAlgorithm\n"
        "ALGORITHM = ThresholdAlgorithm(company='Co', device_key='Dev',\n"
        "    power_column='total_current', threshold_w=10, smoothing_minutes=4.5,\n"
        "    min_load_minutes=2.5)\n")
    return d


def test_overrides_window_and_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DSN", "postgresql://x")
    seen = []
    run = lambda s: seen.append(s) or [MagicMock(result="ok", error_detail=None)]
    assert backfill.main(["--days", "30", "--dir", str(_spec_dir(tmp_path))], run=run) == 0
    assert seen[0].status_window_days == 30
    assert seen[0].algorithms_dir == str(tmp_path / "Co")


def test_retries_while_service_holds_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DSN", "postgresql://x")
    answers = [[], [], [MagicMock(result="ok", error_detail=None)]]
    sleeps = []
    rc = backfill.main(["--days", "3", "--dir", str(_spec_dir(tmp_path))],
                       run=lambda s: answers.pop(0), sleep=sleeps.append)
    assert rc == 0 and len(sleeps) == 2


def test_gives_up_without_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DSN", "postgresql://x")
    rc = backfill.main(["--days", "3", "--dir", str(_spec_dir(tmp_path)), "--retries", "2"],
                       run=lambda s: [], sleep=lambda _: None)
    assert rc == 2


def test_failed_device_makes_exit_nonzero(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DSN", "postgresql://x")
    rc = backfill.main(["--days", "3", "--dir", str(_spec_dir(tmp_path))],
                       run=lambda s: [MagicMock(result="error", error_detail="boom")])
    assert rc == 1


@pytest.mark.parametrize("argv", [["--days", "0", "--dir", "."], ["--days", "3", "--dir", "/nope"]])
def test_rejects_bad_args(argv, monkeypatch):
    monkeypatch.setenv("DB_DSN", "postgresql://x")
    with pytest.raises(SystemExit):
        backfill.main(argv, run=lambda s: pytest.fail("must not run"))


def test_multi_day_window_rebuilds_every_day(tmp_path):
    # Real run_iteration over a 3-day window at 60 s cadence: every local day in
    # the window is deleted+rebuilt once, and no row crosses local midnight.
    repo = MagicMock()
    repo.try_advisory_lock.return_value = True
    repo.resolve_device_id.return_value = 1
    repo.device_timezone.return_value = "America/Santiago"
    repo.fetch_threshold_minutes.return_value = None
    repo.device_company_id.return_value = 1
    repo.fetch_algo_classifications_for_day.return_value = {}
    repo.fetch_device_schedules.return_value = ([], {})
    now = datetime(2026, 10, 1, 15, tzinfo=timezone.utc)
    t = pd.date_range(pd.Timestamp("2026-09-28 03:00", tz="UTC"), now, freq="60s", inclusive="left")
    cur = np.where((t.hour % 3) == 0, 20.0, 1.0)
    repo.fetch_window.return_value = pd.DataFrame({"time": t, "total_current": cur})

    settings = Settings(db_dsn="x", algorithms_dir=str(_spec_dir(tmp_path)),
                        status_window_days=3)
    results = main_mod.run_iteration(settings, repo=repo, now=now)

    assert [r.result for r in results] == ["ok"]
    start, end = repo.fetch_window.call_args.args[3:5]
    assert start.isoformat() == "2026-09-28T00:00:00-03:00" and end == now
    days = [c.args[2] for c in repo.delete_algo_intervals_for_day.call_args_list]
    assert [str(d) for d in days] == ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]
    tz = ZoneInfo("America/Santiago")
    rows = [r for c in repo.insert_intervals.call_args_list for r in c.args[1]]
    assert rows
    for r in rows:
        if r.end_time is not None:
                assert r.start_time.astimezone(tz).date() == \
                    (r.end_time - pd.Timedelta(microseconds=1)).astimezone(tz).date()
