# Evidence Trust Gate Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Gate proposal (and shared RFP) evidence so we never cite Confirm-only clients, wrong work types, or finalist/competitor files as wins — and never invent refs/certs when KB is empty.

**Architecture:** Shared `evidence_trust` package: ClientList registry → provenance + claim gates on retrieval → VERIFY/FLAG gaps → post-draft claim validator. Promote RFP hard-facts extractor for Stage 1 + proposal context.

**Tech Stack:** Python, Pydantic, existing Supermemory + proposal graph, unittest.

## Global Constraints

- Empty slot → `[VERIFY]`/`[FLAG]` + reason; draft continues
- Best-effort KB fetch before flagging
- Source of truth: `01_ClientList_Approved.md`
- Retrieval gate + post-draft validator
- Stick to RFP requirements only when writing

## File map

| File | Role |
|------|------|
| `backend/app/services/evidence_trust/__init__.py` | Public exports |
| `backend/app/services/evidence_trust/client_list.py` | Parse ClientList markdown |
| `backend/app/services/evidence_trust/provenance.py` | 03/06/07/08/Resonance |
| `backend/app/services/evidence_trust/gate.py` | Filter candidates for a claim |
| `backend/app/services/evidence_trust/flags.py` | VERIFY/FLAG formatters |
| `backend/app/services/evidence_trust/claim_validator.py` | Post-draft scan/fix |
| `backend/app/services/evidence_trust/rfp_hard_facts.py` | Shared hard-facts extract |
| `backend/app/services/evidence_trust/load_client_list.py` | Fetch ClientList from SM |
| Wire: `jit_retrieval.py`, `evidence_selection.py`, `proposal_fulfill_fabrication_guard.py`, `go_no_go_service.py` (import shared hard facts) |
| Tests: `backend/test_evidence_trust_*.py` |

---

### Task 1: ClientList parser + provenance + flags (TDD)

- [ ] Failing tests for Confirm, work types, VERIFY reason format
- [ ] Implement parser/provenance/flags
- [ ] Pass tests

### Task 2: Evidence gate + claim validator (TDD)

- [ ] Tests for claim↔tag, FIN block, empty→VERIFY no invent
- [ ] Implement gate + validator
- [ ] Pass tests

### Task 3: Shared RFP hard facts

- [ ] Extract module; re-export from go_no_go; tests still pass
- [ ] Inject into proposal drafting context path

### Task 4: Wire into generation

- [ ] JIT retrieval filters hits
- [ ] Evidence selection pre-filters catalog
- [ ] Fabrication guard / post-draft uses claim validator
- [ ] Writer prompts: never invent; use VERIFY reasons

### Task 5: Verification

- [ ] Run unit tests
- [ ] Smoke: empty refs → VERIFY with reason
