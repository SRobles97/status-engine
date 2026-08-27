"""Paridad piloto ↔ umbral: lo que el motor debe conservar y etiquetar al
reconstruir un día, y el espejo de configuración piloto→origen.

Las tres piezas nacen del mismo hallazgo: el motor reescribe el día entero en
cada corrida, así que todo lo que no vuelva a derivar se pierde en silencio.
"""
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from engine import repository, runner
from engine.discovery import DiscoveredAlgorithm
from engine.algorithms import ThresholdAlgorithm
from engine.intervals import IntervalRow


def _conn_with_cursor():
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def _off(start, allowed=False, cid=None):
    return IntervalRow(device_id=99, source="algo", state="OFF",
                       start_time=start, end_time=start, measurement_count=1,
                       is_allowed=allowed, on_schedule_seconds=0,
                       on_schedule_ratio=0.0, on_schedule=False,
                       on_schedule_rule="majority", classification_id=cid)


T0 = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 26, 13, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 26, 14, tzinfo=timezone.utc)


# --- repositorio -----------------------------------------------------------

def test_fetch_algo_classifications_indexes_by_start_time():
    conn, cur = _conn_with_cursor()
    cur.fetchall.return_value = [(T0, 5), (T1, 9)]
    got = repository.fetch_algo_classifications_for_day(
        conn, 99, date(2026, 8, 26), "America/Santiago", "algo")
    assert got == {T0: 5, T1: 9}
    sql, params = cur.execute.call_args.args
    assert "classification_id IS NOT NULL" in sql and "state = 'OFF'" in sql
    assert params == (99, "algo", "America/Santiago", date(2026, 8, 26))


def test_get_or_create_allowed_uses_worker_signature():
    """Mismo nombre, color e is_system que el worker umbral: si difieren, el
    reporte pinta dos tajadas distintas para el mismo concepto."""
    conn, cur = _conn_with_cursor()
    cur.fetchone.side_effect = [None, (12,)]
    assert repository.get_or_create_allowed_classification(conn, 3) == 12
    insert_sql, params = cur.execute.call_args_list[1].args
    assert "INSERT INTO classifications" in insert_sql
    assert params[0] == 3 and params[1] == "Paro permitido"
    assert "#4CAF50" in insert_sql


def test_insert_intervals_writes_classification_id():
    conn, _ = _conn_with_cursor()
    with patch("engine.repository.execute_values") as ev:
        repository.insert_intervals(conn, [_off(T0, allowed=True, cid=12)])
    assert "classification_id" in ev.call_args.args[1]
    assert 12 in ev.call_args.args[2][0]


# --- reglas de conservación / etiquetado -----------------------------------

def test_manual_classification_survives_the_rebuild():
    rows = [_off(T0), _off(T1, allowed=True)]
    runner.apply_classifications(rows, {T0: 44, T1: 44}, allowed_id=12)
    assert [r.classification_id for r in rows] == [44, 44]


def test_untouched_allowed_off_gets_paro_permitido():
    rows = [_off(T0, allowed=True), _off(T1, allowed=False)]
    runner.apply_classifications(rows, {}, allowed_id=12)
    assert rows[0].classification_id == 12
    assert rows[1].classification_id is None  # sin asignar = NULL, como umbral


def test_load_rows_are_never_classified():
    row = IntervalRow(device_id=99, source="algo", state="LOAD",
                      start_time=T0, end_time=None, measurement_count=1,
                      is_allowed=False, on_schedule_seconds=0,
                      on_schedule_ratio=0.0, on_schedule=False,
                      on_schedule_rule="majority")
    runner.apply_classifications([row], {T0: 44}, allowed_id=12)
    assert row.classification_id is None


def test_no_allowed_classification_leaves_allowed_rows_null():
    """Empresa sin company_id resoluble: se degrada, no explota."""
    rows = [_off(T0, allowed=True)]
    runner.apply_classifications(rows, {}, allowed_id=None)
    assert rows[0].classification_id is None


# --- espejo de configuración ----------------------------------------------

_SCHED = [({"monday": {}}, None, {"2026-06-29": {}}, date(2026, 5, 1), None, "2.0", "day")]


def test_mirror_schedules_rewrites_when_source_differs():
    conn, cur = _conn_with_cursor()
    cur.fetchall.side_effect = [_SCHED, []]  # origen tiene fila, piloto no
    assert repository.mirror_schedules(conn, 42, 99) is True
    delete_sql, delete_params = cur.execute.call_args_list[-2].args
    assert "DELETE FROM device_schedules" in delete_sql and delete_params == (99,)
    insert_sql, insert_params = cur.execute.call_args_list[-1].args
    # Copia dentro de la BD: el jsonb no vuelve a pasar por psycopg2.
    assert "INSERT INTO device_schedules" in insert_sql and "SELECT" in insert_sql
    assert insert_params == (99, "pilot_mirror", 42)


def test_mirror_schedules_noop_when_identical():
    conn, cur = _conn_with_cursor()
    cur.fetchall.side_effect = [_SCHED, list(_SCHED)]
    assert repository.mirror_schedules(conn, 42, 99) is False
    assert not any("DELETE" in c.args[0] for c in cur.execute.call_args_list)


def test_mirror_threshold_copies_source_value():
    conn, cur = _conn_with_cursor()
    cur.fetchone.side_effect = [(5,), (3,)]  # origen 5, piloto 3
    assert repository.mirror_threshold(conn, 42, 99) is True
    sql, params = cur.execute.call_args.args
    assert "INSERT INTO device_threshold_config" in sql and params == (99, 5)


