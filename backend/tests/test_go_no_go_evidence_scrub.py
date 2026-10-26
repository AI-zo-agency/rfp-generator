"""Go/No-Go must not ship fabricated certs or cross-stitched case-study metrics."""

from __future__ import annotations

import unittest

from app.models.go_no_go import GoNoGoCapabilityRow
from app.services.go_no_go_adjudicator import salvage_grounded_quote
from app.services.go_no_go_evidence_scrub import (
    scrub_capability_row,
    scrub_evidence_text,
)


class FabricatedCertScrubTests(unittest.TestCase):
    def test_strips_planet_bcorp_linkedin(self) -> None:
        raw = (
            "zö agency is certified as WBENC, 1% for the Planet, B-Corporate, "
            "and LinkedIn Gold-Certified."
        )
        scrubbed, logs = scrub_evidence_text(raw)
        self.assertIn("one_percent_planet", logs)
        self.assertIn("b_corp", logs)
        self.assertIn("linkedin_gold", logs)
        self.assertNotIn("1% for the Planet", scrubbed)
        self.assertNotIn("LinkedIn Gold", scrubbed)
        self.assertNotIn("B-Corporate", scrubbed)

    def test_cert_row_rewrites_to_wbenc_wosb(self) -> None:
        row = GoNoGoCapabilityRow(
            requirement=(
                "Minority, Women, Service Disabled Veteran Owned, "
                "or Emerging Small Business certification"
            ),
            status="verified",
            kbSource="02_MasterTemplate_CompanyOverview.pdf",
            evidence=(
                "zö agency is certified as WBENC, 1% for the Planet, "
                "B-Corporate, and LinkedIn Gold-Certified."
            ),
            isCore=True,
            category="compliance",
        )
        out = scrub_capability_row(row)
        self.assertEqual(out.status, "verified")
        self.assertIn("WBENC", out.evidence)
        self.assertIn("WOSB", out.evidence)
        self.assertNotIn("1% for the Planet", out.evidence or "")
        self.assertNotIn("LinkedIn Gold", out.evidence or "")
        self.assertNotIn("B-Corp", out.evidence or "")


class ForeignMetricScrubTests(unittest.TestCase):
    def test_drops_early_admissions_from_festival_evidence(self) -> None:
        raw = (
            "zö agency launched a multi-channel marketing campaign for Rock the "
            "Locks festival, achieving record ticket sales, high PR reach, and "
            "accelerated early admissions without increasing budget."
        )
        scrubbed, logs = scrub_evidence_text(raw)
        self.assertIn("early_admissions", logs)
        self.assertNotIn("early admissions", scrubbed.casefold())
        self.assertIn("Rock the Locks", scrubbed)
        self.assertIn("ticket sales", scrubbed)


class SalvageAnchorTests(unittest.TestCase):
    def test_rock_the_locks_paraphrase_does_not_salvage_admissions_sentence(self) -> None:
        mega = (
            "Rock the Locks festival: multi-channel marketing campaign with "
            "record ticket sales and high PR reach without increasing budget.\n\n"
            "Benedictine University: enrollment campaign accelerated early "
            "admissions and boosted applications year over year."
        )
        paraphrased = (
            "zö agency launched a multi-channel marketing campaign for Rock the "
            "Locks festival, achieving record ticket sales, high PR reach, and "
            "accelerated early admissions without increasing budget."
        )
        salvaged = salvage_grounded_quote(paraphrased, mega)
        self.assertIsNotNone(salvaged)
        self.assertIn("Rock the Locks", salvaged or "")
        self.assertNotIn("early admissions", (salvaged or "").casefold())


