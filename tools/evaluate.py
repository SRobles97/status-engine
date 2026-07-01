"""Compare status-engine's per-measurement classification against the existing
intervals worker's device_state_intervals, to see where the two agree.

Usage:
    DB_DSN=postgresql://postgres:local@localhost:5433/centineldb \
        venv/bin/python tools/evaluate.py <device_key>

It maps every row in measurement_status to the device_state_intervals state that
covers its timestamp, then prints a confusion matrix and an agreement score.
IDLE (k-means only) is counted as LOAD for the comparison, since the intervals
worker only produces LOAD/OFF.
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg2

CONFUSION_SQL = """
    SELECT ms.status AS algo,
           COALESCE(i.state, 'NONE') AS intervals,
           count(*) AS n
      FROM measurement_status ms
      LEFT JOIN device_state_intervals i
        ON i.device_id = ms.device_id
       AND ms.time >= i.start_time
       AND (i.end_time IS NULL OR ms.time < i.end_time)
     WHERE ms.device_id = %s
     GROUP BY 1, 2
     ORDER BY 1, 2
"""


def _norm(status: str) -> str:
    return "LOAD" if status in ("LOAD", "IDLE") else status


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("device_key")
    ap.add_argument("--dsn", default=os.environ.get("DB_DSN"))
    args = ap.parse_args()
    if not args.dsn:
        sys.exit("set DB_DSN or pass --dsn")

    conn = psycopg2.connect(args.dsn)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM devices WHERE device_key = %s", (args.device_key,))
        row = cur.fetchone()
        if not row:
            sys.exit(f"device_key {args.device_key!r} not found")
        device_id = row[0]
        cur.execute(CONFUSION_SQL, (device_id,))
        rows = cur.fetchall()

    if not rows:
        sys.exit(f"no measurement_status rows for {args.device_key} (run status-engine first)")

    total = sum(n for *_, n in rows)
    compared = sum(n for _, intervals, n in rows if intervals != "NONE")
    agree = sum(n for algo, intervals, n in rows if intervals != "NONE" and _norm(algo) == intervals)

    print(f"device {args.device_key} (id {device_id}) — {total} classified measurements\n")
    print(f"{'status-engine':14} {'intervals':10} {'count':>10}")
    print("-" * 36)
    for algo, intervals, n in rows:
        print(f"{algo:14} {intervals:10} {n:>10}")

    if compared:
        print(f"\nagreement (IDLE counted as LOAD): {agree}/{compared} = {agree / compared:.1%}")
    uncovered = total - compared
    if uncovered:
        print(f"note: {uncovered} measurements fall in no interval "
              f"(intervals worker has not classified that time range)")


if __name__ == "__main__":
    main()
