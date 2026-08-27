from __future__ import annotations

from typing import Optional

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from engine.algorithms import ALLOWED_POWER_COLUMNS


def connect(dsn: str):
    return psycopg2.connect(dsn)


def resolve_device_id(conn, device_key: str) -> Optional[int]:
    # device_key is globally unique in this deployment, so the <Company> folder
    # in the algorithms tree is purely organizational and is not used to resolve.
    sql = (
        "SELECT d.id FROM devices d "
        "WHERE d.device_key = %s AND d.is_active = true"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (device_key,))
        row = cur.fetchone()
    return int(row[0]) if row else None


def fetch_device_schedules(conn, device_id: int):
    """Return (schedule_versions, merged_special_days) for a device.

    schedule_versions: list of (valid_from, valid_to, shift_type, day_schedules).
    merged_special_days: special_days from all versions merged into one dict.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT valid_from, valid_to, COALESCE(shift_type, 'day'), day_schedules "
            "FROM device_schedules WHERE device_id = %s ORDER BY valid_from, shift_type",
            (device_id,),
        )
        schedules = [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]
        cur.execute(
            "SELECT special_days FROM device_schedules WHERE device_id = %s",
            (device_id,),
        )
        special: dict = {}
        for (sd,) in cur.fetchall():
            if isinstance(sd, dict):
                special.update(sd)
    return schedules, special


def device_timezone(conn, device_id: int) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT timezone FROM devices WHERE id = %s", (device_id,))
        row = cur.fetchone()
    return row[0] if row else None


def fetch_window(conn, device_id: int, power_column: str, start, end,
                 extra_columns=None) -> pd.DataFrame:
    if power_column not in ALLOWED_POWER_COLUMNS:
        raise ValueError(f"invalid power_column: {power_column!r}")
    extras = list(extra_columns or [])
    for col in extras:
        if col not in ALLOWED_POWER_COLUMNS:
            raise ValueError(f"invalid extra column: {col!r}")
    select_cols = ["time", power_column] + extras
    sql = (
        f"SELECT {', '.join(select_cols)} FROM power_measurements "
        "WHERE device_id = %s AND time >= %s AND time < %s "
        f"AND {power_column} IS NOT NULL ORDER BY time ASC"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (device_id, start, end))
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=select_cols)


def upsert_measurement_status(
    conn, device_id: int, times: list, statuses: list[str], algorithm: str
) -> int:
    rows = [(device_id, t, s, algorithm) for t, s in zip(times, statuses)]
    if not rows:
        return 0
    sql = (
        "INSERT INTO measurement_status (device_id, time, status, algorithm) VALUES %s "
        "ON CONFLICT (device_id, time) DO UPDATE "
        "SET status = EXCLUDED.status, algorithm = EXCLUDED.algorithm, computed_at = now()"
    )
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    return len(rows)


def upsert_device_algo_status(
    conn, device_id: int, status: str, algorithm: str, last_time, count: int
) -> None:
    sql = (
        "INSERT INTO device_algo_status "
        "(device_id, status, algorithm, last_measurement_time, measurement_count, computed_at) "
        "VALUES (%s, %s, %s, %s, %s, now()) "
        "ON CONFLICT (device_id) DO UPDATE SET status = EXCLUDED.status, "
        "algorithm = EXCLUDED.algorithm, last_measurement_time = EXCLUDED.last_measurement_time, "
        "measurement_count = EXCLUDED.measurement_count, computed_at = now()"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (device_id, status, algorithm, last_time, count))


def insert_run_log(conn, *, company, device_key, device_id, algorithm,
                   processed_count, updated_count, result, error_detail, duration_ms) -> None:
    sql = (
        "INSERT INTO status_run_log "
        "(company, device_key, device_id, algorithm, processed_count, updated_count, "
        "result, error_detail, duration_ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (company, device_key, device_id, algorithm, processed_count,
                          updated_count, result, error_detail, duration_ms))


def close_open_algo_intervals_before(conn, device_id: int, cutoff,
                                     source: str = "algo") -> int:
    """Recorta en `cutoff` todo intervalo del motor que invada la ventana.

    `build_intervals` deja ABIERTO el último intervalo de la ventana (= estado
    actual). Cuando la ventana avanza de día, ese abierto queda fuera del
    barrido por día de [delete_algo_intervals_for_day] — que filtra por
    `start_time` dentro del día — pero sigue cubriendo [inicio, ∞), así que
    choca con cada fila que el motor intente insertar
    (`ex_device_interval_overlap`). El motor no puede recuperarse solo: re-deriva
    las mismas filas y vuelve a chocar en cada iteración.

    No basta con mirar `end_time IS NULL`: un intervalo CERRADO que empieza
    antes de la ventana y termina dentro de ella bloquea igual (p. ej. tras
    cerrar un abierto a mano en la fecha equivocada). La condición correcta es
    "empieza antes del corte y se extiende más allá", que es exactamente el
    solapamiento que la EXCLUDE constraint rechaza.

    Recortar en `cutoff` conserva la historia previa y libera el rango que el
    motor va a reconstruir. Devuelve cuántos recortó.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE device_state_intervals SET end_time = %s "
            "WHERE device_id = %s AND source = %s "
            "AND start_time < %s "
            "AND (end_time IS NULL OR end_time > %s)",
            (cutoff, device_id, source, cutoff, cutoff),
        )
        return cur.rowcount


