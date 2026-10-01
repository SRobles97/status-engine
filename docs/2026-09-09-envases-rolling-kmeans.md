# `03-piloto` pasa al KMeans rodante (2026-09-09)

Sucede a `2026-08-10-envases-idle-deploy.md` y `2026-08-14-idle-threshold-recalibration.md`.
Aquellos desplegaron y después recalibraron la **escalera de umbrales**; esto la
retira y pone en su lugar el algoritmo que el cliente corre hoy.

## Qué cambió y por qué

El ingeniero del cliente actualizó
`algoritmos/Envases Exportables/Disp EnvExp Mold1.json` (Colab, `.json` pese al
nombre) el **2026-09-09**. Su clasificador ahora es:

1. `OFF` cuando `total_current < 2` (antes `< 5`);
2. un calentamiento de 120 muestras desde cada arranque;
3. después, un **KMeans k=2 reajustado en CADA muestra** sobre las 120 muestras
   previas y 8 columnas (`phase_a/b/c_current`, `phase_a/b/c_active_power`,
   `total_current`, `total_active_power`), con el orden de los clústeres fijado
   por el centroide de `total_current`;
4. la primera ventana completa se etiqueta hacia atrás de una vez;
5. un tramo que termina ANTES de juntar 120 muestras se clasifica hacia atrás
   contra un corte fijo de 19 A;
6. la celda de TRAMOS CORTOS (huecos de IDLE/OFF de menos de 5 muestras entre
   dos LOAD pasan a LOAD) queda igual.

El motor corría la escalera de umbrales del notebook ANTERIOR. Esto no es una
recalibración: es el cambio de algoritmo que `2026-08-14` dejó pendiente por
escrito — *"si la meseta sigue derivando, no muevas la constante una tercera
vez; portar el KMeans es el arreglo real."*

**La escalera estaba derivando otra vez.** Motor desplegado contra el notebook
nuevo, sobre datos reales del device 66 (copia local):

| día | concordancia por muestra | IDLE motor | IDLE notebook |
|---|---|---|---|
| 2026-08-05 | 95.2% | 34.6% | **39.3%** |
| 2026-08-06 | 97.6% | 50.1% | 49.6% |
| 2026-08-07 | 95.7% | 23.2% | **25.7%** |

Los 4.7 puntos del 08-05 están fuera de la tolerancia de ±3 que
`test_idle_calibration.py` exigía — sobre SU día (2026-08-13) el motor se
desviaba sólo 0.6, que es por lo que la suite estaba verde.

**El cambio de `5` a `2` en el corte OFF es, en esta máquina, un no-op.** La
banda 2–5 A tiene entre 0 y 3 muestras por día sobre ~15.000 en los seis días de
la copia local, y 1 de 17.540 en el fixture del 2026-08-13. Se porta por
fidelidad, no porque mueva un número.

### Una inconsistencia del notebook, sin resolver

La celda [6] declara `umbral = 3.12` ("definido desde el análisis en excel") y
la celda [10] rellena la columna con ese valor, pero **nada lo usa**: el corte
real es el `< 2` escrito a mano en la celda [15], y el comentario de la rama
`else` todavía dice `C>5`. Son tres números para el mismo concepto, dos de ellos
muertos. El motor toma el que decide (`2.0`). Vale la pena confirmarlo con el
ingeniero del cliente antes de que quede fosilizado acá.

## Qué se tocó en el motor

| archivo | qué |
|---|---|
| `engine/idle_rules.py` | `classify_segment_rolling_kmeans` + `resolve_by_threshold` |
| `engine/algorithms.py` | `RollingKMeansIdleAlgorithm`, `KMEANS_FEATURE_COLUMNS`, `StatusAlgorithm.extra_input_columns` |
| `engine/runner.py` | pide a la BD las columnas que declara el algoritmo, no sólo la del guard |
| `algorithms/Envases Exportables/03_piloto.py` | pasa a `RollingKMeansIdleAlgorithm` |
| `tools/make_rolling_kmeans_fixtures.py` | genera los dorados ejecutando las celdas del notebook |
| `tests/fixtures/kmeans_golden_03_2026-08-{05,07,10}.csv.gz` | nuevos, 8 columnas + etiquetas |
| `tests/test_rolling_kmeans.py` | 13 tests de la máquina de estados y del fetch |
| `tests/test_kmeans_golden.py` | fidelidad del port + el archivo desplegado atado a sus constantes |
| `tests/test_idle_calibration.py` | reducido a lo que su fixture de UNA columna todavía puede probar |

