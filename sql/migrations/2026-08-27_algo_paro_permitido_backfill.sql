-- Backfill: 'Paro permitido' en los intervalos OFF del motor (source='algo').
--
-- El motor marcaba `is_allowed = true` pero dejaba `classification_id` en NULL,
-- mientras el worker umbral asigna las dos cosas al cerrar el intervalo
-- (`backfill_is_allowed_for_interval`). Como 'Uso de tiempos' y 'Tendencias'
-- resuelven el paro autorizado por NOMBRE en el cliente, esos minutos salían en
-- rojo ("Apagado") en vez de verde ("Trabajo"), y en 'Clasificación de tiempos
-- no utilizados' aparecían como "Sin asignar" — sin posibilidad de corregirlos,
-- porque la pantalla de asignación filtra por `is_allowed`.
--
-- El motor ya etiqueta las filas NUEVAS (engine/runner.py::apply_classifications).
-- Esto cierra el histórico. Correr UNA vez, después de desplegar el motor.
-- Es idempotente: sólo toca filas que siguen en NULL.
--
--   docker exec -i <contenedor> psql -U dbmanager -d centineldb \
--     -f 2026-08-27_algo_paro_permitido_backfill.sql

BEGIN;

-- 1) Asegura que exista 'Paro permitido' en cada empresa con intervalos algo
--    pendientes. Firma idéntica a la del worker umbral y a la del motor: mismo
--    nombre, mismo color, is_system. Una fila distinta pintaría dos tajadas
--    para el mismo concepto.
INSERT INTO classifications (company_id, name, description, status, color, is_work, is_system)
SELECT DISTINCT d.company_id, 'Paro permitido', '', 'active', '#4CAF50', false, true
FROM device_state_intervals i
JOIN devices d ON d.id = i.device_id
WHERE i.source = 'algo'
  AND i.state = 'OFF'
  AND COALESCE(i.is_allowed, false) = true
  AND i.classification_id IS NULL
  AND d.company_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM classifications c
    WHERE c.company_id = d.company_id AND c.name = 'Paro permitido');

-- 2) Etiqueta sólo lo que nadie tocó (classification_id IS NULL). Un
--    'Sin asignar' explícito es una decisión de un usuario y no se pisa, igual
--    que hace el motor al reconstruir el día.
CREATE TEMP TABLE afectados ON COMMIT DROP AS
WITH tagged AS (
  UPDATE device_state_intervals i
  SET classification_id = c.id, updated_at = now()
  FROM devices d
  JOIN classifications c ON c.company_id = d.company_id AND c.name = 'Paro permitido'
  WHERE d.id = i.device_id
    AND i.source = 'algo'
    AND i.state = 'OFF'
    AND COALESCE(i.is_allowed, false) = true
    AND i.classification_id IS NULL
  RETURNING i.device_id, i.start_time, COALESCE(d.timezone, 'America/Santiago') AS tz
)
SELECT DISTINCT device_id, (start_time AT TIME ZONE tz)::date AS day
FROM tagged;

SELECT count(*) AS dias_afectados FROM afectados;

-- 3) Recomputa device_daily_classification_facts de esos device-día.
--    Sólo cambió el reparto por clasificación, no los minutos totales, así que
--    device_daily_facts no se toca.
DELETE FROM device_daily_classification_facts f
USING afectados a
WHERE f.device_id = a.device_id AND f.day = a.day AND f.source = 'algo';

INSERT INTO device_daily_classification_facts (
  device_id, day, source, classification_id, state,
  minutes, minutes_on_schedule, minutes_off_schedule, interval_count, computed_at)
SELECT
  i.device_id, a.day, 'algo',
  COALESCE(i.classification_id, u.id)::bigint, 'OFF',
  SUM(i.duration_seconds / 60.0)::real,
  SUM(i.on_schedule_seconds / 60.0)::real,
  SUM((i.duration_seconds - i.on_schedule_seconds) / 60.0)::real,
  COUNT(*)::int, now()
FROM afectados a
JOIN devices d ON d.id = a.device_id
JOIN device_state_intervals i
  ON i.device_id = a.device_id
 AND i.source = 'algo'
 AND i.state = 'OFF'
 AND i.end_time IS NOT NULL
 AND i.duration_seconds IS NOT NULL
 AND i.duration_seconds >= 0
 AND (i.start_time AT TIME ZONE COALESCE(d.timezone, 'America/Santiago'))::date = a.day
JOIN classifications u ON u.company_id = d.company_id AND u.name = 'Sin asignar'
GROUP BY i.device_id, a.day, COALESCE(i.classification_id, u.id);

COMMIT;

-- Verificación: debe devolver 0 filas.
SELECT d.device_key, count(*) AS todavia_sin_clasificar
FROM device_state_intervals i
JOIN devices d ON d.id = i.device_id
WHERE i.source = 'algo' AND i.state = 'OFF'
  AND COALESCE(i.is_allowed, false) = true
  AND i.classification_id IS NULL
GROUP BY 1;
