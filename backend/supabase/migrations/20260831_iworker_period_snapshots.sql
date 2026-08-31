CREATE TABLE IF NOT EXISTS iworker_period_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  spreadsheet_id TEXT NOT NULL,
  granularity TEXT NOT NULL CHECK (granularity IN ('week', 'month')),
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  contractor TEXT NOT NULL,
  hours NUMERIC NOT NULL DEFAULT 0,
  spend_usd NUMERIC NOT NULL DEFAULT 0,
  scope_risk_usd NUMERIC NOT NULL DEFAULT 0,
  entries_count INT NOT NULL DEFAULT 0,
  active_contractors INT NOT NULL DEFAULT 0,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (spreadsheet_id, granularity, period_start, contractor)
);

CREATE INDEX IF NOT EXISTS iworker_period_snapshots_lookup
  ON iworker_period_snapshots (spreadsheet_id, granularity, period_start DESC);

ALTER TABLE iworker_period_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE iworker_period_snapshots FROM anon, authenticated;
GRANT ALL ON TABLE iworker_period_snapshots TO service_role;
