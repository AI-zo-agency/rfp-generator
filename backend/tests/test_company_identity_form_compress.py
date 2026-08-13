"""RFP Offeror/Company Information forms must not restate Section 1.3."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_section_dedup import (
    compress_rfp_company_identity_forms,
    is_rfp_company_identity_form_section,
)


class CompanyIdentityFormCompressTests(unittest.TestCase):
    def test_offeror_form_compressed_to_business_info_crossref(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-co-dup",
            sections=[
                ProposalSection(
                    id="section-1-business-info",
                    title="1.3 — Business Information",
                    content=(
                        "## Business Information\n\n"
                        "| Field | Detail |\n"
                        "| --- | --- |\n"
                        "| Legal Name | Z'Onion Creative Group LLC |\n"
                        "| DBA | zö agency |\n"
                        "| Primary Contact | Ron Comer |\n"
                        "| Contact Phone | (541) 350-2778 |\n"
                        "| Contact Email | connect@zo.agency |\n"
                        "| Office Address | 220 NW Oregon Ave, Suite 204, Bend, OR 97703 |\n"
                    ),
                ),
                ProposalSection(
                    id="rfp-sec-20",
                    title="20 Offeror Identification (Section 4 Form)",
                    content=(
                        "## Company Information\n\n"
                        "| FIELD | RESPONSE |\n"
                        "| --- | --- |\n"
                        "| Legal Name | Z'Onion Creative Group LLC |\n"
                        "| DBA | zö agency |\n"
                        "| Primary Contact | Ron Comer, Senior Account Manager |\n"
                        "| Contact Phone | (541) 350-2778 |\n"
                        "| Contact Email | connect@zo.agency |\n"
                        "| Office Address | 220 NW Oregon Ave, Suite 204, Bend, OR 97703 |\n"
                        "| Mailing Address | 70 SW Century Drive #1100, Bend, OR 97702 |\n"
                    ),
                ),
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        self.assertTrue(
            is_rfp_company_identity_form_section(
                section_id="rfp-sec-20",
                title="20 Offeror Identification (Section 4 Form)",
                content=draft.sections[1].content or "",
            )
        )
        updated, logs = compress_rfp_company_identity_forms(draft)
        self.assertTrue(logs)
        form = next(s for s in updated.sections if s.id == "rfp-sec-20")
        body = form.content or ""
        self.assertIn("1.3 — Business Information", body)
        self.assertIn("not a second company profile", body.casefold())
        self.assertIn("See **1.3 — Business Information**", body)
        # Must not duplicate the company field table — 1.3 owns that data.
        self.assertNotIn("| Legal Name |", body)
        self.assertEqual(form.title, "20 Offeror Identification (Section 4 Form)")

    def test_remove_company_info_ask_detected(self) -> None:
        from app.services.proposal_section_dedup import (
            user_asks_remove_company_identity_dump,
        )

        self.assertTrue(
            user_asks_remove_company_identity_dump("here remove this company info")
        )
        self.assertFalse(
            user_asks_remove_company_identity_dump(
                "Designer-compact: tables + layout, keep every RFP ask."
            )
        )

    def test_designer_compact_does_not_hijack_remove_ask(self) -> None:
        from app.models.proposal import ProposalSection
        from app.services.proposal_manuscript_compact import (
            should_run_designer_compact_for_chat,
        )

        section = ProposalSection(
            id="rfp-sec-19",
            title="19 Offeror Identification (Section 4 Form)",
            content=("We are a women-owned agency. " * 80),
            word_target=420,
        )
        # Improve-pin + long section must still send remove asks to Claude.
        self.assertFalse(
            should_run_designer_compact_for_chat(
                user_message="here remove this company info",
                improve_section_pinned=True,
                section=section,
            )
        )
        self.assertFalse(
            should_run_designer_compact_for_chat(
                user_message="improve this section",
                improve_section_pinned=True,
                section=section,
            )
        )
        self.assertTrue(
            should_run_designer_compact_for_chat(
                user_message="Designer-compact: tables + layout, keep every RFP ask.",
                improve_section_pinned=True,
                section=section,
            )
        )

    def test_does_not_touch_section_13(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-co-dup2",
            sections=[
                ProposalSection(
                    id="section-1-business-info",
                    title="1.3 — Business Information",
                    content=(
                        "| Field | Detail |\n| --- | --- |\n"
                        "| Legal Name | Z'Onion Creative Group LLC |\n"
                        "| DBA | zö agency |\n"
                        "| Office Address | Bend |\n"
                    ),
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        updated, logs = compress_rfp_company_identity_forms(draft)
        self.assertEqual(logs, [])
        self.assertEqual(
            updated.sections[0].content, draft.sections[0].content
        )


if __name__ == "__main__":
    unittest.main()
