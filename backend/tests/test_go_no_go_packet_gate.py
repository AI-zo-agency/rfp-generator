"""Packet gate: solicitation notices must not enter Go/No-Go scoring."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.rfp import RfpRecord
from app.services import llm
from app.services.go_no_go_packet_gate import (
    build_blocked_packet_analysis,
    parse_packet_read,
)
from app.services.go_no_go_service import (
    RfpContentInfo,
    analysis_activity_note,
    analyze_rfp,
)

NOTICE = """
Tacoma-Pierce County Health Department
RFP 2026-0904 Media Advertising Consultant
This document is a solicitation notice only. It does not include the scope of work, proposal structure, evaluation criteria, or pricing requirements.
To receive the complete RFP packet, email solicitations@tpchd.org.
Submission deadline: October 21, 2026, at 4 PM.
"""

COMPLETE = """
The City seeks a firm to provide media advertising consulting services.
Proposals shall include a technical approach, a staffing plan, and a cost proposal.
Proposals will be evaluated on approach, experience, and price.
Offerors must request the required pricing workbook by emailing procurement@city.example before submission.
"""


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="rfp-notice",
        title="RFP 2026-0904 Media Advertising Consultant",
        client="Tacoma-Pierce County Health Department",
        dueDate="2026-10-21",
        receivedDate="2026-09-01",
        lastActivity="2026-09-04",
        lastActivityNote="uploaded",
    )


def _content() -> RfpContentInfo:
    return RfpContentInfo(
        pdf_path=None,
        pdf_text="",
        description=NOTICE,
        substantive_chars=len(NOTICE),
        metadata_only=False,
    )


class PacketGateParseTests(unittest.TestCase):
    def test_solicitation_notice_blocks_and_keeps_grounded_email(self) -> None:
        parsed = parse_packet_read(
            {
                "documentKind": "solicitation_notice",
                "blocksScoring": True,
                "headline": "Solicitation notice, not the RFP",
                "whatItIs": (
                    "This upload is a one-page solicitation notice for RFP 2026-0904. "
                    "It announces the opportunity and tells vendors to email for the packet. "
                    "It does not include the scope, proposal structure, evaluation criteria, "
                    "or pricing requirements, so it cannot be scored."
                ),
                "missing": [
                    "scope of work",
                    "proposal structure",
                    "evaluation criteria",
                    "pricing requirements",
                ],
                "nextStep": "Email solicitations@tpchd.org and request the complete RFP packet.",
                "deadlineQuote": "Submission deadline: October 21, 2026, at 4 PM.",
                "evidenceQuote": (
                    "This document is a solicitation notice only. It does not include "
                    "the scope of work, proposal structure, evaluation criteria, or pricing requirements."
                ),
                "buyerDemands": [
                    {
                        "action": "Email solicitations@tpchd.org and request the complete RFP packet.",
                        "contact": "solicitations@tpchd.org",
                        "whyEssential": "The notice does not contain the materials needed to propose.",
                        "quote": "To receive the complete RFP packet, email solicitations@tpchd.org.",
                    }
                ],
            },
            rfp_text=NOTICE,
        )
        self.assertTrue(parsed.blocks_scoring)
        self.assertEqual(parsed.document_kind, "solicitation_notice")
        self.assertIn("solicitations@tpchd.org", parsed.next_step)
        self.assertEqual(len(parsed.buyer_demands), 1)
        self.assertIn("October 21, 2026", parsed.deadline_note)
        self.assertIn("scope of work", parsed.missing)

    def test_quote_still_grounds_when_pdf_punctuation_differs(self) -> None:
        parsed = parse_packet_read(
            {
                "documentKind": "solicitation_notice",
                "blocksScoring": True,
                "whatItIs": "A notice that tells vendors to request the packet by email.",
                "evidenceQuote": (
                    "This document is a solicitation notice only. It does not include "
                    "the scope of work, proposal structure, evaluation criteria, or pricing requirements"
                ),
                "deadlineQuote": "Submission deadline: October 21, 2026, at 4 PM",
            },
            rfp_text=NOTICE,
        )
        self.assertTrue(parsed.blocks_scoring)
        self.assertIn("October 21, 2026", parsed.deadline_note)

    def test_ungrounded_block_fails_open(self) -> None:
        parsed = parse_packet_read(
            {
                "documentKind": "solicitation_notice",
                "blocksScoring": True,
                "whatItIs": "This is not an RFP.",
                "evidenceQuote": "This sentence was invented and is not in the upload.",
                "nextStep": "Email nobody@example.com",
            },
            rfp_text=COMPLETE,
        )
        self.assertFalse(parsed.blocks_scoring)
        self.assertEqual(parsed.document_kind, "complete_rfp")

    def test_invented_email_is_stripped_from_a_real_block(self) -> None:
        parsed = parse_packet_read(
            {
                "documentKind": "incomplete_packet",
                "blocksScoring": True,
                "whatItIs": "The upload tells the reader to request the full packet by email.",
                "evidenceQuote": "To receive the complete RFP packet, email solicitations@tpchd.org.",
                "nextStep": "Email invented-person@not-in-the-file.test for the packet.",
                "buyerDemands": [
                    {
                        "action": "Email invented-person@not-in-the-file.test",
                        "contact": "invented-person@not-in-the-file.test",
                        "quote": "To receive the complete RFP packet, email solicitations@tpchd.org.",
                    }
                ],
            },
            rfp_text=NOTICE,
        )
        self.assertTrue(parsed.blocks_scoring)
        self.assertNotIn("invented-person", parsed.next_step)
        self.assertNotIn("invented-person", parsed.buyer_demands[0].action)
        self.assertEqual(parsed.buyer_demands[0].contact, "")

    def test_complete_rfp_keeps_essential_demand_and_still_scores(self) -> None:
        parsed = parse_packet_read(
            {
                "documentKind": "complete_rfp",
                "blocksScoring": False,
                "whatItIs": "This is a complete services RFP.",
                "evidenceQuote": "Proposals will be evaluated on approach, experience, and price.",
                "missing": ["should be dropped"],
                "buyerDemands": [
                    {
                        "action": "Request the required pricing workbook from procurement@city.example.",
                        "contact": "procurement@city.example",
                        "whyEssential": "The cost proposal cannot be completed without the workbook.",
                        "quote": (
                            "Offerors must request the required pricing workbook by "
                            "emailing procurement@city.example before submission."
                        ),
                    },
                    {
                        "action": "Email a contact that was not stated.",
                        "contact": "ghost@example.com",
                        "quote": "This demand quote does not appear in the RFP.",
                    },
                ],
            },
            rfp_text=COMPLETE,
        )
        self.assertFalse(parsed.blocks_scoring)
        self.assertEqual(parsed.missing, [])
        self.assertEqual(len(parsed.buyer_demands), 1)
        self.assertEqual(parsed.buyer_demands[0].contact, "procurement@city.example")

    def test_blocked_analysis_does_not_score(self) -> None:
        parsed = parse_packet_read(
            {
                "documentKind": "solicitation_notice",
                "blocksScoring": True,
                "headline": "Solicitation notice, not the RFP",
                "whatItIs": "One-page notice. The complete packet must be requested.",
                "missing": ["scope of work"],
                "nextStep": "Email solicitations@tpchd.org and request the complete RFP packet.",
                "evidenceQuote": "To receive the complete RFP packet, email solicitations@tpchd.org.",
            },
            rfp_text=NOTICE,
        )
        analysis = build_blocked_packet_analysis(parsed)
        self.assertTrue(analysis.insufficient_data)
        self.assertIsNone(analysis.recommendation)
        self.assertIsNone(analysis.fit_score)
        self.assertEqual(analysis.provider, "packet-gate")
        self.assertEqual(analysis.decision_matrix, [])
        self.assertIn("complete packet", analysis.summary.casefold())
        self.assertEqual(
            analysis_activity_note(analysis),
            "Go/No-Go skipped — upload is not a complete RFP",
        )


RFI = """
City of Rivergate
Request for Information — Marketing Capabilities
The City is not requesting proposals and will not award a contract from this RFI.
Respondents should describe their communications capabilities by November 2, 2026.
A competitive RFP may be issued later to firms that respond.
"""

RFQ = """
County of Pine
Request for Quotation — Social Media Advertising Placement
Vendors shall submit a firm price quotation for the placement described below.
This RFQ is not a request for proposals. Award will be to the lowest responsive quote.
Quotes are due October 30, 2026 at 2:00 PM.
"""


class PacketInstrumentTests(unittest.TestCase):
    def test_rfi_blocks_even_when_model_forgets_the_flag(self) -> None:
        parsed = parse_packet_read(
            {
                "documentKind": "rfi",
                "blocksScoring": False,
                "headline": "This is an RFI, not an RFP",
                "whatItIs": (
                    "The City of Rivergate is collecting marketing capabilities. "
                    "It will not award a contract from this RFI."
                ),
                "nextStep": "Describe communications capabilities by November 2, 2026.",
                "evidenceQuote": (
                    "The City is not requesting proposals and will not award a contract from this RFI."
                ),
                "deadlineQuote": "Respondents should describe their communications capabilities by November 2, 2026.",
            },
            rfp_text=RFI,
        )
        self.assertTrue(parsed.blocks_scoring)
        self.assertEqual(parsed.document_kind, "rfi")
        self.assertIn("RFI", parsed.headline)
        analysis = build_blocked_packet_analysis(parsed)
        self.assertEqual(
            analysis_activity_note(analysis),
            "Go/No-Go skipped — upload is an RFI, not an RFP",
        )
        self.assertIsNone(analysis.recommendation)

    def test_rfq_is_named_and_not_scored(self) -> None:
        parsed = parse_packet_read(
            {
                "documentKind": "rfq",
                "blocksScoring": True,
                "whatItIs": "",
                "evidenceQuote": "This RFQ is not a request for proposals.",
                "deadlineQuote": "Quotes are due October 30, 2026 at 2:00 PM.",
            },
            rfp_text=RFQ,
        )
        self.assertTrue(parsed.blocks_scoring)
        self.assertEqual(parsed.document_kind, "rfq")
        self.assertIn("RFQ", parsed.headline)
        self.assertIn("quote", parsed.what_it_is.casefold())
        self.assertIn("October 30, 2026", parsed.deadline_note)
        analysis = build_blocked_packet_analysis(parsed)
        self.assertEqual(
            analysis_activity_note(analysis),
            "Go/No-Go skipped — upload is an RFQ, not an RFP",
        )

    def test_ungrounded_rfi_label_does_not_block_a_real_rfp(self) -> None:
        parsed = parse_packet_read(
            {
                "documentKind": "rfi",
                "blocksScoring": True,
                "whatItIs": "This is an RFI.",
                "evidenceQuote": "This sentence was not in the uploaded RFP.",
            },
            rfp_text=COMPLETE,
        )
        self.assertFalse(parsed.blocks_scoring)
        self.assertEqual(parsed.document_kind, "complete_rfp")


class PacketGateSkipTests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_rfp_does_not_score_a_solicitation_notice(self) -> None:
        with (
            patch.object(llm, "is_configured", return_value=True),
            patch(
                "app.services.go_no_go_service._assess_rfp_content",
                return_value=_content(),
            ),
            patch(
                "app.services.go_no_go_service.classify_rfp_packet",
                new=AsyncMock(
                    return_value=parse_packet_read(
                        {
                            "documentKind": "solicitation_notice",
                            "blocksScoring": True,
                            "headline": "Solicitation notice, not the RFP",
                            "whatItIs": (
                                "This is a solicitation notice for the media advertising "
                                "consultant opportunity, not the RFP packet."
                            ),
                            "missing": ["scope of work", "evaluation criteria"],
                            "nextStep": "Email solicitations@tpchd.org and upload the complete packet.",
                            "deadlineQuote": "Submission deadline: October 21, 2026, at 4 PM.",
                            "evidenceQuote": (
                                "This document is a solicitation notice only. It does not include "
                                "the scope of work, proposal structure, evaluation criteria, or "
                                "pricing requirements."
                            ),
                            "buyerDemands": [
                                {
                                    "action": "Email solicitations@tpchd.org for the complete RFP packet.",
                                    "contact": "solicitations@tpchd.org",
                                    "quote": "To receive the complete RFP packet, email solicitations@tpchd.org.",
                                }
                            ],
                        },
                        rfp_text=NOTICE,
                    )
                ),
            ),
            patch.object(
                llm,
                "chat_json",
                new=AsyncMock(side_effect=AssertionError("scoring should not run")),
            ),
            patch(
                "app.services.go_no_go_service._gather_knowledge_context",
                new=AsyncMock(side_effect=AssertionError("kb should not run")),
            ),
        ):
            analysis = await analyze_rfp(_rfp())

        self.assertEqual(analysis.provider, "packet-gate")
        self.assertIsNone(analysis.recommendation)
        self.assertTrue(analysis.packet_read and analysis.packet_read.blocks_scoring)
        self.assertIn("solicitations@tpchd.org", analysis.summary)
        self.assertIn("October 21, 2026", analysis.packet_read.deadline_note)
