# Prompts and Guardrails Directory

This document provides a comprehensive inventory of all files containing **prompts** (LLM system instructions, prompt templates, agent instructions) and **guardrails** (anti-hallucination checks, presubmit validators, zero fabrication rules, consistency checkers, and scrubbers) across the codebase.

---

## 1. Prompts & LLM Instructions

### **Backend Generation & Drafting Prompts**
* [`backend/app/services/proposal_drafting_prompts.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_drafting_prompts.py)
  * System prompt definitions for proposal section generation with anti-hallucination rules and verified evidence instructions.
* [`backend/app/services/proposal_drafting_graph.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_drafting_graph.py#L122)
  * `DRAFT_BATCH_PROMPT` — Multi-section batch drafting prompt for government/commercial RFP responses.
* [`backend/app/services/proposal_sections_graph.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_sections_graph.py)
  * Section-level multi-turn agent drafting system prompts and node instructions.
* [`backend/app/services/proposal_ralph.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_ralph.py)
  * Ralph persona prompt injection (`inject_ralph_into_system_prompt`) for proposal tone and brand voice.

### **Interactive Chat & Section Editor Prompts**
* [`backend/app/services/proposal_section_editor.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_section_editor.py)
  * `SECTION_CHAT_ADVISORY_PROMPT` (Line 128) — Proposal advisory chatbot system prompt.
  * `EDIT_SCOPE_PLAN_PROMPT` (Line 2488) — Plans scope of user-driven edits.
  * `REFINE_QUERIES_PROMPT` (Line 3153) — Generates targeted Knowledge Base retrieval queries for edits.
  * `SECTION_IMPROVE_PLAN_PROMPT` (Line 3174) — First-pass section improvement planner.
  * `SECTION_REDRAFT_PROMPT` (Line 3202) — Complete section redraft prompt.
  * `SELECTION_EDIT_PROMPT` (Line 3245) — Surgical excerpt edit prompt.
  * `SELECTION_KB_PLAN_PROMPT` (Line 3276) — Excerpt-specific KB search planner.
  * `APPLY_FIX_REDRAFT_PROMPT` (Line 3292) — Applies presubmit suggested fixes.
  * `STATIC_SECTION_REDRAFT_PROMPT` (Line 3305) — Redrafts static content (company overview, team bios, case studies).
* [`backend/app/services/proposal_chat_structure.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_chat_structure.py)
  * `STRUCTURE_PLAN_PROMPT` (Line 60) — Structural modification prompt for proposal outlines.
  * `EXTRACT_SPLIT_PROMPT` (Line 2360) — Splits a proposal section into two distinct sections.

### **Go/No-Go & RFP Analysis Prompts**
* [`backend/app/services/go_no_go_service.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/go_no_go_service.py)
  * `SYSTEM_PROMPT` (Line 214) — Stage 1 RFP fit analyst prompt.
  * `KB_QUERY_PLANNER_PROMPT` (Line 379) — Knowledge Base query planner for qualification.
* [`backend/app/services/go_no_go_adjudicator.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/go_no_go_adjudicator.py)
  * `ADJUDICATOR_PROMPT` (Line 36) — Requirement adjudication against KB evidence.
  * `GAP_RECOVER_PROMPT` (Line 329) — Secondary gap recovery evaluator.
* [`backend/app/services/go_no_go_evidence_agent.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/go_no_go_evidence_agent.py#L30)
  * `FOLLOW_UP_PROMPT` — Evidence extraction follow-up agent.
* [`backend/app/services/go_no_go_requirements.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/go_no_go_requirements.py#L36)
  * `REQUIREMENT_PLANNER_PROMPT` — RFP capability requirement extraction prompt.

### **Retrieval & Knowledge Base Prompts**
* [`backend/app/services/proposal_retrieval_graph.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_retrieval_graph.py)
  * `RFP_ANALYSIS_PROMPT` (Line 31) — RFP structure and strategy analyzer.
  * `BATCH_QUERY_PLANNER_PROMPT` (Line 87) — Multi-section search query planner.
  * `BATCH_COVERAGE_EVAL_PROMPT` (Line 99) — Scores KB evidence coverage.
* [`backend/app/services/proposal_knowledge_base_tools.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_knowledge_base_tools.py#L44)
  * `PROPOSAL_QUERY_PLANNER_PROMPT` — Plans Supermemory KB queries for Sections 1–3.
* [`backend/app/services/proposal_case_study_fit.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_case_study_fit.py#L74)
  * `CASE_STUDY_QUERY_PROMPT` — Matches agency case studies to RFP requirements.

### **Repair, Financial & Review Prompts**
* [`backend/app/services/proposal_presubmit_autofix.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_presubmit_autofix.py)
  * `SURGICAL_FIX_PROMPT` (Line 72) — Repairs section issues flagged in presubmit review.
  * `PROCUREMENT_FIX_PROMPT` (Line 92) — Form/compliance section repair prompt.
* [`backend/app/services/proposal_money_intelligence.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_money_intelligence.py)
  * `_PASS_A_PROMPT` (Line 31) — Triages dollar figures mentioned outside budget tables.
  * `_PASS_B_PROMPT` (Line 166) — Budget narrative and math integrity checker.
* [`backend/app/services/proposal_budget_sync.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_budget_sync.py)
  * `FEE_SLOT_PLAN_PROMPT` (Line 32) — Synchronizes narrative pricing with canonical budget tables.
  * `FEE_GROUNDING_CHECK_PROMPT` (Line 53) — Verifies narrative fee claims against evidence.
* [`backend/app/services/proposal_manuscript_auditor.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_manuscript_auditor.py#L27)
  * `_AUDIT_PROMPT` — Whole-manuscript adversarial auditor prompt.
* [`backend/app/services/proposal_fee_justification.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_fee_justification.py#L16)
  * `FEE_MEMO_PROMPT` — Generates internal pricing rationale memos.
* [`backend/app/services/proposal_proof_points.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_proof_points.py#L23)
  * `PROOF_POINT_PROMPT` — Proof point extraction prompt.
* [`backend/app/services/proposal_loss_lessons.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_loss_lessons.py#L18)
  * `LOSS_SYNTHESIS_PROMPT` — Integrates past bid loss learnings into current drafting.

### **Frontend Quick Prompts**
* [`frontend/src/components/SectionEditChat.tsx`](file:///Users/mahipatel/ZO-AGENCY/frontend/src/components/SectionEditChat.tsx#L21)
  * `QUICK_PROMPTS` — Predefined UI edit prompts for quick section updates.
* [`frontend/src/components/ProposalSectionChatPanel.tsx`](file:///Users/mahipatel/ZO-AGENCY/frontend/src/components/ProposalSectionChatPanel.tsx#L63)
  * `QUICK_PROMPTS` — Predefined chat prompts in the section editing sidebar.

---

## 2. Guardrail, Verification & Safety Files

### **Anti-Hallucination & Zero Fabrication**
* [`backend/app/services/proposal_zero_fabrication.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_zero_fabrication.py)
  * `apply_zero_fabrication_guards` — Reverts ungrounded entity claims, team experience stats, and invented references.
* [`backend/app/services/proposal_hallucination_detector.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_hallucination_detector.py)
  * Detects recycled hallucinated metrics (e.g. "29 points / 62% cost") and unverified statistics.
* [`backend/app/services/proposal_fulfill_fabrication_guard.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_fulfill_fabrication_guard.py)
  * Guard preventing fabrication injection during gap satisfaction passes.
* [`backend/app/services/proposal_fulfill_guard.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_fulfill_guard.py)
  * Validates section outputs from fulfillment pipelines.
* [`backend/app/services/proposal_capability_bio_grounding.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_capability_bio_grounding.py)
  * Grounding checks for team member experience years, certifications, and bios.
* [`backend/app/services/proposal_scan_compliance_fabrication.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_scan_compliance_fabrication.py)
  * Named entity and subcontractor grounding validators.

### **Presubmit & Tier Validation Gates**
* [`backend/app/services/proposal_presubmit_review.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_presubmit_review.py)
  * `run_presubmit_review` — Phase 4 final review suite checking compliance, placeholders, and structure.
* [`backend/app/services/proposal_t1_validators.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_t1_validators.py)
  * Tier 1 blocker validators: checks for internal note leaks (`FLAG FOR`, `TODO`), mid-sentence cutoffs, and syntax corruptions.
* [`backend/app/services/proposal_t2_validators.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_t2_validators.py)
  * Tier 2 quality validators: checks for style formatting, passive tone, and missing section components.
* [`backend/app/services/proposal_integrity_guards.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_integrity_guards.py)
  * Manuscript integrity checks (`apply_manuscript_integrity_guards`, reference contact phone number evidence guards, pricing tier guards).
* [`backend/app/services/proposal_evidence_gate.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_evidence_gate.py)
  * Gates section content on KB evidence availability.
* [`backend/app/services/proposal_blocker_prevention.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_blocker_prevention.py)
  * Normalizes headings and section titles to prevent presubmit blockers.

### **Adversarial Audit & Repair Loop**
* [`backend/app/services/proposal_adversarial_repair.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_adversarial_repair.py)
  * Main bounded adversarial audit & repair loop (`run_adversarial_repair_loop`).
* [`backend/app/services/proposal_adversarial_repair_planner.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_adversarial_repair_planner.py)
  * Routes audit findings (e.g. fabrication, scope breach) to targeted KB retrieval and repair passes.
* [`backend/app/services/proposal_adversarial_repair_verifier.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_adversarial_repair_verifier.py)
  * `verify_repair_attempt` — Verifies that a repair fixed the targeted issue without introducing new defects.

### **Consistency & Contradiction Enforcement**
* [`backend/app/services/proposal_consistency.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_consistency.py)
  * Detects cross-section contradictions in timelines, point of contact names, and company details.
* [`backend/app/services/proposal_consistency_enforcement.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_consistency_enforcement.py)
  * Enforces primary contact info, scrubbed schedule overruns, and methodology alignment.
* [`backend/app/services/proposal_manuscript_budget_contradictions.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_manuscript_budget_contradictions.py)
  * Cross-verifies dollar values in text against the canonical budget table.
* [`backend/app/services/proposal_manuscript_fact_contradictions.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_manuscript_fact_contradictions.py)
  * Cross-verifies facts across all draft sections for consistency.
* [`backend/app/services/proposal_scan_rfp_contradictions.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_scan_rfp_contradictions.py)
  * Scans draft text against explicit RFP instructions to prevent non-compliance.

### **Claim Scrubbing & Tag Cleaning**
* [`backend/app/services/proposal_cert_claim_scrub.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_cert_claim_scrub.py)
  * Scrubs unverified certification claims (e.g., ISO, MBE/WBE, SOC2) if absent from KB evidence.
* [`backend/app/services/proposal_rfp_optional_claim_scrub.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_rfp_optional_claim_scrub.py)
  * Strips optional claims (percent time commitments, named subcontractors) when RFP is silent.
* [`backend/app/services/proposal_verify_optional_scrub.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_verify_optional_scrub.py)
  * Removes unresolvable `[VERIFY: ...]` tags when details cannot be corroborated.

### **Budget & Financial Guardrails**
* [`backend/app/services/proposal_budget_validation.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_budget_validation.py)
  * Strict budget schema, rate floor, and math validator.
* [`backend/app/services/proposal_budget_floor.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_budget_floor.py)
  * Enforces minimum agency pricing floors.
* [`backend/app/services/proposal_scan_budget_check.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_scan_budget_check.py)
  * Validates budget line item totals against overall bid amounts.
* [`backend/app/services/commission_budget_sanitizer.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/commission_budget_sanitizer.py)
  * Sanitizes media buying commission calculations.

### **Section Isolation, Health & Quality**
* [`backend/app/services/proposal_section_health.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_section_health.py)
  * Health classifier for section completeness (`classifySectionHealth`).
* [`backend/app/services/proposal_overlap_detector.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_overlap_detector.py)
  * Prevents duplicate content across adjacent proposal sections.
* [`backend/app/services/proposal_voice_enforcement.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_voice_enforcement.py)
  * Audits passive tone and ensures active, agency-level brand voice.
* [`backend/app/services/proposal_section_isolation.py`](file:///Users/mahipatel/ZO-AGENCY/backend/app/services/proposal_section_isolation.py)
  * Prevents model tier leaks and cross-section context contamination.

---

## 3. Automated Guardrail Tests

The guardrail and prompt enforcement test suites are maintained under `backend/tests/`:
* [`backend/tests/test_zero_fabrication_guards.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_zero_fabrication_guards.py)
* [`backend/tests/test_proposal_integrity_guards.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_proposal_integrity_guards.py)
* [`backend/tests/test_proposal_t1_validators.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_proposal_t1_validators.py)
* [`backend/tests/test_proposal_t2_validators.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_proposal_t2_validators.py)
* [`backend/tests/test_proposal_adversarial_repair.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_proposal_adversarial_repair.py)
* [`backend/tests/test_proposal_consistency_enforcement.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_proposal_consistency_enforcement.py)
* [`backend/tests/test_verification_facts.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_verification_facts.py)
* [`backend/tests/test_cert_claim_scrub.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_cert_claim_scrub.py)
* [`backend/tests/test_verify_optional_scrub.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_verify_optional_scrub.py)
* [`backend/tests/test_reference_phone_evidence_guard.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_reference_phone_evidence_guard.py)
* [`backend/tests/test_capability_bio_grounding.py`](file:///Users/mahipatel/ZO-AGENCY/backend/tests/test_capability_bio_grounding.py)
