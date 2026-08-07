"""Regresión del bucle por intervalo algo ABIERTO de un día anterior.

Producción 2026-08-07: el motor llevaba dos días muerto. Cada iteración fallaba
con `ex_device_interval_overlap` en device 71 (tubera-piloto):

    nuevo    (71, algo, ["2026-08-06 07:10:00", "2026-08-06 07:11:14.359"))
    choca con (71, algo, ["2026-08-05 12:43:37.763", infinity))

`build_intervals` deja ABIERTO el último intervalo de la ventana (= estado
actual). Con STATUS_WINDOW_DAYS=0 la ventana sólo cubre HOY, y
`delete_algo_intervals_for_day` borra por `start_time` dentro del día: el
intervalo abierto de AYER queda fuera del barrido pero sigue cubriendo
[ayer, ∞), así que choca con todo lo que se inserte hoy. El motor no puede
recuperarse solo — re-deriva las mismas filas y vuelve a chocar cada 5 min.

Peor: la excepción envenena la transacción, y como `insert_run_log` corre en la
MISMA conexión, el motivo real nunca llega a status_run_log (queda sólo
"current transaction is aborted"). Un algoritmo roto mata a los seis.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from engine.algorithms import ThresholdAlgorithm
from engine.discovery import DiscoveredAlgorithm
from engine import runner

NOW = datetime(2026, 8, 7, 13, 0, tzinfo=timezone.utc)


def _algo():
    return ThresholdAlgorithm(company="Formac", device_key="tubera-piloto",
                              power_column="phase_a_active_power",
                              threshold_w=1100, smoothing_minutes=0)


def _repo_emitting():
    df = pd.DataFrame({
        "time": pd.date_range("2026-08-07T11:00", periods=3, freq="h", tz="UTC"),
        "phase_a_active_power": [0.0, 5000.0, 6000.0]})
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    repo.fetch_window.return_value = df
    repo.fetch_device_schedules.return_value = ([], {})
    repo.fetch_threshold_minutes.return_value = 15.0
    repo.device_company_id.return_value = 13
    repo.get_or_create_unassigned_classification.return_value = 7
    repo.upsert_measurement_status.return_value = 3
    return repo


def test_stale_open_interval_is_closed_before_insert():
    """El motor debe cerrar el abierto de días previos, o chocará para siempre."""
    repo = _repo_emitting()
    disc = DiscoveredAlgorithm(algorithm=_algo(), device_id=71)
    with patch.object(runner.reporting_mod, "refresh_daily_facts"), \
         patch.object(runner.reporting_mod, "refresh_classification_facts"):
        res = runner.run_once(repo, MagicMock(), disc, NOW, 0, "UTC",
                              on_schedule_only=False, emit_intervals=True,
                              gap_seconds=7200, interval_source="algo",
                              on_schedule_rule="majority")

    assert res.result == "ok"
    assert repo.close_open_algo_intervals_before.called, (
        "sin este cierre, el intervalo abierto de ayer bloquea todo insert de hoy"
    )
    # Se cierra ANTES del primer día procesado, y sólo para el device destino.
    args = repo.close_open_algo_intervals_before.call_args.args
    assert args[1] == 71
    cutoff = args[2]
    inserted = repo.insert_intervals.call_args.args[1]
    assert cutoff <= min(r.start_time for r in inserted)


def test_failing_algorithm_does_not_poison_the_rest():
    """FR-08 real: un algoritmo que falla no debe matar a los demás."""
    good_repo = _repo_emitting()
    good_repo.upsert_device_algo_status.side_effect = [RuntimeError("boom"), None]

    d1 = DiscoveredAlgorithm(algorithm=_algo(), device_id=71)
    d2 = DiscoveredAlgorithm(algorithm=_algo(), device_id=63)
    conn = MagicMock()
    with patch.object(runner.reporting_mod, "refresh_daily_facts"), \
         patch.object(runner.reporting_mod, "refresh_classification_facts"):
        results = runner.run_all(good_repo, conn, [d1, d2], NOW, 0, "UTC",
                                 emit_intervals=True, interval_source="algo")

    assert len(results) == 2
    assert results[0].result == "error"
    assert results[1].result == "ok", "el segundo algoritmo debe seguir corriendo"
    # El fallo se revierte para que el log del run sí se pueda escribir.
    assert good_repo.rollback_to_savepoint.called
    assert good_repo.insert_run_log.call_count == 2
