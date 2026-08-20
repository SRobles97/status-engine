# Pilotos Revesol: Torno Hyunday y Cortadora Láser

Replica el patrón de piloto ya usado en Riñihue/Tubexa/Formac para dos máquinas
más de Revesol (empresa 9), y agrega al motor la única pieza que faltaba: un
piso de duración para las rachas de LOAD.

## Qué se agregó

**Motor — `min_load_minutes`.** Los notebooks nuevos de Revesol corren *dos*
pasadas de suavizado; el motor tenía una sola. La segunda borra las rachas de
LOAD demasiado cortas, reemplazándolas por el estado siguiente (el `bfill` del
notebook). Vive en `engine/smoothing.py::drop_short_loads` y se aplica en
`engine/runner.py::_classify`, siempre después de `smooth_statuses`:

```
clasificar → rellenar huecos cortos → borrar LOAD cortos → guard
```

El orden es parte del contrato. Invertirlo mediría rachas que el relleno
todavía no unió, y partiría en dos un ciclo real de trabajo.

El campo default es `0.0`, que desactiva la pasada. Los siete algoritmos ya
desplegados no cambian de comportamiento; eso es lo que verifica la suite
existente al seguir en verde.

**Dos specs nuevos** en `algorithms/Revesol/`:

| spec | piloto | origen | columna | umbral | huecos | piso LOAD |
|---|---|---|---|---|---|---|
| `THyunday_piloto.py` | `thyunday-piloto` | `Revesol` (id 72) | `total_current` | 21.5 | 3 min | 3 min |
| `CLaser_piloto.py` | `claser-piloto` | `c laser` (id 75) | `total_current` | 11 | 5 min | 3 min |

`REV1_piloto.py` **no se tocó**: el notebook `Disp Rev T_WIA` es idéntico en
algoritmo al `Disp Rev CM1` que ya estaba codificado (umbral 1100 sobre
`phase_a_active_power`, relleno de 5 min). Solo cambia el rango de fechas y un
gráfico en vez del export a Excel.

## De muestras a minutos

Los notebooks cuentan **muestras**; el motor mide **tiempo**. La conversión usa
la cadencia real de cada dispositivo, hoy 5 s en ambos:

- Cortadora Láser: 60 muestras → 5 min, 36 muestras → 3 min.
- Torno Hyunday: 36 muestras → 3 min en ambas pasadas.

**Ojo con el Torno Hyunday.** Su notebook fija la ventana en 2026-08-10, cuando
el dispositivo todavía reportaba cada ~29 s; con esa cadencia los mismos 36
habrían sido 18 min. Se eligió la cadencia actual (5 s → 3 min): un piso de 18
min borraría casi todos los ciclos reales de un torno. Si algún día el gráfico
del piloto no se parece al del notebook, este es el primer lugar donde mirar.

Codificar minutos y no muestras también absorbe la deriva de cadencia: cuando
el dispositivo cambió de 29 s a 5 s, el `36` del notebook pasó a significar otra
cosa sin que nadie lo tocara. Los minutos no se mueven.

## Orden de despliegue

1. **Primero la BD**: `sql/pilots/2026-08-20-revesol-pilots.sql` contra
   centineldb. Crea los dos dispositivos espejo (ocultos,
   `card_source='algoritmo'`) y les copia `device_threshold_config` y
   `device_schedules` desde su máquina origen. Es idempotente.
   Revisa que la consulta de pre-vuelo devuelva 0 filas: `resolve_device_id()`
   busca por `device_key` sin filtrar empresa, así que una clave repetida haría
   que el motor le escriba al dispositivo equivocado.
2. **Después el motor**, en `/srv/status-engine`:
   ```bash
   git pull
   docker-compose down
   docker-compose up -d --build
   ```
   El código va horneado en la imagen (`COPY . .`, sin volumen), así que hace
   falta `--build`, no solo reiniciar. El `down` previo evita el `KeyError:
   ContainerConfig` de compose v1 al recrear.

Si se despliega el motor antes que la BD, los dos specs nuevos quedan en
`skipped: unresolved device` hasta que existan las filas — molesto pero
inofensivo, y se cura solo.

## Verificación

```sql
SELECT device_key, result, error_detail, processed_count, created_at
FROM algo_run_log
WHERE device_key IN ('thyunday-piloto','claser-piloto')
ORDER BY created_at DESC LIMIT 10;
```

Se esperan `ok` con `processed_count > 0`. Un `empty window` significa que la
máquina origen no está reportando; se cura sola cuando vuelve.

Los pilotos son `is_hidden=true`: solo los superusuarios ven sus tarjetas.

## Qué se comprobó

- La suite completa (`pytest`), 146 casos en verde, incluidos 12 nuevos para
  `drop_short_loads` y el orden de las dos pasadas.
- **Fidelidad contra el notebook**: se reimplementó el código pandas de
  `Disp Revesol THyunday` tal cual y se corrió junto al motor sobre 15.584
  muestras reales del Torno Hyunday (2026-08-07 a 08-10, copia local).
  Coincidencia exacta, etiqueta por etiqueta, en todos los pisos de LOAD
  probados entre 1 y 20 min.
  Las únicas divergencias aparecen al convertir minutos a muestras con la
  cadencia *mediana* de un tramo que corrió a otra velocidad — es decir, en el
  arnés de prueba, y son justamente la deriva que la codificación en minutos
  existe para absorber.
- Corrida end-to-end del motor contra la copia local: el piloto del Torno
  Hyunday clasificó 525 muestras y emitió 4 intervalos LOAD / 4 OFF, donde la
  fuente por umbral producía 27 y 27 el mismo día. Esa compactación es
  exactamente el efecto buscado.
