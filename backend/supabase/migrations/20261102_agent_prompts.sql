-- Editable agent prompts behind the Prompts page.
-- body is what agents use; default_body is the text shipped in code, kept for reset.
-- Rows are seeded and kept current by prompt_store.sync_catalog on backend startup.
-- Service-role only: the browser reaches this through /api/v1/prompts.

CREATE TABLE IF NOT EXISTS agent_prompts (
  key          TEXT PRIMARY KEY,
  group_name   TEXT NOT NULL,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  source       TEXT NOT NULL DEFAULT '',
  body         TEXT NOT NULL,
  default_body TEXT NOT NULL,
  is_edited    BOOLEAN GENERATED ALWAYS AS (body IS DISTINCT FROM default_body) STORED,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by   TEXT
);

-- Each process polls MAX(updated_at) to notice edits without re-reading every body.
CREATE INDEX IF NOT EXISTS agent_prompts_updated_at_idx ON agent_prompts (updated_at DESC);

ALTER TABLE agent_prompts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE agent_prompts FROM anon, authenticated;
GRANT ALL ON TABLE agent_prompts TO service_role;
