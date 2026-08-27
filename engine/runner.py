from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from engine.algorithms import DegenerateWindowError
from engine.discovery import DiscoveredAlgorithm
from engine import intervals as intervals_mod
from engine import reporting as reporting_mod
from engine.schedule import on_schedule_mask
from engine.smoothing import drop_short_loads, smooth_statuses


@dataclass
class RunResult:
    device_id: Optional[int]
    algorithm: str
    processed_count: int
    updated_count: int
    result: str
    error_detail: Optional[str] = None


def _classify(algo, df):
    statuses = algo.classify(df)
    statuses = smooth_statuses(statuses, df["time"], algo.smoothing_minutes)
    # Orden fijo: rellenar huecos cortos primero, medir las rachas de LOAD
    # después. Invertirlo mediría rachas que el relleno todavía no unió.
    statuses = drop_short_loads(statuses, df["time"], algo.min_load_minutes)
    if algo.guard_column:
        statuses = statuses.mask(
            df[algo.guard_column].astype(float) < algo.guard_min, "OFF")
    return statuses


def apply_classifications(rows, preserved: dict, allowed_id):
    """Resuelve el `classification_id` de las filas OFF recién reconstruidas.

    Tres reglas, en este orden:

    1. **Lo que un usuario clasificó, se conserva.** `preserved` es la foto
       tomada antes del delete (ver `repository.fetch_algo_classifications_for_day`).
       Incluye tanto una clasificación manual como un 'Sin asignar' explícito:
       cualquier valor no nulo que NO sea el auto-tag es una decisión de alguien,
       y el motor no la pisa.
    2. **'Paro permitido' es del motor, no del usuario.** Si la fila reconstruida
       ya no cae bajo el umbral, la etiqueta se cae con la regla que la puso — es
       la pasada REVERT de `reapply_allowed_threshold_for_device`, que acá hay que
       replicar porque el motor reconstruye el día por su cuenta y el `reapply`
       del backend sólo corre cuando alguien edita el umbral. Dejarla puesta
       daría un intervalo pintado como paro autorizado en el reporte mientras
       `allowed_off_minutes` — que sale de `is_allowed` — dice que no lo es.
    3. **Lo que nadie tocó y cae bajo el umbral, se etiqueta 'Paro permitido'.**
       Es lo que hace el worker umbral al cerrar el intervalo
       (`backfill_is_allowed_for_interval`). Sin esto los paros cortos del piloto
       llegan al reporte como 'Sin asignar' — y encima invisibles, porque la
       pantalla de clasificación filtra por `is_allowed`.

    El resto queda en NULL, que es como el worker umbral deja los no permitidos;
    los facts lo resuelven a 'Sin asignar' al agregar.
    """
    for r in rows:
        if r.state != "OFF":
            continue
        was = preserved.get(r.start_time)
        if was is not None and was != allowed_id:
            r.classification_id = was
        elif r.is_allowed and allowed_id is not None:
            r.classification_id = allowed_id


def _emit_algo_intervals(repo, conn, write_id, df, statuses_full, tz_name,
                         schedules, special, gap_seconds, source, rule,
                         emits_idle=False):
    labels = statuses_full if emits_idle else intervals_mod.remap_idle_to_load(statuses_full)
    times = list(df["time"])
    allowed_minutes = repo.fetch_threshold_minutes(conn, write_id)
    rows = intervals_mod.build_intervals(
        device_id=write_id, times=times, statuses=list(labels),
        tz_name=tz_name, schedules=schedules, special=special,
        allowed_minutes=allowed_minutes, gap_seconds=gap_seconds,
        source=source, rule=rule)

    tz = ZoneInfo(tz_name)
    by_day = OrderedDict()
    for r in rows:
        day = r.start_time.astimezone(tz).date()
        by_day.setdefault(day, []).append(r)
    # also refresh days the window covers even if they produced no rows
    for t in times:
        by_day.setdefault(t.astimezone(tz).date(), [])

    # Cierra el intervalo abierto que haya quedado de días anteriores. El
    # barrido por día sólo alcanza filas cuyo start_time cae en ese día, así que
    # un abierto de ayer sobrevive cubriendo [ayer, ∞) y choca con todo lo que
    # se inserte hoy (ex_device_interval_overlap), dejando al motor en bucle.
    if by_day:
        cutoff = datetime.combine(min(by_day), dt_time(0, 0), tzinfo=tz)
        repo.close_open_algo_intervals_before(conn, write_id, cutoff, source)

    company_id = repo.device_company_id(conn, write_id)
    unassigned_id = (repo.get_or_create_unassigned_classification(conn, company_id)
                     if company_id is not None else None)
    allowed_id = (repo.get_or_create_allowed_classification(conn, company_id)
                  if company_id is not None else None)
    for day, day_rows in by_day.items():
        # La foto va ANTES del delete: después el día está vacío y se perdería
        # todo lo que alguien clasificó desde la corrida anterior.
        preserved = repo.fetch_algo_classifications_for_day(
            conn, write_id, day, tz_name, source)
        repo.delete_algo_intervals_for_day(conn, write_id, day, tz_name, source)
        apply_classifications(day_rows, preserved, allowed_id)
        repo.insert_intervals(conn, day_rows)
        reporting_mod.refresh_daily_facts(conn, write_id, day, tz_name, source, special)
        if unassigned_id is not None:
            reporting_mod.refresh_classification_facts(
                conn, write_id, day, tz_name, unassigned_id, source, special)


