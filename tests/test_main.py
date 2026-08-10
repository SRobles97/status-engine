from datetime import datetime, timezone
from unittest.mock import MagicMock

import main as main_mod
from engine.config import Settings


def _settings(tmp_path):
    (tmp_path / "Co").mkdir()
    (tmp_path / "Co" / "Dev.py").write_text(
        "from engine.algorithms import ThresholdAlgorithm\n"
        "ALGORITHM = ThresholdAlgorithm(company='Co', device_key='Dev',\n"
        "    power_column='total_active_power', threshold_w=10)\n"
    )
    return Settings(db_dsn="postgresql://x", algorithms_dir=str(tmp_path))


def test_run_iteration_acquires_lock_and_runs(tmp_path):
    settings = _settings(tmp_path)
    repo = MagicMock()
    repo.connect.return_value = MagicMock()
    repo.try_advisory_lock.return_value = True
    repo.resolve_device_id.return_value = 1
    repo.device_timezone.return_value = "UTC"
    import pandas as pd
    repo.fetch_window.return_value = pd.DataFrame({
        "time": pd.date_range("2026-06-20", periods=1, freq="min", tz="UTC"),
        "total_active_power": [50.0]})
    repo.fetch_device_schedules.return_value = ([], {})  # no schedule → classify all
    repo.upsert_measurement_status.return_value = 1

    results = main_mod.run_iteration(
        settings, repo=repo, now=datetime(2026, 6, 20, 12, tzinfo=timezone.utc))
    assert [r.result for r in results] == ["ok"]
    repo.try_advisory_lock.assert_called_once_with(repo.connect.return_value, 9_400_000_000)


def test_run_iteration_skips_when_lock_not_acquired(tmp_path):
    settings = _settings(tmp_path)
    repo = MagicMock()
    repo.connect.return_value = MagicMock()
    repo.try_advisory_lock.return_value = False
    results = main_mod.run_iteration(
        settings, repo=repo, now=datetime(2026, 6, 20, 12, tzinfo=timezone.utc))
    assert results == []
    repo.fetch_window.assert_not_called()


def _idle_settings(tmp_path, *, algo_gap_seconds, global_gap_seconds):
    (tmp_path / "Co").mkdir()
    (tmp_path / "Co" / "Dev.py").write_text(
        "from engine.algorithms import IdleThresholdAlgorithm\n"
        "ALGORITHM = IdleThresholdAlgorithm(company='Co', device_key='Dev',\n"
        "    power_column='total_active_power',\n"
        f"    gap_seconds={algo_gap_seconds})\n"
    )
    return Settings(db_dsn="postgresql://x", algorithms_dir=str(tmp_path),
                     gap_seconds=global_gap_seconds)


def _run_with_idle_algorithm(tmp_path, *, algo_gap_seconds, global_gap_seconds):
    settings = _idle_settings(
        tmp_path, algo_gap_seconds=algo_gap_seconds, global_gap_seconds=global_gap_seconds)
    repo = MagicMock()
    repo.connect.return_value = MagicMock()
    repo.try_advisory_lock.return_value = True
    repo.resolve_device_id.return_value = 1
    repo.device_timezone.return_value = "UTC"
    import pandas as pd
    repo.fetch_window.return_value = pd.DataFrame({
        "time": pd.date_range("2026-06-20", periods=1, freq="min", tz="UTC"),
        "total_active_power": [50.0]})
    repo.fetch_device_schedules.return_value = ([], {})
    repo.upsert_measurement_status.return_value = 1
    main_mod.run_iteration(
        settings, repo=repo, now=datetime(2026, 6, 20, 12, tzinfo=timezone.utc))


def test_run_iteration_warns_on_gap_seconds_mismatch(tmp_path, capsys):
    _run_with_idle_algorithm(tmp_path, algo_gap_seconds=300.0, global_gap_seconds=120.0)
    err = capsys.readouterr().err
    assert "ADVERTENCIA" in err
    assert "Dev" in err
    assert "300" in err and "120" in err


def test_run_iteration_stays_quiet_when_gap_seconds_agree(tmp_path, capsys):
    _run_with_idle_algorithm(tmp_path, algo_gap_seconds=300.0, global_gap_seconds=300.0)
    err = capsys.readouterr().err
    assert "ADVERTENCIA" not in err
