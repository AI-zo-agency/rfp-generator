-- Teamwork nightly mirror schema.

CREATE TABLE IF NOT EXISTS teamwork_projects (
  site_id TEXT NOT NULL,
  project_id BIGINT NOT NULL,
  name TEXT,
  status TEXT,
  health TEXT,
  company_id BIGINT,
  company_name TEXT,
  start_date DATE,
  due_date DATE,
  tasks_open INTEGER NOT NULL DEFAULT 0,
  tasks_completed INTEGER NOT NULL DEFAULT 0,
  tasks_overdue INTEGER NOT NULL DEFAULT 0,
  progress_pct INTEGER NOT NULL DEFAULT 0,
  updated_at_remote TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  PRIMARY KEY (site_id, project_id)
);

CREATE TABLE IF NOT EXISTS teamwork_tasks (
  site_id TEXT NOT NULL,
  task_id BIGINT NOT NULL,
  name TEXT,
  status TEXT,
  priority TEXT,
  project_id BIGINT,
  project_name TEXT,
  due_date DATE,
  assignee_names TEXT[] NOT NULL DEFAULT '{}',
  task_bucket TEXT NOT NULL,
  updated_at_remote TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  PRIMARY KEY (site_id, task_id)
);

CREATE TABLE IF NOT EXISTS teamwork_people (
  site_id TEXT NOT NULL,
  person_id BIGINT NOT NULL,
  name TEXT,
  email TEXT,
  title TEXT,
  company_name TEXT,
  updated_at_remote TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  PRIMARY KEY (site_id, person_id)
);

CREATE TABLE IF NOT EXISTS teamwork_time_entries (
  site_id TEXT NOT NULL,
  timelog_id BIGINT NOT NULL,
  project_id BIGINT,
  project_name TEXT,
  user_id BIGINT,
  user_name TEXT,
  minutes INTEGER NOT NULL DEFAULT 0,
  billable BOOLEAN NOT NULL DEFAULT false,
  time_logged TIMESTAMPTZ,
  updated_at_remote TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  PRIMARY KEY (site_id, timelog_id)
);

CREATE TABLE IF NOT EXISTS teamwork_milestones (
  site_id TEXT NOT NULL,
  milestone_id BIGINT NOT NULL,
  name TEXT,
  status TEXT,
  project_id BIGINT,
  project_name TEXT,
  due_date DATE,
  progress_pct INTEGER,
  updated_at_remote TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL,
  raw JSONB NOT NULL,
  PRIMARY KEY (site_id, milestone_id)
);

CREATE TABLE IF NOT EXISTS teamwork_panel_cache (
  site_id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  as_of DATE NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS teamwork_sync_state (
  site_id TEXT PRIMARY KEY,
  updated_after_cursor TIMESTAMPTZ,
  backfill_completed_at TIMESTAMPTZ,
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  last_started_at TIMESTAMPTZ,
  last_success_at TIMESTAMPTZ,
  last_error TEXT,
  last_mode TEXT
);

CREATE TABLE IF NOT EXISTS teamwork_sync_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  entities_upserted JSONB,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_teamwork_projects_status
  ON teamwork_projects (site_id, status);
CREATE INDEX IF NOT EXISTS idx_teamwork_projects_due_date
  ON teamwork_projects (site_id, due_date);
CREATE INDEX IF NOT EXISTS idx_teamwork_projects_updated_at_remote
  ON teamwork_projects (site_id, updated_at_remote);

CREATE INDEX IF NOT EXISTS idx_teamwork_tasks_bucket
  ON teamwork_tasks (site_id, task_bucket);
CREATE INDEX IF NOT EXISTS idx_teamwork_tasks_due_date
  ON teamwork_tasks (site_id, due_date);
CREATE INDEX IF NOT EXISTS idx_teamwork_tasks_updated_at_remote
  ON teamwork_tasks (site_id, updated_at_remote);

CREATE INDEX IF NOT EXISTS idx_teamwork_people_updated_at_remote
  ON teamwork_people (site_id, updated_at_remote);

CREATE INDEX IF NOT EXISTS idx_teamwork_time_entries_time_logged
  ON teamwork_time_entries (site_id, time_logged);
CREATE INDEX IF NOT EXISTS idx_teamwork_time_entries_updated_at_remote
  ON teamwork_time_entries (site_id, updated_at_remote);

CREATE INDEX IF NOT EXISTS idx_teamwork_milestones_status
  ON teamwork_milestones (site_id, status);
CREATE INDEX IF NOT EXISTS idx_teamwork_milestones_due_date
  ON teamwork_milestones (site_id, due_date);
CREATE INDEX IF NOT EXISTS idx_teamwork_milestones_updated_at_remote
  ON teamwork_milestones (site_id, updated_at_remote);

CREATE INDEX IF NOT EXISTS idx_teamwork_panel_cache_computed_at
  ON teamwork_panel_cache (computed_at);
CREATE INDEX IF NOT EXISTS idx_teamwork_sync_state_lease_expires_at
  ON teamwork_sync_state (lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_teamwork_sync_runs_started_at
  ON teamwork_sync_runs (site_id, started_at DESC);

ALTER TABLE teamwork_projects ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE teamwork_projects FROM anon, authenticated;
GRANT ALL ON TABLE teamwork_projects TO service_role;

ALTER TABLE teamwork_tasks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE teamwork_tasks FROM anon, authenticated;
GRANT ALL ON TABLE teamwork_tasks TO service_role;

ALTER TABLE teamwork_people ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE teamwork_people FROM anon, authenticated;
GRANT ALL ON TABLE teamwork_people TO service_role;

ALTER TABLE teamwork_time_entries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE teamwork_time_entries FROM anon, authenticated;
GRANT ALL ON TABLE teamwork_time_entries TO service_role;

ALTER TABLE teamwork_milestones ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE teamwork_milestones FROM anon, authenticated;
GRANT ALL ON TABLE teamwork_milestones TO service_role;

ALTER TABLE teamwork_panel_cache ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE teamwork_panel_cache FROM anon, authenticated;
GRANT ALL ON TABLE teamwork_panel_cache TO service_role;

ALTER TABLE teamwork_sync_state ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE teamwork_sync_state FROM anon, authenticated;
GRANT ALL ON TABLE teamwork_sync_state TO service_role;

ALTER TABLE teamwork_sync_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE teamwork_sync_runs FROM anon, authenticated;
GRANT ALL ON TABLE teamwork_sync_runs TO service_role;
