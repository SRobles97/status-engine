# Recalibrating `03-piloto`'s idle threshold (2026-08-14)

Follow-up to `2026-08-10-envases-idle-deploy.md`. That deploy shipped with a
miscalibrated constant; this is the fix, the evidence, and the backfill.

## What was wrong

`algorithms/Envases Exportables/03_piloto.py` set `idle_threshold_low = 18.3`.
`select_threshold()` picks the low rung whenever the rolling sigma is under 0.5
— about **41% of samples**, which is precisely the stable idle plateau. On
2026-08-13 the idle band sat at p5 18.087 / median 18.349 / p95 18.739 A, so
18.3 landed *inside* it and split it down the middle. Roughly half of all idle
time was emitted as `LOAD`.

`off_threshold = 5.0` was never implicated: OFF matched the client's labels to
the second. The entire defect was that one constant.

Production vs. the client's own algorithm, 2026-08-13, shift window
08:00:01–16:59:57 (16,187 samples, gap-weighted seconds):

| state | client's algorithm | engine as deployed | engine after this change |
|---|---|---|---|
| OFF | 33.3% | 33.3% | 33.3% |
| IDLE | **47.6%** | **21.9%** | **48.5%** |
| LOAD | **18.4%** | **44.9%** | **18.3%** |
| per-sample agreement | — | 73.3% | 98.5% |

In `device_daily_facts` terms for that day, `idle_minutes` moves 118.1 → ~262
and `load_minutes` 255.6 → ~99.

**This was never right — it is not a recent drift.** The idle plateau's median
sat above 18.3 on 14 of the 16 days with data (2026-07-20…08-14, range
17.85–18.84 A). One of the two days below the line is 2026-08-10, which is a
golden-fixture day. That is why it validated cleanly when it was built.

## Why the test suite could not see it

`tools/make_golden_fixtures.py` produces `expected_state` by exec'ing cells
[10,11,14,16] of `Desarrollo/Desarrollo Disp EnvExp Mold1.ipynb` — the same
notebook the engine copied 18.3 / 19.0 from. Oracle and implementation share
the constant, so `test_idle_golden.py` agreed 97.9–99.7% while production was
off by 25 points of IDLE. It is structurally incapable of catching a
misplaced cut.

That test is still useful, but only as a **port-fidelity** check: it proves the
engine reproduces the notebook's state machine *given the notebook's
constants*. It now passes those constants explicitly instead of inheriting them
from the dataclass defaults, so its meaning can't drift, and it is documented
as not being a calibration check.

Calibration is now covered by `tests/test_idle_calibration.py`, which compares
against an **independent** oracle: the labels from the client's newer notebook,
`algoritmos/Envases Exportables/Disp EnvExp Mold1.json` (Colab, 2026-08-07 —
`.json` despite the name), which replaced the threshold ladder with a **rolling
KMeans k=2 over a 120-sample window** on 8 columns. It shares no constant with
the engine. The fixture is built by `tools/make_kmeans_fixture.py`, which
validates its decoding against the production row counts before writing.

That test loads the algorithm through the engine's own `load_algorithm_specs`,
so it breaks if anyone recalibrates `03_piloto.py` — the file that was wrong.

### Two traps in that Excel, both handled in the tool

1. A Spanish-locale import read `.` as a thousands separator on 3-decimal
   values: `18.389` is stored as the integer `18389`. Values that didn't form a
   valid 3-digit group (`0.16`, `114.06`) survived as text. Rule: string cell →
   `float()`, numeric cell → `/1000`. Labels were computed in Python before
   export, so they are unaffected and the reported percentages are valid.
2. Its `hora` column is **UTC**, not America/Santiago.

## What changed

- `algorithms/Envases Exportables/03_piloto.py` — `idle_threshold_low`
  18.3 → 19.0, retiring the low rung.
- `tests/test_idle_calibration.py` — new, the independent-oracle gate.
- `tests/fixtures/kmeans_03_2026-08-13.csv` — new fixture.
- `tools/make_kmeans_fixture.py` — new, generates it.
- `tests/test_idle_golden.py` — constants pinned explicitly, renamed and
  redocumented as a port-fidelity test.

The `IdleThresholdAlgorithm` dataclass defaults are **deliberately left at
18.3 / 19.0**. Nothing in production reads them any more — `03_piloto.py` is
the only construction outside tests, and it now passes both explicitly. They
are a trap for the next machine, though: a per-machine calibration has no
business being a class default. Worth removing when a second idle device
appears, which is when the right value stops being guessable.

## Deploy

```bash
docker-compose down
docker-compose up -d --build
```

`--build` is required — the engine bakes its code into the image, so a plain
`up` keeps the old constant. Compose v1, hyphenated.