# Savepoints: aíslan el fallo de UN algoritmo. Sin ellos, una excepción de BD
# envenena la transacción compartida y hasta el insert_run_log posterior falla,
# de modo que el motivo real nunca se registra y los demás algoritmos mueren.
def savepoint(conn, name: str = "algo_sp") -> None:
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {name}")


def rollback_to_savepoint(conn, name: str = "algo_sp") -> None:
    with conn.cursor() as cur:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")


def release_savepoint(conn, name: str = "algo_sp") -> None:
    with conn.cursor() as cur:
        cur.execute(f"RELEASE SAVEPOINT {name}")


def try_advisory_lock(conn, key: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        row = cur.fetchone()
    return bool(row[0])


def fetch_threshold_minutes(conn, device_id: int) -> Optional[float]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT duration_minutes FROM device_threshold_config WHERE device_id = %s",
            (device_id,),
        )
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def device_company_id(conn, device_id: int) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT company_id FROM devices WHERE id = %s", (device_id,))
        row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def get_or_create_unassigned_classification(conn, company_id: int) -> int:
    name = "Sin asignar"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM classifications WHERE company_id = %s AND name = %s",
            (company_id, name),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
        cur.execute(
            "INSERT INTO classifications (company_id, name, description, status, "
            "color, is_work, is_system) VALUES (%s, %s, '', 'active', '#9E9E9E', "
            "false, true) RETURNING id",
            (company_id, name),
        )
        return int(cur.fetchone()[0])


