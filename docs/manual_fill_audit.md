# MANUAL FILL Tag Audit (Step 1)

**Date:** 2026-07-23  
**Scope:** Inventory producers/consumers before chat protection work.  
**Conclusion (Step 1):** Architecture map claim is **confirmed** — MANUAL FILL had **no rewrite protection** in section chat at audit time.  
**Implemented:** See `docs/architecture/2026-07-23-manual-fill-chat-guard-report.md` (Steps 2–5).

---

## 1. Canonical tag format

**Primary regex** (`proposal_manual_flags.py`):

```python
MANUAL_FILL_TAG_RE = re.compile(r"\[MANUAL\s+FILL:[^\]]+\]", re.I)
```

**Canonical producer format** (from `convert_verify_tags_to_manual_fill` / `gaps_to_manual_fill_flags`):

```text
[MANUAL FILL: {Owner} — {field description}]
```

Examples:
- `[MANUAL FILL: Sonja — confirm before submission]`
- `[MANUAL FILL: Ella — NJ public-college reference contacts]`
- `[MANUAL FILL: attach COI naming HTA as additional insured]`

### Format variants found in producers (all still match `MANUAL_FILL_TAG_RE`)

| Variant | Example producer | Notes |
|---------|------------------|-------|
| Owner — field | `convert_verify_tags_to_manual_fill` | Canonical handoff |
| Instruction without owner | budget / insurance / closing package | e.g. `[MANUAL FILL: wet/digital signature]` |
| Bare / short | `proposal_budget_content.py` | `[MANUAL FILL]` and `[MANUAL FILL or N/A]` — **still match** because `:` is required by regex… |

**Important format caveat:**

`MANUAL_FILL_TAG_RE` requires a **colon** after `FILL`. These variants:

- `[MANUAL FILL]` (no colon) — used in `proposal_budget_content.py` Title row and truncation repair Title/Date rows  
- `[MANUAL FILL or N/A]` — Fax row in budget content  

**do NOT match** the current regex. They are still placeholder-like but invisible to `scan_tags_in_section` / `MANUAL_FILL_TAG_RE`. Step 2 should either:
- (A) broaden detection to also catch `[MANUAL FILL]` / `[MANUAL FILL …]` without requiring colon, or  
- (B) leave them alone and only protect colon-form tags (canonical handoff).

**Recommendation for this task:** Protect tags matching an expanded pattern that covers both colon and bare forms used in producers, without changing VERIFY.

**Shipped (Step 2):** Used `r"\[MANUAL\s+FILL[^\]]*\]"` (slightly broader than the audit’s `(?::[^\]]*)?` sketch) so `[MANUAL FILL or N/A]` is included. Step 5 confirmed bare tags are **load-bearing** on the chat rewrite path (budget/pricing sections), not precautionary-only.

---

## 2. Producers (where MANUAL FILL is introduced)

| File | How introduced |
|------|----------------|
| `proposal_manual_flags.py` `convert_verify_tags_to_manual_fill` | VERIFY → `[MANUAL FILL: {owner} — {field}]` at finalize |
| `proposal_manual_flags.py` `gaps_to_manual_fill_flags` / `apply_finalize_handoff_to_draft` | Appends handoff tags for compliance gaps |
| `proposal_submission_gap_finalizer.py` | Orchestrates finalize → MANUAL FILL handoff |
| `proposal_budget_content.py` | Pricing/questionnaire table stubs (`[MANUAL FILL: …]`, bare `[MANUAL FILL]`, `[MANUAL FILL or N/A]`) |
| `proposal_insurance_rfp_table.py` | COI attachment placeholders |
| `proposal_fulfill_truncation_repair.py` | CVC / Exhibit H signature table stubs |
| `proposal_fulfill_rfp_gaps.py` | Stub components: `[MANUAL FILL: complete {title} …]` |
| `proposal_fulfill_rfp_budget_kpi.py` | Budget worksheet attach note |
| `proposal_closing_package.py` | Prompt instructions telling writers to emit MANUAL FILL (signatures, attachments, leadership decisions) |
| `proposal_rfp_submission_requirements.py` | Vendor qualification stubs |
| `proposal_drafting_graph.py` | Prompt rule: use `[MANUAL FILL: leadership decision]` for reference gaps |
| `proposal_intelligence/agents/dynamic_section_planner.py` | Prompt note: forms may be checklist + MANUAL FILL |

