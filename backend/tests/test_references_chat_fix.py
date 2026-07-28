"""Deterministic References §21 contact fixes from chat."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalSection
from app.services.proposal_section_editor import (
    _extract_contacts_from_ask,
    _try_deterministic_references_fix,
)


class TestReferencesChatFix(unittest.TestCase):
    def test_extract_contacts(self) -> None:
        msg = (
            "City of Carbondale: Steven Mitchell, Economic Development Director, "
            "steven.mitchell@carbondaleil.gov, (618) 457-3286\n"
            "Oregon Employment Department: Sytel G. Oelke, "
            "Sytel.G.Oelke@employ.oregon.gov, (503) 341-5661\n"
            "For Maricopa County: [VERIFY: client-side reference contact required]\n"
        )
        contacts = _extract_contacts_from_ask(msg)
        self.assertIn("oregon", contacts)
        self.assertIn("sytel", contacts["oregon"].casefold())
        self.assertIn("carbondale", contacts)
        self.assertIn("maricopa", contacts)
        self.assertIn("VERIFY", contacts["maricopa"])

    def test_apply_fix_to_section(self) -> None:
        section = ProposalSection(
            id="rfp-ref",
            title="21. References — Current Clients",
            content=(
                "Oregon Employment Department — Reference contact details available upon request.\n"
                "City of Carbondale — Reference contact details available upon request.\n"
                "Maricopa County — Reference contact details available upon request.\n"
                "We have pre-cleared all three for direct contact by Tarrant County's "
                "evaluation team, and each has agreed to respond to reference checks "
                "within standard procurement timelines.\n"
            ),
            status="generated",
            source="rfp",
            mode="write",
        )
        ask = (
            "Fix §21 References only.\n"
            "City of Carbondale: Steven Mitchell, Economic Development Director, "
            "steven.mitchell@carbondaleil.gov, (618) 457-3286\n"
            "Oregon Employment Department: Sytel G. Oelke, "
            "Sytel.G.Oelke@employ.oregon.gov, (503) 341-5661\n"
            "For Maricopa County, do not invent. Replace with "
            "[VERIFY: client-side reference contact required — confirm with Sonja "
            "or Ella before submission; not currently on file].\n"
            "Delete the pre-cleared sentence.\n"
        )
        result = _try_deterministic_references_fix(
            section=section,
            user_message=ask,
            conversation_history=None,
        )
        self.assertIsNotNone(result)
        assert result is not None
        working, reply = result
        body = working.content
        self.assertIn("Sytel", body)
        self.assertIn("Steven Mitchell", body)
        self.assertIn("[VERIFY:", body)
        self.assertNotIn("upon request", body.casefold())
        self.assertNotIn("pre-cleared", body.casefold())
        self.assertIn("References", reply)

    def test_umatilla_ask_does_not_rerun_references_fix(self) -> None:
        section = ProposalSection(
            id="rfp-ref",
            title="21. References — Current Clients",
            content=(
                "Oregon Employment Department — Sytel G. Oelke.\n"
                "Additional references, including the City of Bend Water "
                "Conservation, are available on request.\n"
            ),
            status="generated",
            source="rfp",
            mode="write",
        )
        ask = (
            "1. Section 11 (Umatilla case study) still misrepresents Rock the Locks. "
            "I flagged this before the References fix."
        )
        result = _try_deterministic_references_fix(
            section=section,
            user_message=ask,
            conversation_history=[
                {
                    "role": "user",
                    "content": "Fix §21 References. Replace upon request.",
                }
            ],
        )
        self.assertIsNone(result)

    def test_bend_sentence_revert(self) -> None:
        section = ProposalSection(
            id="rfp-ref",
            title="21. References — Current Clients",
            content=(
                "Oregon Employment Department — Sytel G. Oelke.\n"
                "Additional references, including the City of Bend Water "
                "Conservation, are [VERIFY: reference contact — name, title, "
                "organization, phone, email from KB].\n"
            ),
            status="generated",
            source="rfp",
            mode="write",
        )
        ask = (
            'Revert the last sentence in §21 to exactly: "Additional references, '
            'including the City of Bend Water Conservation, are available on request." '
            "Do not add a VERIFY tag there."
        )
        result = _try_deterministic_references_fix(
            section=section,
            user_message=ask,
            conversation_history=None,
        )
        self.assertIsNotNone(result)
        assert result is not None
        working, reply = result
        self.assertIn("are available on request.", working.content)
        self.assertNotIn("[VERIFY:", working.content)
        self.assertIn("available on request", reply.casefold())


if __name__ == "__main__":
    unittest.main()
