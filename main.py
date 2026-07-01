from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from engine import repository as default_repo
from engine.config import Settings
from engine.discovery import discover
from engine.runner import run_all


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
        results = run_all(repo, conn, discovered,
                          now, settings.status_window_days, settings.default_tz,
                          on_schedule_only=settings.on_schedule_only)
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
