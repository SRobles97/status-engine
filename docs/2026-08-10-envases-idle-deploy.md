# Deploying `03-piloto` (Envases Exportables idle state)

> **Superseded in part — read `2026-08-14-idle-threshold-recalibration.md` too.**
> This deploy shipped with `idle_threshold_low = 18.3`, which cut through the
> middle of the idle current band and emitted about half the machine's idle time
> as `LOAD`. The threshold is now 19.0, the golden test here is documented as a
> port-fidelity check rather than a calibration one, and history needs a
> backfill. The deploy steps below are otherwise still accurate.

This turns on the three-state (OFF / IDLE / LOAD) classifier for Envases
Exportables device `03` ("Exportable", id 66, company 14), rendered on its
piloto twin `03-piloto`, exactly as `F1-piloto` and the Tubexa/Revesol
pilotos already work today.

**This is not a deploy into a clean slate.** Five hidden piloto devices
already carry `card_source='algoritmo'`:

```
 id |  device_key   | company_id | is_hidden | card_source
 63 | F1-piloto     |          6 | t         | algoritmo
 68 | tbxo-piloto   |          3 | t         | algoritmo
 69 | tbxp-piloto   |          3 | t         | algoritmo
 70 | rev1-piloto   |          9 | t         | algoritmo
 71 | tubera-piloto |         13 | t         | algoritmo
```

As originally written, the Flutter card gated on `device.cardSource ==
'algoritmo'` alone (`machine_card_content.dart:50`), so **all five would
switch to the three-band bar the moment the app's web build ships** — not
just `03-piloto`. Their cards would lose the centred efficiency percentage
and show amber (IDLE) permanently at 0%, because none of their algorithms
currently emit IDLE. (See the "Closed on 2026-08-13" note below — this no
longer happens.)

This is a deliberate, accepted consequence, not an oversight: all five are
`is_hidden = true`, and `visible_devices_provider.dart` filters hidden
devices out for every non-superuser — so no client ever sees the changed
card. Only superusers, who already understand these are validation twins,
will see it. This matches the app's existing convention for piloto devices
and needs no code change to accept.

**Closed on 2026-08-13** by `docs/superpowers/specs/2026-08-13-card-shows-idle-design.md`:
the bands are now gated on `devices.card_shows_idle`, which defaults to `false`.
These five pilotos are never updated by the migration, so they keep the
efficiency-fill card they had before the IDLE work. Only `03-piloto` is created
with the flag on.

Four repositories are involved. This deploy unit is the `feat/idle-state`
branches below **plus** the `feat/card-shows-idle` branches of `backend` and
`smart_look_app` — the card-flag work is not optional or deferrable here. The
"Closed on 2026-08-13" note above, step 3's `card_shows_idle = true` write,
and step 6's `dashboard_repository` dependency are only true if those two
branches ship in this same deploy. Deploy the `feat/idle-state` builds alone
and the flag column exists but the backend never selects it and the app still
gates the three-band bar on `card_source` alone — the five hidden pilotos flip
anyway, which is the exact outcome the "Closed" note says no longer happens.

| repo | branch | what it carries |
|---|---|---|
| `status-engine` | `feat/idle-state` | classifier, IDLE intervals, idle facts, the `2026-08-10_*` migration, `03_piloto.py` |
| `specs/timescale-playground` | `feat/card-shows-idle` | the `2026-08-13_add_devices_card_shows_idle.sql` migration |
| `backend` | `feat/idle-state` + `feat/card-shows-idle` | facts mirror, card payload, energy, reports; CRUD schemas and dashboard payload field for `card_shows_idle` |
| `smart_look_app` | `feat/idle-state` + `feat/card-shows-idle` | the three-band bar; the card-flag gate and the "Fuente de tarjeta" selector |

Read the three items below before you touch anything: two deploy-ordering
hazards and one known gap this deploy ships with. Hazard #2 is genuinely
silent — wrong numbers, no error, nothing in the logs to point at it.
Hazard #1 is loud (a red row in `status_run_log`), but only if you know to
look there, so it is included for the same reason: skip it and you burn an
on-call shift on the wrong theory. #3 is not a deploy hazard at all — it is
a scope decision, documented so the next person does not have to rediscover
it.

## Read this first: two deploy-ordering hazards, and one known gap

### 1. The migration must land before the engine does