class OrthogonalEvidenceTests(unittest.TestCase):
    def test_insurance_quote_rejected_for_audit_requirement(self) -> None:
        from app.services.go_no_go_adjudicator import (
            quote_evidences_requirement,
            quote_has_expired_coverage_date,
            rows_from_assessments,
            build_adjudication_payload,
        )
        from app.services.go_no_go_requirements import RfpRequirement

        reqs = [
            RfpRequirement(
                requirement="Compliance with audit and evaluation requirements",
                category="compliance",
                isCore=True,
            )
        ]
        hit = {
            "title": "01_companyfacts.pdf",
            "content": (
                "Commercial General Liability $1,000,000 / $2,000,000 aggregate. "
                "Policy period expires 2019-02-13."
            ),
        }
        _body, sources, full = build_adjudication_payload(
            reqs, {reqs[0].requirement: [hit]}
        )
        self.assertFalse(
            quote_evidences_requirement(
                reqs[0].requirement,
                "Commercial General Liability $1,000,000 / $2,000,000 aggregate.",
            )
        )
        self.assertTrue(
            quote_has_expired_coverage_date(
                "Commercial General Liability coverage expires 2019-02-13."
            )
        )
        rows, rejected, _rec = rows_from_assessments(
            reqs,
            [
                {
                    "requirement": reqs[0].requirement,
                    "status": "verified",
                    "kbSource": "01_companyfacts.pdf",
                    "quote": (
                        "Commercial General Liability $1,000,000 / $2,000,000 "
                        "aggregate. Policy period expires 2019-02-13."
                    ),
                }
            ],
            sources,
            full_sources=full,
        )
        self.assertEqual(rows[0].status, "gap")
        self.assertTrue(rejected)

    def test_wbenc_rejected_for_eeo_and_ors_asks(self) -> None:
        from app.services.go_no_go_adjudicator import quote_evidences_requirement

        wbenc = "zö agency is a Women Business Enterprise (WBENC/WOSB) registered in Oregon"
        self.assertFalse(
            quote_evidences_requirement(
                "Equal opportunity and non-discrimination policy affirmation",
                wbenc,
            )
        )
        self.assertFalse(
            quote_evidences_requirement(
                "Compliance with Oregon Revised Statutes (ORS) and public sector "
                "contracting requirements",
                wbenc,
            )
        )
        self.assertTrue(
            quote_evidences_requirement(
                "Minority, Women, Service Disabled Veteran Owned, or Emerging "
                "Small Business certification",
                wbenc,
            )
        )

    def test_municipal_services_rejected_for_bonding_insurance(self) -> None:
        from app.services.go_no_go_adjudicator import quote_evidences_requirement

        self.assertFalse(
            quote_evidences_requirement(
                "Ability to contract with public university and meet bonding/"
                "insurance requirements",
                "zö agency services nonprofits, municipalities, and private businesses.",
            )
        )

    def test_ownership_cert_row_rewrites_to_wbenc_wosb(self) -> None:
        row = GoNoGoCapabilityRow(
            requirement=(
                "Minority, Women, Service Disabled Veteran Owned, "
                "or Emerging Small Business certification (if applicable)"
            ),
            status="verified",
            evidence=(
                "Sonja Anderson is the sole owner of zö agency and holds 51% "
                "ownership as a woman-owned business"
            ),
            isCore=True,
            category="compliance",
        )
        out = scrub_capability_row(row)
        self.assertIn("WBENC", out.evidence or "")
        self.assertIn("WOSB", out.evidence or "")
        self.assertNotIn("51%", out.evidence or "")


if __name__ == "__main__":
    unittest.main()


class ProposalCommitmentEvidenceTests(unittest.TestCase):
    """A won proposal's plans are not delivered work (Minnesota Lottery / Maricopa)."""

    def _row(self, evidence: str, source: str, status: str = "verified") -> GoNoGoCapabilityRow:
        return GoNoGoCapabilityRow(
            requirement="Creative production across video, radio, digital",
            status=status,
            isCore=True,
            category="service",
            evidence=evidence,
            kbSource=source,
        )

    def test_task_list_line_from_won_proposal_caps_at_partial(self) -> None:
        row = scrub_capability_row(
            self._row(
                "Produce broadcast-quality video for TV/cinema",
                "06_WON_MaricopaCounty_Proposal_2025.pdf",
            )
        )
        self.assertEqual(row.status, "partial")
        self.assertIn("not delivered work", row.downgrade_reason)

    def test_will_help_promise_caps_at_partial(self) -> None:
        row = scrub_capability_row(
            self._row(
                "zö agency will help Maricopa County strengthen its connection with residents.",
                "06_WON_MaricopaCounty_Proposal_2025.pdf",
            )
        )
        self.assertEqual(row.status, "partial")

    def test_past_delivery_in_won_proposal_stays_verified(self) -> None:
        row = scrub_capability_row(
            self._row(
                "Over multiple years, we've completed more than 200 projects for Deschutes Public Library.",
                "06_WON_CityofUmatilla_Proposal_2026.pdf",
            )
        )
        self.assertEqual(row.status, "verified")

    def test_case_study_is_not_touched(self) -> None:
        row = scrub_capability_row(
            self._row(
                "We will keep producing the video series next season.",
                "03_CS_Hampton Lumber_Educational Packet_2025.pdf",
            )
        )
        self.assertEqual(row.status, "verified")


class ProposalSelfDescriptionTests(unittest.TestCase):
    def test_years_of_experience_line_caps_at_partial(self) -> None:
        """'30+ years of combined experience' is the agency describing itself, not proof."""
        row = scrub_capability_row(
            GoNoGoCapabilityRow(
                requirement="Omnichannel media planning and buying",
                status="verified",
                isCore=True,
                category="service",
                evidence="Media Strategy & Buying: Traditional and digital media planning with 30+ years of combined experience",
                kbSource="06_WON_CityofUmatilla_Proposal_2026.pdf",
            )
        )
        self.assertEqual(row.status, "partial")
        self.assertIn("describing itself", row.downgrade_reason)
