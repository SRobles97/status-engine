from engine.config import Settings


def test_from_env_defaults_when_only_dsn_given():
    s = Settings.from_env({"DB_DSN": "postgresql://x"})
    assert s.db_dsn == "postgresql://x"
    assert s.run_interval_seconds == 300
    assert s.status_window_days == 0
    assert s.default_tz == "America/Santiago"
    assert s.algorithms_dir == "algorithms"
    assert s.advisory_lock_key == 9_400_000_000


def test_from_env_overrides_and_coerces_types():
    s = Settings.from_env({
        "DB_DSN": "postgresql://y",
        "RUN_INTERVAL_SECONDS": "60",
        "STATUS_WINDOW_DAYS": "2",
        "DEFAULT_TZ": "UTC",
        "ALGORITHMS_DIR": "algos",
    })
    assert s.run_interval_seconds == 60
    assert s.status_window_days == 2
    assert s.default_tz == "UTC"
    assert s.algorithms_dir == "algos"


def test_from_env_missing_dsn_raises():
    import pytest
    with pytest.raises(ValueError):
        Settings.from_env({})


def test_on_schedule_only_default_true_and_env_override():
    assert Settings.from_env({"DB_DSN": "x"}).on_schedule_only is True
    assert Settings.from_env({"DB_DSN": "x", "STATUS_ON_SCHEDULE_ONLY": "false"}).on_schedule_only is False
    assert Settings.from_env({"DB_DSN": "x", "STATUS_ON_SCHEDULE_ONLY": "0"}).on_schedule_only is False
    assert Settings.from_env({"DB_DSN": "x", "STATUS_ON_SCHEDULE_ONLY": "true"}).on_schedule_only is True


def test_interval_settings_defaults_and_env():
    s = Settings.from_env({"DB_DSN": "x"})
    assert s.emit_intervals is True
    assert s.gap_seconds == 300.0
    assert s.interval_source == "algo"
    assert s.on_schedule_rule == "majority"

    s2 = Settings.from_env({
        "DB_DSN": "x", "EMIT_INTERVALS": "false",
        "GAP_SECONDS": "120", "INTERVAL_SOURCE": "algo",
        "ON_SCHEDULE_RULE": "strict",
    })
    assert s2.emit_intervals is False
    assert s2.gap_seconds == 120.0
    assert s2.on_schedule_rule == "strict"
