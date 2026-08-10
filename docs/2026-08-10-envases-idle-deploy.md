# Deploying `03-piloto` (Envases Exportables idle state)

This turns on the three-state (OFF / IDLE / LOAD) classifier for Envases
Exportables device `03` ("Exportable", id 66, company 14), rendered on its
piloto twin `03-piloto`, exactly as `F1-piloto` and `TBX_O-piloto` already
work today. Everything this deploy touches has been sitting inert — no
algorithm currently sets `emits_idle=True` and no device has
`card_source='algoritmo'` — so until you finish the steps below, nothing
anywhere behaves any differently.

Three repositories are involved, each with its own unpushed `feat/idle-state`
branch:

| repo | branch | what it carries |
|---|---|---|
| `status-engine` | `feat/idle-state` | classifier, IDLE intervals, idle facts, the migration, `03_piloto.py` |
| `backend` | `feat/idle-state` | facts mirror, card payload, energy, reports |
| `smart_look_app` | `feat/idle-state` | the three-band bar |

Read the two warnings below before you touch anything. Both produce **silent**
wrong behaviour — no crash, no error in the logs, just numbers that are wrong
or missing — which is exactly the kind of thing that eats an on-call shift.

## Read this first: two ways to silently break this

### 1. The migration must land before the engine does

`refresh_daily_facts` is the only code path that turns IDLE intervals into
idle minutes in `device_daily_facts`. If the engine (built with
`emits_idle=True` for `03-piloto`) runs against a database that does not yet
have the idle columns — or if somehow an old engine build runs after the
migration — the IDLE minutes get written as `device_state_intervals` rows and
then **vanish** when facts are aggregated. There is no error. The numbers are
just absent from `device_daily_facts`. If you ever see a piloto with IDLE
intervals in the interval table but zero idle minutes in the daily facts,
this is why — check the migration first.

**Order that must be respected: migration → engine → everything else.**

### 2. The piloto device needs its own `schedules` rows, not just a device row

The engine classifies `03-piloto` off of `03`'s *live measurements*
(`source_device_key`), but the card reads `shift_start` and
`total_schedule_minutes` from `device_current_status` joined on the
**piloto's own device id** — not device 66's. If you create the `03-piloto`
device row and stop there, `total_schedule_minutes` is `0`. That number is
the denominator for all three bands (OFF / IDLE / LOAD percentages), so the
card renders **0% / 0% / 0%** while the classification underneath is
completely correct. This looks exactly like a broken feature, and it will
send you down the wrong path chasing the classifier instead of the missing
schedule. Copy device 66's `schedules` rows onto `03-piloto` in the same step
you create the device — do not treat it as a follow-up.

## Deploy steps, in order

### 1. Pre-flight: confirm the card's shift-info assumption still holds

Before doing anything, run this against the target database and confirm
neither row has a null `total_schedule_minutes`:

```sql
SELECT d.device_key, s.shift_start, s.shift_end, s.total_schedule_minutes
FROM devices d LEFT JOIN device_current_status s ON s.device_id = d.id
WHERE d.device_key IN ('F1-piloto', 'TBX_O-piloto');
```

Both existing pilotos read their shift info this way today, which is the
basis for hazard #2 above. If either row has a null `total_schedule_minutes`,
the design assumption behind this runbook does not hold on this database —
**stop and re-check the design doc before deploying anything else.**

### 2. Apply the migration

Apply `sql/migrations/2026-08-10_device_daily_facts_idle_columns.sql`
(status-engine repo) against the target database. It adds four columns to
`device_daily_facts` (`idle_minutes`, `idle_minutes_on_schedule`,
`idle_minutes_off_schedule`, `idle_interval_count`), all additive with
`DEFAULT 0`. **This must happen before step 4 (the engine deploy).** See
hazard #1.

### 3. Create the `03-piloto` device row and its schedule

Create the device:

- `device_key = '03-piloto'`
- `company_id = 14`
- `measurement_source = 'power'`
- `card_source = 'algoritmo'`
- `timezone = 'America/Santiago'`

Then, in the same step, copy device `66`'s (`03`'s) `schedules` rows onto the
new piloto's device id. Do not defer this — see hazard #2. Skipping it does
not fail loudly; it produces a card that looks broken while everything else
is working.

### 4. Deploy status-engine

```bash
docker-compose up --build
```

Compose v1 (hyphenated `docker-compose`, not `docker compose` — see the
existing VPS convention). `--build` is required: this worker bakes its code
into the image, so a plain `up` will keep running the old image and silently
skip `03_piloto.py` entirely.

### 5. Verify the engine is actually classifying three states

```sql
SELECT state, count(*)
FROM device_state_intervals
WHERE device_id = <03-piloto's device id> AND source = 'algo'
GROUP BY state;
```

Expect to see `OFF`, `IDLE`, and `LOAD` all present. Then:

```sql
SELECT * FROM status_run_log ORDER BY id DESC LIMIT 5;
```

Expect a recent row for `03-piloto` with `result = 'ok'`.

### 6. Deploy backend, then the app web build — in that order

Backend before app, not the reverse. The app reads `idle_minutes` and
`off_minutes` from the backend payload; against an **old** backend those keys
are simply absent, and the app already defaults both to `0.0`, so worst case
an app-first deploy shows an empty-looking (but not crashing) band on the
piloto card until backend catches up. Deploying backend first avoids even
that. Web needs an explicit redeploy — it does not pick up backend changes
automatically.

### 7. Rollback

Delete `algorithms/Envases Exportables/03_piloto.py` and rebuild the engine
(`docker-compose up --build`). `03-piloto` simply stops being classified and
stops updating — nothing else needs to be touched. Leave the migration in
place; its columns are additive and default to `0`, so they are harmless on
any device that never emits IDLE. Nothing else in this deploy needs
reverting: every other change (backend routing, the app's three-band bar) is
inert until some device actually has `card_source='algoritmo'`, and after
this rollback none does.

## What was not verified before this handoff

The build environment for this task could not run every gate the project
normally requires. Whoever deploys should know exactly what is unchecked,
rather than assume it was covered:

- **`ruff` and `mypy`** are not installed in either Python venv used here
  (status-engine or backend) — no lint or type-check was run on the Python
  changes.
- **`bandit`, `pip-audit`, `semgrep`** were not run against the backend
  changes — no dependency or static-security scan has happened yet.
- **`gitleaks`**/secret scanning was not run as a dedicated tool; only a
  manual review of the diff.
- **`dart format --set-exit-if-changed`** could not be run tree-wide: the
  local Dart SDK is a beta (`3.13.0-167.1`) against the app's pinned
  `^3.8.1`, and running it reformats 303 unrelated files. Only the touched
  files were checked for formatting by hand.

None of these are a reason to skip the deploy, but they are gates a CI run
or a reviewer should close before or shortly after, not gates this runbook
can claim as passed.

## Ordering summary

```
migration  →  status-engine  →  backend  →  app (web)
   (2)            (4)             (6)         (6)
```

Device row + schedule (step 3) must exist before step 4 produces anything
useful, but the engine will not error without it — it will just fail to
resolve `03-piloto` and log it as unresolved in `status_run_log`. Do steps in
the numbered order above and this deploy has no surprises.
