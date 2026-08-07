"""Ralph — RFP fidelity (draft-time page budget + anti-invention; no hard chops)."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_ralph import (
    apply_ralph_to_draft,
    ralph_document_word_budget,
    ralph_non_draft_reserve_fraction,
    strip_invented_asset_promises,
)


def _section(sid: str, content: str, *, word_target: int | None = None) -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=sid,
        content=content,
        wordTarget=word_target,
    )


class RalphFidelityTests(unittest.TestCase):
    def test_short_rfp_holds_larger_reserve(self) -> None:
        self.assertGreater(
            ralph_non_draft_reserve_fraction(12),
            ralph_non_draft_reserve_fraction(30),
        )

    def test_twelve_page_budget_is_under_hard_cap(self) -> None:
        budget = ralph_document_word_budget(12)
        assert budget is not None
        self.assertLessEqual(budget, int(12 * 350 * 0.92))
        self.assertGreater(budget, 2000)

    def test_strips_invented_designer_diagram_notes(self) -> None:
        body = (
            "We will report monthly.\n\n"
            "[DESIGNER NOTE: At a Glance timeline — horizontal milestone graphic]\n\n"
            "Next steps follow."
        )
        cleaned, logs = strip_invented_asset_promises(body)
        self.assertNotIn("DESIGNER NOTE", cleaned)
        self.assertTrue(any("designer-note" in x for x in logs))
        self.assertIn("report monthly", cleaned)

    def test_replaces_fake_attached_diagram_claims(self) -> None:
        body = "Please see the attached reporting dashboard diagram for KPIs."
        cleaned, logs = strip_invented_asset_promises(body)
        self.assertIn("[VERIFY:", cleaned)
        self.assertTrue(logs)

    def test_apply_ralph_does_not_hard_chop_whole_manuscript_to_page_budget(self) -> None:
        # Even if total words exceed a 12-page advisory budget, Ralph must NOT
        # proportionally chop the whole doc — page fit is draft-time allocation.
        fat = "word " * 400  # under wordTarget×1.25 when wt=400
        draft = ProposalDraft(
            rfpId="rfp-ralph",
            sections=[_section(f"s-{i}", fat, word_target=400) for i in range(10)],
            updatedAt="2026-01-01T00:00:00Z",
        )
        before = sum(len((s.content or "").split()) for s in draft.sections)
        updated, logs = apply_ralph_to_draft(
            draft,
            page_limit=12,
            rfp_text="The proposal is limited to twelve (12) pages.",
        )
        after = sum(len((s.content or "").split()) for s in updated.sections)
        self.assertEqual(after, before)
        self.assertFalse(any("page-budget" in x for x in logs))

    def test_apply_ralph_trims_section_that_overshoots_word_target(self) -> None:
        # 800 words vs wordTarget 400 → soft ceiling 500 (×1.25)
        overshoot = "word " * 800
        draft = ProposalDraft(
            rfpId="rfp-ralph",
            sections=[_section("approach", overshoot, word_target=400)],
            updatedAt="2026-01-01T00:00:00Z",
        )
        updated, logs = apply_ralph_to_draft(draft, page_limit=12)
        after = len((updated.sections[0].content or "").split())
        self.assertLessEqual(after, 500)
        self.assertTrue(any("trim-overshoot" in x for x in logs))


if __name__ == "__main__":
    unittest.main()
