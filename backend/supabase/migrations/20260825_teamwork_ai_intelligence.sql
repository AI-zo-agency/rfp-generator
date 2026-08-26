CREATE TABLE IF NOT EXISTS teamwork_capacity_snapshots (
  site_id TEXT NOT NULL,
  as_of DATE NOT NULL,
  person_id TEXT NOT NULL,
  person_name TEXT NOT NULL,
  logged_minutes INTEGER NOT NULL DEFAULT 0,
  billable_minutes INTEGER NOT NULL DEFAULT 0,
  capacity_minutes INTEGER NOT NULL DEFAULT 2400,
  utilization_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
  overdue_tasks INTEGER NOT NULL DEFAULT 0,
  due_soon_tasks INTEGER NOT NULL DEFAULT 0,
  active_projects INTEGER NOT NULL DEFAULT 0,
  budget_exposed_projects INTEGER NOT NULL DEFAULT 0,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (site_id, as_of, person_id)
);

CREATE INDEX IF NOT EXISTS teamwork_capacity_snapshots_history_idx
  ON teamwork_capacity_snapshots (site_id, person_id, as_of DESC);

ALTER TABLE teamwork_capacity_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE teamwork_capacity_snapshots FROM anon, authenticated;
GRANT ALL ON TABLE teamwork_capacity_snapshots TO service_role;
