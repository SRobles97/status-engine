-- PRE-VUELO del despliegue 2026-08-27 (paridad pilotos ↔ reportes).
-- Ejecutar ANTES de levantar el motor nuevo. Sólo lee y crea tablas de respaldo:
-- no modifica nada de lo que usan la app ni los reportes.
--
--   docker exec -i -e PGPASSWORD=<pass> timescale.timescaledb \
--     psql -U dbmanager -d centineldb -X -v ON_ERROR_STOP=1 < pre-deploy-2026-08-27.sql
--
-- Por qué hace falta: en la PRIMERA corrida del motor nuevo, `mirror_schedules`
-- BORRA todas las filas de `device_schedules` del piloto y reinserta las del
-- origen. Es recuperable por definición (es un espejo), pero el respaldo lo hace
-- trivial y deja constancia de cómo estaba.

\set ON_ERROR_STOP on

DROP TABLE IF EXISTS pilot_map;
CREATE TEMP TABLE pilot_map(pilot_key text, source_key text);
INSERT INTO pilot_map VALUES
  ('03-piloto',       '03'),
  ('tubera-piloto',   '02'),
  ('claser-piloto',   'c laser'),
  ('rev1-piloto',     'REV1'),
  ('thyunday-piloto', 'Revesol'),
  ('F1-piloto',       'F1'),
  ('tbxo-piloto',     'INF TBX'),
  ('tbxp-piloto',     'LS TBX');

-- ============================================================
-- A) RESPALDOS  (persistentes; borrarlos a mano cuando ya no hagan falta)
-- ============================================================

DROP TABLE IF EXISTS bak_20260827_device_schedules;
CREATE TABLE bak_20260827_device_schedules AS
SELECT ds.* FROM device_schedules ds
JOIN devices d ON d.id = ds.device_id
JOIN pilot_map m ON m.pilot_key = d.device_key;

DROP TABLE IF EXISTS bak_20260827_threshold;
CREATE TABLE bak_20260827_threshold AS
SELECT t.* FROM device_threshold_config t
JOIN devices d ON d.id = t.device_id
JOIN pilot_map m ON m.pilot_key = d.device_key;

-- Clasificación actual de TODO intervalo algo OFF: permite deshacer el backfill
-- de 'Paro permitido' fila por fila si hiciera falta.
DROP TABLE IF EXISTS bak_20260827_algo_off_class;
CREATE TABLE bak_20260827_algo_off_class AS
SELECT i.id, i.device_id, i.start_time, i.is_allowed, i.classification_id
FROM device_state_intervals i
WHERE i.source = 'algo' AND i.state = 'OFF';

SELECT 'device_schedules' AS respaldo, count(*) FROM bak_20260827_device_schedules
UNION ALL SELECT 'device_threshold_config', count(*) FROM bak_20260827_threshold
UNION ALL SELECT 'algo OFF classification_id', count(*) FROM bak_20260827_algo_off_class;

-- ============================================================
-- B) QUÉ VA A CAMBIAR
-- ============================================================

-- B1) Los ocho pilotos existen y están bien configurados.
--     card_source DEBE ser 'algoritmo': si no, los reportes leen source='power'
--     y el piloto sale vacío (nadie escribe 'power' para él).
--     Un pilot_id NULL = el spec quedará en "skipped: unresolved device".
SELECT m.pilot_key, p.id AS pilot_id, s.id AS source_id,
       p.is_active, p.is_hidden, p.card_source
FROM pilot_map m
LEFT JOIN devices p ON p.device_key = m.pilot_key
LEFT JOIN devices s ON s.device_key = m.source_key
ORDER BY m.pilot_key;

-- B2) A qué pilotos les va a REESCRIBIR el horario la primera corrida.
--     La firma replica la comparación de `mirror_schedules`: el juego ordenado
--     de (day_schedules, extra_hours, special_days, valid_from, valid_to,
--     version, shift_type).
WITH sig AS (
  SELECT d.device_key,
         jsonb_agg(jsonb_build_array(ds.day_schedules, ds.extra_hours,
                                     ds.special_days, ds.valid_from, ds.valid_to,
                                     ds.version, ds.shift_type)
                   ORDER BY ds.shift_type, ds.valid_from)
           FILTER (WHERE ds.id IS NOT NULL) AS s
  FROM devices d
  LEFT JOIN device_schedules ds ON ds.device_id = d.id
  GROUP BY d.device_key
)
SELECT m.pilot_key,
       (p.s IS DISTINCT FROM o.s)                    AS se_reescribe,
       jsonb_array_length(COALESCE(p.s, '[]'::jsonb)) AS filas_piloto_hoy,
       jsonb_array_length(COALESCE(o.s, '[]'::jsonb)) AS filas_del_origen
FROM pilot_map m
LEFT JOIN sig p ON p.device_key = m.pilot_key
LEFT JOIN sig o ON o.device_key = m.source_key
ORDER BY m.pilot_key;

-- B3) Umbrales de paro permitido: piloto vs origen.
--     Si difieren, la primera corrida cambia `is_allowed` del DÍA EN CURSO pero
--     NO de los días anteriores (el motor no los revisita). Ver la nota sobre
--     STATUS_WINDOW_DAYS en el runbook si la diferencia importa.
SELECT m.pilot_key, tp.duration_minutes AS umbral_piloto,
       ts.duration_minutes AS umbral_origen,
       (tp.duration_minutes IS DISTINCT FROM ts.duration_minutes) AS cambia
FROM pilot_map m
LEFT JOIN devices p ON p.device_key = m.pilot_key
LEFT JOIN devices s ON s.device_key = m.source_key
LEFT JOIN device_threshold_config tp ON tp.device_id = p.id
LEFT JOIN device_threshold_config ts ON ts.device_id = s.id
ORDER BY m.pilot_key;

-- B4) Cuánto va a etiquetar el backfill de 'Paro permitido'.
SELECT d.device_key, count(*) AS intervalos, round(sum(i.duration_seconds)/60.0, 1) AS minutos,
       min((i.start_time AT TIME ZONE 'America/Santiago')::date) AS desde,
       max((i.start_time AT TIME ZONE 'America/Santiago')::date) AS hasta
FROM device_state_intervals i
JOIN devices d ON d.id = i.device_id
WHERE i.source = 'algo' AND i.state = 'OFF'
  AND COALESCE(i.is_allowed, false) = true
  AND i.classification_id IS NULL
GROUP BY d.device_key ORDER BY d.device_key;

-- B5) Clasificaciones manuales vivas sobre intervalos algo del DÍA EN CURSO.
--     Con el motor viejo estas se están perdiendo cada 5 min; con el nuevo
--     sobreviven. Si aquí sale algo, el despliegue las salva.
SELECT d.device_key, count(*) AS clasificadas_hoy
FROM device_state_intervals i
JOIN devices d ON d.id = i.device_id
JOIN classifications c ON c.id = i.classification_id
WHERE i.source = 'algo' AND i.state = 'OFF'
  AND c.name NOT IN ('Sin asignar', 'Paro permitido')
  AND (i.start_time AT TIME ZONE 'America/Santiago')::date
      = (now() AT TIME ZONE 'America/Santiago')::date
GROUP BY d.device_key ORDER BY d.device_key;
