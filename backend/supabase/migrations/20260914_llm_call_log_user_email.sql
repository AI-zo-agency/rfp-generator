-- Attribute LLM spend to the signed-in user (email) for proposal runs.
-- Safe to re-run: ADD COLUMN IF NOT EXISTS is Postgres 9.1+ via DO block.

ALTER TABLE llm_call_log
  ADD COLUMN IF NOT EXISTS user_email TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_llm_call_log_user_email
  ON llm_call_log (user_email);

CREATE INDEX IF NOT EXISTS idx_llm_call_log_created_user
  ON llm_call_log (created_at, user_email);
