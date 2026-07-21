-- Durable manuscript archives (survive draft row delete / Reset).
-- Run in Supabase SQL editor if not applied via migration tooling.

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
