from datetime import datetime, timezone
from unittest.mock import MagicMock
import pandas as pd
from engine.algorithms import ThresholdAlgorithm
from engine.discovery import DiscoveredAlgorithm, discover
from engine import runner

NOW = datetime(2026, 6, 20, 15, 0, tzinfo=timezone.utc)


def _algo(source=None):
    return ThresholdAlgorithm(company="Riñihue", device_key="F1-piloto",
        power_column="phase_a_active_power", threshold_w=1100, source_device_key=source)


def test_algo_carries_source_device_key():
    assert _algo("F1").source_device_key == "F1"
    assert _algo().source_device_key is None


def test_discover_resolves_source_and_target():
    import tempfile, pathlib
    root = pathlib.Path(tempfile.mkdtemp())
    (root / "Riñihue").mkdir()
    (root / "Riñihue" / "F1_piloto.py").write_text(
        "from engine.algorithms import ThresholdAlgorithm\n"
        "ALGORITHM = ThresholdAlgorithm(company='Riñihue', device_key='F1-piloto',\n"
        "  power_column='phase_a_active_power', threshold_w=1100, source_device_key='F1')\n")
    mapping = {"F1-piloto": 99, "F1": 42}
    out = discover(root, resolver=lambda c, d: mapping.get(d))
    assert out[0].device_id == 99 and out[0].source_device_id == 42


def test_run_once_reads_source_writes_target():
    df = pd.DataFrame({
        "time": pd.date_range("2026-06-20", periods=2, freq="min", tz="UTC"),
        "phase_a_active_power": [0.0, 5000.0]})
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    repo.fetch_window.return_value = df
    repo.upsert_measurement_status.return_value = 2
    disc = DiscoveredAlgorithm(algorithm=_algo("F1"), device_id=99, source_device_id=42)
    res = runner.run_once(repo, MagicMock(), disc, NOW, 0, "UTC", on_schedule_only=False)
    assert res.result == "ok" and res.device_id == 99          # RunResult is the TARGET
    assert repo.fetch_window.call_args.args[1] == 42            # READ from source
    assert repo.upsert_measurement_status.call_args.args[1] == 99  # WRITE to target
    assert repo.upsert_device_algo_status.call_args.args[1] == 99


def test_run_once_skips_when_source_unresolved():
    disc = DiscoveredAlgorithm(algorithm=_algo("F1"), device_id=99, source_device_id=None)
    res = runner.run_once(MagicMock(), MagicMock(), disc, NOW, 0, "UTC")
    assert res.result == "skipped" and "source" in (res.error_detail or "")


def test_run_once_no_source_uses_target_for_both():
    df = pd.DataFrame({
        "time": pd.date_range("2026-06-20", periods=2, freq="min", tz="UTC"),
        "phase_a_active_power": [0.0, 5000.0]})
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    repo.fetch_window.return_value = df
    repo.upsert_measurement_status.return_value = 2
    disc = DiscoveredAlgorithm(algorithm=_algo(), device_id=42)   # no source_device_id given
    res = runner.run_once(repo, MagicMock(), disc, NOW, 0, "UTC")
    assert res.result == "ok"
    assert repo.fetch_window.call_args.args[1] == 42
    assert repo.upsert_measurement_status.call_args.args[1] == 42
