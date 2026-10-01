-- Wave 3 Lead Finder: read-only mirror of HubSpot contacts and companies.
-- HubSpot stays the source of truth; these rows are disposable and rebuilt by
-- POST /api/v1/leads/hubspot/sync. Never edit them by hand.

CREATE TABLE IF NOT EXISTS hs_companies (
  hs_id BIGINT PRIMARY KEY,
  domain TEXT,
  name TEXT,
  industry TEXT,            -- display label, not HubSpot's internal enum value
  city TEXT,
  state TEXT,
  properties JSONB NOT NULL,
  hs_updated_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hs_companies_domain_idx ON hs_companies (lower(domain));

CREATE TABLE IF NOT EXISTS hs_contacts (
  hs_id BIGINT PRIMARY KEY,
  email TEXT,
  firstname TEXT,
  lastname TEXT,
  jobtitle TEXT,
  phone TEXT,
  company_hs_id BIGINT,     -- associatedcompanyid; often empty, domain join is the fallback
  owner_name TEXT,
  last_activity_at TIMESTAMPTZ,   -- notes_last_updated ("Last Activity Date" in the UI)
  properties JSONB NOT NULL,      -- full payload, so a new field needs no migration
  hs_updated_at TIMESTAMPTZ NOT NULL,
  archived BOOLEAN NOT NULL DEFAULT false,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hs_contacts_email_idx ON hs_contacts (lower(email));

CREATE TABLE IF NOT EXISTS hubspot_sync_state (
  object_type TEXT PRIMARY KEY,
  watermark TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS hubspot_sync_runs (
  id BIGSERIAL PRIMARY KEY,
  mode TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  contacts_upserted INT,
  companies_upserted INT,
  contacts_archived INT,
  error TEXT
);

-- Service-role only, like the QuickBooks mirror: contact PII never goes to the browser via PostgREST.
ALTER TABLE hs_companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE hs_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE hubspot_sync_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE hubspot_sync_runs ENABLE ROW LEVEL SECURITY;