`IdleThresholdAlgorithm` **no se borra**: `test_idle_golden.py` y sus tres
fixtures siguen documentando la escalera, y es el camino de rollback.

### El fetch de ocho columnas era el modo de falla silencioso

`runner.run_once` sólo pedía `power_column` (más `guard_column`). Un algoritmo
de ocho columnas contra ese fetch recibe un frame de dos: o revienta, o —peor—
clasifica sobre una sola dimensión sin error y sin log. Ahora el algoritmo
declara `extra_input_columns` y el runner las suma. `repository.fetch_window` ya
las validaba contra `ALLOWED_POWER_COLUMNS`, así que la superficie SQL no
cambia.

## Verificación hecha antes de este handoff

- **Suite: 184 en verde** (164 previos + 21 nuevos entre
  `test_rolling_kmeans.py` (13) y `test_kmeans_golden.py` (8), menos uno porque
  el de calibración pasó de 2 tests a 1). Corrida DOS veces:
  - en el venv local (pandas 3.0.3 / numpy 2.5.0 / scikit-learn 1.9.0);
  - **dentro de la imagen Docker real** (`pandas 2.2.2 / numpy 1.26.4 /
    scikit-learn 1.5.1`, que es lo que fija `requirements.txt` y lo único que
    corre en el VPS).

  Esto último importa más que de costumbre: el venv de desarrollo está tres
  versiones mayores por delante de lo que se despliega, y el resultado de KMeans
  podría no ser estable entre ellas. Es idéntico — el test dorado exige
  **igualdad exacta muestra a muestra** contra el notebook y pasa en ambos.

  Como efecto colateral, el generador de fixtures **sólo corre bajo pandas 2.x**:
  con la copy-on-write de pandas 3, `data_with_clusters['umbral'].to_numpy()`
  devuelve un array de sólo lectura y la celda [15] muere con
  `ValueError: assignment destination is read-only` en su pasada hacia atrás. No
  es un bug del cliente (en Colab corre), pero regenerá los fixtures dentro de la
  imagen, no en el venv.

- **Fidelidad del port: exacta.** Sobre 40.952 muestras reales de tres días
  (2026-08-05 / 08-07 / 08-10), cero discrepancias contra las etiquetas
  producidas ejecutando las celdas [10, 15, 17] del notebook del cliente.

- **Corrida end-to-end contra la copia local**, no sólo tests con mocks: se
  aplicó la migración del 2026-08-10, se creó un `03-piloto` (id 74) espejando
  los horarios del 66 y se corrió `run_iteration` con `STATUS_WINDOW_DAYS=35`.
  Resultado `ok`, 51.925 muestras, 34 s, con intervalos y `device_daily_facts`
  escritos. Los minutos coinciden exactamente con el cálculo offline.

- **`ruff` / `mypy` no están instalados** en este entorno; no se corrió lint ni
  chequeo de tipos, igual que en los handoffs anteriores de este repo.

## Lo que el backfill va a cambiar

Minutos por estado, escalera desplegada vs. KMeans, mismos días y mismo armado
de intervalos:

| día | LOAD antes → después | IDLE antes → después | OFF |
|---|---|---|---|
| 2026-08-05 | 139.1 → **106.9** | 240.2 → **272.4** | 178.7 (igual) |
| 2026-08-06 | 111.4 → 114.2 | 308.5 → 305.7 | 141.1 (igual) |
| 2026-08-07 | 54.6 → **42.3** | 110.6 → **122.9** | 312.3 (igual) |
| 2026-08-10 | 43.2 → 38.6 | 104.0 → 108.7 | 43.7 (igual) |

El patrón es consistente: **OFF no se mueve** (era la mitad que ya estaba bien),
LOAD baja e IDLE sube en la misma cantidad. El día peor mueve 32 minutos, un 23%
del LOAD de esa jornada.

## Despliegue

### 1. Motor

