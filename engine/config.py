from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    db_dsn: str
    run_interval_seconds: int = 300
    status_window_days: int = 0
    default_tz: str = "America/Santiago"
    algorithms_dir: str = "algorithms"
    advisory_lock_key: int = 9_400_000_000
    on_schedule_only: bool = True
    emit_intervals: bool = True
    gap_seconds: float = 300.0
    interval_source: str = "algo"
    on_schedule_rule: str = "majority"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        dsn = env.get("DB_DSN")
        if not dsn:
            raise ValueError("DB_DSN is required")
        return cls(
            db_dsn=dsn,
            run_interval_seconds=int(env.get("RUN_INTERVAL_SECONDS", "300")),
            status_window_days=int(env.get("STATUS_WINDOW_DAYS", "0")),
            default_tz=env.get("DEFAULT_TZ", "America/Santiago"),
            algorithms_dir=env.get("ALGORITHMS_DIR", "algorithms"),
            on_schedule_only=env.get("STATUS_ON_SCHEDULE_ONLY", "true").strip().lower()
            not in ("false", "0", "no", "off"),
            emit_intervals=env.get("EMIT_INTERVALS", "true").strip().lower()
            not in ("false", "0", "no", "off"),
            gap_seconds=float(env.get("GAP_SECONDS", "300")),
            interval_source=env.get("INTERVAL_SOURCE", "algo"),
            on_schedule_rule=env.get("ON_SCHEDULE_RULE", "majority"),
        )
