"""Edge-case guards: bio-as-RFP cites, blank names, county/city, hollow refs."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_edge_case_guards import (
    apply_edge_case_guards_to_draft,
    scrub_bio_marks_used_as_rfp_cites,
    scrub_blank_name_before_will,
    scrub_county_city_manager_mismatch,
    scrub_empty_list_items,
    scrub_gap_narration_prose,
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

    def test_empty_numbered_list_item_is_removed_and_renumbered(self) -> None:
        body = (
            "The undersigned declares that:\n\n"
            "1. This proposal is submitted in good faith.\n"
            "2.\n"
            "3. Zö agency has not sought to fix any proposal price.\n"
            "4. All statements contained in this proposal are true.\n"
        )
        cleaned, logs = scrub_empty_list_items(body)
        self.assertTrue(logs)
        self.assertNotRegex(cleaned, r"(?m)^2\.\s*$")
        self.assertIn("1. This proposal is submitted in good faith.", cleaned)
        self.assertIn("2. Zö agency has not sought to fix any proposal price.", cleaned)
        self.assertIn("3. All statements contained in this proposal are true.", cleaned)
        draft = ProposalDraft(
            rfpId="rfp-alameda",
            sections=[
                _sec("non-collusion", "Completed Non-Collusion Declaration", body),
            ],
            updatedAt="2026-09-09T00:00:00Z",
        )
        out, out_logs = apply_edge_case_guards_to_draft(draft)
        self.assertTrue(any("empty list" in log for log in out_logs))
        self.assertIn(
            "2. Zö agency has not sought to fix any proposal price.",
            out.sections[0].content or "",
        )

    def test_gap_narration_references_prose_becomes_manual_fill_only(self) -> None:
        body = (
            "We can't complete a reference table with verified contact names, titles, "
            "organizations, phone numbers, and emails from our knowledge base for this "
            "response. [MANUAL FILL: Sonja, pull reference contacts.]\n\n"
            "What we can stand behind is the operating pattern those references would "
            "confirm. Section D names the City of Umatilla engagement.\n\n"
            "That pattern is the actual evidence of past performance here. A reference "
            "call would tell you we showed up on schedule.\n\n"
            "We'll supply complete, verifiable reference contacts as part of finalizing "
            "this submission.\n"
        )
        cleaned, logs = scrub_gap_narration_prose(
            body, title="References and past performance"
        )
        self.assertTrue(logs)
        self.assertNotIn("can't complete", cleaned.casefold())
        self.assertNotIn("what we can stand behind", cleaned.casefold())
        self.assertNotIn("reference call would tell you", cleaned.casefold())
        self.assertNotIn("as part of finalizing", cleaned.casefold())
        self.assertIn("[MANUAL FILL", cleaned)
        draft = ProposalDraft(
            rfpId="rfp-alameda",
            sections=[
                _sec("refs", "References and past performance", body),
            ],
            updatedAt="2026-09-09T00:00:00Z",
        )
        out, out_logs = apply_edge_case_guards_to_draft(draft)
        self.assertTrue(any("gap-narration" in log for log in out_logs))
        self.assertNotIn("can't complete", (out.sections[0].content or "").casefold())

    def test_hollow_references_tab_gets_engagement_table(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-alameda",
            sections=[
                _sec(
                    "section-3-umatilla",
                    "3.1 — City of Umatilla Digital Campaign",
                    "Case study body.",
                ),
                _sec(
                    "section-3-maricopa",
                    "3.2 — Maricopa County Brand Video",
                    "Case study body.",
                ),
                _sec(
                    "d-refs",
                    "D. Relevant Experience and References",
                    (
                        "| Engagement | Scope | Relevance | Reference Contact | Contact Info |\n"
                        "|---|---|---|---|---|\n"
                        "| City of Umatilla | Digital campaign | Municipal cadence | "
                        "[MANUAL FILL: Sonja] | [MANUAL FILL: Sonja] |\n"
                    ),
                ),
                _sec(
                    "eval-refs",
                    "References and past performance",
                    "[MANUAL FILL: Sonja, pull reference contacts before submission.]",
                ),
            ],
            updatedAt="2026-09-09T00:00:00Z",
        )
        out, logs = apply_edge_case_guards_to_draft(draft)
        self.assertTrue(any("rebuilt References contact table" in log for log in logs))
        body = out.sections[-1].content or ""
        self.assertIn("| Engagement |", body)
        self.assertIn("City of Umatilla", body)
        self.assertIn("[MANUAL FILL", body)
        self.assertGreater(len(body.split()), 40)

    def test_list_shaped_references_rebuild_to_column_table(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-refs",
            sections=[
                _sec(
                    "refs",
                    "References",
                    (
                        "Single 4-column table, one column per reference; "
                        "no additional layout needed.\n\n"
                        "1. City of Umatilla, Oregon, Rock the Locks\n"
                        "**Contact:**\n\n"
                        "1. Deschutes County, Oregon, Brand Identity\n"
                        "2. City of Santa Clara, California, Campaign\n\n"
                        "These references reflect comparable municipal work.\n"
                    ),
                ),
            ],
            updatedAt="2026-09-10T00:00:00Z",
        )
        out, logs = apply_edge_case_guards_to_draft(draft)
        self.assertTrue(any("rebuilt References" in log for log in logs))
        body = out.sections[0].content or ""
        self.assertIn("| Field |", body)
        self.assertIn("Reference 1", body)
        self.assertIn("City of Umatilla", body)
        self.assertIn("Deschutes County", body)
        self.assertNotIn("no additional layout needed", body.casefold())
        self.assertNotRegex(body, r"(?m)^\*\*Contact:\*\*\s*$")
        self.assertNotRegex(body, r"(?m)^1\.\s+City of Umatilla")


if __name__ == "__main__":
    unittest.main()
