-- Brand voice standards revisions: append-only history plus one active default.
-- Service-role only. Applied by hand in the Supabase SQL editor.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS brand_voice_revisions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  label TEXT NOT NULL CHECK (char_length(label) BETWEEN 1 AND 40),
  body TEXT NOT NULL CHECK (octet_length(body) <= 200000),
  sha256 TEXT NOT NULL UNIQUE,
  notes TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS brand_voice_active (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  revision_id UUID NOT NULL REFERENCES brand_voice_revisions(id),
  updated_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A revision never changes once written.
CREATE OR REPLACE FUNCTION brand_voice_revisions_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'brand_voice_revisions is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS brand_voice_revisions_no_change ON brand_voice_revisions;
CREATE TRIGGER brand_voice_revisions_no_change
  BEFORE UPDATE OR DELETE ON brand_voice_revisions
  FOR EACH ROW EXECUTE FUNCTION brand_voice_revisions_immutable();

ALTER TABLE brand_voice_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_voice_active ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE brand_voice_revisions FROM PUBLIC;
REVOKE ALL ON TABLE brand_voice_revisions FROM anon, authenticated;
GRANT ALL ON TABLE brand_voice_revisions TO service_role;
REVOKE ALL ON TABLE brand_voice_active FROM PUBLIC;
REVOKE ALL ON TABLE brand_voice_active FROM anon, authenticated;
GRANT ALL ON TABLE brand_voice_active TO service_role;

COMMENT ON TABLE brand_voice_revisions IS 'zö Brand & Writing Standards revisions. Append-only.';
COMMENT ON TABLE brand_voice_active IS 'One row: the global default revision for new proposals.';
