"""Cost Proposal format: personnel_loading from agent budgetFormat (not synonym regex)."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_budget_content import (
    apply_rfp_required_budget_instrument,
    extract_rfp_labor_role_labels,
    render_budget_markdown,
    reshape_budget_for_rfp_form,
)
from app.services.proposal_integrity_guards import scrub_ungrounded_case_study_percent_metrics


class PersonnelLoadingRenderTests(unittest.TestCase):
    def test_role_labels_assist_when_rfp_lists_roles(self) -> None:
        rfp = (
            "Provide hourly rates for each of the following roles.\n"
            "1. Account Director\n"
            "2. Senior Strategist\n"
            "3. Creative Director\n"
        )
        roles = extract_rfp_labor_role_labels(rfp)
        self.assertIn("Account Director", roles)
        self.assertIn("Senior Strategist", roles)

    def test_render_uses_budget_format_not_rfp_scan(self) -> None:
        rfp = "Optional narrative budget only — no hourly table required."
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="personnel_loading",
            optionTermNotes="Year-2 increase: 3%. Year-3 increase: 3%.",
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="labor",
                    description="Media Buyer — paid social",
                    roleTitle="Media Buyer",
                    unit="hours",
                    quantity=1,
                    rate=165,
                    extended=165,
                    lineItemType="agency_fee",
                ),
            ],
        )
        md = render_budget_markdown(budget, rfp_text=rfp)
        self.assertIn("Hourly Rate Schedule", md)
        self.assertIn("Media Buyer", md)
        self.assertIn("$165", md)
        self.assertNotIn("Fee Detail by Phase", md)

    def test_phased_format_keeps_phase_table(self) -> None:
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="phased",
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="Discovery",
                    description="Phase 1 Discovery",
                    unit="flat",
                    quantity=1,
                    rate=5000,
                    extended=5000,
                    lineItemType="agency_fee",
                ),
            ],
        )
        md = render_budget_markdown(budget, rfp_text="")
        self.assertIn("Fee Detail by Phase", md)

    def test_reshape_forces_from_budget_format(self) -> None:
        rfp = "Any RFP text — format comes from the budget object."
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="budget",
                    title="Cost Proposal",
                    content="## Fee Detail by Phase\n| Phase | Amount |\n",
                )
            ],
        )
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="personnel_loading",
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="labor",
                    description="Strategist",
                    unit="hours",
                    quantity=1,
                    rate=150,
                    extended=150,
                    lineItemType="agency_fee",
                )
            ],
        )
        updated = reshape_budget_for_rfp_form(draft, budget, rfp_text=rfp)
        self.assertIsNotNone(updated)
        body = updated.sections[0].content or ""
        self.assertIn("Hourly Rate Schedule", body)

    def test_apply_rfp_required_budget_instrument(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="budget",
                    title="Budget & Pricing",
                    content="## Fee Detail by Phase\n| Phase | Amount |\n| Discovery | $5,000 |\n",
                )
            ],
        )
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="personnel_loading",
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="labor",
                    description="Account Director",
                    roleTitle="Account Director",
                    unit="hours",
                    quantity=1,
                    rate=200,
                    extended=200,
                    lineItemType="agency_fee",
                )
            ],
        )
        new_draft, new_budget, changed = apply_rfp_required_budget_instrument(
            draft, budget, rfp_text="ignored for format"
        )
        self.assertTrue(changed)
        self.assertEqual(new_budget.budget_format, "personnel_loading")
        self.assertIn("Hourly Rate Schedule", new_draft.sections[0].content or "")


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
        from app.models.proposal import ProposalDraft, ProposalSection
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
