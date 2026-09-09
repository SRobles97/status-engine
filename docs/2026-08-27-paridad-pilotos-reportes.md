# Paridad de los pilotos con los reportes

Los ocho pilotos construían bien sus intervalos OFF, pero tres cosas se perdían
entre el motor y los reportes. Esto las cierra. Toca `status-engine` y
`smart_look_app`; el backend no cambia.

## 1. El motor ya no borra las clasificaciones del día

`_emit_algo_intervals` reconstruye el día entero en cada corrida (DELETE +
INSERT). El INSERT no llevaba `classification_id`, así que **todo lo que un
usuario clasificara hoy desaparecía en la siguiente iteración**, ~5 minutos
después. Con `STATUS_WINDOW_DAYS=0` el daño era el día en curso; un backfill con
`STATUS_WINDOW_DAYS=N` se llevaba N días por delante, en silencio.

Ahora, por cada día que se reconstruye:

```
fetch_algo_classifications_for_day  →  DELETE  →  apply_classifications  →  INSERT
```

La foto se toma **antes** del delete y se vuelve a aplicar sobre las filas
nuevas, emparejando por `start_time` (el `id` es nuevo en cada reconstrucción;
los OFF de un mismo dispositivo+source no se solapan, así que `start_time` los
identifica sin ambigüedad). Un paro que cambia de inicio entre corridas es otro
paro y no arrastra su clasificación — a propósito.

## 2. Los pilotos ya reciben 'Paro permitido'

El motor marcaba `is_allowed = true` pero dejaba `classification_id` en NULL,
mientras el worker umbral asigna las dos cosas al cerrar el intervalo
(`backfill_is_allowed_for_interval`). Como 'Uso de tiempos' y 'Tendencias'
resuelven el paro autorizado **por nombre** en el cliente — no por el
`allowed_off_minutes` que manda la API — esos minutos salían en rojo ("Apagado")
en vez de verde ("Trabajo").

Peor: en 'Clasificación de tiempos no utilizados' aparecían como "Sin asignar",
y la pantalla de asignación **los oculta** porque filtra por `is_allowed`. Eran
minutos contados como paro sin clasificar que nadie podía clasificar.

`apply_classifications` los etiqueta ahora, con la misma firma de clasificación
que crea el worker umbral (mismo nombre, `#4CAF50`, `is_system`).

**El auto-tag es del motor, no del usuario.** Si el umbral baja y el paro deja de
ser permitido, la etiqueta se cae con la regla que la puso — es la pasada REVERT
de `reapply_allowed_threshold_for_device`, que acá hay que replicar porque el
motor reconstruye el día por su cuenta y el `reapply` del backend sólo corre
cuando alguien edita el umbral. Sin eso quedaría un intervalo pintado como paro
autorizado en el reporte mientras `allowed_off_minutes` — que sale de
`is_allowed` — dice que no lo es. Cualquier OTRA clasificación no nula, incluido
un 'Sin asignar' explícito, es una decisión de alguien y se respeta.

**El histórico necesita un backfill**, una sola vez, después de desplegar:
`sql/migrations/2026-08-27_algo_paro_permitido_backfill.sql`. Es idempotente.
Contra la copia local (7 días, corte 2026-08-10) tocó 488 intervalos en 47
device-día y dejó 0 pendientes.

## 3. La configuración del piloto se sincroniza sola

Un piloto se clasifica con las mediciones de otro equipo, pero **los reportes
leen su configuración del piloto mismo**: 'Uso de tiempos' calcula las horas
programadas desde `device_schedules` del piloto, y `is_allowed` sale de su
`device_threshold_config`. El motor, en cambio, recorta los OFF con el horario
del **origen**. Las dos copias se crearon a mano una vez y nunca se volvieron a
sincronizar.

