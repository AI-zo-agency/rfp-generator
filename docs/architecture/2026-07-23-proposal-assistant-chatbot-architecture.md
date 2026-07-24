# Proposal Assistant Chatbot — Architecture Map

**Date:** 2026-07-23  
**Purpose:** Ground-truth map of the section chat / improve assistant (for improvement planning).  
**Rule:** Factual only — no optimization recommendations in this pass.

---

## 1. What it is

Not a multi-agent LangGraph chat. It is a **single HTTP turn** per user message:

```
UI (ProposalSectionChatPanel)
  → Next.js proxy (maxDuration 3600)
  → FastAPI POST .../sections/{sectionId}/improve
  → improve_proposal_section()   # one orchestrator
  → response: section + full draft + assistantMessage + draftChanged
```

**No LangGraph** on this path. LangGraph is only used for initial Sections 1–3 generation (`proposal_sections_graph.py`).

---

## 2. Entry points

### API

| Layer | Path |
|-------|------|
| FastAPI | `backend/app/api/v1/proposals.py` → `improve_section_endpoint` |
| Route | `POST /api/v1/rfps/{rfp_id}/proposal/sections/{section_id}/improve` |
| Next proxy | `frontend/src/app/api/rfps/[id]/proposal/sections/[sectionId]/improve/route.ts` |
| Client | `frontend/src/lib/proposal-api.ts` → `improveProposalSection` |

**Request** (`SectionImproveRequest` in `backend/app/models/proposal.py`):

- `message`
- `selectionStart` / `selectionEnd` / `selectionText` (optional excerpt pin)
- `conversationHistory` (`SectionChatTurn[]`)
- `proposalWide` (true when no selection pin)

**Response** (`ProposalSectionImproveResponse`):

- `section`, `draft`, `research`, `assistantMessage`, `draftChanged`

### Also called from (non-UI)

- `proposal_self_edit_loop.py` — fallback section repair
- `proposal_generator.py` — targeted empty-section improve after Sections 1–3

---

## 3. Frontend architecture

| Piece | File | Role |
|-------|------|------|
| Chat panel | `frontend/src/components/ProposalSectionChatPanel.tsx` | Bubbles, composer, quick prompts, pin chip |
| Host | `frontend/src/components/ProposalDraftWorkspace.tsx` | Message state, draft merge after improve |
| Editor hooks | `frontend/src/components/DraftSectionEditor.tsx` | “Improve full section” / “Revise content” → open chat with pin |
| Section resolve | `frontend/src/lib/proposal-section-resolve.ts` | `resolveSectionFromMention`, `messageLooksStructural`, `sectionPersonName` |

### Pin modes (`SectionChatReference`)

| Mode | Meaning | `proposalWide` |
|------|---------|----------------|
| `section` | Full section pin | `true` |
| `selection` | Highlighted excerpt | `false` |
| none | Fall back to open tab / named section in message | `true` |

### Quick prompts (hardcoded in panel)

1. Check duplicates thoroughly.  
2. Remove fabricated content (content → RFP → KB).  
3. Fill `[VERIFY]` tags from KB only.  
4. Does this meet the RFP?

### After a successful edit

1. `onSectionUpdated` → `applySectionImproveFromServer` (avoids immediate re-PUT race with autosave).  
2. Server snapshot: `Saved after chat — {title}`.  
3. Optional local revision drawer (browser storage) — separate from draft snapshots.

**Orphan:** `SectionEditChat.tsx` exists but is **not imported** by the workspace.

---

## 4. Backend orchestration (one turn)

**Orchestrator:** `improve_proposal_section`  
**File:** `backend/app/services/proposal_section_editor.py`

### Decision tree

```
1. classify_chat_op(message)          # proposal_chat_ops.py (regex)
   └─ if not none AND not selection_mode
        → run_chat_ops → persist if changed → return

2. if not selection_mode
     plan_chat_structure_action()     # proposal_chat_structure.py
     ├─ clarify  → reply only, draftChanged=false
     ├─ add/delete → apply_chat_structure_plan → persist → return
     └─ edit     → continue (may retarget section id)

3. Resolve focus section (message mention / pin / path id)

4. _wants_section_edit?
   └─ no  → _section_chat_advisory_reply (Q&A, no draft change)
   └─ yes → plan queries → KB retrieve → rewrite → legal gate → persist
```

### Supporting modules

| Module | Responsibility |
|--------|----------------|
| `proposal_chat_ops.py` | Duplicate audit, remove duplicates, remove fabricated, trust audit |
| `proposal_chat_structure.py` | Add/delete bios & case studies; clarify; coerce VERIFY/KB-only to in-place edit |
| `proposal_langchain_agents.py` | `redraft_section_agent` (`USER_REVISE`) with tools |
| `proposal_manual_flags.py` | `_replace_verify_tags_from_blob` for `[VERIFY]` |
| `legal_attestation_gate.py` | Keep E-Verify / conflict claims as VERIFY |
| `proposal_draft_snapshots.py` | `push_after_section_edit_snapshot` |

---

## 5. The five exits (mental model for improvements)