No hay migración. Las cuatro columnas idle de `device_daily_facts` están desde
el 2026-08-10 y este cambio no agrega ninguna.

```bash
cd /srv/status-engine
git pull
docker-compose down
docker-compose up -d --build
```

`docker-compose` con guión — el VPS usa compose **v1**. `--build` es
obligatorio: el código va horneado en la imagen (`COPY . .`, sin volumen), así
que un `up` pelado sigue corriendo la escalera. El `down` previo evita el
`KeyError: 'ContainerConfig'` de compose 1.29.2 al recrear.

### 2. Verificar el tick

```sql
SELECT device_key, result, error_detail, processed_count, duration_ms, created_at
FROM status_run_log ORDER BY id DESC LIMIT 10;
```

`03-piloto` con `result='ok'` y `processed_count > 0`. Ojo: la tabla se llama
`status_run_log` — `2026-08-20-revesol-pilotos-deploy.md` la llama
`algo_run_log`, que no existe.

`duration_ms` es la métrica nueva a mirar: este algoritmo ajusta un KMeans por
muestra, así que el tick de `03-piloto` pasa de milisegundos a **decenas de
segundos**. Contra `RUN_INTERVAL_SECONDS=300` sobra margen; si alguna vez se
acerca, el lock advisory hace que los ticks se salteen, no que se pisen.

```sql
SELECT state, count(*), round(sum(extract(epoch from
         (coalesce(end_time, now()) - start_time)))/60.0, 1) AS minutes
FROM device_state_intervals
WHERE device_id = 74 AND source = 'algo'
  AND (start_time AT TIME ZONE 'America/Santiago')::date = CURRENT_DATE
GROUP BY state;
```

Los tres estados presentes, IDLE la banda más grande o la segunda.

### 3. Backfill desde el jueves 2026-09-03

Alcance decidido el 2026-09-09: se reescriben **sólo los últimos 6 días**
(2026-09-03 … hoy), no toda la historia desde la recalibración.

**`STATUS_WINDOW_DAYS = 6`.** `window_bounds` calcula `medianoche local de hoy −
window_days`, así que 6 deja el inicio de la ventana clavado en
`2026-09-03 00:00` de Santiago, con el jueves entero adentro. Uno más alcanza el
miércoles 09-02 y lo reescribe también; uno menos deja el jueves a medias. Si
esto se corre otro día, recalculalo — el número depende de la fecha:

```sql
SELECT (CURRENT_DATE - DATE '2026-09-03') AS days_back;   -- 6 el 2026-09-09
```

**Consecuencia que hay que aceptar a propósito:** del 2026-08-14 al 2026-09-02
`03-piloto` conserva el reparto de la escalera, así que su historial queda
partido en dos regímenes con el corte en el 09-03. Por el orden de magnitud de
la tabla de más arriba, esos días subestiman IDLE y sobrestiman LOAD en algo del
orden de 30 minutos diarios. No rompe nada — el motor no mira hacia atrás —,
pero cualquier comparación mes contra mes cruza esa costura. Ampliar la ventana
más adelante los arregla en el lugar, sin nada que deshacer.

```bash
# .env: STATUS_WINDOW_DAYS=6
docker-compose down && docker-compose up -d
docker-compose logs -f          # esperá "iteration done: N/N ok", después Ctrl-C
# .env: STATUS_WINDOW_DAYS=0
docker-compose down && docker-compose up -d
docker exec status_engine env | grep STATUS_WINDOW_DAYS   # confirmá que quedó en 0
```

Sin `--build` en ninguno de los dos — la imagen ya está al día, sólo cambia el
env.

Cuatro cosas que conviene saber antes:

- **Cuánto tarda ese tick.** 6 días son unas 65.000 muestras dentro de horario y
  el KMeans se reajusta en cada una. La medición local fue 51.925 muestras en
  34 s (~1.500/s), así que esperá **~45 s acá y del orden de 1–3 minutos en el
  VPS**. A diferencia de un backfill largo, éste entra cómodo dentro de
  `RUN_INTERVAL_SECONDS=300` y no debería saltearse ningún tick. Igual esperá el
  `iteration done`, no el reloj: si alguna vez se pasa de 300 s, el lock advisory
  simplemente saltea los ticks siguientes hasta que termine.

