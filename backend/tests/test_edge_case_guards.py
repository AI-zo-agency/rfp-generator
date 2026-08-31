"""Edge-case guards: bio-as-RFP cites, blank names, county/city, hollow refs."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_edge_case_guards import (
    apply_edge_case_guards_to_draft,
    scrub_bio_marks_used_as_rfp_cites,
    scrub_blank_name_before_will,
    scrub_county_city_manager_mismatch,
)
from app.services.proposal_scan_fact_repairs import scrub_leaked_system_fragments


def _sec(sid: str, title: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content=content,
        wordTarget=200,
        status="generated",
    )


class EdgeCaseGuardTests(unittest.TestCase):
    def test_bio_mark_as_rfp_cite_becomes_verify(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-nm",
            sections=[
                _sec("bio-sonja", "2.1 — Sonja Anderson", "Bio body."),
                _sec("bio-letitia", "2.8 — Letitia Hopper", "Bio body."),
                _sec(
                    "bond",
                    "Proposal Bond",
                    (
                        "We confirm compliance with proposal guarantee requirements "
                        "outlined in RFP §2.1 (Sonja Anderson) and §2.8 (Letitia Hopper)(C)."
                    ),
                ),
                _sec(
                    "gifts",
                    "Gifts Policy",
                    "In full compliance with the Cone of Silence provisions (§2.3 (Vivek Patel)).",
                ),
                _sec("bio-vivek", "2.3 — Vivek Patel", "Bio body."),
            ],
            updatedAt="2026-08-26T00:00:00Z",
        )
        out, logs = apply_edge_case_guards_to_draft(draft)
        bond = next(s for s in out.sections if s.id == "bond").content or ""
        gifts = next(s for s in out.sections if s.id == "gifts").content or ""
        self.assertIn("[VERIFY:", bond)
        self.assertNotIn("RFP §2.1 (Sonja Anderson)", bond)
        self.assertIn("[VERIFY:", gifts)
        self.assertNotIn("provisions (§2.3 (Vivek Patel))", gifts)
        self.assertTrue(logs)

    def test_resume_tab_keeps_bio_pointers(self) -> None:
        """Resume / key-personnel tabs must keep §2.N bio cross-refs — not VERIFY."""
        draft = ProposalDraft(
            rfpId="rfp-resume",
            sections=[
                _sec("section-2-bio-sonja", "2.1 — Sonja Anderson", "Bio body."),
                _sec("section-2-bio-todd", "2.2 — Todd Anderson", "Bio body."),
                _sec(
                    "resumes",
                    "Resumes of Key Personnel",
                    (
                        "Resumes for key personnel are provided in Section 2:\n"
                        "- See §2.1 (Sonja Anderson) for this narrative.\n"
                        "- See §2.2 (Todd Anderson) for this narrative.\n"
                    ),
                ),
            ],
            updatedAt="2026-08-26T00:00:00Z",
        )
        out, logs = apply_edge_case_guards_to_draft(draft)
        body = next(s for s in out.sections if s.id == "resumes").content or ""
        self.assertIn("§2.1 (Sonja Anderson)", body)
        self.assertIn("§2.2 (Todd Anderson)", body)
        self.assertNotIn("incorrectly substituted", body)
        self.assertFalse(any("resumes" in x.casefold() and "bio mark" in x.casefold() for x in logs))

    def test_blank_name_before_will(self) -> None:
        body = (
            "Todd Anderson will lead strategy. , will ensure resource allocation "
            "and schedule adherence. , will execute technical implementation."
        )
        updated, logs = scrub_blank_name_before_will(body)
        self.assertTrue(logs)
        self.assertIn("[MANUAL FILL: name] will ensure", updated)
        self.assertIn("[MANUAL FILL: name] will execute", updated)

    def test_county_city_manager_rewrite(self) -> None:
        body = (
            "We established Maricopa County's brand strategy as a comprehensive "
            "PR and brand partner, working directly with the city manager and "
            "communications department on a multi-year contract."
        )
        updated, logs = scrub_county_city_manager_mismatch(body)
        self.assertTrue(logs)
        self.assertIn("county leadership", updated.casefold())
        self.assertNotIn("city manager", updated.casefold())

    def test_hollow_references_get_manual_fill(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-nm",
            sections=[
                _sec("refs", "5.6 References", "To be provided.\n"),
            ],
            updatedAt="2026-08-26T00:00:00Z",
        )
        out, logs = apply_edge_case_guards_to_draft(draft)
        body = out.sections[0].content or ""
        self.assertIn("[MANUAL FILL:", body)
        self.assertTrue(logs)

    def test_leaked_confirm_whether_fragment(self) -> None:
        body = (
            "Budget workbook attached.\n"
            ".... Confirm whether budget file requires separate upload or "
            "inclusion in proposal packet.]\n"
        )
        cleaned, logs = scrub_leaked_system_fragments(body)
        self.assertTrue(logs)
        self.assertNotIn("Confirm whether", cleaned)
        self.assertIn("Budget workbook", cleaned)


if __name__ == "__main__":
    unittest.main()
