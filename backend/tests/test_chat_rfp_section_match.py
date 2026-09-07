"""Chat improve must resolve mapped RFP sections by title, not id alone."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalResearchCache, RfpSectionMap
from app.services.proposal_section_editor import (
    _find_rfp_section,
    _requirement_coverage_gaps,
    _rfp_section_requirements_list,
    _user_asks_voice_or_style_only,
)


def _research(*sections: RfpSectionMap) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="rfp-gilroy",
        updatedAt="2026-09-07T00:00:00Z",
        rfpSections=list(sections),
    )


class ChatRfpSectionMatchTests(unittest.TestCase):
    def test_title_match_when_structure_id_differs(self) -> None:
        research = _research(
            RfpSectionMap(
                id="sec-exec",
                title="Executive Summary",
                requirements=[
                    "Summarize approach to year-round engagement",
                    "Name key deliverables for website and social",
                ],
                uncoveredRequirements=["Sponsorship tier modernization"],
            )
        )
        mapped = _find_rfp_section(
            research,
            "rfp-structure-executive-summary",
            section_title="4. Executive Summary",
        )
        self.assertIsNotNone(mapped)
        assert mapped is not None
        self.assertEqual(mapped.id, "sec-exec")
        reqs = _rfp_section_requirements_list(
            research,
            "rfp-structure-executive-summary",
            section_title="4. Executive Summary",
        )
        self.assertGreaterEqual(len(reqs), 2)

    def test_id_match_still_works(self) -> None:
        research = _research(
            RfpSectionMap(
                id="sec-budget",
                title="Budget",
                requirements=["Fee table by phase"],
            )
        )
        mapped = _find_rfp_section(research, "sec-budget", section_title="Budget")
        self.assertIsNotNone(mapped)
        assert mapped is not None
        self.assertEqual(mapped.title, "Budget")

    def test_coverage_gaps_flag_missing_asks(self) -> None:
        draft = (
            "We'll run monthly website conversion checks and keep Instagram fed "
            "year-round with volunteer stories."
        )
        gaps = _requirement_coverage_gaps(
            draft,
            [
                "Website conversion checks",
                "Sponsorship tier modernization and collateral",
                "On-site cashless payment framework",
            ],
        )
        self.assertTrue(
            any("sponsorship" in g.casefold() for g in gaps),
            gaps,
        )
        self.assertTrue(any("cashless" in g.casefold() for g in gaps), gaps)
        self.assertFalse(any("website conversion" in g.casefold() for g in gaps), gaps)

    def test_voice_ask_detection(self) -> None:
        self.assertTrue(
            _user_asks_voice_or_style_only("Make this section align with zo voice")
        )
        self.assertFalse(
            _user_asks_voice_or_style_only("Add a third client reference with phone")
        )


if __name__ == "__main__":
    unittest.main()
