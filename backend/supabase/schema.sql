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

CREATE TABLE IF NOT EXISTS sync_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  rfps_found INTEGER DEFAULT 0,
  pdfs_downloaded INTEGER DEFAULT 0,
  error TEXT
);
