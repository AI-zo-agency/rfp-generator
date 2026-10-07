"""Cover/interest letter mode follows Phase 2 meaning stamps, not title synonyms."""

from __future__ import annotations

import unittest

from app.services.proposal_brand_voice import classify_section_register
from app.services.proposal_draft_structure_stubs import (
    is_cover_letter_section,
    letter_stamps_from_research,
)
from app.services.proposal_evidence_gate import EvidenceDecision, decide_evidence_action
from app.services.proposal_submission_authority import VALID_INSTRUMENTS


class CoverLetterInstrumentStampTests(unittest.TestCase):
    def test_letter_instrument_is_valid(self) -> None:
        self.assertIn("letter", VALID_INSTRUMENTS)

    def test_odd_title_with_letter_instrument_is_cover_letter(self) -> None:
        self.assertTrue(
            is_cover_letter_section(
                title="Transmittal Package A — Opening Statement",
                section_id="rfp-sec-9",
                submission_instrument="letter",
            )
        )
        self.assertEqual(
            classify_section_register(
                section_id="rfp-sec-9",
                title="Transmittal Package A — Opening Statement",
                submission_instrument="letter",
            ),
            "cover_letter",
        )

    def test_plan_register_beats_narrative_looking_title(self) -> None:
        self.assertEqual(
            classify_section_register(
                section_id="rfp-sec-1",
                title="Introductory Remarks",
                plan_register="cover_letter",
            ),
            "cover_letter",
        )

    def test_evidence_gate_retrieves_for_letter_instrument(self) -> None:
        gate = decide_evidence_action(
            section_id="rfp-sec-1",
            section_title="Opening Remarks to the Board",
            submission_instrument="letter",
        )
        self.assertEqual(gate.action, EvidenceDecision.RETRIEVE_THEN_WRITE)
        self.assertEqual(gate.reason, "cover_letter_won_exemplars")

    def test_letter_stamps_from_research_dict_plan(self) -> None:
        class _R:
            proposal_execution_plan = {
                "writing": {
                    "proposalOutline": {
                        "sections": [
                            {
                                "id": "rfp-sec-1",
                                "title": "Opening Remarks",
                                "submissionInstrument": "letter",
                            }
                        ]
                    },
                    "sectionPlans": {
                        "plans": [
                            {
                                "sectionId": "rfp-sec-1",
                                "register": "cover_letter",
                            }
                        ]
                    },
                }
            }

        inst, reg = letter_stamps_from_research(_R(), "rfp-sec-1")
        self.assertEqual(inst, "letter")
        self.assertEqual(reg, "cover_letter")


if __name__ == "__main__":
    unittest.main()
