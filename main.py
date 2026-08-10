from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from engine import repository as default_repo
from engine.config import Settings
from engine.discovery import DiscoveredAlgorithm, discover
from engine.runner import run_all


def _warn_gap_seconds_mismatch(discovered: list[DiscoveredAlgorithm], settings: Settings) -> None:
    """Avisa si el `gap_seconds` de un algoritmo (usado por `classify` para
    segmentar sigma/Min5/donde) difiere del `gap_seconds` global de
    `Settings` (env `GAP_SECONDS`, usado por `collapse_runs` al construir
    intervalos). Hoy ambos valen 300 por defecto y coinciden siempre, pero
    nada los ata: si algún día difieren, el clasificador segmentaría en un
    punto distinto de donde el motor cierra los intervalos, produciendo un
    mal etiquetado sutil sin error ni log. Solo advierte — una diferencia
    deliberada no debe tumbar el motor, y no se sobreescribe el valor del
    algoritmo.
    """
    for d in discovered:
        algo_gap = getattr(d.algorithm, "gap_seconds", None)
        if algo_gap is not None and algo_gap != settings.gap_seconds:
            print(
                f"[status-engine] ADVERTENCIA: {d.algorithm.device_key} usa "
                f"gap_seconds={algo_gap} para clasificar pero el motor arma "
                f"los intervalos con Settings.gap_seconds={settings.gap_seconds} "
                "(env GAP_SECONDS) — revisa si la diferencia es intencional.",
                file=sys.stderr,
            )


def run_iteration(settings: Settings, *, repo=default_repo, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    conn = repo.connect(settings.db_dsn)
    try:
        if not repo.try_advisory_lock(conn, settings.advisory_lock_key):
            return []
        discovered = discover(
            Path(settings.algorithms_dir),
            resolver=lambda c, d: repo.resolve_device_id(conn, d),
        )
        _warn_gap_seconds_mismatch(discovered, settings)
        results = run_all(repo, conn, discovered,
                          now, settings.status_window_days, settings.default_tz,
                          on_schedule_only=settings.on_schedule_only,
                          emit_intervals=settings.emit_intervals,
                          gap_seconds=settings.gap_seconds,
                          interval_source=settings.interval_source,
                          on_schedule_rule=settings.on_schedule_rule)
        conn.commit()
        return results
    finally:
        conn.close()


def main() -> None:
    settings = Settings.from_env(os.environ)
    while True:
        try:
            results = run_iteration(settings)
            ok = sum(1 for r in results if r.result == "ok")
            print(f"[status-engine] iteration done: {ok}/{len(results)} ok", flush=True)
        except Exception as e:  # keep the unattended worker alive across transient failures
            print(f"[status-engine] iteration failed: {e}", flush=True)
        time.sleep(settings.run_interval_seconds)


if __name__ == "__main__":
    main()
