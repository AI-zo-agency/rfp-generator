"""Advisory chat must see THIS tab's RFP form pages — not only the TOC head."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_section_editor import _section_chat_advisory_reply


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="r1",
        title="RFQ 26-22SF",
        client="Port of San Diego",
        sector="Government",
        source="manual",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-01",
        lastActivityNote="t",
    )


def _section() -> ProposalSection:
    return ProposalSection(
        id="att-a",
        title="Attachment A, Statement of Qualifications",
        content=(
            "## Attachment A, Statement of Qualifications\n\n"
            "[DESIGNER NOTE: Place the completed form here.]\n"
            "[MANUAL FILL: Sonja — attach signed Attachment A before submit.]\n"
        ),
        status="generated",
    )


_TOC_HEAD = (
    "RFQ 26-22SF AS-NEEDED CREATIVE AGENCY SERVICES\n\n"
    "TABLE OF CONTENTS\n"
    "I. Introduction ................................ 1\n"
    "II. Scope of Services ........................... 4\n"
    "V. Submittal Requirements ....................... 12\n"
    "Attachment A Statement of Qualifications ...... 19\n"
    "Attachment B .................................. 23\n"
)

_ATTACHMENT_A_BODY = """
ATTACHMENT A STATEMENT OF QUALIFICATIONS

Legal Name of Respondent as it will appear on any final Agreement:
_______________________________________________
Company or Organization Name

Respondent's Representative for purposes of communication relating to this submittal:
Name, Title _____________ Tel. No. _____________ Email _____________
Street _____________ City _____________ State ______ ZIP ______

Respondent's Representative with signature authority for contract documents:
(IF DIFFERENT THAN ADDRESS STATED ABOVE)
Name, Title _____________ Tel. No. _____________ Email _____________

The Respondent is a (check one):
( ) Sole Proprietorship
( ) Partnership
( ) Corporation — STATE OF INCORPORATION: _____________
( ) Joint Venture

I, _________________ (PRINT NAME), affirm that all the information furnished
in and with this questionnaire, is true, complete and correct.
"""


class AdvisorySectionFormExcerptTests(unittest.IsolatedAsyncioTestCase):
    async def test_informational_form_ask_sees_attachment_a_fields(self) -> None:
        """Open Attachment A + 'does RFP have a form?' must pack the form page.

        Regression: informational asks used only planner markers / TOC head
        (~2.5k), so Ralph claimed the RFQ was truncated before Attachment A
        even when pdf_text contained the fillable Statement of Qualifications.
        """
        captured: list[str] = []

        async def _capture(messages, **_kwargs):  # noqa: ANN001
            user = next(m for m in messages if m.get("role") == "user")
            captured.append(user["content"])
            return (
                {
                    "reply": (
                        "Yes — Attachment A is a fillable Statement of Qualifications "
                        "with Legal Name, representative contacts, entity type, and affirmation."
                    )
                },
                "stub",
            )

        draft = ProposalDraft(
            rfpId="r1",
            sections=[_section()],
            updatedAt="2026-01-01T00:00:00Z",
        )
        # rfp_context = truncated head (what load_rfp historically hands chat)
        rfp_context = _TOC_HEAD + ("[boilerplate]\n" * 200)
        rfp_full = _TOC_HEAD + ("Scope filler.\n" * 400) + _ATTACHMENT_A_BODY

        with (
            patch(
                "app.services.proposal_section_editor.chat_json_with_repair",
                new=AsyncMock(side_effect=_capture),
            ),
            # Keep this unit test offline — packing must not depend on KB.
            patch(
                "app.services.proposal_section_editor._advisory_needs_kb_lookup",
                return_value=False,
            ),
            patch(
                "app.services.proposal_section_kb_evidence.user_asks_kb_fetch_or_fill",
                return_value=False,
            ),
        ):
            await _section_chat_advisory_reply(
                section=_section(),
                rfp=_rfp(),
                rfp_context=rfp_context,
                user_message=(
                    "for this section could u pls find if rfp has inbuilt form for this?"
                ),
                conversation_history=[],
                selection_text="",
                requirements_block="",
                manuscript_digest="1. Attachment A, Statement of Qualifications",
                draft=draft,
                rfp_full_text=rfp_full,
            )

        self.assertEqual(len(captured), 1)
        prompt = captured[0]
        self.assertIn("Legal Name of Respondent", prompt)
        self.assertIn("Sole Proprietorship", prompt)
        self.assertIn("PRINT NAME", prompt)
        # Must not be TOC-only
        self.assertGreater(prompt.casefold().count("attachment a"), 0)


if __name__ == "__main__":
    unittest.main()
