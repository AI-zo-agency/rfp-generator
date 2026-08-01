"""MANUAL FILL tags must not mid-sentence truncate finding prose."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_adversarial_repair import _append_manual_fill


class ManualFillTagTests(unittest.TestCase):
    def test_uses_finding_code_not_raw_slice(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Pricing",
                    content="Fee table here.",
                    status="generated",
                )
            ],
        )
        long_issue = (
            "Manuscript states professional service fees are $141,400, but canonical "
            "agency fee subtotal is $111,400 which includes pass-through detail"
        )
        updated, tag = _append_manual_fill(
            draft,
            section_id="s1",
            issue=long_issue,
            finding_code="deterministic.consistency.fee_mismatch",
        )
        self.assertIsNotNone(tag)
        assert tag is not None
        self.assertIn("deterministic.consistency.fee_mismatch", tag)
        # Must not end mid-word like recon[cile] from a hard [:100] cut of prose alone.
        self.assertFalse(tag.rstrip("]").endswith("recon"))
        self.assertIn(tag, updated.sections[0].content or "")


if __name__ == "__main__":
    unittest.main()
