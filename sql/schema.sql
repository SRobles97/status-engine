-- Dynamic Device Status Processing — sidecar tables (no existing table is altered).
CREATE TABLE IF NOT EXISTS measurement_status (
  device_id   bigint      NOT NULL,
  time        timestamptz NOT NULL,
  status      text        NOT NULL,
  algorithm   text        NOT NULL,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (device_id, time)
);
CREATE INDEX IF NOT EXISTS idx_measurement_status_device_time
  ON measurement_status(device_id, time DESC);

CREATE TABLE IF NOT EXISTS device_algo_status (
  device_id             bigint      PRIMARY KEY,
  status                text        NOT NULL,
  algorithm             text        NOT NULL,
  last_measurement_time timestamptz,
  measurement_count     integer,
  computed_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS status_run_log (
  id              bigserial   PRIMARY KEY,
  run_at          timestamptz NOT NULL DEFAULT now(),
  company         text,
  device_key      text,
  device_id       bigint,
  algorithm       text,
  processed_count integer,
  updated_count   integer,
  result          text,
  error_detail    text,
  duration_ms     integer
);
