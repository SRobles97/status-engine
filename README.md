# status-engine

Alternative, non-interfering device-status classifier. Runs per-device
threshold / k-means algorithms over `power_measurements` and writes:

- `measurement_status` (per-measurement OFF/IDLE/LOAD)
- `device_algo_status` (latest status per device)
- `status_run_log` (execution log)

It never modifies `power_measurements` or the existing intervals worker tables.

## Setup
1. `cp .env.example .env` and set `DB_DSN`.
2. Apply the schema: `psql "$DB_DSN" -f sql/schema.sql`.
3. Run tests: `python -m pytest`.
4. Run locally: `python main.py`. Or with Docker: `docker-compose up --build`.

## Adding a device
Create `algorithms/<Company>/<Device>.py` exposing an `ALGORITHM` whose
`device_key` matches `devices.device_key` (resolution is by `device_key`, which is
globally unique; the `<Company>` folder and the `company` field are organizational only).
Unresolved or failing devices are logged in `status_run_log` and skipped.
After creating or editing an algorithm file, the Docker image must be rebuilt because code is baked in at build time — e.g. `docker-compose up --build`.
