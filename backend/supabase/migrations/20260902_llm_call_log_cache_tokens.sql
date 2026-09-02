-- Prompt-cache accounting for llm_call_log.
--
-- record_llm_call has always sent cache_creation_input_tokens /
-- cache_read_input_tokens in its row, and the SQLite path self-migrates for them
-- (llm_call_log._ADDED_COLUMNS). The Supabase path is deliberately a no-op
-- ("use migration") and no migration ever added them, so on Supabase PostgREST
-- rejected the WHOLE insert with PGRST204:
--
--   Could not find the 'cache_creation_input_tokens' column of 'llm_call_log'
--
-- record_llm_call swallows that (observability must never break generation), so
-- the symptom was silent: prompt caching was working correctly at the API — a
-- probe showed prompt tokens drop 4,144 -> 22 with cache_read=4,122 on a repeat
-- call — while llm_call_log recorded NOTHING. Not just the cache columns: every
-- LLM cost/token row for every call was being dropped.
--
-- 20260824_financial_llm_calls.sql added these same two columns to the separate
-- financial_llm_calls table, which is why the financial side kept reporting.
--
-- Apply in the Supabase SQL editor after 20260723_llm_call_log.sql.

ALTER TABLE llm_call_log
  ADD COLUMN IF NOT EXISTS cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0;

ALTER TABLE llm_call_log
  ADD COLUMN IF NOT EXISTS cache_read_input_tokens INTEGER NOT NULL DEFAULT 0;