| Exit | Trigger | Mutates draft? | Module / function |
|------|---------|----------------|-------------------|
| **Ops** | “check/remove duplicates”, “remove fabricated”, “trust audit” | Sometimes | `classify_chat_op` → `run_chat_ops` |
| **Structure** | Add/delete bios, case studies, tabs; ambiguous → clarify | Yes (or no if clarify) | `plan_chat_structure_action` → `apply_chat_structure_plan` |
| **Advisory** | Questions without edit intent | **No** | `_section_chat_advisory_reply` |
| **Excerpt edit** | Selection pin + edit ask | Yes (span only) | `_improve_section_selection` |
| **Full redraft** | Edit ask on a section | Yes | `_redraft_rfp_section` / `_improve_static_section` |

---

## 6. Message flow detail

### Classify layers (not a single classifier)

1. **`classify_chat_op`** — regex → `check_duplicates` \| `remove_duplicates` \| `remove_fabricated` \| `trust_audit` \| `none`
2. **`plan_chat_structure_action`** — heuristics then `llm.chat_json` with `STRUCTURE_PLAN_PROMPT`; safety coerce VERIFY/E-Verify asks → in-place `edit`
3. **`_wants_section_edit`** — questions → advisory; edit verbs → rewrite
4. **`_plan_section_improve`** — LLM plan: `understoodAsk`, `editorInstruction`, `kbQueries`

### KB retrieval by path

| Context | Mechanism |
|---------|-----------|
| RFP dynamic sections | `_search_hits` → `supermemory.search_hybrid`; merge into `research.evidence_corpus` |
| Static template sections | `proposal_knowledge_base_tools.search_knowledge_base` |
| Selection / excerpt | `_fetch_kb_blob_for_selection` (hybrid + chunk) |
| New case study tabs | `kb_rag_retrieve.retrieve_for_question` |
| New bios | `_fetch_member_bio_kb` from sections graph helpers |

### Persist

`_persist_section_improve_draft` → `push_after_section_edit_snapshot` → `asave_proposal_draft` + `asave_research_cache`.

Comment in code: **no pre-chat “Before chat edit” snapshot** (those empty copies wiped improvements when restored).

---

## 7. Structure actions (bios / case studies)

**Planner:** `plan_chat_structure_action`  
**Applier:** `apply_chat_structure_plan`

| Action | Behavior |
|--------|----------|
| `add_sections` | Bios via `_build_bio_section`; case studies via `_build_case_study_section`; renumber via `renumber_dynamic_group_titles` |
| `delete_sections` | Resolve by id/title; refuse if deleting almost entire proposal |
| `clarify` | Ask one question; no mutation |
| `edit` | Retarget `edit_section_id` then fall through to rewrite |

Heuristics force in-place edit for “fill VERIFY / KB only / E-Verify” so those never become a bogus sidebar tab titled “Kb Only”.

---

## 8. VERIFY / MANUAL FILL via chat

| Tag | Handled in chat? | Mechanism |
|-----|------------------|-----------|
| `[VERIFY: …]` | **Yes** | `_replace_verify_tags_from_blob`; structure coerce to in-place edit; `gate_section_legal_attestations` after rewrite |
| `[MANUAL FILL: …]` | **No dedicated path** | Introduced by pipeline finalize; chat may rewrite around them with no guard |

Confirmed gap (see also `docs/manual_fill_audit.md`): MANUAL FILL is the unguarded side door relative to VERIFY.

---

## 9. Models / tiers

Default for chat LLM calls: **`tier="heavy"`** → `settings.llm_heavy_model` or `openrouter_model` (`anthropic/claude-sonnet-4`).

| Call site | Model tier |
|-----------|------------|
| Structure plan | heavy |
| Query plan / advisory / selection edit | heavy |
| `redraft_section_agent(USER_REVISE)` | heavy (`max_tokens=4096`, up to 4 tool rounds) |
| Chat ops | usually **no LLM** |

Light tier (`QUERY_PLANNER` profile) is **not** used on the main chat improve path.

---

## 10. Snapshots

| Concern | Implementation |
|---------|----------------|
| After successful edit | `push_after_section_edit_snapshot` → label `Saved after chat — {title}` |
| Pre-chat undo snapshot | **Not created** (intentional — empty copies wiped chat work) |
| Prune | `prune_clutter_snapshots`; max **12** |
| Restore | `restore_proposal_snapshot` + FE version menu |

---

## 11. Known limitations (from code)

| Issue | Evidence |
|-------|----------|
| Phase 2 required for dynamic RFP sections | 400 if `evidence_corpus` missing |
| Selection bounds drift | 400 re-highlight |
| Inadequate rewrite | 422 |
| MANUAL FILL unprotected | No chat guard (audit confirmed) |
| Chat ops skipped under selection pin | `classify_chat_op` only when `not selection_mode` |
| `_apply_attestation_inplace_fix` | Defined but **never called** |
| Cost `run_id` for chat turns | Not dedicated per chat turn yet (Part 1 chat instrumentation still pending) |
| Heavy-only LLM | Almost every chat LLM call is Sonnet-class |

---

## 12. Improvement priority (from prior product ranking — not part of this map)

Already sequenced outside this doc:

1. **MANUAL FILL hardening** (correctness / fabrication risk) — first  
2. **Advisory quality** — high frequency, low stakes  
3. **Cheaper planning models** — after chat cost instrumentation + shadow tests  
4. Later: case-study swap quality, delete orphan `SectionEditChat.tsx`

---

*Architecture map generated from repository source on 2026-07-23.*
