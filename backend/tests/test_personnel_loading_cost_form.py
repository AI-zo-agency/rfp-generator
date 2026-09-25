"""Cost Proposal format: personnel_loading from agent budgetFormat (not synonym regex)."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_integrity_guards import scrub_ungrounded_case_study_percent_metrics


class CaseStudyMetricScrubTests(unittest.TestCase):
    def test_removes_percent_absent_from_source(self) -> None:
        prose = (
            "Challenge\nSF Travel needed media support.\n\n"
            "Solution / Our Approach\nWe ran paid social that increased bookings by 22%.\n"
        )
        source = "San Francisco Travel campaign. Qualitative brand lift. No quantified bookings."
        cleaned, logs = scrub_ungrounded_case_study_percent_metrics(
            prose, source_text=source
        )
        self.assertTrue(logs)
        self.assertNotIn("22%", cleaned)

    def test_removes_impressions_clicks_ctr_absent_from_source(self) -> None:
        prose = (
            "Oregon Employment Department campaign.\n\n"
            "Campaign generated 392,346 impressions and 568 clicks in October "
            "2025 with a 0.14% CTR.\n\n"
            "The contract was renewed three consecutive times."
        )
        source = (
            "03_CS Oregon Employment Department. Qualitative KPIs consistently "
            "crush industry standards. Contract renewed three consecutive times."
        )
        cleaned, logs = scrub_ungrounded_case_study_percent_metrics(
            prose, source_text=source
        )
        self.assertTrue(logs)
        self.assertNotIn("392,346", cleaned)
        self.assertNotIn("568 clicks", cleaned)
        self.assertNotIn("0.14%", cleaned)
        self.assertIn("renewed three consecutive times", cleaned)

    def test_keeps_volume_metrics_present_in_source(self) -> None:
        prose = "Campaign generated 392,346 impressions in October 2025."
        source = "The flight delivered 392,346 impressions in October 2025."
        cleaned, logs = scrub_ungrounded_case_study_percent_metrics(
            prose, source_text=source
        )
        self.assertFalse(logs)
        self.assertIn("392,346 impressions", cleaned)

    def test_keeps_classification_hourly_rate_schedule_table(self) -> None:
        """Year-2 % Increase columns must not wipe Cost rate tables as CS metrics."""
        schedule = (
            "## Hourly Rate Schedule by Classification\n\n"
            "Billable rates from the agency fee schedule.\n\n"
            "| Role / Labor Category | Hourly Rate (billable) | Year-2 % Increase | Year-3 % Increase |\n"
            "| --- | ---: | ---: | ---: |\n"
            "| Account Manager | $275 | — | — |\n"
            "| Agency Director | $400 | — | — |\n"
        )
        source = "Case study: tourism board campaign. Qualitative brand lift only."
        cleaned, logs = scrub_ungrounded_case_study_percent_metrics(
            schedule, source_text=source
        )
        self.assertFalse(logs)
        self.assertIn("| Account Manager | $275 |", cleaned)
        self.assertIn("| Agency Director | $400 |", cleaned)

    def test_budget_section_skipped_by_draft_metric_scrub(self) -> None:
        from app.services.proposal_integrity_guards import (
            apply_case_study_metric_scrub_to_draft,
        )

        body = (
            "## Hourly Rate Schedule by Classification\n\n"
            "| Role / Labor Category | Hourly Rate (billable) | Year-2 % Increase | Year-3 % Increase |\n"
            "| --- | ---: | ---: | ---: |\n"
            "| Account Manager | $275 | — | — |\n"
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="rfp-eval-5",
                    title="Cost, rate structure, and overall value",
                    content=body,
                )
            ],
        )
        out, logs = apply_case_study_metric_scrub_to_draft(
            draft, source_text="No percentages in case studies."
        )
        self.assertEqual(logs, [])
        self.assertIn("| Account Manager | $275 |", out.sections[0].content or "")


if __name__ == "__main__":
    unittest.main()
