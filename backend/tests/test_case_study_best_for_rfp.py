"""Helpers + tests: Section 3 must pick RFP-best case studies, not catalog filler."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.company_qualification.agents import evidence_selection as es
from app.services.company_qualification.schemas import EvidenceCandidate, ProposalContext
from app.services.proposal_case_study_fit import (
    CaseStudyCandidate,
    CaseStudyFitReport,
    CaseStudyFitResult,
    capabilities_for_case_study_fit,
    select_best_case_study_titles,
)


def _cand(source: str, score: float) -> CaseStudyCandidate:
    return CaseStudyCandidate(
        source=source,
        excerpt=f"excerpt for {source}",
        fitScore=score,
        fitLabel="strong_fit" if score >= 0.3 else "weak_fit",
        matchedTerms=["geo"] if score >= 0.3 else [],
    )


class SelectBestCaseStudyTitlesTests(unittest.TestCase):
    def test_prefers_strong_fits_and_skips_weak_filler(self) -> None:
        report = CaseStudyFitReport(
            results=[
                CaseStudyFitResult(
                    capability="digital advertising / geofencing",
                    candidates=[
                        _cand("03_CS_Oregon_Geofencing.pdf", 0.72),
                        _cand("03_CS_Deschutes_Brand.pdf", 0.12),
                        _cand("03_CS_Website_Redesign.pdf", 0.08),
                    ],
                    gap=False,
                ),
                CaseStudyFitResult(
                    capability="paid social campaigns",
                    candidates=[
                        _cand("03_CS_Paid_Social_Tourism.pdf", 0.55),
                        _cand("03_CS_Deschutes_Brand.pdf", 0.10),
                    ],
                    gap=False,
                ),
            ]
        )
        titles = select_best_case_study_titles(report, min_count=2, max_count=4)
        self.assertEqual(
            titles,
            [
                "03_CS_Oregon_Geofencing.pdf",
                "03_CS_Paid_Social_Tourism.pdf",
            ],
        )
        self.assertNotIn("03_CS_Deschutes_Brand.pdf", titles)
        self.assertNotIn("03_CS_Website_Redesign.pdf", titles)

    def test_does_not_pad_with_weak_fits_to_hit_min(self) -> None:
        report = CaseStudyFitReport(
            results=[
                CaseStudyFitResult(
                    capability="geofencing",
                    candidates=[
                        _cand("03_CS_Oregon_Geofencing.pdf", 0.8),
                        _cand("03_CS_Unrelated.pdf", 0.05),
                    ],
                    gap=False,
                )
            ]
        )
        titles = select_best_case_study_titles(report, min_count=3, max_count=5)
        self.assertEqual(titles, ["03_CS_Oregon_Geofencing.pdf"])

    def test_capabilities_prefer_services_over_generic_sector_filler(self) -> None:
        caps = capabilities_for_case_study_fit(
            services_requested=["Digital advertising including geofencing"],
            rfp_context="Vendor must run paid digital advertising and geofencing campaigns.",
            rfp_sector="government",
        )
        joined = " ".join(caps).casefold()
        self.assertTrue(any("geofenc" in c.casefold() or "digital" in c.casefold() for c in caps))
        self.assertNotIn("case study project outcomes", joined)


class EvidenceSelectionFitTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_fit_ranked_titles_not_catalog_padding(self) -> None:
        context = ProposalContext(
            proposalType="government",
            industry="tourism",
            servicesRequested=["Digital advertising including geofencing"],
            summary="Need geofencing and paid media proof.",
        )
        candidates = [
            EvidenceCandidate(
                title="Deschutes Brand Messaging",
                snippet="Brand strategy and messaging framework",
                source="03_CS_Deschutes_Brand.pdf",
            ),
            EvidenceCandidate(
                title="Website Redesign",
                snippet="Full website redesign and CMS migration",
                source="03_CS_Website_Redesign.pdf",
            ),
            EvidenceCandidate(
                title="Oregon Geofencing",
                snippet="Geofencing digital advertising campaign",
                source="03_CS_Oregon_Geofencing.pdf",
            ),
        ]
        fit_report = CaseStudyFitReport(
            results=[
                CaseStudyFitResult(
                    capability="Digital advertising including geofencing",
                    candidates=[_cand("03_CS_Oregon_Geofencing.pdf", 0.9)],
                    gap=False,
                )
            ]
        )

        with (
            patch.object(
                es,
                "load_client_list_registry",
                new=AsyncMock(return_value=type("R", (), {"entries": []})()),
            ),
            patch.object(
                es,
                "assess_case_study_fit",
                new=AsyncMock(return_value=fit_report),
            ),
            patch.object(
                es.llm,
                "chat_json",
                new=AsyncMock(side_effect=AssertionError("LLM must not run when fit is enough")),
            ),
        ):
            result, provider = await es.run_evidence_selection_agent(
                proposal_context=context,
                rfp_context="Digital advertising RFP requiring geofencing campaigns.",
                rfp_client="Acme County",
                candidates=candidates,
                rfp_sector="government",
            )

        self.assertEqual(provider, "case_study_fit")
        self.assertEqual(result.selected_studies, ["Oregon Geofencing"])
        self.assertNotIn("Deschutes Brand Messaging", result.selected_studies)
        self.assertNotIn("Website Redesign", result.selected_studies)


if __name__ == "__main__":
    unittest.main()
