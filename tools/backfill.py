"""Corrida única del motor sobre los últimos N días, para reconstruir el
historial 'algo' de dispositivos que recién pasaron a card_source='algoritmo'.

El servicio sólo procesa HOY (STATUS_WINDOW_DAYS=0), así que un dispositivo
nuevo en el motor no tiene intervalos 'algo' para días anteriores — y el
backend enruta por card_source sin mirar la fecha, por lo que sus reportes
históricos salen vacíos. Esta corrida los reconstruye con el mismo código del
servicio (run_iteration): corta a medianoche y reemplaza día por día.

Usage (en el VPS, junto al servicio corriendo):
    docker-compose run --rm status_engine \
        python -u tools/backfill.py --days 30 --dir algorithms/Repairco

--dir es OBLIGATORIO: sin él se reescribiría el historial de TODOS los
algoritmos con sus parámetros actuales, incluidos pilotos recalibrados.

El servicio y esta corrida comparten el advisory lock; si el servicio está a
mitad de iteración, se reintenta. Mientras el backfill lo tiene, el servicio se
salta sus iteraciones (no pierde nada: la siguiente rehace el día).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import reporting as reporting_mod  # noqa: E402
from engine import repository as default_repo  # noqa: E402
from engine.config import Settings  # noqa: E402
from main import run_iteration  # noqa: E402

# Copia cada clasificación MANUAL de un paro 'power' al paro 'algo' que más se
# le solapa. Los paros de las dos fuentes no coinciden uno a uno (el algoritmo
# rellena huecos y descarta cargas cortas), así que se elige por solape: cada
# paro 'algo' toma la clasificación del paro 'power' con el que comparte más
# tiempo. Sólo se escribe sobre lo que nadie decidió en 'algo' (NULL o un
# auto-tag), igual que la regla 1 de runner.apply_classifications.
_CARRY_SQL = """
    WITH manual AS (
      SELECT p.device_id, p.start_time, COALESCE(p.end_time, now()) AS end_time,
             p.classification_id
      FROM device_state_intervals p
      JOIN classifications c ON c.id = p.classification_id
      WHERE p.device_id = ANY(%(ids)s) AND p.source = 'power' AND p.state = 'OFF'
        AND c.name NOT IN ('Sin asignar', 'Paro permitido')
    ), best AS (
      SELECT DISTINCT ON (a.id) a.id, m.classification_id
      FROM device_state_intervals a
      JOIN manual m ON m.device_id = a.device_id
       AND a.start_time < m.end_time AND COALESCE(a.end_time, now()) > m.start_time
      LEFT JOIN classifications ac ON ac.id = a.classification_id
      WHERE a.source = 'algo' AND a.state = 'OFF'
        AND (a.classification_id IS NULL OR ac.name IN ('Sin asignar', 'Paro permitido'))
      ORDER BY a.id,
               LEAST(COALESCE(a.end_time, now()), m.end_time)
                 - GREATEST(a.start_time, m.start_time) DESC
    )
    UPDATE device_state_intervals a
    SET classification_id = best.classification_id, updated_at = now()
    FROM best, devices d
    WHERE a.id = best.id AND d.id = a.device_id
    RETURNING a.device_id,
              (a.start_time AT TIME ZONE COALESCE(d.timezone, 'America/Santiago'))::date,
              COALESCE(d.timezone, 'America/Santiago')
"""


def carry_classifications(conn, device_ids, repo=default_repo) -> int:
    """Copia las clasificaciones manuales 'power' → 'algo' y recalcula los
    facts de clasificación de los días tocados. Devuelve los paros copiados."""
    with conn.cursor() as cur:
        cur.execute(_CARRY_SQL, {"ids": list(device_ids)})
        touched = cur.fetchall()
    for device_id, day, tz in sorted(set(touched)):
        _, special = repo.fetch_device_schedules(conn, device_id)
        company_id = repo.device_company_id(conn, device_id)
        unassigned_id = repo.get_or_create_unassigned_classification(conn, company_id)
        reporting_mod.refresh_classification_facts(
            conn, device_id, day, tz, unassigned_id, "algo", special)
    return len(touched)


def main(argv=None, *, run=run_iteration, sleep=time.sleep, repo=default_repo) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--days", type=int, required=True,
                   help="días completos hacia atrás desde la medianoche de hoy")
    p.add_argument("--dir", required=True,
                   help="carpeta de algoritmos a reconstruir (p. ej. algorithms/Repairco)")
    p.add_argument("--retries", type=int, default=60,
                   help="intentos si el servicio tiene el lock (cada 10 s)")
    p.add_argument("--carry-classifications", action="store_true",
                   help="copiar las clasificaciones manuales de los paros 'power' "
                        "al paro 'algo' que más se les solapa")
    args = p.parse_args(argv)

    if args.days < 1:
        p.error("--days debe ser >= 1")
    if not Path(args.dir).is_dir():
        p.error(f"no existe la carpeta {args.dir}")

    settings = replace(Settings.from_env(os.environ),
                       status_window_days=args.days, algorithms_dir=args.dir)

    for attempt in range(1, args.retries + 1):
        started = time.monotonic()
        results = run(settings)
        if results:
            secs = time.monotonic() - started
            for r in results:
                detail = f" — {r.error_detail}" if r.error_detail else ""
                print(f"device_id={r.device_id} {r.result} "
                      f"muestras={r.processed_count}{detail}", flush=True)
            bad = [r for r in results if r.result != "ok"]
            print(f"backfill {args.days} días: {len(results) - len(bad)}/{len(results)} ok "
                  f"en {secs:.0f} s", flush=True)
            if bad:
                return 1
            if args.carry_classifications:
                ids = [r.device_id for r in results]
                conn = repo.connect(settings.db_dsn)
                try:
                    # Con el lock: si el servicio reconstruye HOY a la vez, su
                    # foto de clasificaciones no vería la copia y la borraría.
                    for _ in range(args.retries):
                        if repo.try_advisory_lock(conn, settings.advisory_lock_key):
                            break
                        sleep(10)
                    else:
                        print("no se obtuvo el lock para copiar clasificaciones", flush=True)
                        return 2
                    n = carry_classifications(conn, ids, repo)
                    conn.commit()
                finally:
                    conn.close()
                print(f"clasificaciones copiadas power→algo: {n} paros", flush=True)
            return 0
        print(f"lock ocupado por el servicio (intento {attempt}), reintento en 10 s",
              flush=True)
        sleep(10)
    print("no se obtuvo el lock; nada se escribió", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
