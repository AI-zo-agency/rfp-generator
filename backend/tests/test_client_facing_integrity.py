"""Client-facing integrity: roles, staffing history, truncated pointers, dupes."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_client_facing_integrity import (
    align_staff_table_roles_to_roster,
    apply_client_facing_integrity_to_draft,
    collapse_duplicate_adjacent_table_rows,
    collapse_repeated_adjacent_sentences,
    repair_truncated_covered_pointers,
    scrub_client_facing_staffing_history,
    scrub_generalist_prompt_bleed,
)


class StaffRoleAlignmentTests(unittest.TestCase):
    def test_rewrites_wrong_staff_assigned_roles(self) -> None:
        roles = {
            "haley neff": "Account Manager",
            "timi oyewunmi": "Executive Assistant",
            "rachel rice": "Development Coordinator",
            "oyetola oyewunmi": "Operations Coordinator",
        }
        body = (
            "## Staff Assigned to Account\n\n"
            "| Name | Role |\n"
            "| --- | --- |\n"
            "| Oyetola Oyewunmi | Account Manager |\n"
            "| Timi Oyewunmi | Development Coordinator |\n"
            "| Haley Neff | Account Manager |\n"
            "| Rachel Rice | Development Coordinator |\n"
        )
        out, logs = align_staff_table_roles_to_roster(body, roles)
        self.assertIn("| Oyetola Oyewunmi | Operations Coordinator |", out)
        self.assertIn("| Timi Oyewunmi | Executive Assistant |", out)
        self.assertIn("| Haley Neff | Account Manager |", out)
        self.assertTrue(any("oyetola" in x.casefold() for x in logs))


class StaffingHistoryScrubTests(unittest.TestCase):
    def test_drops_retirement_explanation(self) -> None:
        text = (
            "Haley Neff is Account Manager for this engagement. "
            "[blank], formerly our Senior Account Manager, has retired. "
            "Haley now carries his accounts along with her own and leads day-to-day work. "
            "She partners with the creative team on campaign delivery."
        )
        out, logs = scrub_client_facing_staffing_history(text)
        self.assertTrue(logs)
        self.assertNotIn("retired", out.casefold())
        self.assertNotIn("formerly our", out.casefold())
        self.assertNotIn("[blank]", out.casefold())
        self.assertIn("Haley Neff is Account Manager", out)
        self.assertIn("partners with the creative team", out)


class FormatEchoTests(unittest.TestCase):
    def test_repairs_truncated_covered_pointer(self) -> None:
        text = "See **Creative** for this narrative (already covered there."
        out, logs = repair_truncated_covered_pointers(text)
        self.assertTrue(logs)
        self.assertIn("already covered there — not restated here).", out)
        self.assertNotIn("(already covered there.", out)

    def test_scrubs_generalist_prompt_bleed(self) -> None:
        text = (
            "each named person carries a standing specialization "
            "(creative direction, brand strategy, digital media buying, or production)"
            "generalist."
        )
        out, logs = scrub_generalist_prompt_bleed(text)
        self.assertTrue(logs)
        self.assertIn("or production).", out)
        self.assertNotIn("generalist", out.casefold())

    def test_collapses_duplicate_table_rows(self) -> None:
        body = (
            "| Deliverable | Detail |\n"
            "| --- | --- |\n"
            "| Content Creation | Monthly social media content package (16 posts) |\n"
            "| Content Creation | Monthly social media content package (16 posts) |\n"
            "| Strategy | Quarterly planning |\n"
        )
        out, logs = collapse_duplicate_adjacent_table_rows(body)
        self.assertTrue(logs)
        self.assertEqual(out.casefold().count("monthly social media content package"), 1)

    def test_collapses_repeated_sentences_in_cell(self) -> None:
        cell = (
            "Monthly social media content package (16 posts). "
            "Monthly blog package (4 posts). "
            "Monthly social media content package (16 posts). "
            "Monthly blog package (4 posts)."
        )
        out, logs = collapse_repeated_adjacent_sentences(cell)
        self.assertTrue(logs)
        self.assertEqual(out.casefold().count("monthly social media"), 1)
        self.assertEqual(out.casefold().count("monthly blog package"), 1)


class DraftWireTests(unittest.TestCase):
    def test_draft_pass_fixes_roles_and_history(self) -> None:
        draft = ProposalDraft(
            rfpId="r",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="section-1-org-structure",
                    title="1.2 — Organizational Structure",
                    content=(
                        "**Haley Neff** Account Manager\n"
                        "**Timi Oyewunmi** Executive Assistant\n"
                        "**Rachel Rice** Development Coordinator\n"
                        "**Oyetola Oyewunmi** Operations Coordinator\n"
                    ),
                    status="generated",
                ),
                ProposalSection(
                    id="section-2-bio-haley-neff",
                    title="2.3 — Haley Neff",
                    content="**Role on this engagement:** Account Manager\n",
                    status="generated",
                ),
                ProposalSection(
                    id="section-2-bio-timi",
                    title="2.4 — Timi Oyewunmi",
                    content=(
                        "**Role on this engagement:** Executive Assistant\n\n"
                        "[blank], formerly our Senior Account Manager, has retired. "
                        "Haley now carries his accounts along with her own."
                    ),
                    status="generated",
                ),
                ProposalSection(
                    id="rfp-staff-assigned",
                    title="20. Staff Assigned to Account",
                    content=(
                        "| Name | Role |\n"
                        "| --- | --- |\n"
                        "| Oyetola Oyewunmi | Account Manager |\n"
                        "| Timi Oyewunmi | Development Coordinator |\n"
                    ),
                    status="generated",
                ),
                ProposalSection(
                    id="rfp-creative-pointer",
                    title="Creative",
                    content="See **Approach** for this narrative (already covered there.",
                    status="generated",
                ),
            ],
        )
        fixed, logs = apply_client_facing_integrity_to_draft(draft)
        self.assertTrue(logs)
        staff = next(s for s in fixed.sections if s.id == "rfp-staff-assigned")
        self.assertIn("Operations Coordinator", staff.content or "")
        self.assertIn("Executive Assistant", staff.content or "")
        self.assertNotIn("| Oyetola Oyewunmi | Account Manager |", staff.content or "")
        bio = next(s for s in fixed.sections if s.id == "section-2-bio-timi")
        self.assertNotIn("retired", (bio.content or "").casefold())
        creative = next(s for s in fixed.sections if s.id == "rfp-creative-pointer")
        self.assertIn("not restated here", creative.content or "")


if __name__ == "__main__":
    unittest.main()
