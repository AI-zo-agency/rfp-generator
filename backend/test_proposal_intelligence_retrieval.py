import unittest

from app.services.proposal_intelligence.memory import upsert_memory
from app.services.proposal_intelligence.retrieval import (
    INTELLIGENCE_BUCKETS,
    is_writing_evidence_source,
)
from app.services.proposal_intelligence.schemas import ProposalMemory


class ProposalIntelligenceRetrievalTests(unittest.TestCase):
    def test_upsert_memory_merges_facts(self) -> None:
        mem = ProposalMemory()
        mem = upsert_memory(
            mem, "rfp_understanding", {"clientName": "City of X", "cms": "Drupal"}
        )
        self.assertEqual(mem.facts["clientName"], "City of X")
        self.assertIn("rfp_understanding", mem.updated_by)

    def test_writing_evidence_sources_blocked(self) -> None:
        self.assertTrue(is_writing_evidence_source("03_CS_City_Website.pdf"))
        self.assertTrue(is_writing_evidence_source("04_Bio_Sonja.pdf"))
        self.assertFalse(is_writing_evidence_source("00_Guide_Pricing.pdf"))
        self.assertFalse(is_writing_evidence_source("playbook_qa.pdf"))

    def test_intelligence_buckets_defined(self) -> None:
        self.assertIn("won_patterns", INTELLIGENCE_BUCKETS)
        self.assertIn("pricing", INTELLIGENCE_BUCKETS)


if __name__ == "__main__":
    unittest.main()