**Do the `down` first.** Going straight to `up -d --build` hits the compose
v1.29.2 `KeyError: 'ContainerConfig'` bug on recreate (the image builds fine;
only the container swap fails). `down` then `up` sidesteps it entirely and is
cleaner than the `docker rm -f <container>` workaround used elsewhere in these
runbooks. This service declares no volumes, so removing the container costs
nothing.

Verify:

```sql
SELECT state, count(*), round(sum(extract(epoch from
         (coalesce(end_time, now()) - start_time)))/60.0, 1) AS minutes
FROM device_state_intervals
WHERE device_id = 74 AND source = 'algo'
  AND (start_time AT TIME ZONE 'America/Santiago')::date = CURRENT_DATE
GROUP BY state;
```

IDLE should now be the largest or second-largest band on a normal production
day, not a sliver.

## Backfilling the corrected history

Every `device_state_intervals` and `device_daily_facts` row for `03-piloto`
since the original deploy carries the wrong split. The engine rewrites whole
days, so widening its window fixes them in place.

Find how far back to go:

```sql
SELECT min((start_time AT TIME ZONE 'America/Santiago')::date) AS first_day
FROM device_state_intervals WHERE device_id = 74 AND source = 'algo';
```

Set `STATUS_WINDOW_DAYS` to **exactly** `days_back` — `window_bounds` computes
`today's local midnight − window_days`, so `days_back` lands the window start on
the first day that has data. Going one higher reaches back to a day with no
facts and *creates* rows there, which looks like the backfill overshot.

Restart, wait for one tick, confirm, and **put it back to `0`**:

```bash
# .env: STATUS_WINDOW_DAYS=<days_back>
docker-compose down && docker-compose up -d
docker-compose logs -f          # wait for "iteration done: N/N ok", then Ctrl-C
# .env: STATUS_WINDOW_DAYS=0
docker-compose down && docker-compose up -d
docker exec status_engine env | grep STATUS_WINDOW_DAYS   # confirm it took
```

No `--build` on either — the image is already current and only the env changes.

### Outcome of the 2026-08-14 run (recorded as a reference)

`STATUS_WINDOW_DAYS=4` covered 2026-08-10…08-14 (953 interval rows, ~85k
samples); one tick, well under a minute, `7/7 ok`. Verified `device_daily_facts`
for `03-piloto` on 2026-08-13 against the prediction from the offline replay:

| field | before | predicted | actual |
|---|---|---|---|
| `total_minutes` | 553.32 | 553.32 | 553.3 |
| `load_minutes` | 255.58 | ~100.8 | 100.75 |
| `idle_minutes` | 118.08 | ~272.9 | 272.9 |
| `off_minutes` | 179.65 | 179.65 | 179.65 |
| counts L/I/O | 173/175/12 | 177/181/12 | 177/181/12 |

Idle as a share of active time came out consistent across every backfilled day
— 71.8% / 71.4% / 73.0% / 68.5% (partial) — against 72.0% in the client's
Excel. `2026-08-12` has no row because device 66 reported no measurements that
day at all (it is likewise absent from `power_measurements`), not because the
backfill missed it.

Three things to know before doing this:

- **`STATUS_WINDOW_DAYS` is global.** It reprocesses every algorithm, not just
  `03-piloto`. That is safe here because all six others are `ThresholdAlgorithm`,
  which classifies each sample independently of the window — the same samples
  produce the same labels regardless of how many days are fetched. It would
  **not** be safe if a `KMeansAlgorithm` were ever deployed: it fits over the
  whole fetched frame, so a wider window would give different cluster centres
  and silently rewrite that device's history with different labels. Check
  `algorithms/` before widening the window again.
- **Leaving it wide is not harmless.** Every tick would re-fetch and
  re-classify the full range; `rolling_sigma` is a per-sample Python loop, so
  cost grows with the window and a long one can outrun `RUN_INTERVAL_SECONDS`.
  The advisory lock prevents overlap, but ticks would simply be skipped.
- **Reports still omit IDLE.** Hazard #3 of the original runbook is unchanged:
  `get_device_time_statistics` and `_aggregate_trends` have no `idle_minutes`
  term, so corrected idle time will render as "Programado sin datos" in
  *Estadísticas de tiempo*. Backfilling makes the numbers right in the database
  and on the card; it does not fix those two screens. With idle now correctly
  ~48% of the day rather than ~22%, that gap gets substantially more visible.

## Residual risk

19.0 clears every plateau observed across the 16 days — but only by about
0.34 A against the worst of them (18.66 A on 2026-08-04), and the IDLE/LOAD
separation on this machine is under 5%. This is a better bet than 18.3, not a
safe one.

If the plateau drifts again, **do not move the constant a third time.** The
client's engineer already reached this conclusion and acted on it: the
2026-08-07 notebook's rolling KMeans re-centres on each 120-sample window, so
it tracks the plateau instead of guessing where it will sit. Porting it is the
real fix — a new algorithm class taking 8 input columns, with per-window
fitting cost on every tick, and its own validation story. It was deferred here
only to keep the hotfix small.
