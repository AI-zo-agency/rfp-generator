# Proposal Model Tiers + Senior Editor — Implementation Plan

> **For agentic workers:** Execute task-by-task. Steps use checkbox syntax.

**Goal:** Route heavy/light LLM tiers; redesign Senior Editor to emit dedupe/coverage tickets and dispatch Phase 3 single-section redrafts with hard isolation; keep KB fact-checker as sole fact owner with cost-aware queries.

**Architecture:** Config-driven `llm_heavy_model` / `llm_light_model`; `chat_json`/`get_chat_model` accept `tier`. Senior Editor returns tickets only; self-edit loop calls Phase 3 single-section draft and asserts other sections unchanged.

**Tech Stack:** Python, FastAPI backend, OpenRouter Anthropic models, existing drafting graph / self-edit loop.

## Global Constraints

- Hard isolation: ticket for section S may only mutate S.
- Coverage/dedupe dispatch uses Phase 3 drafting path (single section).
- KB fact-checker owns facts; Senior Editor does not VERIFY-hunt.
- Cost first: query planner + gates use light tier.

---

## Task 1: Model tier router

- [x] Add `llm_heavy_model` / `llm_light_model` to settings
- [x] Add `resolve_llm_model(tier)` + `tier` on `chat_json` / `chat_text` / `get_chat_model`
- [x] Unit test resolver
- [x] Wire AgentProfile.tier (heavy for writers/editors, light for QUERY_PLANNER)

## Task 2: Section isolation helpers

- [x] `snapshot_section_contents` / `assert_only_section_changed` / `replace_section_isolated`
- [x] Unit tests (pass + fail when sibling changes)

## Task 3: Senior Editor tickets + Phase 3 single-section dispatch

- [x] Rewrite `SENIOR_EDITOR_SYSTEM` for tickets (no fact-fill primary)
- [x] `senior_editor_emit_tickets(...)` returning dedupe + coverage tickets
- [x] `draft_single_rfp_section_phase3(...)` wrapping `_draft_batch` for one mapped section
- [x] Self-edit loop: emit tickets → Phase 3 redraft S only → fact-check S → isolation assert

## Task 4: KB fact-checker cost knobs

- [x] Cap planned queries at 4; pass light tier into query planner path
- [x] Keep skip-when-clean behavior

## Task 5: Spec status + smoke

- [x] Mark design spec Approved
- [x] Run unit tests for tier + isolation
