"""RFP Offeror/Company Information forms must not restate Section 1.3.

Classification is by content shape (identity dump vs multi-ask), not title keywords.
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_section_dedup import (
    compress_rfp_company_identity_forms,
    is_rfp_company_identity_form_section,
    repair_emptied_vendor_questionnaires,
)


class CompanyIdentityFormCompressTests(unittest.TestCase):
    def test_multi_ask_questionnaire_is_not_compressed(self) -> None:
        """Extra non-identity asks keep the tab — regardless of the word vendor."""
        body = (
            "## Vendor Questionnaire\n\n"
            "| Field | Response |\n"
            "| --- | --- |\n"
            "| Legal Name | Z'Onion Creative Group LLC |\n"
            "| DBA | zö agency |\n"
            "| Contact Phone | (541) 350-2778 |\n"
            "| Years providing airport marketing | 8 |\n"
            "| Describe your media buying approach | We plan flights from audience data "
            "and report weekly on CPA and brand lift. |\n"
            "| List three relevant public-sector campaigns | Hillsboro Library; "
            "Umatilla; Northglenn. |\n"
            "| Insurance: can you meet the RFP GL limit? | See Section 1.5; confirm on COI. |\n"
        )
        self.assertFalse(
            is_rfp_company_identity_form_section(
                section_id="rfp-vq",
                title="Vendor Questionnaire",
                content=body,
            )
        )

    def test_pointer_only_tab_is_restored_without_title_keywords(self) -> None:
        body = (
            "*Company identity for this form matches **1.3 — Business Information** "
            "(same legal name, contacts, and addresses — not a second company profile).*\n\n"
            "See **1.3 — Business Information** for legal name, DBA, FEIN, contacts, "
            "and addresses. Complete any form-specific fields below only if this RFP "
            "requires them here and they are not already in that tab."
        )
        draft = ProposalDraft(
            rfpId="rfp-vq",
            sections=[
                ProposalSection(
                    id="rfp-vq",
                    title="Supplier Intake Sheet",
                    content=body,
                    status="generated",
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        out, logs = repair_emptied_vendor_questionnaires(draft)
        self.assertTrue(logs)
        fixed = out.sections[0].content or ""
        self.assertIn("Draft this RFP-required section", fixed)
        self.assertIn("FIELD | RESPONSE", fixed)
        self.assertNotIn("not a second company profile", fixed.casefold())

    def test_pure_identity_dump_compressed_with_synced_table(self) -> None:
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
        # Synced table stays so the buyer form is not empty chrome.
        self.assertIn("| Legal Name |", body)
        self.assertIn("Z'Onion Creative Group LLC", body)
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
