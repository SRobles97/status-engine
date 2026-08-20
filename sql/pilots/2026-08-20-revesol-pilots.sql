-- Pilotos Revesol (empresa 9): Torno Hyunday (72) y Cortadora Láser (75).
--
-- Crea el dispositivo espejo oculto de cada máquina y le copia la configuración
-- que el BACKEND lee del propio piloto: device_threshold_config (minutos
-- permitidos de OFF) y device_schedules (horas programadas de los reportes).
-- El MOTOR, en cambio, lee horario y timezone del dispositivo ORIGEN vía
-- source_device_key, así que no depende de estas filas.
--
-- Idempotente: se puede correr dos veces sin duplicar nada.
-- Ejecutar ANTES de desplegar status-engine; si no, los specs quedan en
-- "skipped: unresolved device" hasta que existan las filas.

-- PRE-VUELO: resolve_device_id() busca SOLO por device_key, sin filtrar empresa
-- (engine/repository.py:16), pero la tabla apenas lo exige único POR EMPRESA.
-- Si alguna de estas claves aparece dos veces, el motor podría escribirle al
-- dispositivo equivocado. Debe devolver 0 filas: si no, hay que renombrar antes
-- de seguir.
SELECT device_key, count(*) AS repetido
FROM devices
WHERE device_key IN ('Revesol', 'c laser', 'thyunday-piloto', 'claser-piloto')
GROUP BY device_key HAVING count(*) > 1;

BEGIN;

-- is_active queda explícito: resolve_device_id() exige is_active = true, así que
-- un piloto inactivo se descubre como "unresolved device" sin más aviso.
INSERT INTO devices (company_id, device_key, display_name, measurement_source,
                     timezone, is_active, is_hidden, card_source)
VALUES
  (9, 'thyunday-piloto', 'Torno Hyunday piloto',   'power', 'America/Santiago', true, true, 'algoritmo'),
  (9, 'claser-piloto',   'Cortadora Láser piloto', 'power', 'America/Santiago', true, true, 'algoritmo')
ON CONFLICT (company_id, device_key) DO NOTHING;

-- Cada INSERT lleva su propia lista piloto → origen; sin tabla temporal, para
-- poder pegar el bloque tal cual en una sesión psql.
INSERT INTO device_threshold_config (device_id, duration_minutes)
SELECT p.id, c.duration_minutes
FROM (VALUES ('thyunday-piloto','Revesol'), ('claser-piloto','c laser')) AS pp(pilot_key, source_key)
JOIN devices p ON p.device_key = pp.pilot_key
JOIN devices s ON s.device_key = pp.source_key
JOIN device_threshold_config c ON c.device_id = s.id
ON CONFLICT (device_id) DO NOTHING;

-- Copia el horario del origen COMPLETO, incluidos special_days: el piloto 70
-- se creó sin ellos y sus horas programadas quedaron distintas a las de su
-- máquina origen.
INSERT INTO device_schedules (device_id, day_schedules, extra_hours, special_days,
                              valid_from, valid_to, version, source, shift_type)
SELECT p.id, sch.day_schedules, sch.extra_hours, sch.special_days,
       sch.valid_from, sch.valid_to, sch.version, 'pilot_copy', sch.shift_type
FROM (VALUES ('thyunday-piloto','Revesol'), ('claser-piloto','c laser')) AS pp(pilot_key, source_key)
JOIN devices p ON p.device_key = pp.pilot_key
JOIN devices s ON s.device_key = pp.source_key
JOIN device_schedules sch ON sch.device_id = s.id
WHERE NOT EXISTS (
  SELECT 1 FROM device_schedules e
  WHERE e.device_id = p.id AND e.shift_type = sch.shift_type);

COMMIT;

-- Verificación.
SELECT d.id, d.device_key, d.display_name, d.is_hidden, d.card_source,
       tc.duration_minutes,
       (SELECT count(*) FROM device_schedules x WHERE x.device_id = d.id) AS horarios
FROM devices d
LEFT JOIN device_threshold_config tc ON tc.device_id = d.id
WHERE d.device_key IN ('thyunday-piloto', 'claser-piloto')
ORDER BY d.id;
-- Esperado: 2 filas, is_hidden=t, card_source=algoritmo, duration_minutes y
-- horarios copiados del origen (Torno Hyunday=5 min; Cortadora Láser, el suyo).
