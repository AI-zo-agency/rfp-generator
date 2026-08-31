"""Pointer-page integrity: real § marks + EDITOR NOTES inserts."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_pointer_page_integrity import (
    apply_pointer_page_integrity,
    apply_pointer_page_integrity_to_draft,
    format_addressed_in_cell,
    resolve_addressed_in_target,
    rewrite_cross_ref_addressed_in_table,
    rewrite_prose_section_citations,
)


def _sec(sid: str, title: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content=content,
        wordTarget=400,
        status="generated",
    )


class PointerPageIntegrityTests(unittest.TestCase):
    def _draft(self) -> ProposalDraft:
        return ProposalDraft(
            rfpId="rfp-orland",
            sections=[
                _sec(
                    "tech",
                    "20. Technical Proposal",
                    (
                        "| RFP Requirement | Addressed In |\n"
                        "| --- | --- |\n"
                        "| Experience, municipal, tourism, economic development | "
                        "Section 3 (Experience, Municipal, Tourism & Economic Development Marketing) |\n"
                        "| Operating History, company background and capability | "
                        "Section 1.1 (Who We Are) and Section 1.3 (Business Information) |\n"
                        "| Qualifications, assigned personnel, abilities, licenses | "
                        "Section 2 (Qualifications of Assigned Personnel) |\n"
                        "| Proposed Fee, scope of work pricing sheet | Section 6 (Proposed Fee) |\n"
                        "| Proposed Approach and Work Plan | "
                        "Section 4 (Proposed Approach, Work Plan & Project Timeline) |\n"
                        "| Portfolio Samples | Section 5 (Portfolio Samples) |\n"
                        "\n"
                        "### EDITOR NOTES, INSERTS REQUIRED IN OTHER SECTIONS BEFORE SUBMISSION\n\n"
                        "The following two items were extracted from the prior Technical Proposal draft.\n\n"
                        "**INSERT INTO §21 (Experience, Municipal, Tourism & Economic Development Marketing), "
                        "add these two rows to the case study table:**\n\n"
                        "| Client | Engagement Type | Scope Summary |\n"
                        "| --- | --- | --- |\n"
                        "| City of Medford, Oregon | Municipal brand identity | Rogue X brand. |\n"
                        "| City of Santa Clara, California | Municipal PR | Stadium authority. |\n\n"
                        "**INSERT INTO §22 (Qualifications of Assigned Personnel), "
                        "replace the blank Digital Media Strategist row with:**\n\n"
                        "| Name | Role | Relevant Credential |\n"
                        "| --- | --- | --- |\n"
                        "| Letitia Hopper | Digital Media Strategist | Measurement framework |\n"
                    ),
                ),
                _sec(
                    "who",
                    "1.1 — Who We Are",
                    "Agency background founded 2013.",
                ),
                _sec(
                    "biz",
                    "1.3 — Business Information",
                    "Legal name and FEIN.",
                ),
                _sec(
                    "cs",
                    "3.1 — City of Umatilla Digital Campaign",
                    "Case study body.",
                ),
                _sec(
                    "exp-21",
                    "21. Experience — Municipal, Tourism & Economic Development Marketing",
                    (
                        "## Experience\n\n"
                        "| Client | Engagement Type | Scope Summary |\n"
                        "| --- | --- | --- |\n"
                        "| Deschutes County | Brand refresh | Logo modernization. |\n"
                    ),
                ),
                _sec(
                    "qual-22",
                    "22. Qualifications of Assigned Personnel",
                    (
                        "| Name | Role | Relevant Credential |\n"
                        "| --- | --- | --- |\n"
                        "| Sonja Anderson | Executive Sponsor | 25 years |\n"
                        "|  | Digital Media Strategist |  |\n"
                    ),
                ),
                _sec(
                    "approach-23",
                    "23. Proposed Approach, Work Plan & Project Timeline",
                    "Phases and cadence.",
                ),
                _sec(
                    "port-25",
                    "25. Portfolio Samples",
                    "Selected work.",
                ),
                _sec(
                    "fee-26",
                    "26. Fee Proposal",
                    "Year 1 investment.",
                ),
            ],
            updatedAt="2026-08-26T00:00:00Z",
        )

    def test_resolve_experience_to_section_21_not_case_study_3(self) -> None:
        draft = self._draft()
        entry = resolve_addressed_in_target(
            draft,
            "Experience, municipal, tourism, economic development",
            self_section_id="tech",
        )
        assert entry is not None
        self.assertEqual(entry.mark, "21")
        self.assertIn("§21", format_addressed_in_cell(entry))

    def test_rewrite_table_uses_live_marks(self) -> None:
        draft = self._draft()
        tech = draft.sections[0]
        rewritten, n, unresolved = rewrite_cross_ref_addressed_in_table(
            tech.content or "", draft, self_section_id="tech"
        )
        self.assertGreaterEqual(n, 4)
        self.assertIn("§21 (", rewritten)
        self.assertIn("§22 (", rewritten)
        self.assertIn("§23 (", rewritten)
        self.assertIn("§25 (", rewritten)
        self.assertIn("§26 (", rewritten)
        self.assertNotIn("Section 3 (", rewritten)
        self.assertNotIn("Section 6 (", rewritten)
        self.assertEqual(unresolved, [])

    def test_prose_section_citation_remaps_wrong_number(self) -> None:
        draft = self._draft()
        body = (
            "Details live in Section 3 (Experience, Municipal, Tourism & "
            "Economic Development Marketing). Fees are in See Section 6 "
            "(Proposed Fee)."
        )
        rewritten, n, unresolved = rewrite_prose_section_citations(
            body, draft, self_section_id="tech"
        )
        self.assertGreaterEqual(n, 2)
        self.assertIn("§21 (", rewritten)
        self.assertIn("§26 (", rewritten)
        self.assertNotIn("Section 3 (", rewritten)
        self.assertNotIn("Section 6 (", rewritten)
        self.assertEqual(unresolved, [])

    def test_draft_wide_integrity_rewrites_see_bold_titles(self) -> None:
        draft = self._draft()
        cover = draft.sections[0].model_copy(
            update={
                "content": (
                    "Overview only.\n\n"
                    "See **21. Experience — Municipal, Tourism & Economic "
                    "Development Marketing** for this narrative "
                    "(already covered there — not restated here).\n"
                )
            }
        )
        # No Addressed-In table — prose sweep alone must still run.
        sections = [cover] + list(draft.sections[1:])
        draft = draft.model_copy(update={"sections": sections})
        out, logs = apply_pointer_page_integrity_to_draft(draft)
        body = next(s for s in out.sections if s.id == "tech").content or ""
        self.assertIn("See **§21 (", body)
        self.assertTrue(any("prose cross-reference" in line for line in logs))

    def test_unapplied_editor_notes_become_manual_fill(self) -> None:
        """Missing § target must not silently discard insert content."""
        draft = ProposalDraft(
            rfpId="rfp-orphan-insert",
            sections=[
                _sec(
                    "tech",
                    "20. Technical Proposal",
                    (
                        "| RFP Requirement | Addressed In |\n"
                        "| --- | --- |\n"
                        "| Experience | Section 3 |\n\n"
                        "### EDITOR NOTES\n\n"
                        "**INSERT INTO §99 (Missing Tab), add rows:**\n\n"
                        "| Client | Scope |\n"
                        "| --- | --- |\n"
                        "| City of Nowhere | Brand work. |\n"
                    ),
                ),
                _sec("exp-21", "21. Experience", "Body."),
            ],
            updatedAt="2026-08-26T00:00:00Z",
        )
        out, logs = apply_pointer_page_integrity(draft, source_section_id="tech")
        tech = next(s for s in out.sections if s.id == "tech")
        body = tech.content or ""
        self.assertNotRegex(body, r"(?i)#{1,4}\s*EDITOR\s+NOTES?")
        self.assertNotRegex(body, r"(?i)\*\*INSERT\s+INTO")
        self.assertIn("[MANUAL FILL:", body)
        self.assertIn("§99", body)
        self.assertIn("City of Nowhere", body)
        self.assertTrue(any("MANUAL FILL" in line for line in logs))

    def test_apply_integrity_inserts_and_strips_editor_notes(self) -> None:
        draft = self._draft()
        out, logs = apply_pointer_page_integrity(draft, source_section_id="tech")
        tech = next(s for s in out.sections if s.id == "tech")
        exp = next(s for s in out.sections if s.id == "exp-21")
        qual = next(s for s in out.sections if s.id == "qual-22")
        body = tech.content or ""
        self.assertNotIn("EDITOR NOTES", body.upper())
        self.assertNotIn("INSERT INTO", body.upper())
        self.assertIn("§21 (", body)
        self.assertIn("Medford", exp.content or "")
        self.assertIn("Santa Clara", exp.content or "")
        self.assertIn("Letitia Hopper", qual.content or "")
        self.assertTrue(any("remapped" in line for line in logs))
        self.assertTrue(any("applied insert" in line for line in logs))
        self.assertTrue(any("stripped" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