def test_mirror_threshold_noop_when_equal():
    conn, cur = _conn_with_cursor()
    cur.fetchone.side_effect = [(5,), (5,)]
    assert repository.mirror_threshold(conn, 42, 99) is False


# --- integración en el runner ---------------------------------------------

def _pilot_algo():
    return ThresholdAlgorithm(company="Riñihue", device_key="F1-piloto",
                              power_column="phase_a_active_power", threshold_w=1100,
                              source_device_key="F1")


def _repo_mock():
    repo = MagicMock()
    repo.device_timezone.return_value = "UTC"
    repo.fetch_window.return_value = pd.DataFrame({
        "time": pd.date_range("2026-08-26", periods=2, freq="min", tz="UTC"),
        "phase_a_active_power": [0.0, 5000.0]})
    repo.fetch_device_schedules.return_value = ([], {})
    repo.upsert_measurement_status.return_value = 2
    return repo


def test_run_once_mirrors_pilot_config_from_source():
    repo = _repo_mock()
    disc = DiscoveredAlgorithm(algorithm=_pilot_algo(), device_id=99, source_device_id=42)
    runner.run_once(repo, MagicMock(), disc, datetime(2026, 8, 26, 15, tzinfo=timezone.utc),
                    0, "UTC", on_schedule_only=False)
    assert repo.mirror_schedules.call_args.args[1:] == (42, 99)
    assert repo.mirror_threshold.call_args.args[1:] == (42, 99)


def test_run_once_does_not_mirror_a_non_pilot():
    repo = _repo_mock()
    algo = ThresholdAlgorithm(company="Riñihue", device_key="F1",
                              power_column="phase_a_active_power", threshold_w=1100)
    disc = DiscoveredAlgorithm(algorithm=algo, device_id=42)
    runner.run_once(repo, MagicMock(), disc, datetime(2026, 8, 26, 15, tzinfo=timezone.utc),
                    0, "UTC", on_schedule_only=False)
    repo.mirror_schedules.assert_not_called()
    repo.mirror_threshold.assert_not_called()


def test_emit_snapshots_classifications_before_deleting_the_day():
    """El orden importa: leer DESPUÉS del delete devolvería siempre vacío."""
    repo = MagicMock()
    repo.fetch_threshold_minutes.return_value = 15
    repo.device_company_id.return_value = 3
    repo.get_or_create_unassigned_classification.return_value = 7
    repo.get_or_create_allowed_classification.return_value = 12
    repo.fetch_algo_classifications_for_day.return_value = {}
    calls = []
    repo.fetch_algo_classifications_for_day.side_effect = lambda *a, **k: (
        calls.append("fetch") or {})
    repo.delete_algo_intervals_for_day.side_effect = lambda *a, **k: calls.append("delete")
    repo.insert_intervals.side_effect = lambda *a, **k: calls.append("insert")

    df = pd.DataFrame({"time": pd.date_range("2026-08-26", periods=2, freq="min", tz="UTC")})
    with patch.object(runner.reporting_mod, "refresh_daily_facts"), \
         patch.object(runner.reporting_mod, "refresh_classification_facts"):
        runner._emit_algo_intervals(repo, MagicMock(), 99, df, pd.Series(["OFF", "OFF"]),
                                    "UTC", [], {}, 300.0, "algo", "majority")
    assert calls.index("fetch") < calls.index("delete") < calls.index("insert")


# --- el auto-tag es del motor, no del usuario -------------------------------

def test_stale_paro_permitido_is_dropped_when_no_longer_allowed():
    """Baja el umbral y el paro deja de ser permitido: la etiqueta AUTOMÁTICA
    tiene que caerse con la regla que la puso.

    Sin esto, la fila queda con is_allowed=false pero clasificada 'Paro
    permitido': el reporte la pinta como paro autorizado mientras
    `allowed_off_minutes` (que sale de is_allowed) dice que no lo es. Es la
    pasada REVERT de `reapply_allowed_threshold_for_device`, que el motor tiene
    que replicar porque reconstruye el día por su cuenta.
    """
    rows = [_off(T0, allowed=False)]
    runner.apply_classifications(rows, {T0: 12}, allowed_id=12)
    assert rows[0].classification_id is None


def test_manual_classification_survives_even_when_it_stops_being_allowed():
    rows = [_off(T0, allowed=False)]
    runner.apply_classifications(rows, {T0: 44}, allowed_id=12)
    assert rows[0].classification_id == 44


def test_paro_permitido_is_kept_while_the_rule_still_holds():
    rows = [_off(T0, allowed=True)]
    runner.apply_classifications(rows, {T0: 12}, allowed_id=12)
    assert rows[0].classification_id == 12


def test_preserved_lookup_matches_pandas_timestamps():
    """La foto vuelve de psycopg2 como `datetime`; los `start_time` de las filas
    reconstruidas salen de un DataFrame y son `pandas.Timestamp`.

    El emparejamiento es por clave de dict, así que depende de que los dos
    hasheen igual. Se cumple mientras no haya nanosegundos — y no los hay, porque
    `power_measurements.time` llega con precisión de microsegundo. Si algún día
    la foto pasa a devolver strings, o los tiempos ganan nanosegundos, esto se
    rompe EN SILENCIO: la clasificación no se pierde con un error, simplemente
    no se vuelve a encontrar y el intervalo reaparece sin clasificar.
    """
    ts = pd.Timestamp("2026-08-26T12:00:00.699Z")
    row = _off(ts)
    runner.apply_classifications(
        [row], {datetime(2026, 8, 26, 12, 0, 0, 699000, tzinfo=timezone.utc): 44},
        allowed_id=12)
    assert row.classification_id == 44
