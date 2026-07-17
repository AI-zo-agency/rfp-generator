# Winning Pattern Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Phase 2 Winning Pattern Intelligence Agent that stores structured writing patterns on each section plan and lets Phase 3 writers use those patterns without copying old proposal prose.

**Architecture:** The new agent runs after Dynamic Section Planner and before Section Strategy Planner. It uses existing Supermemory intelligence retrieval to inspect similar won proposal snippets, extracts pattern-only guidance, stores it as `SectionPlan.winningPattern`, and Phase 3 formats that pattern into the drafter context. A frontend action lets users start the pipeline after already-generated Sections 1-3.

**Tech Stack:** Python, Pydantic v2, LangGraph, unittest/pytest, Next.js/React/TypeScript.

## Global Constraints

- V1 only: no Supermemory metadata schema, proposal tagging, section-level indexing, embedding migration, re-indexing, or new retrieval infrastructure.
- Winning proposal raw text must not be persisted in the execution plan.
- Phase 3 JIT retrieval remains for factual evidence; won proposal prose is not used as drafting evidence.
- Follow existing aliases and camelCase JSON contract in `proposal_intelligence/schemas.py`.

---

## File Structure

- Modify `backend/app/services/proposal_intelligence/schemas.py`: add `WinningPattern` and `winningPattern` on `SectionPlan`.
- Create `backend/app/services/proposal_intelligence/agents/winning_pattern_intelligence.py`: retrieve won-pattern intelligence and populate section plans.
- Modify `backend/app/services/proposal_intelligence/agents/__init__.py`: export agent if needed by local pattern.
- Modify `backend/app/services/proposal_intelligence/graph.py`: insert node after `dynamic_section`.
- Modify `backend/app/services/proposal_intelligence/agents/section_strategy_planner.py`: preserve existing winning patterns while filling remaining brief fields.
- Modify `backend/app/services/proposal_drafting_graph.py`: format `winningPattern` in section plan context.
- Modify `frontend/src/components/ProposalDraftWorkspace.tsx`: add button to run full pipeline starting from Phase 2.
- Tests: update/add focused backend tests for schema, graph node, agent behavior, and Phase 3 formatting. Use existing frontend lint diagnostics for TSX edit.

## Task 1: Schema + Tests

**Files:**
- Modify: `backend/app/services/proposal_intelligence/schemas.py`
- Test: `backend/test_proposal_intelligence_schemas.py`

- [ ] Write failing schema test proving `winningPattern` round-trips on `SectionPlan`.
- [ ] Run focused schema test and confirm failure.
- [ ] Add `WinningPattern` model and `SectionPlan.winning_pattern`.
- [ ] Re-run schema test and confirm pass.

## Task 2: Winning Pattern Agent

**Files:**
- Create: `backend/app/services/proposal_intelligence/agents/winning_pattern_intelligence.py`
- Modify: `backend/app/services/proposal_intelligence/graph.py`
- Test: `backend/test_proposal_intelligence_graph.py`
- Test: add focused agent test if no suitable existing file exists.

- [ ] Write failing test for graph containing `winning_pattern`.
- [ ] Write failing test that agent stores structured patterns and strips raw content-like keys.
- [ ] Implement agent using existing `retrieve_intelligence("won_patterns", ...)` and `safe_chat_json`.
- [ ] Insert graph edge: `dynamic_section -> winning_pattern -> section_strategy`.
- [ ] Re-run focused tests.

## Task 3: Preserve Patterns in Section Strategy

**Files:**
- Modify: `backend/app/services/proposal_intelligence/agents/section_strategy_planner.py`
- Test: focused agent/schema test.

- [ ] Write failing test that an existing section `winningPattern` survives section strategy generation.
- [ ] Merge by `sectionId` after strategy output.
- [ ] Ensure fallback section plans include empty/default winning patterns.
- [ ] Re-run focused tests.

## Task 4: Phase 3 Drafting Context

**Files:**
- Modify: `backend/app/services/proposal_drafting_graph.py`
- Test: add/update focused drafting test.

- [ ] Write failing test for `_format_plan_context` including `Winning Pattern`.
- [ ] Add formatted pattern context with explicit "do not copy prior prose" rule.
- [ ] Re-run focused drafting test.

## Task 5: Start After Sections 1-3 UI

**Files:**
- Modify: `frontend/src/components/ProposalDraftWorkspace.tsx`

- [ ] Add a secondary button near the main proposal button: "Start After Sections 1-3".
- [ ] Button calls `generateFullProposalStaged` with `startFrom: "phase-2"` and `forceRestart: false`.
- [ ] Disable while any pipeline is running.
- [ ] Surface progress and recovery through existing state handlers.
- [ ] Run lints/diagnostics for the TSX file.

## Task 6: Verification

- [ ] Run focused backend tests for proposal intelligence and drafting context.
- [ ] Run frontend diagnostics/lints for edited TSX/lib files.
- [ ] Report exact verification results and any remaining risks.

