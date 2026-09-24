-- Run once in Supabase SQL editor (zo-agency RFP app)
--
-- STORAGE BUCKET (separate from this SQL — do in Dashboard or run migrate script):
--   1. Supabase → Storage → New bucket
--   2. Name: rfp-pdfs  (must match SUPABASE_RFP_BUCKET in backend/.env)
--   3. Public: OFF (private — backend uses service role to read/write)
--   Or run: python scripts/migrate_pdfs_to_supabase_storage.py (creates bucket if possible)
--
-- PDF layout in bucket:  {rfp_id}/rfp.pdf
-- Postgres pdf_path column stores pointer:  supabase:manual-xxx/rfp.pdf

CREATE TABLE IF NOT EXISTS rfps (
  id TEXT PRIMARY KEY,
  external_id TEXT UNIQUE,
  title TEXT NOT NULL,
  client TEXT,
  source TEXT DEFAULT 'justwin',
  sector TEXT,
  location TEXT,
  due_date TEXT,
  received_date TEXT,
  stage TEXT DEFAULT 'intake',
  status TEXT DEFAULT 'new',
  priority TEXT DEFAULT 'medium',
  fit_score INTEGER,
  worth_score INTEGER,
  go_no_go TEXT,
  assigned_to TEXT,
  estimated_value INTEGER,
  page_limit INTEGER,
  last_activity TIMESTAMPTZ,
  last_activity_note TEXT,
  contract_role TEXT DEFAULT 'prime',
  description TEXT,
  justwin_tab TEXT,
  pdf_path TEXT,
  justwin_detail_url TEXT,
  synced_at TIMESTAMPTZ,
  go_no_go_analysis JSONB
);

CREATE INDEX IF NOT EXISTS idx_rfps_status ON rfps(status);
CREATE INDEX IF NOT EXISTS idx_rfps_due_date ON rfps(due_date);

CREATE TABLE IF NOT EXISTS proposal_research (
  rfp_id TEXT PRIMARY KEY REFERENCES rfps(id) ON DELETE CASCADE,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proposal_drafts (
  rfp_id TEXT PRIMARY KEY REFERENCES rfps(id) ON DELETE CASCADE,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proposal_draft_archives (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rfp_id TEXT NOT NULL REFERENCES rfps(id) ON DELETE CASCADE,
  archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reason TEXT NOT NULL,
  label TEXT,
  section_count INTEGER NOT NULL DEFAULT 0,
  filled_count INTEGER NOT NULL DEFAULT 0,
  payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proposal_draft_archives_rfp_archived
  ON proposal_draft_archives (rfp_id, archived_at DESC);

CREATE TABLE IF NOT EXISTS llm_call_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  rfp_id TEXT NOT NULL DEFAULT '',
  node_name TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  tier TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '',
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  -- Prompt-cache token counts. llm_call_log.record_llm_call writes these; when
  -- they are absent it falls back to a second, legacy insert, so every LLM call
  -- costs an extra failed round-trip until this exists in the live table.
  cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  tokens_estimated BOOLEAN NOT NULL DEFAULT FALSE,
  user_email TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_call_log_run_id ON llm_call_log (run_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_rfp_id ON llm_call_log (rfp_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_user_email ON llm_call_log (user_email);

CREATE TABLE IF NOT EXISTS sync_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  rfps_found INTEGER DEFAULT 0,
  pdfs_downloaded INTEGER DEFAULT 0,
  rfps_skipped INTEGER DEFAULT 0,
  rfps_created INTEGER DEFAULT 0,
  error TEXT
);

CREATE TABLE IF NOT EXISTS user_activity_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  workspace     TEXT NOT NULL CHECK (workspace IN ('rfp', 'financial', 'leads')),
  actor_email   TEXT NOT NULL DEFAULT 'system'
                CHECK (length(trim(actor_email)) > 0 AND length(actor_email) <= 320),
  action        TEXT NOT NULL CHECK (length(trim(action)) > 0 AND length(action) <= 200),
  outcome       TEXT NOT NULL DEFAULT 'recorded'
                CHECK (outcome IN ('recorded', 'started', 'completed', 'failed', 'cancelled')),
  entity_type   TEXT,
  entity_id     TEXT,
  entity_label  TEXT,
  summary       TEXT NOT NULL CHECK (length(trim(summary)) > 0 AND length(summary) <= 500),
  metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  request_id    TEXT,
  run_id        TEXT
);

CREATE INDEX IF NOT EXISTS idx_uae_workspace_time
  ON user_activity_events (workspace, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uae_actor_time
  ON user_activity_events (actor_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uae_workspace_actor_time
  ON user_activity_events (workspace, actor_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uae_entity
  ON user_activity_events (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_uae_action
  ON user_activity_events (workspace, action);
CREATE INDEX IF NOT EXISTS idx_uae_run_id
  ON user_activity_events (run_id)
  WHERE run_id IS NOT NULL;

ALTER TABLE user_activity_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE user_activity_events FROM PUBLIC;
REVOKE ALL ON TABLE user_activity_events FROM anon, authenticated;
GRANT ALL ON TABLE user_activity_events TO service_role;

CREATE TABLE IF NOT EXISTS user_analytics_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  workspace     TEXT NOT NULL CHECK (workspace IN ('rfp', 'financial', 'leads')),
  actor_email   TEXT NOT NULL DEFAULT 'system',
  session_id    TEXT NOT NULL DEFAULT '',
  event_type    TEXT NOT NULL
                CHECK (event_type IN ('page_view', 'tab_view', 'ui_click', 'heartbeat', 'funnel_step')),
  path          TEXT,
  tab           TEXT,
  view          TEXT,
  feature       TEXT,
  entity_type   TEXT,
  entity_id     TEXT,
  duration_ms   INTEGER NOT NULL DEFAULT 0,
  engaged       BOOLEAN NOT NULL DEFAULT FALSE,
  metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  client_ts     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_uane_workspace_time
  ON user_analytics_events (workspace, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uane_workspace_actor_time
  ON user_analytics_events (workspace, actor_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uane_workspace_type_time
  ON user_analytics_events (workspace, event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uane_workspace_feature_time
  ON user_analytics_events (workspace, feature, created_at DESC)
  WHERE feature IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_uane_session_time
  ON user_analytics_events (session_id, created_at DESC);

ALTER TABLE user_analytics_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE user_analytics_events FROM PUBLIC;
REVOKE ALL ON TABLE user_analytics_events FROM anon, authenticated;
GRANT ALL ON TABLE user_analytics_events TO service_role;
