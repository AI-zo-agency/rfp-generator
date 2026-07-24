# MANUAL FILL Chat Guard — Step 5 Report

**Date:** 2026-07-23  
**Task:** Protect `[MANUAL FILL…]` tags in section chat (`improve_proposal_section`).  
**Decision input:** Expand regex to cover bare + colon forms (post–Step 1).

---

## 1. Step 1 confirmation

Step 1 audit (`docs/manual_fill_audit.md`) was **correct**:

- MANUAL FILL had **no** chat rewrite guard (mask / preserve / retry / explicit fill).
- VERIFY already had deterministic fill + legal re-lock; MANUAL FILL did not.
- Format caveat: producers emit bare `[MANUAL FILL]` and `[MANUAL FILL or N/A]` in `proposal_budget_content.py`, which the old colon-required regex missed.

Assumption corrected only on **tag format variants**; the “zero chat protection” claim stood.

---

## 2. What was added

### Detection + helpers — `proposal_manual_flags.py`

- Expanded `MANUAL_FILL_TAG_RE` to:

  ```python
  r"\[MANUAL\s+FILL[^\]]*\]"
  ```

  Slightly broader than the audit’s `(?::[^\]]*)?` proposal so `[MANUAL FILL or N/A]` is included (that form is not colon-only).

- New helpers: `extract_manual_fill_tags`, `is_manual_fill_request`, `mask_manual_fill_tags`, `unmask_manual_fill_tags`, `missing_manual_fill_placeholders`, `manual_fill_tags_preserved`, `fill_manual_fill_tags` (user message first, then KB; never invent; log source).

### Rewrite guard — `proposal_section_editor.py`

- Pre-mask → LLM → post-unmask with deterministic validate; **one retry**; then **422** if tags still dropped.
- Wired into excerpt edit, full RFP redraft, and static improve.
- Explicit fill exit when `is_manual_fill_request`: resolve from user/KB only; if nothing matches, reply with gap and **do not** fall through to general rewrite.
- Fill path inserts resolved values **as-is** (no `enforce_narrative_voice` / brand-tone pass).

### Structure routing — `proposal_chat_structure.py`

- `_is_in_place_manual_fill_edit` (parallel to VERIFY/KB in-place heuristic).
- Coercion keeps MANUAL FILL fill asks on in-place edit (not a new sidebar tab).
- VERIFY helpers left intact.

### Tests — `backend/test_manual_fill_chat_guard.py`

Covers regex variants, mask roundtrip, user/KB fill, gap leave-tag, unrelated rewrite preserve, drop→retry→422.

---

## 3. Test results

```text
.venv/bin/python -m unittest test_manual_fill_chat_guard -v
Ran 12 tests in ~0.02s — OK
```

---

## 4. Bare-form reachability (load-bearing vs precautionary)

**Verdict: load-bearing — bare tags are reachable from chat rewrite.**

| Fact | Evidence |
|------|----------|
| Where bare tags live | `proposal_budget_content.py` pricing/questionnaire stubs: Title → `[MANUAL FILL]`, Fax → `[MANUAL FILL or N/A]` (also colon-form siblings in the same tables). |
| How they enter the draft | Budget content is written into chat-editable sections (e.g. `section-budget-pricing`, source `generated`, plus any existing budget/pricing section updated in place). |
| Chat path | Writers can open that section and send an unrelated revise (“tighten…”) through `improve_proposal_section` → `_redraft_rfp_section` (or selection edit), which feeds **full section content** into the rewrite prompt. |
| Why broadening matters | Without the expanded regex, bare tags would not be masked/validated and could still be eaten by an incidental rewrite — the exact miss Step 1 caught. |

So the expanded pattern is not precautionary-only; it protects placeholders that already sit on the same rewrite surfaces as colon-form tags.

Truncation-repair Title/Date bare stubs (also noted in Step 1) follow the same rule wherever that markdown lands in a section the UI can chat-edit.

---

## 5. Producer format handling

| Producer variant | Handled? |
|------------------|----------|
| `[MANUAL FILL: Owner — field]` | Yes |
| `[MANUAL FILL: instruction…]` | Yes |
| `[MANUAL FILL]` | Yes (expanded regex) |
| `[MANUAL FILL or N/A]` | Yes (expanded regex) |

No producer was left on the old colon-only pattern.

---

## 6. Explicit non-goals (confirmed untouched)

- **VERIFY** fill (`_replace_verify_tags_from_blob`) and legal attestation gate internals — not redesigned.
- **Model tiering / cost Part 2** — not started.
- **Advisory quality**, case-study swap, orphan `SectionEditChat.tsx` — not in this change set.
- Ops / Structure / Advisory exits’ unrelated behavior — only MANUAL FILL detection/coercion added beside VERIFY; no advisory prompt rewrite, no ops changes.

---

## 7. Prompt compliance fix (Step 3 brand-voice rule)

Step 3 requires resolved MANUAL FILL values inserted as-is with **no** brand-voice/tone pass. An earlier draft of the fill exit called `enforce_narrative_voice` after substitution; that call is removed. The user-fill test now asserts exact content (`"Role: Director of Marketing"`) and fails if voice enforcement is invoked.

---

## 8. Follow-ups (out of this task)

1. Advisory quality pass (next in ranked sequence).
2. Chat cost `run_id` per turn + shadow light tiers for advisory/structure-plan only.
3. Optional: ensure LangChain `redraft_section_agent` system prompt also cites `«MFILL_N»` preserve (user-block mask + post-validate already cover drops today).
