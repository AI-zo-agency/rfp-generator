# Proposal manuscript durability (soft-regenerate + archive rollback)

**Date:** 2026-07-21  
**Status:** Approved (A + B)

## Problem

Filled proposals were lost when Generate / Sections 1–3 ran with `force_regenerate=True`, which **deleted** the `proposal_drafts` row. In-payload snapshots die with that row. Research could survive while prose disappeared.

## Goals

1. **Never auto-delete** a draft during Generate, recover, or Sections 1–3.
2. **Hard delete only** on explicit Reset Draft (confirmed).
3. **Archive table** retains full payloads so users can roll back even after Reset.
4. **In-draft snapshots** remain for quick version compare while the live row exists.

## Approach A — Soft regenerate

- Remove `adelete_proposal_draft` from `generate_sections_1_3(force_regenerate=True)`.
- Before rewriting sections with existing filled content: push in-draft snapshot + write archive row (`reason=before_sections_1_3_regen`).
- Frontend `forceRestart` must **not** call `resetProposal()`; clear UI only; backend owns durable writes.
- PUT empty-over-filled stays 409; also block empty overwrite when research shows completed phases and existing draft has content (belt).

## Approach B — Archive table

```sql
CREATE TABLE proposal_draft_archives (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rfp_id text NOT NULL,
  archived_at timestamptz NOT NULL DEFAULT now(),
  reason text NOT NULL,
  label text,
  section_count int NOT NULL DEFAULT 0,
  filled_count int NOT NULL DEFAULT 0,
  payload jsonb NOT NULL
);
CREATE INDEX ON proposal_draft_archives (rfp_id, archived_at DESC);
```

- Archive before: soft regen (if filled), explicit Reset (if filled).
- Cap retained archives per RFP (e.g. 20).
- APIs: `GET .../proposal/archives`, `POST .../proposal/archives/{id}/restore`.
- UI: list archives + Restore (same mental model as saved versions).

## Non-goals

- Point-in-time recovery of already-lost Greenville prose (no archive existed then).
- Changing LLM routing or generation quality.
