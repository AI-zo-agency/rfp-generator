-- Cite-linked packet exhibits beside the primary RFP PDF (one active file per cite target).
CREATE TABLE IF NOT EXISTS rfp_packet_files (
  id TEXT PRIMARY KEY,
  rfp_id TEXT NOT NULL REFERENCES rfps (id) ON DELETE CASCADE,
  cite_target TEXT NOT NULL,
  cite_quote TEXT NOT NULL DEFAULT '',
  filename TEXT NOT NULL DEFAULT '',
  storage_path TEXT NOT NULL,
  content_sha256 TEXT NOT NULL DEFAULT '',
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  uploaded_by TEXT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS rfp_packet_files_rfp_cite_uidx
  ON rfp_packet_files (rfp_id, cite_target);

CREATE INDEX IF NOT EXISTS rfp_packet_files_rfp_id_idx
  ON rfp_packet_files (rfp_id);