Primary **pipeline finalize** path:  
`run_submission_gap_finalize_pass` → `apply_finalize_handoff_to_draft` / `convert_verify_tags_to_manual_fill`.

---

## 3. Consumers / readers today

| File | Handling |
|------|----------|
| `proposal_manual_flags.py` | Scan tags → UI `ManualFillFlag`; convert VERIFY→MANUAL FILL; **no rewrite protection** |
| `proposal_rfp_compliance.py` | `MANUAL_FILL_MARKER = "[MANUAL FILL"` — treats presence as handoff for gap satisfaction; prompts tell polish to emit MANUAL FILL |
| `proposal_presubmit_review.py` | Counts MANUAL FILL via placeholder regex as issues; attaches `manual_fill_flags` |
| `proposal_presubmit_autofix.py` | Counts `[MANUAL FILL` in placeholder tally; finalizer attaches flags |
| `proposal_fulfill_rfp_repairs.py` | Mentions "manual fill" in blob heuristics |
| `proposal_pipeline_checkpoint.py` | Comment only: leftover VERIFY/MANUAL FILL = handoff |
| **`proposal_section_editor.py`** | **No MANUAL FILL handling** — content with tags passed wholesale into rewrite prompts |
| **`proposal_chat_ops.py`** | **No MANUAL FILL handling** |
| **`proposal_chat_structure.py`** | Mentions VERIFY only in prompts/heuristics; **no MANUAL FILL** |
| **`proposal_hallucination_detector.py`** | **No MANUAL FILL handling** |

**Confirmed:** Architecture map claim is accurate — **no dedicated chat handler / rewrite guard for MANUAL FILL**.

---

## 4. Does rewrite see MANUAL FILL as plain text today?

**Yes.** Paths that send section/excerpt content into LLM prompts without filtering MANUAL FILL:

| Function | Prompt constant | Content fed |
|----------|-----------------|-------------|
| `_redraft_rfp_section` | `SECTION_REDRAFT_PROMPT` | Previous full section content |
| `_improve_static_section` | `STATIC_SECTION_REDRAFT_PROMPT` | Previous content |
| `_improve_section_selection` | `SELECTION_EDIT_PROMPT` | Excerpt (+ full section context) |

Those prompts mention VERIFY preservation/filling extensively but **never mention MANUAL FILL**. An unrelated ask (“tighten this paragraph”) can therefore cause the LLM to drop, paraphrase, or invent over `[MANUAL FILL: …]` spans.

VERIFY is partially protected after the fact for legal tags via `gate_section_legal_attestations`, and partially filled deterministically via `_replace_verify_tags_from_blob`. MANUAL FILL has neither chat-side pre-guard nor post-validate restore.

---

## 5. Implications for Steps 2–3

1. Add `extract_manual_fill_tags` (and optionally broaden regex for bare `[MANUAL FILL]` / `[MANUAL FILL or N/A]`).
2. Pre-rewrite: if tags present and message is not an explicit fill ask → enforce preserve + post-validate + one retry.
3. Explicit fill path: mirror `_is_in_place_kb_or_verify_edit` → detect MANUAL FILL fill asks; resolve from user text then KB; never invent; log source.
4. Do **not** change `_replace_verify_tags_from_blob` or legal VERIFY lock behavior.

---

## 6. Step 1 sign-off

Assumption corrected only on tag **format variants** (bare `[MANUAL FILL]` without colon). Protection claim stands: **MANUAL FILL currently has zero chat rewrite protection.**
