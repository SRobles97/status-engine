from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from engine.algorithms import IdleThresholdAlgorithm, ThresholdAlgorithm
from engine import intervals as intervals_mod
from engine import runner


def test_threshold_algorithm_does_not_emit_idle_by_default():
    algo = ThresholdAlgorithm(
        company="X", device_key="k", power_column="total_current", threshold_w=10.0
    )
    assert algo.emits_idle is False


def test_remap_applied_only_when_emits_idle_is_false():
    statuses = pd.Series(["OFF", "IDLE", "LOAD"])
    assert list(intervals_mod.remap_idle_to_load(statuses)) == ["OFF", "LOAD", "LOAD"]
    # emits_idle=True must pass the series through untouched
    assert list(statuses) == ["OFF", "IDLE", "LOAD"]


def test_emit_algo_intervals_collapses_idle_to_load_when_flag_false():
    """With emits_idle=False, IDLE should be remapped to LOAD before build_intervals."""
    df = pd.DataFrame({
        "time": pd.date_range("2026-08-07T11:00", periods=3, freq="h", tz="UTC"),
        "total_current": [0.0, 5.0, 15.0]
    })
    statuses = pd.Series(["OFF", "IDLE", "LOAD"], index=df.index)

    repo = MagicMock()
    repo.fetch_threshold_minutes.return_value = 15.0
    repo.device_company_id.return_value = 13
    repo.get_or_create_unassigned_classification.return_value = 7

    conn = MagicMock()
    with patch.object(runner.reporting_mod, "refresh_daily_facts"), \
         patch.object(runner.reporting_mod, "refresh_classification_facts"), \
         patch.object(runner.intervals_mod, "build_intervals") as mock_build:
        mock_build.return_value = []
        runner._emit_algo_intervals(
            repo, conn, write_id=42, df=df, statuses_full=statuses,
            tz_name="UTC", schedules=[], special={}, gap_seconds=300.0,
            source="algo", rule="majority", emits_idle=False
        )

    # Capture what was passed to build_intervals
    statuses_arg = mock_build.call_args.kwargs["statuses"]

    # With emits_idle=False, IDLE should be remapped to LOAD
    assert "IDLE" not in statuses_arg, (
        f"IDLE should be remapped to LOAD, but got: {statuses_arg}")
    assert statuses_arg == ["OFF", "LOAD", "LOAD"], (
        f"Expected ['OFF', 'LOAD', 'LOAD'], got: {statuses_arg}")


def test_emit_algo_intervals_preserves_idle_when_flag_true():
    """With emits_idle=True, IDLE should pass through untouched to build_intervals."""
    df = pd.DataFrame({
        "time": pd.date_range("2026-08-07T11:00", periods=3, freq="h", tz="UTC"),
        "total_current": [0.0, 5.0, 15.0]
    })
    statuses = pd.Series(["OFF", "IDLE", "LOAD"], index=df.index)

    repo = MagicMock()
    repo.fetch_threshold_minutes.return_value = 15.0
    repo.device_company_id.return_value = 13
    repo.get_or_create_unassigned_classification.return_value = 7

    conn = MagicMock()
    with patch.object(runner.reporting_mod, "refresh_daily_facts"), \
         patch.object(runner.reporting_mod, "refresh_classification_facts"), \
         patch.object(runner.intervals_mod, "build_intervals") as mock_build:
        mock_build.return_value = []
        runner._emit_algo_intervals(
            repo, conn, write_id=42, df=df, statuses_full=statuses,
            tz_name="UTC", schedules=[], special={}, gap_seconds=300.0,
            source="algo", rule="majority", emits_idle=True
        )

    # Capture what was passed to build_intervals
    statuses_arg = mock_build.call_args.kwargs["statuses"]

    # With emits_idle=True, IDLE should pass through untouched
    assert "IDLE" in statuses_arg, (
        f"IDLE should be preserved, but got: {statuses_arg}")
    assert statuses_arg == ["OFF", "IDLE", "LOAD"], (
        f"Expected ['OFF', 'IDLE', 'LOAD'], got: {statuses_arg}")


def _df(values, step_seconds=2):
    base = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    return pd.DataFrame({
        "time": [base + timedelta(seconds=i * step_seconds) for i in range(len(values))],
        "total_current": values,
    })


def _algo(**kw):
    return IdleThresholdAlgorithm(
        company="Envases Exportables", device_key="03-piloto",
        power_column="total_current", emits_idle=True, **kw)


def test_name_is_idle_threshold():
    assert _algo().name == "idle_threshold"


def test_classify_labels_every_sample():
    values = np.concatenate([np.full(30, 0.1), np.full(120, 18.5), np.full(60, 20.5)])
    out = _algo().classify(_df(values))
    assert len(out) == len(values)
    assert set(out) <= {"OFF", "IDLE", "LOAD"}


def test_classify_separates_idle_from_load():
    # 18.0 sits BELOW the stable-stretch threshold (18.3) and 20.5 above it, so
    # this exercises the threshold split itself. A value between 18.3 and 19.0
    # would come back all-LOAD and the only IDLE would be the warm-up fallback.
    values = np.concatenate([np.full(120, 18.0), np.full(120, 20.5)])
    out = list(_algo().classify(_df(values)))
    assert set(out[:120]) == {"IDLE"}
    assert set(out[120:]) == {"LOAD"}


def test_each_segment_is_classified_independently():
    """Un hueco de reporte divide la ventana: cada tramo debe clasificarse igual
    que si hubiera llegado solo.

    Sin la división, sigma, el umbral y el estado `donde` cruzarían el hueco y
    contaminarían el tramo siguiente. El tramo B empieza con una muestra OFF a
    propósito: es lo que reinicia `donde` y obliga a consultar sigma de nuevo,
    que es donde la contaminación se vuelve observable.
    """
    base = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    seg_a = np.concatenate([np.full(60, 6.0), np.full(60, 22.0)])
    seg_b = np.concatenate([np.full(1, 0.5), np.full(119, 18.4)])
    times_a = [base + timedelta(seconds=2 * i) for i in range(120)]
    times_b = [base + timedelta(hours=2, seconds=2 * i) for i in range(120)]

    joined = pd.DataFrame({"time": times_a + times_b,
                           "total_current": np.concatenate([seg_a, seg_b])})
    alone_a = pd.DataFrame({"time": times_a, "total_current": seg_a})
    alone_b = pd.DataFrame({"time": times_b, "total_current": seg_b})

    algo = _algo()
    assert list(algo.classify(joined)) == (
        list(algo.classify(alone_a)) + list(algo.classify(alone_b)))
