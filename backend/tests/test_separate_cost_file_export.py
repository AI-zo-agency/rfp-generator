"""Separate Response File vs Cost File Word export when the RFQ requires it."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_docx_export import (
    build_export_packets,
    build_proposal_docx_bytes,
    rfp_requires_separate_cost_file,
    split_draft_for_cost_packets,
)


class SeparateCostFileExportTests(unittest.TestCase):
    def test_detects_do_not_include_cost_file_rule(self) -> None:
        text = (
            "RESPONSE FILE contents…\n"
            "DO NOT INCLUDE A COPY OF YOUR COST FILE WITH THE MAIN PROPOSAL.\n"
            "Upload the Cost File separately on PlanetBids.\n"
        )
        self.assertTrue(rfp_requires_separate_cost_file(text))

    def test_detects_response_and_cost_file_slots(self) -> None:
        text = (
            "Proposers shall upload a Response File and a separate Cost File "
            "through the electronic portal."
        )
        self.assertTrue(rfp_requires_separate_cost_file(text))

    def test_ordinary_budget_section_does_not_force_split(self) -> None:
        text = "Include a budget and fee schedule in your proposal narrative."
        self.assertFalse(rfp_requires_separate_cost_file(text))

    def test_split_moves_budget_to_cost_packet(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Cover Letter",
                    content="We are pleased to submit.",
                    status="generated",
                ),
                ProposalSection(
                    id="s2",
                    title="Budget & Pricing",
                    content="| Phase | Amount |\n| --- | --- |\n| A | $1 |",
                    status="generated",
                ),
                ProposalSection(
                    id="s3",
                    title="References",
                    content="Three references.",
                    status="generated",
                ),
            ],
        )
        response, cost = split_draft_for_cost_packets(draft)
        self.assertEqual([s.title for s in response.sections], ["Cover Letter", "References"])
        self.assertEqual([s.title for s in cost.sections], ["Budget & Pricing"])

    def test_build_export_packets_returns_zip_when_required(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Approach",
                    content="Our method.",
                    status="generated",
                ),
                ProposalSection(
                    id="s2",
                    title="Budget & Pricing",
                    content="Fee table here with enough words for export.",
                    status="generated",
                ),
            ],
        )
        rfp_text = "DO NOT INCLUDE A COPY OF YOUR COST FILE WITH THE MAIN PROPOSAL."
        with patch(
            "app.services.proposal_docx_export.build_proposal_docx_bytes",
            side_effect=lambda **kwargs: b"DOCX:" + kwargs["doc_title"].encode(),
        ):
            packets = build_export_packets(
                draft=draft,
                rfp_title="Newport Beach RFQ",
                rfp_text=rfp_text,
            )
        self.assertEqual(packets.mode, "separate_cost")
        self.assertEqual(len(packets.files), 2)
        names = [f.filename for f in packets.files]
        self.assertTrue(any("Response" in n for n in names))
        self.assertTrue(any("Cost" in n for n in names))
        self.assertTrue(packets.zip_bytes and packets.zip_bytes[:2] == b"PK")

    def test_build_export_packets_single_when_not_required(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Approach",
                    content="Our method.",
                    status="generated",
                ),
                ProposalSection(
                    id="s2",
                    title="Budget & Pricing",
                    content="Fees.",
                    status="generated",
                ),
            ],
        )
        packets = build_export_packets(
            draft=draft,
            rfp_title="Simple RFP",
            rfp_text="Include pricing in the proposal.",
        )
        self.assertEqual(packets.mode, "single")
        self.assertEqual(len(packets.files), 1)
        self.assertIsNone(packets.zip_bytes)


if __name__ == "__main__":
    unittest.main()