`refresh_daily_facts` is the only code path that turns IDLE intervals into
idle minutes in `device_daily_facts`. If the engine (built with
`emits_idle=True` for `03-piloto`) runs against a database that does not yet
have the idle columns — or if somehow an old engine build runs after the
migration — this fails **loudly and atomically, not silently**. The SQL in
`refresh_daily_facts` names `idle_minutes` (and the other three idle
columns) directly in its `INSERT`, so against a database missing them
Postgres raises `UndefinedColumn`. The engine runs one savepoint per
algorithm (`engine/runner.py`) specifically so a failure like this can't
poison the shared transaction or the algorithms that ran before it: the
savepoint rolls back, so **the entire tick for that algorithm — including
the `device_state_intervals` rows it would have written — is undone**,
nothing partial is left behind, and `status_run_log` gets a row for
`03-piloto` with `result = 'error'` and the `UndefinedColumn` message in
it. Every subsequent tick repeats the same failure until the migration
lands.

What you will actually see if you get the order wrong: no new intervals for
`03-piloto`, no new daily facts, and a run of `error` rows in
`status_run_log` naming the missing column. Nothing "vanishes" — there is
never anything to vanish, because the savepoint means it was never
committed in the first place. If `03-piloto` looks frozen after a deploy,
read `status_run_log` before looking anywhere else.

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

### 3. Known limitation: the report screens do not know about IDLE (documented, not fixed here)

Unlike hazards #1 and #2, this one is not a way to *break* the deploy — it
is a gap the deploy ships with, on purpose, deferred to a follow-up.

Two backend aggregations compute a device's "total" time from `LOAD` and
`OFF` only, with no `IDLE` term:

- `backend/app/database/intervals_repository.py:564`, inside
  `get_device_time_statistics` —
  `total_time_minutes = round(load_minutes + total_off_minutes, 2)`.
- `backend/app/database/intervals_repository.py:910`, inside
  `_aggregate_trends` —
  `grand_total = p_load_minutes_on + p_allowed_off_minutes_on + p_off_minutes_on`.

Neither has an `idle_minutes` term. On a `card_source='algoritmo'` device
these functions do not count IDLE minutes as "accounted for", so **Estadísticas
de tiempo** renders IDLE minutes as *"Programado sin datos"* — the gap
between recorded and scheduled time — even though the engine classified
them correctly and the card shows them correctly as the amber band. On the
fixture days used to build this feature, IDLE is 35–53% of samples, so this
is not a rounding-error-sized gap.

Separately: the one endpoint that *did* gain an `idle_hours` field,
`/api/reports/work-schedule`, is never called by the app — grepping `lib/`
for it returns zero hits. So fixing just that endpoint would not close the
gap the client actually sees; the two functions named above are where the
client-visible screens actually live.

**This deploy ships with the gap documented, not fixed.** Once `03-piloto`
is shown to the client (see the visibility note in step 3 below), its
**Estadísticas de tiempo** and trend reports will misattribute idle time
until someone teaches `get_device_time_statistics` and `_aggregate_trends`
about the `IDLE` state. Do that before the piloto is client-visible, not
after.

## Deploy steps, in order

### 1. Pre-flight: confirm the card's shift-info assumption still holds

Before doing anything, run this against the target database and confirm
none of the five rows has a null `total_schedule_minutes`:

```sql
SELECT d.device_key, s.shift_start, s.shift_end, s.total_schedule_minutes
FROM devices d LEFT JOIN device_current_status s ON s.device_id = d.id
WHERE d.device_key IN
  ('F1-piloto', 'tbxo-piloto', 'tbxp-piloto', 'rev1-piloto', 'tubera-piloto');
```

(Note the real key is `tbxo-piloto`, not `TBX_O-piloto` — a query using the
wrong key silently returns fewer rows and the "confirm none is null" check
passes vacuously on whichever piloto it missed.)

Already run against real data — recorded here so you have something to
compare against instead of running blind:

```
F1-piloto      510
tbxo-piloto    510
tbxp-piloto    510
rev1-piloto    510
tubera-piloto  530
```

All non-null, which confirms hazard #2's mechanism against real data. For
comparison, device 66 (`03`, the machine `03-piloto` will mirror) reads
`total_schedule_minutes = 540` — that is the value `03-piloto` should also
read once you copy its schedule rows in step 3.

All five existing pilotos read their shift info this way today, which is
the basis for hazard #2 above. If any row has a null `total_schedule_minutes`,
the design assumption behind this runbook does not hold on this database —
**stop and re-check the design doc before deploying anything else.**

### 2. Apply both migrations

Apply `sql/migrations/2026-08-10_device_daily_facts_idle_columns.sql`
(status-engine repo) against the target database. It adds four columns to
`device_daily_facts` (`idle_minutes`, `idle_minutes_on_schedule`,
`idle_minutes_off_schedule`, `idle_interval_count`), all additive with
`DEFAULT 0`. **This must happen before step 4 (the engine deploy).** See
hazard #1.

