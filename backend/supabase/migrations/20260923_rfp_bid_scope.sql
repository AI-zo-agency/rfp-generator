-- Bid scope: which Fit track(s) the agency is bidding on a multi-role RFP.
ALTER TABLE rfps ADD COLUMN IF NOT EXISTS selected_tracks JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE rfps ADD COLUMN IF NOT EXISTS bid_scope_locked_at TIMESTAMPTZ NULL;
