-- Nightly AI-written insights, one row per source per day.
--
-- Deliberately not qb_panel_cache: that table is keyed (realm_id, year) and
-- overwritten every sync, so insights stored there would be erased each night.
-- Keeping a row per night is what makes week-over-week comparison possible.

CREATE TABLE IF NOT EXISTS ai_insights (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source       TEXT NOT NULL,          -- 'quickbooks' | 'teamwork' | 'combined'
  scope_key    TEXT NOT NULL,          -- realm_id for quickbooks, site_id for teamwork
  as_of        DATE NOT NULL,
  payload      JSONB NOT NULL,         -- { brief, notes } — what the model wrote
  evidence     JSONB NOT NULL,         -- signals + rows the model was given
  provider     TEXT,
  model        TEXT,
  status       TEXT NOT NULL,          -- 'ok' | 'failed'
  error        TEXT,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source, scope_key, as_of)
);

CREATE INDEX IF NOT EXISTS ai_insights_latest_idx
  ON ai_insights (source, scope_key, as_of DESC);
