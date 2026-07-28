-- T3.1 / OQ-6: record requested max_tokens alongside served usage.
-- Apply in Supabase SQL editor after 20260723_llm_call_log.sql.

ALTER TABLE llm_call_log
  ADD COLUMN IF NOT EXISTS requested_max_tokens INTEGER NOT NULL DEFAULT 0;

ALTER TABLE llm_call_log
  ADD COLUMN IF NOT EXISTS effective_max_tokens INTEGER NOT NULL DEFAULT 0;
