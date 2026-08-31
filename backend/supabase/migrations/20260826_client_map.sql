-- Agency master dashboard Phase A: TW↔QB client mapping (app-owned).

CREATE TABLE IF NOT EXISTS client_map (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tag_code               TEXT NOT NULL,
  client_name            TEXT NOT NULL,
  qb_customer_ids        TEXT[] NOT NULL DEFAULT '{}',
  qb_customer_names      TEXT[] NOT NULL DEFAULT '{}',
  teamwork_company_ids   BIGINT[] NOT NULL DEFAULT '{}',
  teamwork_company_names TEXT[] NOT NULL DEFAULT '{}',
  city                   TEXT,
  state                  TEXT,
  current_am             TEXT,
  status                 TEXT,
  source                 TEXT,
  highest_value          TEXT,
  is_internal            BOOLEAN NOT NULL DEFAULT FALSE,
  link_confidence        TEXT NOT NULL DEFAULT 'unmatched'
                         CHECK (link_confidence IN ('confirmed', 'suggested', 'unmatched')),
  link_reason            TEXT,
  notes                  TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS client_map_tag_code_idx ON client_map (UPPER(tag_code));
CREATE INDEX IF NOT EXISTS client_map_confidence_idx ON client_map (link_confidence);

CREATE TABLE IF NOT EXISTS client_map_job_override (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id              TEXT NOT NULL,
  project_id           BIGINT NOT NULL,
  client_map_id        UUID REFERENCES client_map(id) ON DELETE SET NULL,
  qb_customer_ids      TEXT[] NOT NULL DEFAULT '{}',
  qb_customer_names    TEXT[] NOT NULL DEFAULT '{}',
  link_confidence      TEXT NOT NULL DEFAULT 'confirmed'
                       CHECK (link_confidence IN ('confirmed', 'suggested', 'unmatched')),
  notes                TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (site_id, project_id)
);

ALTER TABLE client_map ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_map_job_override ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE client_map FROM anon, authenticated;
REVOKE ALL ON TABLE client_map_job_override FROM anon, authenticated;
GRANT ALL ON TABLE client_map TO service_role;
GRANT ALL ON TABLE client_map_job_override TO service_role;
