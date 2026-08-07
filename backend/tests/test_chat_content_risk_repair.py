"""Tests for chat content-risk repair routing."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_chat_content_repair import (
    run_content_risk_repair,
    user_asks_content_risk_repair,
)
from app.services.proposal_chat_ops import classify_chat_op


def _rfp() -> RfpRecord:
    return RfpRecord.model_validate(
        {
            "id": "rfp-x",
            "title": "Talent Attraction",
            "client": "City of Oshkosh",
            "sector": "public",
            "dueDate": "2026-08-21",
            "receivedDate": "2026-08-01",
            "status": "active",
            "lastActivity": "2026-08-05",
            "lastActivityNote": "n",
        }
    )


SAMPLE_AUDIT = """
Content issues that still matter

1. Reference list still incomplete as content. "City of Bend," "City of Medford,"
   "Deschutes Public Library" — no names, titles, phone, or email.

2. Unverified/unsubstantiated client claims still in the qualifications section.
   Santa Clara multi-year; Maricopa five-year every department.

3. Positioning tagline is fabricated mid-document. "Oshkosh: Where Your Next Chapter Begins"

4. Executive summary restates the RFP's evaluation criteria back at the evaluator.

5. Case studies are strong content, but Medford/Bend thinner than Umatilla.

Please fix these content issues.
"""


class ContentRiskDetectTests(unittest.TestCase):
    def test_detects_pasted_audit_with_fix_ask(self) -> None:
        self.assertTrue(user_asks_content_risk_repair(SAMPLE_AUDIT))

    def test_classify_chat_op_routes_to_fix_content_risks(self) -> None:
        self.assertEqual(classify_chat_op(SAMPLE_AUDIT), "fix_content_risks")

    def test_plain_question_not_forced(self) -> None:
        self.assertFalse(
            user_asks_content_risk_repair("What is the due date for this RFP?")
        )


class ContentRiskRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_repairs_qualifications_claims_via_llm(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[
                ProposalSection(
                    id="rfp-sec-3",
                    title="11. Firm Qualifications and Relevant Experience Including Three References",
                    content=(
                        "City of Bend — no contact.\n"
                        "Maricopa County: five-year contract spanning every department.\n"
                        "Santa Clara: comprehensive PR and brand partner for both the city "
                        "and stadium authority on a multi-year contract."
                    ),
                    status="generated",
                ),
                ProposalSection(
                    id="rfp-sec-4",
                    title="12. Project Approach and Work Plan",
                    content=(
                        "Campaign theme: Oshkosh: Where Your Next Chapter Begins\n"
                        "We will execute against this established tagline."
                    ),
                    status="generated",
                ),
            ],
            updatedAt="2026-08-07T00:00:00Z",
        )
        repaired_qual = (
            "City of Bend — [VERIFY: distinct reference contact — name, title, org, "
            "phone, email from KB]\n"
            "Maricopa County — municipal marketing partnership "
            "[VERIFY: substantiate from 03_CS / ClientList — five-year every department]."
        )
        repaired_approach = (
            "Working creative direction (to validate in discovery): talent-attraction "
            "messaging for Oshkosh — not a locked campaign theme."
        )

        async def _fake_chat_json(messages, **kwargs):
            user = messages[-1]["content"]
            if "REFERENCES" in user or "Unverified quantified" in user or "UNVERIFIED" in user:
                return (
                    {"content": repaired_qual, "changed": True, "notes": "refs+claims"},
                    "test",
                )
            return (
                {"content": repaired_approach, "changed": True, "notes": "tagline"},
                "test",
            )

        with patch(
            "app.services.proposal_fulfill_fabrication_guard.repair_fabricated_qualifications",
            return_value=(draft, [], []),
        ), patch(
            "app.services.proposal_chat_content_repair.llm.chat_json",
            new=AsyncMock(side_effect=_fake_chat_json),
        ), patch(
            "app.services.proposal_chat_content_repair.llm.is_configured",
            return_value=True,
        ):
            result = await run_content_risk_repair(
                draft=draft,
                rfp=_rfp(),
                rfp_context="Talent attraction RFP for Oshkosh. " * 20,
                research=None,
                user_message=SAMPLE_AUDIT,
            )
        self.assertTrue(result.sections_changed)
        quals = next(s for s in result.draft.sections if s.id == "rfp-sec-3")
        self.assertIn("[VERIFY:", quals.content or "")
        self.assertNotIn("five-year contract spanning every department", quals.content or "")
        approach = next(s for s in result.draft.sections if s.id == "rfp-sec-4")
        self.assertNotIn("established tagline", (approach.content or "").casefold())


if __name__ == "__main__":
    unittest.main()
