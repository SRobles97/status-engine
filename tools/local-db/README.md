# Copy the VPS database locally (for testing status-engine)

Goal: get a faithful copy of the VPS `centineldb` (TimescaleDB) onto your machine so
status-engine can classify **real** measurements without touching production.

Approach: logical `pg_dump` (custom format) from the VPS container → restore into a
local TimescaleDB container, wrapped in TimescaleDB's `pre_restore`/`post_restore`.

The VPS DB credentials are in the workers' `.env` (user `dbmanager`, db `centineldb`).
Set the password in your shell once and never paste it into a committed file:

```bash
export PGPASS='<dbmanager password from the workers .env>'
```

## 1. (Once) confirm the DB name and TimescaleDB version on the VPS

```bash
ssh root@<vps-host>
docker exec -e PGPASSWORD="$PGPASS" timescale.timescaledb \
  psql -U dbmanager -d centineldb -tAc \
  "select current_database(), (select extversion from pg_extension where extname='timescaledb');"
exit
```

Note the timescaledb version. The restore requires the local image's TimescaleDB to
MATCH the VPS version (post_restore aborts on "catalog version mismatch"). The VPS is
on **2.21.0**, so `docker-compose.yml` is pinned to `timescale/timescaledb:2.21.0-pg15`.
If your VPS reports a different version, change that tag to `<version>-pg15`.

## 2. Dump the VPS DB straight to a local file (no large file left on the VPS)

`pg_dump` runs *inside* the VPS container (so its version always matches the server),
and the custom-format stream is piped over SSH to a local file:

```bash
ssh root@<vps-host> \
  "docker exec -e PGPASSWORD='$PGPASS' timescale.timescaledb \
     pg_dump -U dbmanager -d centineldb -Fc --no-owner --no-privileges" \
  > centineldb.dump
```

`centineldb.dump` now sits in your current directory. (It includes the existing
workers' tables too — harmless, and useful for comparing classifications.)

Tip: if the dump is large and you only want recent data to test on, dump a subset
instead, e.g. add `-T 'device_state_intervals'` to skip the intervals table, or take
a time-bounded copy of `power_measurements` separately. Full copy is simplest.

## 3. Start the local container and restore

From this directory:

```bash
docker compose up -d          # or: docker-compose up -d
./restore.sh ../../../centineldb.dump   # path to the dump you created in step 2
```

`restore.sh` drops/creates `centineldb`, creates the extension, runs
`timescaledb_pre_restore()` → `pg_restore` → `timescaledb_post_restore()`, then prints
device and power-measurement row counts so you can confirm the copy looks right.

## 4. Point status-engine at the local copy

```bash
export DB_DSN='postgresql://postgres:local@localhost:5433/centineldb'

# apply the status-engine sidecar tables (does NOT touch existing tables)
psql "$DB_DSN" -f ../../sql/schema.sql        # or via docker exec if psql isn't installed

# run one pass (or `python main.py` for the loop)
cd ../.. && python -c "from engine.config import Settings; import main, os; \
  os.environ['DB_DSN']='$DB_DSN'; print(main.run_iteration(Settings.from_env(os.environ)))"
```

Then inspect results:

```bash
psql "$DB_DSN" -c "select status, count(*) from measurement_status group by status;"
psql "$DB_DSN" -c "select * from device_algo_status;"
psql "$DB_DSN" -c "select device_key, algorithm, processed_count, updated_count, result, error_detail from status_run_log order by run_at desc limit 20;"
```

## Teardown

```bash
docker compose down -v        # removes the local container and its volume
```

---

### Security note
Check that the database port is not published to `0.0.0.0` (`docker ps`) — if it is,
Postgres is reachable from outside the host and anyone with the DB password can connect
directly. Bind it to `127.0.0.1` (like the API containers) or firewall the port.
Unrelated to this copy, but worth checking.