- **Ensanchar la ventana NO cambia las etiquetas de este algoritmo**, y eso es
  deliberado. `2026-08-14-idle-threshold-recalibration.md` advierte que un
  `KMeansAlgorithm` sería inseguro con una ventana ancha porque ajusta sobre
  todo el frame; **esto es otra cosa**: la ventana es fija en 120 muestras y los
  tramos se cortan en cada hueco mayor a `gap_seconds` (300 s), así que cada día
  se re-deriva idéntico. Está clavado con un test
  (`test_labels_do_not_depend_on_how_much_history_the_window_fetched`) y
  verificado contra los datos: el device 66 tiene exactamente 5 huecos mayores a
  300 s en la copia local — los 5 límites de día —, el más chico de 12.5 horas,
  contra un hueco intradía máximo de 32 s. El margen es enorme.

- **`STATUS_WINDOW_DAYS` es global**: reprocesa los 8 algoritmos. Los otros 7 son
  `ThresholdAlgorithm`, que clasifica cada muestra por su cuenta, así que son
  indiferentes al ancho.

- **Lo que alguien clasificó a mano sobrevive.** `_emit_algo_intervals` saca la
  foto ANTES del delete por día. El 'Paro permitido' que puso el motor se
  re-deriva, como siempre.

### 4. Rollback

`git revert` del commit y `docker-compose down && docker-compose up -d --build`.
`IdleThresholdAlgorithm` sigue en el motor con sus tests y sus fixtures, así que
volver es restaurar un archivo, no recuperar código borrado. Después re-corré el
backfill (paso 3) sobre la misma ventana para reescribir la historia al revés.
El esquema no cambia en ninguna dirección.

## Lo que este despliegue NO arregla

- **Ya no hay ninguna prueba de CALIBRACIÓN.** Mientras el motor corría la
  escalera, `test_idle_calibration.py` comparaba contra un oráculo
  independiente. Ahora el motor **es** el algoritmo del cliente: las tres
  pruebas doradas son de fidelidad del port — demuestran que el motor reproduce
  el notebook, no que el notebook acierte. Que acierte pasa a ser
  responsabilidad del ingeniero del cliente, que es exactamente el punto de
  espejar su algoritmo en vez de mantener una constante propia. Pero conviene
  saber que de este lado ya nadie lo verifica. Del fixture del 2026-08-13 sólo
  sobrevive el chequeo del corte OFF; para recuperar el día entero hay que
  exportarlo de producción con las ocho columnas (receta en el docstring de
  `tools/make_rolling_kmeans_fixtures.py`).

- **Los reportes ya saben de IDLE, pero confirmá que esté DESPLEGADO.** El
  hazard #3 de `2026-08-10-envases-idle-deploy.md` está cerrado en el código:
  `get_device_time_statistics` suma `idle_minutes_on_schedule` como
  `idle_time_minutes` y `_aggregate_trends` lo mete en `grand_total`
  (`backend/app/database/intervals_repository.py`, commit `93bad1c`, en
  `origin/main`). Lo que este runbook no puede verificar desde acá es si el VPS
  corre esa versión del backend. Si no la corre, el tiempo ocioso — que este
  cambio hace CRECER — se sigue dibujando como *"Programado sin datos"* en
  **Estadísticas de tiempo**. Comprobalo antes de mostrarle el piloto a nadie;
  el device 74 es `is_hidden = true` mientras tanto.

  (Sin relación con esto y sin arreglar: `grand_total` suma
  `p_allowed_off_minutes_on` **y** `p_off_minutes_on`, pero los dos productores
  de facts definen `allowed_off_*` como un subconjunto estricto de `off_*`. El
  denominador queda inflado y todos los porcentajes de Tendencias salen
  proporcionalmente bajos, en TODOS los dispositivos. Arreglarlo mueve números
  históricos de toda la flota, así que es una decisión aparte.)

- **Los fixtures dorados llegan hasta el 2026-08-10**, que es donde termina la
  copia local. Los días entre esa fecha y hoy nunca se compararon contra el
  notebook; el argumento de que no hace falta es que este algoritmo no tiene
  constantes que deriven.
