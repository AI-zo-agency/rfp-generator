-- =============================================================================
-- proposal_tweak_events — who changed which manuscript section, with full text
-- =============================================================================
-- Safe to re-run in the Supabase SQL editor (idempotent).
--
-- Separate from user_activity_events on purpose: that table's contract is
-- "never manuscript blobs". This one stores the before/after text of every
-- person-initiated change so the Activity ledger can show it as a diff.
--
-- Access model (same as user_activity_events):
--   • Backend uses the service role only
--   • RLS on; anon + authenticated have no table privileges
--
-- One row = one tweak of one section by one person. Manual typing is merged
-- into one row per section until a 10-minute quiet gap (updated_at moves,
-- before_text stays frozen). Rows are kept indefinitely, including after the
-- RFP is deleted. Each text column is capped at 200,000 bytes by the backend.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS proposal_tweak_events (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

  rfp_id         TEXT NOT NULL,
  rfp_title      TEXT NOT NULL DEFAULT '',
  section_id     TEXT NOT NULL,
  section_title  TEXT NOT NULL DEFAULT '',

  actor_email    TEXT NOT NULL,
  -- manual_edit | personas | chat_apply | chat_confirm | restore_snapshot |
  -- restore_archive | restart | reset | other
  source         TEXT NOT NULL,
  -- manual | ai | restore | other (headline bucket for the source)
  bucket         TEXT NOT NULL,
  -- text | added | removed | renamed
  kind           TEXT NOT NULL,

  instruction     TEXT NOT NULL DEFAULT '',
  has_instruction BOOLEAN NOT NULL DEFAULT FALSE,

  words_before   INTEGER NOT NULL DEFAULT 0,
  words_after    INTEGER NOT NULL DEFAULT 0,
  words_added    INTEGER NOT NULL DEFAULT 0,
  words_removed  INTEGER NOT NULL DEFAULT 0,

  before_title   TEXT NOT NULL DEFAULT '',
  after_title    TEXT NOT NULL DEFAULT '',
  before_text    TEXT NOT NULL DEFAULT '',
  after_text     TEXT NOT NULL DEFAULT '',

  metadata       JSONB NOT NULL DEFAULT '{}'::jsonb
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'proposal_tweak_events_source_check'
  ) THEN
    ALTER TABLE proposal_tweak_events
      ADD CONSTRAINT proposal_tweak_events_source_check
      CHECK (source IN (
        'manual_edit', 'personas', 'chat_apply', 'chat_confirm',
        'restore_snapshot', 'restore_archive', 'restart', 'reset', 'other'
      ));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'proposal_tweak_events_bucket_check'
  ) THEN
    ALTER TABLE proposal_tweak_events
      ADD CONSTRAINT proposal_tweak_events_bucket_check
      CHECK (bucket IN ('manual', 'ai', 'restore', 'other'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'proposal_tweak_events_kind_check'
  ) THEN
    ALTER TABLE proposal_tweak_events
      ADD CONSTRAINT proposal_tweak_events_kind_check
      CHECK (kind IN ('text', 'added', 'removed', 'renamed'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pte_rfp_time
  ON proposal_tweak_events (rfp_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pte_actor_time
  ON proposal_tweak_events (actor_email, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pte_burst
  ON proposal_tweak_events (rfp_id, section_id, actor_email, updated_at DESC)
  WHERE source = 'manual_edit';

-- ---------------------------------------------------------------------------
-- Security — backend service role only (no browser/PostgREST access)
-- ---------------------------------------------------------------------------
ALTER TABLE proposal_tweak_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE proposal_tweak_events FROM PUBLIC;
REVOKE ALL ON TABLE proposal_tweak_events FROM anon, authenticated;
GRANT ALL ON TABLE proposal_tweak_events TO service_role;

COMMENT ON TABLE proposal_tweak_events IS
  'Person-initiated manuscript changes with full before/after section text. Written only by the FastAPI service role.';
