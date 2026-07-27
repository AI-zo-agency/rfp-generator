# Optional VERIFY scrub — implementation plan

**Spec:** `docs/superpowers/specs/2026-07-27-optional-verify-scrub-design.md`

## Files

| File | Role |
|------|------|
| `backend/app/services/proposal_verify_optional_scrub.py` | Shared scrubber + chat intent |
| `backend/test_verify_optional_scrub.py` | Unit tests |
| `backend/app/services/proposal_submission_gap_finalizer.py` | Call scrub after KB fills |
| `backend/app/services/proposal_section_editor.py` | Chat early-path for remove VERIFY |
| `frontend/.../ProposalSectionChatPanel.tsx` | Quick prompt |

## Tasks

1. Tests for intent detect + scrub report shape (mock LLM)
2. Implement scrubber module
3. Wire finalize + chat; fix fill-intent so remove doesn't fill
4. Add chat quick prompt
