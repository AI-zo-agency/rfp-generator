-- HubSpot deals mirror for financial forecast (read-only; rebuilt by sync).
-- Same disposable pattern as hs_contacts / hs_companies (20260928_hubspot_mirror).

CREATE TABLE IF NOT EXISTS hs_deals (
  hs_id BIGINT PRIMARY KEY,
  dealname TEXT,
  amount NUMERIC,
  dealstage TEXT,
  pipeline TEXT,
  closedate TIMESTAMPTZ,
  hs_is_closed BOOLEAN NOT NULL DEFAULT false,
  hs_is_closed_won BOOLEAN NOT NULL DEFAULT false,
  hs_is_closed_lost BOOLEAN NOT NULL DEFAULT false,
  stage_probability NUMERIC,          -- hs_deal_stage_probability at sync time
  company_hs_id BIGINT,               -- associated company when present
  owner_name TEXT,
  properties JSONB NOT NULL DEFAULT '{}'::jsonb,
  hs_updated_at TIMESTAMPTZ,
  archived BOOLEAN NOT NULL DEFAULT false,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hs_deals_closedate_idx ON hs_deals (closedate);
CREATE INDEX IF NOT EXISTS hs_deals_company_idx ON hs_deals (company_hs_id);
CREATE INDEX IF NOT EXISTS hs_deals_open_idx ON hs_deals (hs_is_closed, closedate)
  WHERE archived = false;

CREATE TABLE IF NOT EXISTS hs_deal_stages (
  pipeline_id TEXT NOT NULL,
  stage_id TEXT NOT NULL,
  pipeline_label TEXT,
  stage_label TEXT,
  probability NUMERIC,
  is_closed BOOLEAN NOT NULL DEFAULT false,
  display_order INT,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (pipeline_id, stage_id)
);

ALTER TABLE hubspot_sync_runs
  ADD COLUMN IF NOT EXISTS deals_upserted INT,
  ADD COLUMN IF NOT EXISTS deals_archived INT;

-- Service-role only, like the rest of the HubSpot mirror.
ALTER TABLE hs_deals ENABLE ROW LEVEL SECURITY;
ALTER TABLE hs_deal_stages ENABLE ROW LEVEL SECURITY;
