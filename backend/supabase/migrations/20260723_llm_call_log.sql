-- LLM call cost / token instrumentation (Part 1 — observability only).
-- Apply in Supabase SQL editor if migration tooling is not used.

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
  cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  tokens_estimated BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_call_log_run_id ON llm_call_log (run_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_rfp_id ON llm_call_log (rfp_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_created_at ON llm_call_log (created_at DESC);