Medido en la copia local: **ningún piloto tenía los `special_days` de su
máquina** (F1: 2026-05-01, 05-21, 06-29; REV1: cuatro; Tubexa: tres; `02`: dos).
El motor blanquea esos días desde el calendario del origen y el reporte seguía
contando un turno completo desde la copia del piloto — un día entero de
"Programado sin datos" por cada feriado. Y `F1-piloto` arrancaba en 2026-07-02
contra el 2026-02-26 de `F1`, así que cualquier reporte de junio mostraba
minutos OFF contra 0 horas programadas.

`run_once` ahora llama a `mirror_schedules` / `mirror_threshold` en cada corrida
cuando `read_id != write_id`. Compara primero y sólo reescribe si difiere, así
que en régimen es un no-op barato. Las filas espejadas quedan con
`source = 'pilot_mirror'`; nadie filtra por esa columna, sólo se escribe, así que
sirve de marca de propiedad.

**Consecuencia deliberada:** el motor pasa a ser el dueño del horario y del
umbral de los pilotos. Editar el turno de un piloto en la app deja de tener
efecto — se revierte en la siguiente corrida. Es lo que se quiere de un espejo
oculto: el horario que manda es el de la máquina real.

## 4. IDLE aparece en los reportes (app)

El backend manda `idle_time_minutes` / `idle_minutes` desde el piloto de Envases,
pero ningún reporte los leía. Como el backend **sí** los incluye en
`total_time_minutes`, esos minutos engordaban el denominador sin aparecer en
ninguna tajada: el tiempo desaparecía y el resto de los porcentajes se inflaba.
Sólo afecta a `03-piloto`, el único spec con `emits_idle`.

- **Uso de tiempos**: tajada y segmento propios, 'Sin producir (encendida)'.
- **Tendencias**: serie propia `IDLE`, tercera en la pila (sigue siendo tiempo
  con la máquina encendida, así que va pegada a LOAD y al paro autorizado).
  `PeriodData.fromJson` ya recalculaba su propio total; ahora incluye el IDLE.
  La serie se agrega **sólo si hay tiempo IDLE**, a diferencia de LOAD y
  ALLOWED_OFF que van siempre: las series salen de la unión de los `className`
  de todos los períodos, así que una entrada en 0 le habría puesto a toda la
  flota una leyenda vacía por un único dispositivo. Los períodos sin IDLE dentro
  de un rango que sí lo tiene se resuelven solos — `_SegmentMode.segment` ya
  devuelve un cero cuando no encuentra la clave.
- **Vista simplificada (3 colores)**: el IDLE es el único balde que la
  simplificación NO absorbe, en los dos reportes. Un dispositivo que distingue
  IDLE de LOAD lo hace porque esa diferencia es el dato; plegarla en 'Apagado'
  borra justamente lo que el piloto existe para mostrar — la tarjeta del
  dashboard ya lo trata aparte por lo mismo. Los dispositivos sin IDLE siguen
  viendo exactamente tres baldes: aportan 0 y la banda no se dibuja.
  La banda se omite del todo (leyenda incluida) cuando NINGÚN dispositivo del
  gráfico tiene tiempo IDLE — `buildBarSegments(includeIdle:)` en 'Uso de
  tiempos', `_SegmentMode.keysFor` en 'Tendencias' —, para no dejarle a toda una
  flota una entrada vacía por un único dispositivo.

**El color es el MISMO ámbar que 'Sin funcionar autorizado'**
(`TimeStatisticsPalette.idle = authorized`). Para quien lee el reporte son la
misma categoría: la máquina no está produciendo y no es un paro que reclamar.
Idéntico no es lo mismo que parecido — dos ámbares distintos a ΔE 3 serían
ilegibles (un error), dos bandas del mismo ámbar se leen como una sola categoría
(una decisión). Hoy casi nunca coinciden: sólo `03-piloto` emite IDLE y sus
paros cortos ya no salen como 'Paro permitido'. Cuando coincidan van pegadas en
la pila y se leen como un bloque, con la leyenda y el tooltip separando las
cifras.

## Orden de despliegue

