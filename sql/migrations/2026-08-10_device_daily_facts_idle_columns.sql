-- status-engine/sql/migrations/2026-08-10_device_daily_facts_idle_columns.sql
--
-- IDLE pasa a ser un estado propio en device_state_intervals (ver
-- docs/superpowers/specs/2026-08-10-envases-idle-state-design.md). Los hechos
-- diarios necesitan sus propias columnas: sin ellas los minutos IDLE quedarían
-- sumados dentro de total_minutes pero invisibles para la tarjeta.
--
-- Aditiva y reversible. DEBE aplicarse ANTES de desplegar el motor: el motor
-- nuevo escribe estas columnas y falla si no existen.
BEGIN;

ALTER TABLE device_daily_facts
  ADD COLUMN IF NOT EXISTS idle_minutes              real NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS idle_minutes_on_schedule  real NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS idle_minutes_off_schedule real NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS idle_interval_count       int  NOT NULL DEFAULT 0;

COMMIT;

-- Reversa:
-- ALTER TABLE device_daily_facts
--   DROP COLUMN idle_minutes,
--   DROP COLUMN idle_minutes_on_schedule,
--   DROP COLUMN idle_minutes_off_schedule,
--   DROP COLUMN idle_interval_count;
