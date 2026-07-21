# Proposal Generation Concurrency Fix — Design Spec

**Date:** 2026-07-21
**Status:** Approved for implementation
**Scope:** Root-cause fix for dashboard-wide freezing and duplicate concurrent generation runs

## Problem

Two related symptoms reported in production:

1. While one proposal is generating, the **entire dashboard freezes for every user** — including unrelated actions like login, signup, and viewing other RFPs.
2. When the same RFP proposal is open in two tabs/by two users (or reloaded mid-generation), there's no way to tell a run is already in progress, so a second click kicks off a **duplicate concurrent generation** for the same RFP.

## Root Causes (confirmed by code investigation)

1. **Blocking Supabase calls on the shared event loop.** The backend runs a single uvicorn worker (`backend/Dockerfile:21`, no `--workers` flag) — one process, one event loop, shared by every request. Several `async def` functions on the generation hot path call the *synchronous* `get_research_cache`/`save_research_cache` (and related sync repository functions) directly instead of the already-existing `asyncio.to_thread`-wrapped async versions (`aget_research_cache`/`asave_research_cache`). Call sites include `proposal_generator.py:1633` and most of `proposal_pipeline_checkpoint.py` (lines 54, 69, 133, 176, 271, 343, 358, 364, 369, 517, 560, 565). The retry-backoff helper in `proposal_repository.py`/`rfp_repository.py`/`proposal_google_doc_export.py` also uses `time.sleep()` (up to ~6 retries × 4.5s), which blocks the loop directly when invoked from a sync call site. Each of these is a real network round-trip run on the event loop — during that time, no other request (including login) can be served.

2. **Global (not per-RFP) LLM concurrency semaphores.** `proposal_drafting_graph.py:44` defines `_LLM_SEMAPHORE = asyncio.Semaphore(1)` and `proposal_retrieval_graph.py:27` defines `Semaphore(2)`, both at module scope — shared by every request in the process for the lifetime of the server. User A generating RFP X holds the only slot, so User B's completely unrelated RFP Y generation queues up behind it. `proposal_self_edit_loop.py:510` shows the correct pattern already in use elsewhere in this codebase: create the semaphore fresh inside the function invocation, not at module scope.

3. **No per-RFP "generation in progress" signal.** Nothing tracks whether a given RFP is currently generating. A dormant scaffold exists (`proposal_job_runner.py` — `start_proposal_job`/`is_proposal_job_running`) but is never called from the generate flow. On reload, the frontend loses track of the in-flight pipeline (which the backend keeps running server-side), so the user can click Generate again — starting a second concurrent pipeline run for the same RFP that races the first and doubles load.

## Fix

### 1. Make the generation hot path fully async

Audit every `async def` function in the generation pipeline (`proposal_pipeline_checkpoint.py`, `proposal_generator.py`, and any sibling `proposal_*.py` service invoked during Phase 2 / Phase 3 / self-edit / budget / review) for direct calls to synchronous repository functions, and switch them to the existing async wrappers (`aget_research_cache`/`asave_research_cache`, using `asyncio.to_thread`). Apply the same treatment to the `time.sleep()` retry-backoff path — either route those call sites through the async wrapper or make the retry helper itself `async def` using `await asyncio.sleep(...)`.

No new infrastructure — this uses wrappers that already exist, just wires up the call sites that currently bypass them.

### 2. Scope LLM semaphores per generation run, not globally

Remove the module-level `_LLM_SEMAPHORE` in `proposal_drafting_graph.py` and `proposal_retrieval_graph.py`. Create a semaphore fresh inside each Phase-2/Phase-3 invocation (scoped to that one RFP's run) and thread it down through the call chain to the section-draft / retrieval call sites that currently reference the module-level global — mirroring the working pattern in `proposal_self_edit_loop.py:510`. This preserves the original intent (cap concurrent LLM calls *within* one proposal's own generation) while eliminating cross-RFP blocking entirely.

### 3. Per-RFP live generation status, surfaced in the existing poll

Add fields to the Supabase row already keyed by `rfp_id` (the `proposal_research` cache row that `pipeline_phase()` in `proposal_pipeline_checkpoint.py` already reads/writes per phase):

- `generation_status`: `"idle" | "generating"`
- `generation_phase`: current phase label (reusing existing phase constants, e.g. `phase-3`, `phase-3-6-self-edit`)
- `generation_started_at`: timestamp
- `generation_started_by`: current user's email (already available via Supabase auth, `backend/app/api/auth.py`)

Set `generating` + phase name when a phase starts; reset to `idle` when the full pipeline completes **or** errors (wrapped so an exception can't leave it stuck). No new transport is needed — the frontend already polls `GET /api/rfps/[id]/proposal` every 4s reading directly from Supabase (`frontend/src/lib/proposal-api.ts:240`); just include these fields in that existing response.

In `ProposalDraftWorkspace.tsx`, when `generation_status === "generating"`: disable the Generate button and show the live phase, who started it, and elapsed time — for any viewer of that RFP, whether a second user in another tab or the same user after a reload. This directly fixes the reload-then-duplicate-click bug, since the reloaded tab sees the in-progress status immediately instead of appearing idle.

**Staleness fallback:** if `generation_started_at` is older than ~30 minutes (past the 25-minute max phase timeout) with no fresher phase update, treat the status as stale and automatically re-enable Generate rather than requiring a manual database fix.

## Out of Scope (for this pass)

- Adding `--workers` to the uvicorn command for extra process-level headroom. Not needed once the event loop is no longer blocked by sync I/O; can be revisited later as cheap insurance if new blocking calls are introduced.
- Rebuilding generation as a full background-job/queue architecture (enqueue-and-poll via a dedicated worker, replacing the 25-minute held-open HTTP phase calls). The dormant `proposal_job_runner.py` scaffold shows this was previously attempted; a proper version of it is a bigger, separate project and isn't required once the root causes above are fixed.
- Any new realtime transport (SSE/WebSocket/Supabase Realtime channels) — the existing 4s poll against the Supabase-backed status field is sufficient for a multi-minute generation process.

## Testing / Verification

- Unit-level: exercise the async wrappers directly (confirm `aget_research_cache`/`asave_research_cache` are called at each audited site, not the sync versions).
- Manual: run two concurrent proposal generations for different RFPs and confirm neither stalls waiting on the other (validates the semaphore rescoping).
- Manual: while a generation is in progress, hit an unrelated endpoint (e.g. login) from a second session and confirm it responds promptly (validates the async I/O fix).
- Manual: open the same RFP in two tabs, start generation in one, confirm the second shows live phase/status with Generate disabled; reload the generating tab and confirm it also shows in-progress status rather than allowing a duplicate click.
- Manual: force an error mid-pipeline (e.g. inject a failure) and confirm `generation_status` resets to `idle` rather than sticking at `generating`.
