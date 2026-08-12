-- Optional sync-job counters for JustWin re-sync dedupe reporting.
-- Safe to run on existing DBs; finish_sync_job falls back if columns are absent.

ALTER TABLE sync_jobs ADD COLUMN IF NOT EXISTS rfps_skipped INTEGER DEFAULT 0;
ALTER TABLE sync_jobs ADD COLUMN IF NOT EXISTS rfps_created INTEGER DEFAULT 0;
