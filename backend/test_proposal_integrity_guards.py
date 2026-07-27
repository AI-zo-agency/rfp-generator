"""Tests for proposal integrity guards (references, tier, case-study fidelity)."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalSection
from app.services.evidence_trust.rfp_hard_facts import extract_rfp_hard_facts
from app.services.proposal_integrity_guards import (
    apply_manuscript_integrity_guards,
    case_study_fidelity_ok,
    enforce_pricing_tier_for_cost_weight,
    infer_cost_weight_pct,
    scrub_reference_withholding,
)


class TestReferenceIntegrity(unittest.TestCase):
    def test_upon_request_replaced(self) -> None:
        text = (
            "Oregon Employment Department\n"
            "Reference contact details available upon request.\n"
            "We have pre-cleared all three for direct contact by Tarrant County's "
            "evaluation team, and each has agreed to respond to reference checks."
        )
        out, logs = scrub_reference_withholding(text)
        self.assertIn("[VERIFY: reference contact", out)
        self.assertNotIn("upon request", out.casefold())
        self.assertNotIn("pre-cleared", out.casefold())
        self.assertTrue(logs)

    def test_draft_guard_on_references_section(self) -> None:
        draft = ProposalDraft(
            rfpId="t1",
            sections=[
                ProposalSection(
                    id="rfp-ref",
                    title="21. References",
                    content="Client X — contact information available upon request.",
                    status="generated",
                    source="rfp",
                    mode="write",
                )
            ],
            updatedAt="2026-01-01T00:00:00Z",
        )
        updated, logs = apply_manuscript_integrity_guards(draft)
        body = updated.sections[0].content
        self.assertIn("[VERIFY:", body)
        self.assertNotIn("upon request", body.casefold())
        self.assertTrue(logs)


class TestPricingTierGuard(unittest.TestCase):
    def test_force_low_when_cost_heavy(self) -> None:
        budget = ProposalBudget(
            rfpId="t1",
            pricingTier="Average",
            feeStructure="Pricing is built from the industry Average tier in our approved Pricing Guide.",
            lineItems=[],
            updatedAt="2026-01-01T00:00:00Z",
        )
        out, logs = enforce_pricing_tier_for_cost_weight(budget, cost_weight_pct=35.0)
        self.assertEqual(out.pricing_tier, "Low")
        self.assertTrue(any("Low" in f for f in (out.pricing_flags or [])))
        self.assertIn("Low tier", out.fee_structure)
        self.assertTrue(logs)

    def test_infer_cost_weight_from_points_phrase(self) -> None:
        text = "Price is worth 350 of 1,000 points — 35%, the single largest scoring category."
        pct = infer_cost_weight_pct(text, None)
        self.assertIsNotNone(pct)
        assert pct is not None
        self.assertGreaterEqual(pct, 30)
        self.assertLessEqual(pct, 40)


class TestHardFactsLargeScale(unittest.TestCase):
    def test_accepts_points_over_100(self) -> None:
        text = (
            "Evaluation criteria — points will be awarded as follows:\n"
            "Qualifications: 300 points\n"
            "Portfolio: 300 points\n"
            "Price: 350 points\n"
            "Total: 950 points\n"
        )
        facts = extract_rfp_hard_facts(text)
        joined = " ".join(facts.get("evaluation_lines") or [])
        self.assertIn("350", joined)
        self.assertGreaterEqual(int(facts.get("evaluation_total") or 0), 900)


class TestCaseStudyFidelity(unittest.TestCase):
    def test_generic_rewrite_fails(self) -> None:
        source = (
            "City of Umatilla Digital Campaign 2006. Rock the Locks Festival drove "
            "ticket sales and VIP sellouts in a compressed launch window for festival attendance."
        )
        generic = (
            "a digital campaign tied to municipal communications and community outreach. "
            "Existing outreach lacked the structure to track results or scale across channels."
        )
        ok, reason = case_study_fidelity_ok(source, generic)
        self.assertFalse(ok)
        self.assertIn("Rock the Locks", reason)

    def test_faithful_write_passes(self) -> None:
        source = (
            "City of Umatilla Digital Campaign 2006. Rock the Locks Festival drove "
            "ticket sales and VIP sellouts."
        )
        written = (
            "For the City of Umatilla, we built Rock the Locks Festival — a digital and "
            "traditional campaign that sold out VIP packages and outperformed prior ticket benchmarks."
        )
        ok, _ = case_study_fidelity_ok(source, written)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