def window_bounds(now: datetime, tz_name: str, window_days: int) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    local_now = now.astimezone(tz)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=window_days)
    return start, now


def run_once(repo, conn, discovered: DiscoveredAlgorithm, now: datetime,
             window_days: int, default_tz: str, on_schedule_only: bool = False,
             emit_intervals: bool = False, gap_seconds: float = 300.0,
             interval_source: str = "algo", on_schedule_rule: str = "majority") -> RunResult:
    algo = discovered.algorithm
    write_id = discovered.device_id
    if write_id is None:
        return RunResult(None, algo.name, 0, 0, "skipped",
                         f"unresolved device {algo.company}/{algo.device_key}")
    if algo.source_device_key and discovered.source_device_id is None:
        return RunResult(write_id, algo.name, 0, 0, "skipped",
                         f"unresolved source device {algo.source_device_key}")
    read_id = discovered.source_device_id if algo.source_device_key else write_id

    # Un piloto es un espejo: el motor lo clasifica con el horario del ORIGEN,
    # pero los reportes leen las horas programadas y el umbral de paro permitido
    # del PILOTO. Se sincronizan acá, en cada corrida, para que las dos mitades
    # no puedan divergir (los feriados del origen ya se habían perdido en todos
    # los pilotos). No-op barato cuando ya coinciden.
    if read_id != write_id:
        repo.mirror_schedules(conn, read_id, write_id)
        repo.mirror_threshold(conn, read_id, write_id)

    tz_name = repo.device_timezone(conn, read_id) or default_tz
    start, end = window_bounds(now, tz_name, window_days)
    extra = ([algo.guard_column]
             if algo.guard_column and algo.guard_column != algo.power_column else None)
    df = repo.fetch_window(conn, read_id, algo.power_column, start, end, extra_columns=extra)
    if df.empty:
        return RunResult(write_id, algo.name, 0, 0, "skipped", "empty window")

    schedules, special = ([], {})
    if emit_intervals or on_schedule_only:
        schedules, special = repo.fetch_device_schedules(conn, read_id)

    if emit_intervals:
        try:
            statuses_full = _classify(algo, df)
            _emit_algo_intervals(repo, conn, write_id, df, statuses_full, tz_name,
                                 schedules, special, gap_seconds, interval_source,
                                 on_schedule_rule, emits_idle=algo.emits_idle)
        except DegenerateWindowError as e:
            return RunResult(write_id, algo.name, len(df), 0, "skipped", str(e))

    # Restrict to the device's work hours, matching the production worker's coverage.
    # A device with no configured schedule is classified in full (no filtering).
    if on_schedule_only:
        if schedules:
            mask = on_schedule_mask(list(df["time"]), tz_name, schedules, special)
            df = df[mask]
            if df.empty:
                return RunResult(write_id, algo.name, 0, 0,
                                 "skipped", "no on-schedule measurements")

    try:
        statuses = _classify(algo, df)
    except DegenerateWindowError as e:
        return RunResult(write_id, algo.name, len(df), 0, "skipped", str(e))

    times = list(df["time"])
    updated = repo.upsert_measurement_status(conn, write_id, times, list(statuses), algo.name)
    repo.upsert_device_algo_status(conn, write_id, statuses.iloc[-1], algo.name, times[-1], len(df))
    return RunResult(write_id, algo.name, len(df), updated, "ok")


def run_all(repo, conn, discovered_list, now: datetime, window_days: int,
            default_tz: str, on_schedule_only: bool = False,
            emit_intervals: bool = False, gap_seconds: float = 300.0,
            interval_source: str = "algo", on_schedule_rule: str = "majority") -> list[RunResult]:
    results: list[RunResult] = []
    for discovered in discovered_list:
        algo = discovered.algorithm
        started = time.monotonic()
        # Un savepoint por algoritmo: sin él, un error de BD envenena la
        # transacción compartida y el insert_run_log de más abajo también falla
        # ("current transaction is aborted"), así que el motivo real nunca se
        # registra y los algoritmos siguientes mueren con él. FR-08 pide
        # "log and continue"; continuar exige revertir.
        repo.savepoint(conn)
        try:
            res = run_once(repo, conn, discovered, now, window_days, default_tz,
                           on_schedule_only=on_schedule_only,
                           emit_intervals=emit_intervals, gap_seconds=gap_seconds,
                           interval_source=interval_source, on_schedule_rule=on_schedule_rule)
            repo.release_savepoint(conn)
        except Exception as e:  # FR-08: log and continue
            repo.rollback_to_savepoint(conn)
            res = RunResult(discovered.device_id, algo.name, 0, 0, "error", str(e))
        duration_ms = int((time.monotonic() - started) * 1000)
        repo.insert_run_log(
            conn, company=algo.company, device_key=algo.device_key,
            device_id=discovered.device_id, algorithm=algo.name,
            processed_count=res.processed_count, updated_count=res.updated_count,
            result=res.result, error_detail=res.error_detail, duration_ms=duration_ms)
        results.append(res)
    return results