1. **Motor** (`/srv/status-engine`): `git pull && docker-compose down &&
   docker-compose up -d --build`. El código va horneado en la imagen, así que
   hace falta `--build`; el `down` previo evita el `KeyError: ContainerConfig`
   de compose v1.
2. **Backfill**, una vez, después de que el motor esté arriba:
   `sql/migrations/2026-08-27_algo_paro_permitido_backfill.sql`.
3. **App**: redeploy web.

El orden importa poco salvo por el backfill, que debe ir después del motor: si va
antes, la corrida siguiente reconstruye el día en curso y el motor ya etiqueta
solo — pero el histórico quedaría a medias si el motor todavía no tiene el
cambio.

## Qué se comprobó

- Suite del motor: **164 en verde** (146 previos + 18 nuevos en
  `tests/test_pilot_parity.py`).
- Suite de la app: **577 en verde**, `flutter analyze` limpio.
- **Corrida end-to-end contra la copia local** (`F1-piloto` y `tbxo-piloto`,
  `STATUS_WINDOW_DAYS=17`):
  - horarios espejados — `F1-piloto` pasó de 1 versión sin feriados a 2 con
    feriados; `tbxo-piloto` de 1 a 3. Las filas del origen quedaron intactas
    (`source='mobile_app'`).

    **No verificar por `device_schedules.source = 'pilot_mirror'`.** La
    comparación de `mirror_schedules` mira sólo el CONTENIDO (las siete columnas
    de `_SCHEDULE_COLUMNS`), no la columna `source`, así que un piloto que ya
    estaba sincronizado no se reescribe y conserva su marca vieja para siempre.
    En el despliegue de prod eso hizo parecer que tres pilotos no habían
    espejado cuando estaban perfectos. Lo correcto es comparar contenido piloto
    vs origen — la consulta B2 de `sql/checks/pre-deploy-2026-08-27.sql`, que
    debe dar `en_sync = t` en los ocho.
  - 'Paro permitido' asignado donde antes había NULL.
  - se clasificó a mano un intervalo, se volvió a correr el motor y la
    clasificación **sobrevivió** a la reconstrucción (fila nueva, mismo
    `start_time`), con los `device_daily_classification_facts` repartidos en los
    tres baldes correctos.
  - backfill idempotente: segunda corrida, 0 filas afectadas.
  - **el espejo no churnea**: tres corridas seguidas dejaron los cinco
    `device_schedules.id` y sus `updated_at` intactos. Importa porque el motor
    corre cada 5 minutos.
  - **el espejo converge**: se agregó un feriado al horario del ORIGEN y el
    piloto lo tenía en la corrida siguiente.
  - **REVERT end-to-end**: se bajó el umbral del origen de 10 a 1 min; el espejo
    lo propagó y las cinco etiquetas 'Paro permitido' obsoletas se cayeron junto
    con `is_allowed`. Al restaurar el umbral volvieron, y la clasificación manual
    del mismo día siguió intacta.
  - **la clave del emparejamiento aguanta**: se clasificó a mano un intervalo
    cuyo `start_time` nace de una muestra (`13:48:54.699`, sub-segundo, o sea un
    `pandas.Timestamp` y no un borde de turno redondo) y sobrevivió. Es el caso
    donde un desajuste de hash entre `Timestamp` y `datetime` habría hecho fallar
    la conservación EN SILENCIO; hay un test unitario que lo fija.

## Suelto, no arreglado

- `device_daily_facts` de días viejos puede estar desfasado respecto a sus
  intervalos (visto en `F1`/`F1-piloto` el 2026-07-07: 237.28 min almacenados vs
  236.15 derivados). Es deriva de una versión anterior del motor; se cura sola en
  cualquier día que el motor reconstruya.
- `_aggregate_trends` suma `allowed_off` dos veces en su `grand_total` (es un
  subconjunto de `off`). No se nota en la app porque el cliente recalcula su
  propio total, pero la API cruda está mal.
- `docs/2026-08-20-revesol-pilotos-deploy.md` verifica contra `algo_run_log`; la
  tabla es `status_run_log`.