Also apply `sql/migrations/2026-08-13_add_devices_card_shows_idle.sql`
(specs/timescale-playground repo) in this same step. It adds
`devices.card_shows_idle` (`DEFAULT false`). **This must happen before step 3**:
step 3 writes `card_shows_idle = true` on the new `03-piloto` row, which cannot
succeed — or, worse, can be silently dropped into a workaround that leaves the
flag at its default `false` — if the column does not exist yet. It is also
required before the backend deploy (step 6), for the separate reason given
there.

### 3. Create the `03-piloto` device row and its schedule

Create the device:

- `device_key = '03-piloto'`
- `company_id = 14`
- `measurement_source = 'power'`
- `card_source = 'algoritmo'`
- `card_shows_idle = true` — **required for the three-band card.** Since
  `2026-08-13_add_devices_card_shows_idle.sql`, `card_source='algoritmo'` alone
  only selects the engine as the card's data source; the LOAD/IDLE/OFF split is
  opt-in per device and defaults to `false`. Without this the piloto renders the
  plain efficiency bar over correct data. In the app this pair is the
  "Algoritmo + Inactivo" option of *Fuente de tarjeta* (superuser only).
  Flipping the flag takes effect server-side instantly, no recompute needed —
  but a running app session holds its device list in memory, and the 10s poll
  only refreshes `status`, not the device list itself. The card picks up the
  change on pull-to-refresh, a full app refresh, or provider invalidation, not
  on the next poll tick.
- `timezone = 'America/Santiago'`
- `is_hidden = true` — `devices.is_hidden` defaults to `false`, but every
  existing piloto (`F1-piloto`, `tbxo-piloto`, `tbxp-piloto`, `rev1-piloto`,
  `tubera-piloto`) is hidden. Creating `03-piloto` visible puts a **second
  card for the same physical machine** on Envases Exportables' dashboard,
  showing lower worked minutes than the real `03` card beside it — this
  machine has not been validated against real Exportable data yet, so it
  stays a superuser-only twin until it has.

Then, in the same step, copy device `66`'s (`03`'s) `schedules` rows onto the
new piloto's device id. Do not defer this — see hazard #2. Skipping it does
not fail loudly; it produces a card that looks broken while everything else
is working.

**When you are ready to show the client:** once the bands have been
validated against real Exportable data for a representative stretch of
days, flip `03-piloto.is_hidden` to `false`. That is the only change needed
to reveal it — nothing else in this runbook is gated on visibility.

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

`sql/migrations/2026-08-13_add_devices_card_shows_idle.sql` must already be
applied by this point (step 2). The enriched dashboard query in
`dashboard_repository` selects `d.card_shows_idle` unconditionally, so an old
database errors on this deploy, not just renders a blank card — and this is
not limited to the dashboard: `devices_repository`'s `get_by_id`,
`get_by_company`, and the `create`/`update` `RETURNING` clauses all name the
same column, so the whole device-management screen (list, save, create) 500s
too if you're debugging a partial deploy and only think to check the
dashboard.

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
any device that never emits IDLE.

Note this rollback only un-does `03-piloto`. It does **not** touch the five
pilotos that already had `card_source='algoritmo'` before this deploy
(`F1-piloto`, `tbxo-piloto`, `tbxp-piloto`, `rev1-piloto`,
`tubera-piloto`) — those keep whatever behaviour the backend/app deploy
steps (6) gave them regardless of whether you roll `03-piloto` back. If the
three-band bar itself needs to come out for those five, that is an app-side
rollback (revert the app's `feat/idle-state` branch), not something this
engine-side rollback reaches.

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
migrations  →  status-engine  →  backend  →  app (web)
   (2)            (4)             (6)         (6)
```

Both migrations land together in step 2, before step 3 creates the
`03-piloto` row: `2026-08-10_*` (idle columns, engine-facing) and
`2026-08-13_add_devices_card_shows_idle.sql` (card flag). Step 3 writes
`card_shows_idle = true` directly, so the column must already exist by then.
The card-flag migration is also a hard backend dependency independent of that:
`dashboard_repository`'s enriched dashboard query selects `d.card_shows_idle`
unconditionally and errors without it — see step 6.

Device row + schedule (step 3) must exist before step 4 produces anything
useful, but the engine will not error without it — it will just fail to
resolve `03-piloto` and log it as unresolved in `status_run_log`. Do steps in
the numbered order above and this deploy has no surprises.
