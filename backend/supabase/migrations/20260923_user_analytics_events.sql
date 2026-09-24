-- =============================================================================
-- user_analytics_events — high-volume product analytics (separate from audit)
-- =============================================================================
-- Safe to re-run. Service-role only (same pattern as user_activity_events).
-- Event types: page_view | tab_view | ui_click | heartbeat | funnel_step
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS user_analytics_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  workspace     TEXT NOT NULL,
  actor_email   TEXT NOT NULL DEFAULT 'system',
  session_id    TEXT NOT NULL DEFAULT '',
  event_type    TEXT NOT NULL,
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

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_analytics_events_workspace_check'
  ) THEN
    ALTER TABLE user_analytics_events
      ADD CONSTRAINT user_analytics_events_workspace_check
      CHECK (workspace IN ('rfp', 'financial', 'leads'));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_analytics_events_type_check'
  ) THEN
    ALTER TABLE user_analytics_events
      ADD CONSTRAINT user_analytics_events_type_check
      CHECK (event_type IN ('page_view', 'tab_view', 'ui_click', 'heartbeat', 'funnel_step'));
  END IF;
END $$;

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

COMMENT ON TABLE user_analytics_events IS
  'Product analytics (pageviews, clicks, dwell). Separate from user_activity_events audit log.';