def fetch_algo_classifications_for_day(conn, device_id: int, local_day, tz_name: str,
                                       source: str = "algo") -> dict:
    """`start_time` -> `classification_id` de los OFF ya clasificados de ese día.

    El motor reconstruye el día entero en cada corrida (delete + insert), y el
    insert no sabía de clasificaciones: todo lo que un usuario clasificara hoy
    desaparecía en la siguiente iteración, ~5 min después. Esta foto se toma
    ANTES del delete y se vuelve a aplicar sobre las filas nuevas.

    La llave es `start_time` y no el `id`: el id es nuevo en cada reconstrucción.
    Los OFF de un mismo dispositivo+source no se solapan (lo garantiza
    `ex_device_interval_overlap`), así que `start_time` los identifica sin
    ambigüedad. Un paro que cambia de inicio entre corridas es, a efectos del
    reporte, otro paro; su clasificación no se arrastra a propósito.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT start_time, classification_id FROM device_state_intervals "
            "WHERE device_id = %s AND source = %s AND state = 'OFF' "
            "AND classification_id IS NOT NULL "
            "AND (start_time AT TIME ZONE %s)::date = %s",
            (device_id, source, tz_name, local_day),
        )
        return {row[0]: int(row[1]) for row in cur.fetchall()}


def get_or_create_allowed_classification(conn, company_id: int) -> int:
    """'Paro permitido' de la empresa, con la MISMA firma que crea el worker
    umbral (`job_intervals_incremental.get_or_create_classification`).

    Nombre, color e `is_system` tienen que coincidir exactamente: tanto
    'Uso de tiempos' como 'Tendencias' resuelven el paro autorizado por NOMBRE
    en el cliente, no por el `allowed_off_minutes` que manda la API. Una fila
    distinta para el mismo concepto pinta dos tajadas donde debería haber una.
    """
    name = "Paro permitido"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM classifications WHERE company_id = %s AND name = %s",
            (company_id, name),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
        cur.execute(
            "INSERT INTO classifications (company_id, name, description, status, "
            "color, is_work, is_system) VALUES (%s, %s, '', 'active', '#4CAF50', "
            "false, true) RETURNING id",
            (company_id, name),
        )
        return int(cur.fetchone()[0])


# --- Espejo de configuración piloto ← origen --------------------------------
#
# Un piloto se clasifica con las mediciones de OTRO equipo, pero los reportes
# leen su configuración del piloto mismo: 'Uso de tiempos' calcula las horas
# programadas desde `device_schedules` del piloto, y `is_allowed` sale de su
# `device_threshold_config`. El motor, en cambio, recorta los OFF con el horario
# del ORIGEN. Las dos copias se crearon a mano una vez y nunca se volvieron a
# sincronizar: hoy ningún piloto tiene los `special_days` de su máquina, así que
# cada feriado del origen sale como un día entero de "Programado sin datos".
#
# Sincronizarlas en cada corrida vuelve la deriva imposible. El motor pasa a ser
# el dueño de esas filas para los pilotos — editar el horario de un piloto en la
# app deja de tener efecto, que es justo lo que se quiere de un espejo oculto.

_SCHEDULE_COLUMNS = ("day_schedules", "extra_hours", "special_days",
                     "valid_from", "valid_to", "version", "shift_type")
_MIRROR_SOURCE = "pilot_mirror"


def _schedule_rows(conn, device_id: int) -> list:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_SCHEDULE_COLUMNS)} FROM device_schedules "
            "WHERE device_id = %s ORDER BY shift_type, valid_from",
            (device_id,),
        )
        return [tuple(r) for r in cur.fetchall()]


def mirror_schedules(conn, source_device_id: int, pilot_device_id: int) -> bool:
    """Deja el horario del piloto idéntico al del origen. True si reescribió.

    Compara primero y sólo entonces reescribe: el motor corre cada 5 minutos y
    un delete+insert incondicional churnearía la tabla y quemaría ids para nada.
    Cuando hay que reescribir se borra TODO el horario del piloto y se reinserta
    el del origen; `valid_range` es generada y `ex_device_schedules_overlap`
    rechaza versiones solapadas, así que reemplazar el juego completo dentro de
    la misma transacción es la única forma segura de converger.
    """
    src = _schedule_rows(conn, source_device_id)
    if src == _schedule_rows(conn, pilot_device_id):
        return False
    cols = ", ".join(_SCHEDULE_COLUMNS)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM device_schedules WHERE device_id = %s",
                    (pilot_device_id,))
        if src:
            # INSERT ... SELECT y no un round-trip por Python: `day_schedules` y
            # `special_days` son jsonb, y psycopg2 no readapta el dict que acaba
            # de leer ("can't adapt type 'dict'"). Copiar dentro del motor de BD
            # también evita cualquier pérdida de fidelidad en la ida y vuelta.
            cur.execute(
                f"INSERT INTO device_schedules (device_id, {cols}, source) "
                f"SELECT %s, {cols}, %s FROM device_schedules WHERE device_id = %s",
                (pilot_device_id, _MIRROR_SOURCE, source_device_id),
            )
    return True


def mirror_threshold(conn, source_device_id: int, pilot_device_id: int) -> bool:
    """Deja los minutos de paro permitido del piloto iguales a los del origen.

    Si el origen no tiene umbral configurado no se borra el del piloto: la regla
    automática simplemente no aplica, y borrarlo convertiría en no-permitidos
    paros que ya estaban clasificados como tales.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT duration_minutes FROM device_threshold_config "
                    "WHERE device_id = %s", (source_device_id,))
        src = cur.fetchone()
        if src is None or src[0] is None:
            return False
        cur.execute("SELECT duration_minutes FROM device_threshold_config "
                    "WHERE device_id = %s", (pilot_device_id,))
        dst = cur.fetchone()
        if dst is not None and dst[0] == src[0]:
            return False
        cur.execute(
            "INSERT INTO device_threshold_config (device_id, duration_minutes) "
            "VALUES (%s, %s) ON CONFLICT (device_id) DO UPDATE "
            "SET duration_minutes = EXCLUDED.duration_minutes, updated_at = now()",
            (pilot_device_id, int(src[0])),
        )
    return True


def delete_algo_intervals_for_day(conn, device_id: int, local_day, tz_name: str,
                                  source: str = "algo") -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM device_state_intervals "
            "WHERE device_id = %s AND source = %s "
            "AND (start_time AT TIME ZONE %s)::date = %s",
            (device_id, source, tz_name, local_day),
        )


def insert_intervals(conn, rows) -> int:
    if not rows:
        return 0
    values = [
        (r.device_id, r.source, r.state, r.start_time, r.end_time,
         r.measurement_count, r.is_allowed, r.on_schedule_seconds,
         r.on_schedule_ratio, r.on_schedule, r.on_schedule_rule,
         r.classification_id)
        for r in rows
    ]
    sql = (
        "INSERT INTO device_state_intervals "
        "(device_id, source, state, start_time, end_time, measurement_count, "
        "is_allowed, on_schedule_seconds, on_schedule_ratio, on_schedule, "
        "on_schedule_rule, classification_id) VALUES %s"
    )
    with conn.cursor() as cur:
        execute_values(cur, sql, values)
    return len(values)
