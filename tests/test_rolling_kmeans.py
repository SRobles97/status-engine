# status-engine/tests/test_rolling_kmeans.py
"""Máquina de estados del KMeans rodante (notebook 'Disp EnvExp Mold1.json').

Tests de comportamiento sobre datos sintéticos con separación IDLE/LOAD amplia,
para que el resultado de KMeans no dependa de la semilla. La fidelidad contra
las etiquetas reales del notebook se verifica aparte, en `test_kmeans_golden.py`.
"""
import numpy as np
import pandas as pd
import pytest

from engine.algorithms import RollingKMeansIdleAlgorithm

WINDOW = 120

ALGORITHM = RollingKMeansIdleAlgorithm(
    company="Envases Exportables",
    device_key="03-piloto",
    source_device_key="03",
    power_column="total_current",
    emits_idle=True,
    smoothing_minutes=0,
)


def _frame(values, *, start="2026-09-08T10:00:00Z", step_seconds=2):
    """Frame con las ocho columnas que consume el KMeans.

    Las fases se derivan de `total_current` proporcionalmente, que es como se
    comportan los datos reales de esta máquina: los tres hilos suben y bajan
    juntos, así que ninguna columna aporta una separación que `total_current`
    no tenga.
    """
    tc = np.asarray(values, dtype=float)
    times = pd.date_range(start, periods=len(tc), freq=f"{step_seconds}s", tz="UTC")
    return pd.DataFrame({
        "time": times,
        "phase_a_current": tc / 3.0,
        "phase_b_current": tc / 3.0,
        "phase_c_current": tc / 3.0,
        "phase_a_active_power": tc * 70.0,
        "phase_b_active_power": tc * 70.0,
        "phase_c_active_power": tc * 70.0,
        "total_current": tc,
        "total_active_power": tc * 210.0,
    })


def test_samples_below_the_off_threshold_are_off():
    out = list(ALGORITHM.classify(_frame([0.1] * 10)))
    assert out == ["OFF"] * 10


def test_a_run_shorter_than_the_window_falls_back_to_the_backward_threshold():
    """El notebook nunca ajusta KMeans en un tramo que no alcanza 120 muestras:
    lo clasifica hacia atrás contra un corte fijo de 19 A."""
    values = [0.1] * 5 + [25.0] * 20 + [18.0] * 20 + [0.1] * 5
    out = list(ALGORITHM.classify(_frame(values)))
    assert out[:5] == ["OFF"] * 5
    assert out[5:25] == ["LOAD"] * 20
    assert out[25:45] == ["IDLE"] * 20
    assert out[45:] == ["OFF"] * 5


def test_the_first_full_window_is_labelled_retroactively():
    """Tras el calentamiento el notebook etiqueta las 120 muestras de la primera
    ventana de una vez, no sólo la actual."""
    values = [18.0] * 100 + [25.0] * 100
    out = list(ALGORITHM.classify(_frame(values)))
    assert out[:100] == ["IDLE"] * 100
    assert out[100:] == ["LOAD"] * 100


@pytest.mark.parametrize("high_first", [True, False], ids=["high-first", "low-first"])
def test_the_higher_current_cluster_is_always_load(high_first):
    """La identidad del clúster la fija el centroide de `power_column`, no el id
    que devuelva sklearn — que depende del orden de los datos."""
    block_a, block_b = (25.0, 18.0) if high_first else (18.0, 25.0)
    out = list(ALGORITHM.classify(_frame([block_a] * 100 + [block_b] * 100)))
    high, low = ("LOAD", "IDLE")
    assert out[:100] == [high if high_first else low] * 100
    assert out[100:] == [low if high_first else high] * 100


def test_a_short_idle_gap_between_two_loads_is_absorbed():
    """Celda de TRAMOS CORTOS: menos de 5 muestras de IDLE/OFF entre dos LOAD
    pasan a LOAD."""
    values = [25.0] * 130 + [18.0] * 3 + [25.0] * 60
    out = list(ALGORITHM.classify(_frame(values)))
    assert out[130:133] == ["LOAD"] * 3, "el hueco de 3 muestras debía absorberse"


