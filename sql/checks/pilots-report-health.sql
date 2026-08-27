-- Chequeo de salud de los pilotos frente a los tres reportes.
-- Ejecutar contra centineldb (prod o la copia local en :5433).
--   psql postgresql://dbmanager:localdev@localhost:5433/centineldb -f pilots-report-health.sql
--
-- Un piloto sano necesita, en su PROPIO device_id:
--   * device_threshold_config  -> is_allowed (el motor lo lee del piloto)
--   * device_schedules         -> horas programadas de 'Uso de tiempos'
-- y su horario debe COINCIDIR con el del dispositivo origen, porque el motor
-- recorta los intervalos OFF con el horario del ORIGEN (engine/runner.py:
-- fetch_device_schedules(read_id)) mientras el reporte cuenta lo programado con
-- el del PILOTO. Si divergen, el reporte muestra un hueco fantasma.

\set ON_ERROR_STOP on

-- Mapa piloto -> origen, tal como lo declaran los specs de algorithms/.
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

-- 1) Existencia y configuración de cada piloto.
--    card_source DEBE ser 'algoritmo': si no, todos los reportes leen
--    source='power' y el piloto sale vacío (nadie escribe 'power' para él).
SELECT m.pilot_key,
       p.id                                        AS pilot_id,
       p.is_active, p.is_hidden, p.card_source,
       to_jsonb(p)->>'card_shows_idle'              AS card_shows_idle,
       s.id                                        AS source_id,
       (ptc.device_id IS NOT NULL)                 AS tiene_umbral,
       ptc.duration_minutes                        AS umbral_piloto,
       stc.duration_minutes                        AS umbral_origen,
       (SELECT count(*) FROM device_schedules x WHERE x.device_id = p.id) AS horarios_piloto,
       (SELECT count(*) FROM device_schedules x WHERE x.device_id = s.id) AS horarios_origen
FROM pilot_map m
LEFT JOIN devices p  ON p.device_key = m.pilot_key
LEFT JOIN devices s  ON s.device_key = m.source_key
LEFT JOIN device_threshold_config ptc ON ptc.device_id = p.id
LEFT JOIN device_threshold_config stc ON stc.device_id = s.id
ORDER BY m.pilot_key;

-- 2) Deriva de horario piloto vs origen (incluye special_days).
--    Cualquier fila que salga aqui es un desajuste: el motor recorta los OFF con
--    el horario del ORIGEN y 'Uso de tiempos' cuenta lo programado con el del
--    PILOTO, asi que la diferencia aparece como 'Programado sin datos' o como un
--    paro que el motor nunca emitio. Se espera 0 filas.
WITH sch AS (
  SELECT m.pilot_key,
         COALESCE(ps.shift_type, ss.shift_type) AS shift_type,
         ps.day_schedules AS p_days, ss.day_schedules AS s_days,
         ps.special_days  AS p_spec, ss.special_days  AS s_spec,
         ps.valid_from    AS p_from, ss.valid_from    AS s_from,
         ps.valid_to      AS p_to,   ss.valid_to      AS s_to
  FROM pilot_map m
  JOIN devices p ON p.device_key = m.pilot_key
  JOIN devices s ON s.device_key = m.source_key
  LEFT JOIN device_schedules ps ON ps.device_id = p.id
  LEFT JOIN device_schedules ss ON ss.device_id = s.id
                               AND COALESCE(ss.shift_type,'day') = COALESCE(ps.shift_type,'day')
)
SELECT pilot_key, shift_type,
       (p_days IS NULL)                     AS falta_en_piloto,
       (s_days IS NULL)                     AS falta_en_origen,
       (p_days IS DISTINCT FROM s_days)     AS difiere_day_schedules,
       (p_spec IS DISTINCT FROM s_spec)     AS difiere_special_days,
       (p_from IS DISTINCT FROM s_from)     AS difiere_valid_from,
       (p_to   IS DISTINCT FROM s_to)       AS difiere_valid_to
FROM sch
WHERE p_days IS NULL OR s_days IS NULL
   OR p_days IS DISTINCT FROM s_days
   OR p_spec IS DISTINCT FROM s_spec
ORDER BY pilot_key, shift_type;

-- 3) 'Paro permitido' que el motor NUNCA asigna.
--    Todo intervalo algo con is_allowed=true debería llevar la clasificación
--    'Paro permitido' (así lo hace el worker umbral). El motor lo deja en NULL,
--    y el reporte lo cuenta como 'Sin asignar'.
SELECT d.device_key,
       count(*)                                     AS off_permitidos,
       count(*) FILTER (WHERE i.classification_id IS NULL) AS sin_clasificar,
       round(sum(i.duration_seconds)/60.0, 1)       AS minutos
FROM device_state_intervals i
JOIN devices d ON d.id = i.device_id
WHERE i.source = 'algo'
  AND i.state = 'OFF'
  AND COALESCE(i.is_allowed, false) = true
  AND i.start_time >= now() - interval '30 days'
GROUP BY d.device_key
ORDER BY d.device_key;

-- 4) Clasificaciones manuales que el próximo tick del motor va a borrar.
--    El motor hace DELETE+INSERT del día en curso (STATUS_WINDOW_DAYS=0), y el
--    INSERT no lleva classification_id. Todo lo que salga aquí desaparece
--    dentro de RUN_INTERVAL_SECONDS.
SELECT d.device_key, i.id, i.start_time, i.end_time, c.name AS clasificacion
FROM device_state_intervals i
JOIN devices d ON d.id = i.device_id
JOIN classifications c ON c.id = i.classification_id
WHERE i.source = 'algo'
  AND i.state = 'OFF'
  AND c.name NOT IN ('Sin asignar')
  AND (i.start_time AT TIME ZONE 'America/Santiago')::date
      = (now() AT TIME ZONE 'America/Santiago')::date
ORDER BY d.device_key, i.start_time;

-- 5) Salud del motor por piloto (últimas corridas).
SELECT device_key, result, error_detail, processed_count, max(created_at) AS ultima
FROM status_run_log
WHERE created_at >= now() - interval '2 hours'
GROUP BY device_key, result, error_detail, processed_count
ORDER BY device_key, ultima DESC;
