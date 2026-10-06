"""Post-hoc citation grounding: claim → evidenceCorpus without LLM [E#]."""

from __future__ import annotations

import unittest

from app.models.proposal import (
    EvidenceItem,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_citation_grounding import (
    attach_citation_maps_to_draft,
    ground_section_citations,
    score_claim_against_excerpt,
)


class ScoreClaimTests(unittest.TestCase):
    def test_year_claim_matches_companyfacts_excerpt(self) -> None:
        claim = "zö agency was founded in 2013 and has served public-sector clients since."
        excerpt = (
            "Company overview: zö agency (Z'Onion Creative Group LLC) was founded "
            "in 2013 in Bend, Oregon."
        )
        score = score_claim_against_excerpt(claim, excerpt)
        self.assertGreaterEqual(score, 0.42)

    def test_unrelated_claim_scores_low(self) -> None:
        claim = "Our media buying team will run paid search and social campaigns statewide."
        excerpt = "Senior Strategist — $185/hour (Catalog STR-185)."
        score = score_claim_against_excerpt(claim, excerpt)
        self.assertLess(score, 0.42)


class GroundSectionTests(unittest.TestCase):
    def test_maps_founding_sentence_to_evidence_id(self) -> None:
        corpus = [
            EvidenceItem(
                id="E12",
                source="01_companyfacts verified",
                excerpt=(
                    "Company overview: zö agency was founded in 2013 in Bend, Oregon "
                    "and operates as a full-service creative agency."
                ),
            ),
            EvidenceItem(
                id="E3",
                source="00_guide_pricing",
                excerpt="Senior Strategist — $185/hour (Catalog STR-185).",
            ),
        ]
        content = (
            "## About zö\n\n"
            "zö agency was founded in 2013 and has served public-sector clients since.\n\n"
            "We look forward to partnering with the County.\n"
        )
        cmap = ground_section_citations(content, corpus, section_id="about")
        self.assertTrue(cmap)
        ids = {eid for row in cmap for eid in row.evidence_ids}
        self.assertIn("E12", ids)
        self.assertTrue(all("look forward" not in row.text.casefold() for row in cmap))

    def test_attach_writes_citation_map_and_kb_refs(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="about",
                    title="About",
                    content=(
                        "zö agency was founded in 2013 and has served public-sector "
                        "clients across Oregon since opening."
                    ),
                    status="generated",
                )
            ],
        )
        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="t",
            evidenceCorpus=[
                EvidenceItem(
                    id="E12",
                    source="01_companyfacts",
                    excerpt="zö agency was founded in 2013 in Bend, Oregon.",
                )
            ],
        )
        out, logs = attach_citation_maps_to_draft(draft, research)
        self.assertTrue(logs)
        section = out.sections[0]
        self.assertTrue(section.citation_map)
        self.assertIn("E12", section.kb_refs)
        self.assertNotIn("[E12]", section.content)


if __name__ == "__main__":
    unittest.main()
