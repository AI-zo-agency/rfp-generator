-- =============================================================================
-- user_activity_events — append-only audit log for all three workspaces
-- =============================================================================
-- Safe to re-run in the Supabase SQL editor (idempotent).
--
-- Access model (same as financial_llm_calls / qb_* mirrors):
--   • Backend uses SUPABASE_SERVICE_ROLE_KEY only
--   • RLS on; anon + authenticated have no table privileges
--
-- Workspaces: rfp | financial | leads
-- One row = one durable product action (not pageviews / keystrokes).
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS user_activity_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Which Activity tab owns this row
  workspace     TEXT NOT NULL,
  -- Who (email from auth header / session; use 'system' for cron/jobs)
  actor_email   TEXT NOT NULL DEFAULT 'system',
  -- Stable verb, e.g. proposal.phase_started / qb.sync_refreshed
  action        TEXT NOT NULL,
  -- Lifecycle of the action when known
  outcome       TEXT NOT NULL DEFAULT 'recorded',

  -- Polymorphic target (RFP uuid, invoice id, lead id, …)
  entity_type   TEXT,
  entity_id     TEXT,
  entity_label  TEXT,

  -- Admin-readable one-liner (UI may show as-is)
  summary       TEXT NOT NULL,

  -- Small extras only — never PDFs / manuscript blobs
  metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,

  request_id    TEXT,
  run_id        TEXT
);

-- ---------------------------------------------------------------------------
-- Columns for older partial applies of an earlier draft of this migration
-- ---------------------------------------------------------------------------
ALTER TABLE user_activity_events
  ADD COLUMN IF NOT EXISTS outcome TEXT NOT NULL DEFAULT 'recorded';

ALTER TABLE user_activity_events
  ALTER COLUMN actor_email SET DEFAULT 'system';

-- ---------------------------------------------------------------------------
-- Constraints (idempotent)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_activity_events_workspace_check'
  ) THEN
    ALTER TABLE user_activity_events
      ADD CONSTRAINT user_activity_events_workspace_check
      CHECK (workspace IN ('rfp', 'financial', 'leads'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_activity_events_outcome_check'
  ) THEN
    ALTER TABLE user_activity_events
      ADD CONSTRAINT user_activity_events_outcome_check
      CHECK (outcome IN ('recorded', 'started', 'completed', 'failed', 'cancelled'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_activity_events_actor_email_check'
  ) THEN
    ALTER TABLE user_activity_events
      ADD CONSTRAINT user_activity_events_actor_email_check
      CHECK (length(trim(actor_email)) > 0 AND length(actor_email) <= 320);
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_activity_events_action_check'
  ) THEN
    ALTER TABLE user_activity_events
      ADD CONSTRAINT user_activity_events_action_check
      CHECK (length(trim(action)) > 0 AND length(action) <= 200);
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_activity_events_summary_check'
  ) THEN
    ALTER TABLE user_activity_events
      ADD CONSTRAINT user_activity_events_summary_check
      CHECK (length(trim(summary)) > 0 AND length(summary) <= 500);
  END IF;
END $$;

-- Normalize blank actors left by an earlier draft ('' → system)
UPDATE user_activity_events
SET actor_email = 'system'
WHERE trim(coalesce(actor_email, '')) = '';

-- ---------------------------------------------------------------------------
-- Indexes (feed + filters the Activity UI uses)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- Security — backend service role only (no browser/PostgREST access)
-- ---------------------------------------------------------------------------
ALTER TABLE user_activity_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE user_activity_events FROM PUBLIC;
REVOKE ALL ON TABLE user_activity_events FROM anon, authenticated;
GRANT ALL ON TABLE user_activity_events TO service_role;

-- ---------------------------------------------------------------------------
-- Documentation (visible in Supabase Table Editor)
-- ---------------------------------------------------------------------------
COMMENT ON TABLE user_activity_events IS
  'Append-only user activity audit log for RFP, Financial, and Leads workspaces. Written only by the FastAPI service role.';

COMMENT ON COLUMN user_activity_events.workspace IS
  'Activity tab partition: rfp | financial | leads';

COMMENT ON COLUMN user_activity_events.actor_email IS
  'Signed-in user email, or system for cron/background jobs';

COMMENT ON COLUMN user_activity_events.action IS
  'Stable namespaced verb, e.g. proposal.phase_started, qb.sync_refreshed, prep.generated';

COMMENT ON COLUMN user_activity_events.outcome IS
  'recorded | started | completed | failed | cancelled';

COMMENT ON COLUMN user_activity_events.entity_type IS
  'Polymorphic target kind: rfp, proposal, kb_doc, invoice, lead, sync, …';

COMMENT ON COLUMN user_activity_events.entity_id IS
  'String id of the target (uuid / int / slug) for deep-links';

COMMENT ON COLUMN user_activity_events.entity_label IS
  'Human title snapshot at emit time (RFP name, company, …)';

COMMENT ON COLUMN user_activity_events.summary IS
  'One-line admin-readable text for the Activity feed';

COMMENT ON COLUMN user_activity_events.metadata IS
  'Small JSON extras only (phase, counts, source). No large payloads.';

COMMENT ON COLUMN user_activity_events.run_id IS
  'Optional join key to llm_call_log / financial LLM / pipeline runs';
