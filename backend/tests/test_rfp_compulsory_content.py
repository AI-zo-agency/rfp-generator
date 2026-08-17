"""RFP-compulsory content counts vs the manuscript."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_rfp_compulsory_content import (
    CompulsoryContentAsk,
    audit_compulsory_content,
    count_key_personnel_bios,
    count_reference_entries,
    count_usable_case_study_cards,
    merge_compulsory_gap_stubs,
)


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(rfpId="r1", updatedAt="2026-08-14T00:00:00Z", sections=list(sections))


class CompulsoryContentAuditTests(unittest.TestCase):
    def test_three_case_studies_required_two_present_is_shortfall(self) -> None:
        draft = _draft(
            ProposalSection(
                id="section-3-work-01-oregon",
                title="3.1 — Oregon Employment",
                content="Challenge\nOregon needed outreach.\n\nSolution / Our Approach\nWe built a campaign.",
            ),
            ProposalSection(
                id="section-3-work-02-deschutes",
                title="3.3 — Deschutes County",
                content="Challenge\nDeschutes needed a brand.\n\nSolution / Our Approach\nWe designed a system.",
            ),
        )
        self.assertEqual(count_usable_case_study_cards(draft), 2)
        shortfalls = audit_compulsory_content(
            draft,
            [
                CompulsoryContentAsk(
                    kind="case_studies",
                    minimum=3,
                    rfp_quote="Provide three examples of similar work",
                    pass_fail=True,
                )
            ],
        )
        self.assertEqual(len(shortfalls), 1)
        self.assertEqual(shortfalls[0].found, 2)
        self.assertIn("requires 3", shortfalls[0].message)
        self.assertIn("Qualification", shortfalls[0].message)

    def test_meeting_minimum_is_not_a_shortfall(self) -> None:
        draft = _draft(
            ProposalSection(
                id="section-3-work-01-a",
                title="3.1 — Study A",
                content="Challenge\nNeed.\n\nSolution / Our Approach\nWork.",
            ),
            ProposalSection(
                id="section-3-work-02-b",
                title="3.2 — Study B",
                content="Challenge\nNeed.\n\nSolution / Our Approach\nWork.",
            ),
            ProposalSection(
                id="section-3-work-03-c",
                title="3.3 — Study C",
                content="Challenge\nNeed.\n\nSolution / Our Approach\nWork.",
            ),
        )
        shortfalls = audit_compulsory_content(
            draft,
            [CompulsoryContentAsk(kind="case_studies", minimum=3, rfp_quote="three", pass_fail=True)],
        )
        self.assertEqual(shortfalls, [])

    def test_gap_stub_does_not_invent_a_third_study(self) -> None:
        draft = _draft(
            ProposalSection(
                id="section-3-work-01-oregon",
                title="3.1 — Oregon Employment",
                content="Challenge\nOregon needed outreach.\n\nSolution / Our Approach\nWe built a campaign.",
            ),
        )
        shortfalls = audit_compulsory_content(
            draft,
            [CompulsoryContentAsk(kind="case_studies", minimum=3, rfp_quote="three examples", pass_fail=True)],
        )
        updated, logs = merge_compulsory_gap_stubs(draft, shortfalls)
        self.assertTrue(logs)
        ids = [s.id for s in updated.sections]
        self.assertIn("rfp-compulsory-gap-case_studies", ids)
        gap = next(s for s in updated.sections if s.id == "rfp-compulsory-gap-case_studies")
        self.assertIn("MANUAL FILL", gap.content or "")
        self.assertNotIn("Challenge", gap.content or "")

    def test_reference_table_rows_count(self) -> None:
        draft = _draft(
            ProposalSection(
                id="rfp-closing-references",
                title="References",
                content=(
                    "| Client | Contact |\n"
                    "| --- | --- |\n"
                    "| Oregon Employment | Jane Doe |\n"
                    "| Deschutes County | John Smith |\n"
                ),
            )
        )
        self.assertEqual(count_reference_entries(draft), 2)

    def test_key_personnel_bios_count(self) -> None:
        draft = _draft(
            ProposalSection(
                id="section-2-bio-sonja-anderson",
                title="2.1 — Sonja Anderson",
                content="Sonja Anderson is Principal.",
            ),
            ProposalSection(
                id="section-2-bio-placeholder",
                title="Team",
                content="unused",
            ),
        )
        self.assertEqual(count_key_personnel_bios(draft), 1)


if __name__ == "__main__":
    unittest.main()
