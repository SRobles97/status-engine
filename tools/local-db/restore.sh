#!/usr/bin/env bash
# Restore a pg_dump (custom format) of the VPS centineldb into the local
# TimescaleDB container, using the required pre_restore/post_restore wrap.
#
# Usage: ./restore.sh <dump-file>
# Env (optional): CONTAINER=ts_local  DB_NAME=centineldb
set -euo pipefail

DUMP="${1:?usage: ./restore.sh <dump-file>}"
CONTAINER="${CONTAINER:-ts_local}"
DB="${DB_NAME:-centineldb}"

[ -f "$DUMP" ] || { echo "dump file not found: $DUMP" >&2; exit 1; }

psqlc() { docker exec -i "$CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 "$@"; }

# Wait for the REAL server (TCP up). During first-boot init the entrypoint runs a
# temporary socket-only server and then restarts; checking TCP (-h 127.0.0.1)
# avoids connecting to that transient server and getting killed mid-command.
echo -n ">> waiting for $CONTAINER to be ready"
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" pg_isready -h 127.0.0.1 -U postgres -q 2>/dev/null; then
    echo " ok"; break
  fi
  echo -n "."; sleep 1
done

echo ">> (re)creating database $DB"
psqlc -c "DROP DATABASE IF EXISTS $DB WITH (FORCE);"
psqlc -c "CREATE DATABASE $DB;"

echo ">> creating timescaledb extension + pre_restore"
psqlc -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
psqlc -d "$DB" -c "SELECT timescaledb_pre_restore();"

echo ">> restoring (benign NOTICEs are expected)"
# pg_restore can exit non-zero on harmless object-already-exists notices; the
# post_restore step below is what matters, so don't abort the script here.
docker exec -i "$CONTAINER" pg_restore -U postgres -d "$DB" \
  --no-owner --no-privileges < "$DUMP" || echo "   (pg_restore returned non-zero; continuing to post_restore)"

echo ">> post_restore + analyze"
psqlc -d "$DB" -c "SELECT timescaledb_post_restore();"
psqlc -d "$DB" -c "ANALYZE;"

echo ">> sanity check"
psqlc -d "$DB" -c "SELECT count(*) AS devices FROM devices;" || true
psqlc -d "$DB" -c "SELECT count(*) AS power_rows FROM power_measurements;" || true

echo ">> done. DSN: postgresql://postgres:local@localhost:5433/$DB"
