-- Durable owner reconciliation decisions for QuickBooks invoices.

CREATE TABLE IF NOT EXISTS agency_invoice_resolution (
  realm_id      TEXT NOT NULL,
  invoice_id    TEXT NOT NULL,
  resolution    TEXT NOT NULL CHECK (resolution IN ('linked', 'internal')),
  project_id    BIGINT,
  client_map_id UUID REFERENCES client_map(id) ON DELETE SET NULL,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (realm_id, invoice_id),
  CHECK (
    (resolution = 'linked' AND project_id IS NOT NULL)
    OR (resolution = 'internal' AND project_id IS NULL)
  )
);

ALTER TABLE agency_invoice_resolution ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE agency_invoice_resolution FROM anon, authenticated;
GRANT ALL ON TABLE agency_invoice_resolution TO service_role;
