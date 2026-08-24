-- LLM spend for the financial workspace, kept out of llm_call_log on purpose.
--
-- llm_call_log is proposal-shaped: every read on it (get_rfp_cost_breakdown,
-- /llm-cost/rfps/{rfp_id}, _attach_titles joining RFP titles) assumes a row
-- belongs to an RFP, and get_global_cost_summary sweeps the whole table. A
-- QuickBooks chat turn belongs to no RFP, so writing it there would either
-- corrupt those reports or require an rfp_id that is a lie.
--
-- Keyed by thread and turn, which are the only two units of work that exist
-- here: a thread is a conversation, a turn is one user message and whatever
-- tool rounds it took to answer.

CREATE TABLE IF NOT EXISTS financial_llm_calls (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id                   TEXT NOT NULL,
  turn_id                     TEXT NOT NULL,
  node_name                   TEXT NOT NULL DEFAULT '',
  model                       TEXT NOT NULL DEFAULT '',
  tier                        TEXT NOT NULL DEFAULT '',
  provider                    TEXT NOT NULL DEFAULT '',
  input_tokens                INTEGER NOT NULL DEFAULT 0,
  output_tokens               INTEGER NOT NULL DEFAULT 0,
  cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
  cost_usd                    DOUBLE PRECISION NOT NULL DEFAULT 0,
  latency_ms                  INTEGER NOT NULL DEFAULT 0,
  tokens_estimated            BOOLEAN NOT NULL DEFAULT FALSE,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Both caps read by one of these two keys, on every turn.
CREATE INDEX IF NOT EXISTS financial_llm_calls_thread_idx
  ON financial_llm_calls (thread_id);
CREATE INDEX IF NOT EXISTS financial_llm_calls_turn_idx
  ON financial_llm_calls (turn_id);

ALTER TABLE financial_llm_calls ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE financial_llm_calls FROM anon, authenticated;
GRANT ALL ON TABLE financial_llm_calls TO service_role;
