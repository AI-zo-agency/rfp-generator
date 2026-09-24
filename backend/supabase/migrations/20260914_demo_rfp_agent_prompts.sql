-- Durable system prompts for the rfp-two-agents demo (UI live-edit).
-- One row; agent1 / agent2 bodies. Service-role only (demo server).

CREATE TABLE IF NOT EXISTS demo_rfp_agent_prompts (
  id         TEXT PRIMARY KEY DEFAULT 'rfp-two-agents',
  agent1     TEXT NOT NULL DEFAULT '',
  agent2     TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE demo_rfp_agent_prompts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE demo_rfp_agent_prompts FROM anon, authenticated;
GRANT ALL ON TABLE demo_rfp_agent_prompts TO service_role;

INSERT INTO demo_rfp_agent_prompts (id, agent1, agent2)
VALUES ('rfp-two-agents', '', '')
ON CONFLICT (id) DO NOTHING;
