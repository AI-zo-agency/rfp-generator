"""`_infer_claim_from_entry` must derive a claim from the requirement, not
fall through to a generic "experience" default for anything it doesn't
recognize.

Real defect: a digital-advertising RFP section fell through the keyword
ladder (no "website"/"tourism"/"brand" match) straight to "experience", the
claim that bypasses the Evidence Trust Gate's work-type check entirely — so a
brand-messaging case study with no digital advertising component was treated
as equally valid evidence as a genuine geofencing/digital-ad case study.
"""

from __future__ import annotations

import unittest

from app.services.proposal_intelligence.jit_retrieval import _infer_claim_from_entry
from app.services.proposal_intelligence.schemas import RetrievalEntry


class InferClaimFromEntryTests(unittest.TestCase):
    def test_digital_advertising_requirement_does_not_default_to_experience(self) -> None:
        entry = RetrievalEntry(
            sectionId="rfp-sec-4",
            requiredAssets=["digital advertising campaign with geofencing"],
            queries=["zö agency 03_CS geofencing digital advertising"],
            whyNeeded="RFP requires proof of similar digital advertising work",
        )
        claim = _infer_claim_from_entry(entry)
        self.assertNotEqual(claim, "experience")
        self.assertIn("digital", claim)

    def test_generic_past_performance_requirement_still_maps_to_experience(self) -> None:
        entry = RetrievalEntry(
            sectionId="rfp-sec-9",
            requiredAssets=["past performance references"],
            queries=["zö agency past performance"],
            whyNeeded="General references section",
        )
        self.assertEqual(_infer_claim_from_entry(entry), "experience")

    def test_website_requirement_still_maps_to_website_build(self) -> None:
        entry = RetrievalEntry(
            sectionId="rfp-sec-2",
            requiredAssets=["municipal website redesign case study"],
            queries=["zö agency website build"],
            whyNeeded="RFP requires a web build example",
        )
        self.assertEqual(_infer_claim_from_entry(entry), "website_build")

    def test_empty_entry_falls_back_to_experience(self) -> None:
        entry = RetrievalEntry(sectionId="rfp-sec-1")
        self.assertEqual(_infer_claim_from_entry(entry), "experience")


if __name__ == "__main__":
    unittest.main()
