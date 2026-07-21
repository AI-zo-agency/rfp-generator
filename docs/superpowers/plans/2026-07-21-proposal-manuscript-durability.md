# Proposal manuscript durability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or implement in-session). Spec: `docs/superpowers/specs/2026-07-21-proposal-manuscript-durability-design.md`

**Goal:** Stop auto-deleting proposal drafts; archive filled manuscripts before regen/reset; allow rollback from archives.

**Architecture:** Soft-regen keeps the `proposal_drafts` row. New `proposal_draft_archives` table stores full payloads. Reset is the only hard delete, after archive.

### File map

- `backend/supabase/schema.sql` + `backend/supabase/migrations/20260721_proposal_draft_archives.sql`
- `backend/app/services/proposal_draft_archives.py` (new)
- `backend/app/services/supabase_db.py` / `proposal_repository.py`
- `backend/app/services/proposal_generator.py` (remove adelete on force_regenerate)
- `backend/app/api/v1/proposals.py` (reset archives; list/restore endpoints; PUT guard)
- `frontend/.../proposal-api.ts` + workspace version UI
- `backend/test_proposal_manuscript_durability.py`

### Tasks

1. Schema + archive CRUD + tests for archive/restore helpers  
2. Soft-regen: snapshot + archive, no delete; frontend drop reset on forceRestart  
3. Reset: archive-then-delete; remove silent double-wipe or archive both times  
4. List/restore API + minimal UI restore from archives  
5. Run unit tests  

**Commit after green tests.**
