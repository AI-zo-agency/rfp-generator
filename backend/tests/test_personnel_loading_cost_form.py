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

    def test_schedule_prefers_verified_rates_over_noisy_descriptions(self) -> None:
        from app.models.proposal import VerifiedRate
        from app.services.proposal_budget_content import (
            render_personnel_loading_form_markdown,
        )

        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="personnel_loading",
            verifiedRates=[
                VerifiedRate(
                    personName="",
                    role="Account Manager",
                    hourlyRate=275.0,
                    source="Labor Cost",
                ),
                VerifiedRate(
                    personName="",
                    role="Creative Director",
                    hourlyRate=275.0,
                    source="Labor Cost",
                ),
            ],
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="labor",
                    description=(
                        "Copywriter classification (closest KB labor category"
                    ),
                    roleTitle="Copywriter classification (closest KB labor category",
                    unit="hour",
                    rate=275.0,
                    quantity=1,
                    extended=275.0,
                ),
                BudgetLineItem(
                    id="li-2",
                    category="labor",
                    description="Account Manager classification",
                    roleTitle="Account Manager classification",
                    unit="hour",
                    rate=275.0,
                    quantity=1,
                    extended=275.0,
                ),
            ],
        )
        md = render_personnel_loading_form_markdown(budget)
        self.assertIn("| Account Manager | $275 |", md)
        self.assertIn("| Creative Director | $275 |", md)
        self.assertNotIn("closest KB", md)
        self.assertNotIn("classification", md)
        # One clean AM row — not duplicated from noisy line item.
        self.assertEqual(md.count("| Account Manager |"), 1)

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
                    description="Account Manager — paid social",
                    roleTitle="Account Manager",
                    unit="hours",
                    quantity=1,
                    rate=275,
                    extended=275,
                    lineItemType="agency_fee",
                ),
            ],
        )
        md = render_budget_markdown(budget, rfp_text=rfp)
        self.assertIn("Hourly Rate Schedule", md)
        self.assertIn("Account Manager", md)
        self.assertIn("$275", md)
        self.assertNotIn("Fee Detail by Phase", md)
        self.assertNotIn("Proposed Investment", md)

    def test_on_call_tm_schedule_billing_no_flat_phase(self) -> None:
        rfp = (
            "Provide a Cost Proposal / Schedule of Billing Rates as a separate Cost File.\n"
            "City shall pay Consultant on a time and expense not-to-exceed basis.\n"
            "Consultant shall provide a Letter Proposal for Services requested.\n"
            "No billing rate changes shall be made during the term of this Agreement.\n"
            "Any reimbursable items shall be included and identified.\n"
        )
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="personnel_loading",
            lumpSumTotal=3000,
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="labor",
                    description="Account Manager",
                    roleTitle="Account Manager",
                    unit="hour",
                    quantity=1,
                    rate=275,
                    extended=275,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="li-2",
                    category="labor",
                    description="Programming",
                    roleTitle="Programming",
                    unit="hour",
                    quantity=1,
                    rate=400,
                    extended=400,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="li-3",
                    category="labor",
                    description="Contractor",
                    roleTitle="Contractor",
                    unit="hour",
                    quantity=1,
                    rate=275,
                    extended=275,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="li-4",
                    category="labor",
                    description="Executive",
                    roleTitle="Executive",
                    unit="hour",
                    quantity=1,
                    rate=275,
                    extended=275,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="li-5",
                    category="labor",
                    description="Finance",
                    roleTitle="Finance",
                    unit="hour",
                    quantity=1,
                    rate=275,
                    extended=275,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="li-6",
                    category="reimbursable",
                    description=(
                        "Travel, location fees/permits for photography/videography, "
                        "specialized software licenses, and stock media"
                    ),
                    unit="project",
                    quantity=0,
                    rate=0,
                    extended=0,
                    lineItemType="direct_expense",
                ),
            ],
        )
        md = render_budget_markdown(budget, rfp_text=rfp)
        self.assertIn("Hourly Rate Schedule", md)
        self.assertIn("Account Manager", md)
        self.assertIn("Programming", md)
        self.assertIn("$400", md)
        self.assertIn("Contractor", md)
        self.assertIn("| Executive |", md)
        self.assertIn("| Finance |", md)
        self.assertNotIn("Senior Web Developer", md)
        self.assertNotIn("This table answers", md)
        self.assertNotIn("Do not substitute", md)
        self.assertNotIn("Year-1 Hourly Rate", md)
        self.assertNotIn("Proposed Investment", md)
        self.assertNotIn("$3,000", md)
        self.assertNotIn("Fee Detail", md)
        self.assertNotIn("flat phase", md.casefold())
        self.assertIn("Letter Proposal", md)
        self.assertIn("billed monthly", md.casefold())
        self.assertIn("held for the full contract term", md.casefold())
        self.assertIn("Reimbursable Expenses", md)
        self.assertIn("Travel", md)

    def test_hourly_nte_instrument_emits_task_hours_ledger(self) -> None:
        from app.models.pricing_instrument import PricingInstrument, PricingTrack
        from app.models.proposal import VerifiedRate

        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="t",
            budgetFormat="personnel_loading",
            rfpBudgetCap=950_000.0,
            commissionRate=0.15,
            verifiedRates=[
                VerifiedRate(
                    personName="",
                    role="Account Manager",
                    hourlyRate=275,
                    source="Labor Cost",
                ),
                VerifiedRate(
                    personName="",
                    role="Programming",
                    hourlyRate=400,
                    source="Labor Cost",
                ),
                VerifiedRate(
                    personName="",
                    role="Creative Director",
                    hourlyRate=275,
                    source="Labor Cost",
                ),
            ],
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="labor",
                    description="Account Manager",
                    roleTitle="Account Manager",
                    unit="hour",
                    quantity=1,
                    rate=275,
                    extended=275,
                    lineItemType="agency_fee",
                ),
            ],
        )
        instrument = PricingInstrument(
            kind="personnel_loading",
            confidence=0.9,
            tracks=[
                PricingTrack(
                    id="t1",
                    label="Compensation",
                    nteAnnual=950_000.0,
                    asksHourly=True,
                    asksHours=True,
                )
            ],
        )
        md = render_budget_markdown(
            budget, rfp_text="SOW cost proposal.", pricing_instrument=instrument
        )
        self.assertIn("Task Hours & Cost Ledger", md)
        self.assertIn("MANUAL FILL: hours", md)
        self.assertIn("Account Manager", md)
        self.assertIn("Programming", md)
        self.assertIn("950,000", md)
        self.assertIn("85%", md)
        self.assertIn("15%", md)
        self.assertIn("Guide 6.1", md)
        self.assertNotIn("This table answers", md)
        self.assertNotIn("master contract", md.casefold())
        self.assertNotIn("pass-through at cost", md.casefold())

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
                    description="Account Manager",
                    roleTitle="Account Manager",
                    unit="hours",
                    quantity=1,
                    rate=275,
                    extended=275,
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
                    description="Account Manager",
                    roleTitle="Account Manager",
                    unit="hours",
                    quantity=1,
                    rate=275,
                    extended=275,
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