def test_a_long_idle_gap_between_two_loads_survives():
    values = [25.0] * 130 + [18.0] * 40 + [25.0] * 60
    out = list(ALGORITHM.classify(_frame(values)))
    assert out[135:165] == ["IDLE"] * 30


def test_labels_do_not_depend_on_how_much_history_the_window_fetched():
    """Invariante del que depende el backfill: `STATUS_WINDOW_DAYS` es global y
    ensancha la ventana de TODOS los algoritmos. Si ensancharla cambiara las
    etiquetas de los días ya escritos, una recorrida reescribiría la historia
    con otros números en silencio."""
    today = [0.1] * 5 + [18.0] * 100 + [25.0] * 100 + [0.1] * 5
    alone = list(ALGORITHM.classify(_frame(today, start="2026-09-08T10:00:00Z")))

    yesterday = [0.1] * 5 + [25.0] * 150 + [0.1] * 5
    combined_df = pd.concat(
        [_frame(yesterday, start="2026-09-07T10:00:00Z"),
         _frame(today, start="2026-09-08T10:00:00Z")],
        ignore_index=True)
    combined = list(ALGORITHM.classify(combined_df))

    assert combined[len(yesterday):] == alone


def test_the_engine_never_emits_an_unlabelled_sample():
    """El notebook admite dejar muestras en 'CERO'; el motor escribe una fila por
    muestra, así que todas tienen que resolverse."""
    values = [0.1] * 3 + [18.5] * 7 + [0.1] * 3 + [25.0] * 200 + [0.1] * 3
    out = set(ALGORITHM.classify(_frame(values)))
    assert out <= {"OFF", "IDLE", "LOAD"}


def test_power_column_must_be_one_of_the_feature_columns():
    """El orden de los clústeres se decide por el centroide de `power_column`;
    si no está entre las features no hay centroide que mirar."""
    with pytest.raises(ValueError):
        RollingKMeansIdleAlgorithm(
            company="C", device_key="D", power_column="total_active_power",
            feature_columns=("total_current", "phase_a_current"))


def test_feature_columns_are_restricted_to_the_allow_list():
    with pytest.raises(ValueError):
        RollingKMeansIdleAlgorithm(
            company="C", device_key="D", power_column="total_current",
            feature_columns=("total_current", "drop table devices"))


def test_a_flat_window_falls_back_to_the_fixed_threshold(recwarn):
    """Un tramo perfectamente plano no tiene dos clústeres que separar: KMeans
    devolvería una partición arbitraria, y encima un ConvergenceWarning por
    tick. Desviación deliberada del notebook, que en Colab sólo ve datos reales
    (con ruido) y nunca llega acá."""
    assert list(ALGORITHM.classify(_frame([18.0] * 200))) == ["IDLE"] * 200
    assert list(ALGORITHM.classify(_frame([25.0] * 200))) == ["LOAD"] * 200
    assert not [w for w in recwarn if "cluster" in str(w.message).lower()]


def test_the_runner_fetches_every_feature_column():
    """El motor sólo pedía `power_column` (+ `guard_column`). Un algoritmo de
    ocho columnas contra ese fetch recibe un frame de dos y clasifica sobre una
    sola dimensión — o revienta. Ninguna de las dos cosas es aceptable en
    silencio."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    from engine.discovery import DiscoveredAlgorithm
    from engine import runner

    df = _frame([0.1] * 3 + [25.0] * 200)
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    repo.fetch_window.return_value = df
    repo.upsert_measurement_status.return_value = len(df)
    disc = DiscoveredAlgorithm(algorithm=ALGORITHM, device_id=74, source_device_id=66)

    res = runner.run_once(repo, MagicMock(), disc,
                          datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc),
                          0, "UTC", on_schedule_only=False, emit_intervals=False)

    assert res.result == "ok"
    requested = set(repo.fetch_window.call_args.kwargs["extra_columns"] or ())
    requested.add(repo.fetch_window.call_args.args[2])
    assert requested == set(ALGORITHM.feature_columns)
