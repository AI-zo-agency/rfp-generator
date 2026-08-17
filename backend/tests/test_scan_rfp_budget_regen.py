"""Scan RFP regenerates Phase 3.5 budget when the whole budget is missing."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services.proposal_fulfill_rfp_budget_kpi import (
    manuscript_budget_is_missing,
    manuscript_cost_section_is_hollow,
    pricing_model_lacks_professional_fees,
    run_fulfill_budget_scan,
)


def _rfp(rfp_id: str = "rfp-regen") -> RfpRecord:
    return RfpRecord(
        id=rfp_id,
        title="T",
        client="C",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="n",
    )


class ManuscriptBudgetMissingTests(unittest.TestCase):
    def test_missing_when_no_research_budget(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-regen",
            sections=[
                ProposalSection(
                    id="sec-1",
                    title="Approach",
                    content="We will deliver the work.",
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        self.assertTrue(manuscript_budget_is_missing(draft, None))
        research = ProposalResearchCache(
            rfpId="rfp-regen", updatedAt="2026-08-05T00:00:00+00:00"
        )
        self.assertTrue(manuscript_budget_is_missing(draft, research))

    def test_present_when_budget_model_exists(self) -> None:
        budget = ProposalBudget(
            rfpId="rfp-regen",
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    description="Strategy",
                    category="Labor",
                    extended=5000,
                )
            ],
            lumpSumTotal=5000,
            agencyRevenueEstimate=5000,
        )
        research = ProposalResearchCache(
            rfpId="rfp-regen",
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=budget,
        )
        draft = ProposalDraft(
            rfpId="rfp-regen",
            sections=[],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        self.assertFalse(manuscript_budget_is_missing(draft, research))

    def test_travel_only_cost_proposal_counts_as_missing(self) -> None:
        hollow = (
            "Proposed Investment\n"
            "Direct travel / reimbursables: $2,500\n"
            "Total proposed investment: $2,500 Rates follow zö's Industry Average "
            "pricing guide for comparable municipal / education marketing engagements.\n\n"
            "Complete website redesign. Total proposed investment: $2,500 "
            "($2,500 in direct travel expenses).\n"
        )
        self.assertTrue(manuscript_cost_section_is_hollow(hollow))
        travel_budget = ProposalBudget(
            rfpId="rfp-regen",
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="T1",
                    description="Travel / reimbursables",
                    category="Travel",
                    extended=2500,
                )
            ],
            lumpSumTotal=2500,
            directExpensesTotal=2500,
            agencyRevenueEstimate=0,
        )
        self.assertTrue(pricing_model_lacks_professional_fees(travel_budget))
        draft = ProposalDraft(
            rfpId="rfp-regen",
            updatedAt="2026-08-05T00:00:00+00:00",
            sections=[
                ProposalSection(
                    id="sec-cost",
                    title=(
                        "Cost Proposal That Includes All Charges, Including "
                        "One-Time Build and Migration and One-Time Recommended On-Site Training"
                    ),
                    content=hollow,
                    status="generated",
                )
            ],
        )
        research = ProposalResearchCache(
            rfpId="rfp-regen",
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=travel_budget,
        )
        self.assertTrue(manuscript_budget_is_missing(draft, research))


class BudgetRegenWiringTests(unittest.TestCase):
    def test_missing_budget_calls_phase_3_5(self) -> None:
        rfp_id = "rfp-regen"
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="sec-1",
                    title="Approach",
                    content="We will deliver the work.",
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId=rfp_id, updatedAt="2026-08-05T00:00:00+00:00"
        )
        regenerated_budget = ProposalBudget(
            rfpId=rfp_id,
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    description="Strategy",
                    category="Labor",
                    extended=5000,
                )
            ],
            lumpSumTotal=5000,
            agencyRevenueEstimate=5000,
        )
        regenerated_research = research.model_copy(update={"budget": regenerated_budget})
        regenerated_draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="section-budget-pricing",
                    title="Budget & Pricing",
                    content="| Line | Cost |\n|---|---|\n| Strategy | $5,000 |",
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )

        import app.services.proposal_fulfill_rfp_budget_kpi as scan_mod
        from app.services.proposal_fulfill_rfp_accuracy import RfpScoringFacts

        with patch.object(
            scan_mod,
            "_regen_budget_via_phase_3_5",
            new=AsyncMock(
                return_value=(regenerated_draft, regenerated_research, regenerated_budget)
            ),
        ) as regen, patch.object(
            scan_mod,
            "run_budget_editor_pass",
            side_effect=lambda b, **_kw: b,
        ), patch.object(
            scan_mod,
            "extract_rfp_scoring_facts_llm",
            new=AsyncMock(return_value=RfpScoringFacts()),
        ):
            out_draft, out_research, logs, meta = asyncio.run(
                run_fulfill_budget_scan(
                    rfp_id=rfp_id,
                    rfp=_rfp(rfp_id),
                    draft=draft,
                    research=research,
                    rfp_text="Cost proposal required. Submit itemized budget.",
                    use_llm=False,
                    skip_section_ids=set(),
                )
            )

        regen.assert_awaited_once_with(rfp_id)
        self.assertTrue(meta["budgetRegenerated"])
        self.assertEqual(meta["budgetStatus"], "repaired")
        self.assertIsNotNone(out_research and out_research.budget)
        self.assertTrue(any("regenerated via Phase 3.5" in line for line in logs))
        self.assertTrue(
            any("Budget" in (s.title or "") for s in out_draft.sections)
        )


class HealthyBudgetLeftAloneTests(unittest.TestCase):
    def test_complete_and_clean_does_not_wipe_healthy_pricing_form(self) -> None:
        """Reconcile-only scan must not re-render a filled Pricing Form."""
        rfp_id = "rfp-healthy-budget"
        healthy = (
            "## Pricing Proposal Form\n\n"
            "| Description | UOM | QTY | Price | Extended |\n"
            "| --- | --- | ---: | ---: | ---: |\n"
            "| Communications and Marketing Services | MO | 12 | $12,500 | $150,000 |\n"
            "| **GRAND TOTAL** | | | | **$150,000** |\n\n"
            "GRAND TOTAL (in words): One Hundred Fifty Thousand Dollars\n\n"
            "### Investment Summary\n\n"
            "Monthly rate covers strategy, creative, and account service.\n"
        )
        budget = ProposalBudget(
            rfpId=rfp_id,
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    description="Communications and Marketing Services",
                    category="Labor",
                    quantity=12,
                    unit="month",
                    rate=12_500,
                    extended=150_000,
                    lineItemType="agency_fee",
                )
            ],
            lumpSumTotal=150_000,
            agencyRevenueEstimate=150_000,
            agencyFeeSubtotal=150_000,
            totalClientInvoicing=150_000,
        )
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="rfp-pricing",
                    title="Request for Qualifications Pricing Form",
                    content=healthy,
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=budget,
        )
        import app.services.proposal_fulfill_rfp_budget_kpi as scan_mod
        from app.services.proposal_fulfill_rfp_accuracy import RfpScoringFacts

        with patch.object(
            scan_mod,
            "run_budget_editor_pass",
            side_effect=lambda b, **_kw: b,
        ), patch.object(
            scan_mod,
            "extract_rfp_scoring_facts_llm",
            new=AsyncMock(return_value=RfpScoringFacts()),
        ):
            out_draft, _research, logs, meta = asyncio.run(
                run_fulfill_budget_scan(
                    rfp_id=rfp_id,
                    rfp=_rfp(rfp_id),
                    draft=draft,
                    research=research,
                    rfp_text="Submit the Pricing Proposal Form with the proposal.",
                    use_llm=False,
                    skip_section_ids=set(),
                )
            )

        self.assertFalse(meta.get("budgetChanged"))
        self.assertEqual(out_draft.sections[0].content, healthy)
        self.assertTrue(
            any(
                "left unchanged" in line
                or "already adds up" in line
                or "official Pricing Form" in line
                for line in logs
            )
        )

    def test_complete_and_clean_does_not_rewrite_accurate_fee_table(self) -> None:
        rfp_id = "rfp-accurate-fees"
        healthy = (
            "## Proposed Investment\n\n"
            "**Professional fees: $279,800**\n"
            "**Total proposed investment: $279,800**\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Deliverable | Amount |\n"
            "| --- | --- | ---: |\n"
            "| Phase 1 | Discovery | $40,000 |\n"
            "| Phase 2 | Strategy | $55,000 |\n"
            "| Phase 3 | Creative | $70,000 |\n"
            "| Phase 4 | Production | $60,000 |\n"
            "| Phase 5 | Launch | $54,800 |\n"
            "| **Total** | | **$279,800** |\n"
        )
        budget = ProposalBudget(
            rfpId=rfp_id,
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    description="Discovery",
                    category="Phase 1",
                    extended=40_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L2",
                    description="Strategy",
                    category="Phase 2",
                    extended=55_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L3",
                    description="Creative",
                    category="Phase 3",
                    extended=70_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L4",
                    description="Production",
                    category="Phase 4",
                    extended=60_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L5",
                    description="Launch",
                    category="Phase 5",
                    extended=54_800,
                    lineItemType="agency_fee",
                ),
            ],
            lumpSumTotal=279_800,
            agencyRevenueEstimate=279_800,
            agencyFeeSubtotal=279_800,
            totalClientInvoicing=279_800,
        )
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="section-budget-pricing",
                    title="Budget & Cost Efficiency",
                    content=healthy,
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=budget,
        )
        import app.services.proposal_fulfill_rfp_budget_kpi as scan_mod
        from app.services.proposal_fulfill_rfp_accuracy import RfpScoringFacts

        def _editor_must_not_run(_budget, **_kw):
            raise AssertionError("editor must not rewrite an accurate fee table")

        with patch.object(
            scan_mod,
            "run_budget_editor_pass",
            side_effect=_editor_must_not_run,
        ), patch.object(
            scan_mod,
            "extract_rfp_scoring_facts_llm",
            new=AsyncMock(return_value=RfpScoringFacts()),
        ):
            out_draft, out_research, logs, meta = asyncio.run(
                run_fulfill_budget_scan(
                    rfp_id=rfp_id,
                    rfp=_rfp(rfp_id),
                    draft=draft,
                    research=research,
                    rfp_text="Cost is 20% of score. Submit a fee table.",
                    use_llm=False,
                    skip_section_ids=set(),
                )
            )

        self.assertEqual(out_draft.sections[0].content, healthy)
        self.assertEqual(out_research.budget.agency_fee_subtotal, 279_800)
        self.assertFalse(meta.get("budgetRegenerated"))
        self.assertTrue(any("already adds up" in line for line in logs))

    def test_mismatch_rewrites_budget_tab_from_canonical_ledger(self) -> None:
        rfp_id = "rfp-mismatch-fees"
        broken = (
            "## Proposed Investment\n\n"
            "**Professional fees: $279,800**\n"
            "**Agency Fee Subtotal: $279,800**\n"
            "**Total proposed investment: $279,800**\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Deliverable | Amount |\n"
            "| --- | --- | ---: |\n"
            "| Phase 1 | Discovery | $40,000 |\n"
            "| Phase 2 | Strategy | $55,000 |\n"
            "| Phase 5 | Launch | $50,000 |\n"
            "| Phase 7 | Reporting | $116,300 |\n"
            "| **Total** | | **$261,300** |\n"
        )
        budget = ProposalBudget(
            rfpId=rfp_id,
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    description="Discovery",
                    category="Phase 1",
                    extended=40_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L2",
                    description="Strategy",
                    category="Phase 2",
                    extended=55_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L3",
                    description="Creative",
                    category="Phase 3",
                    extended=70_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L4",
                    description="Production",
                    category="Phase 4",
                    extended=60_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="L5",
                    description="Launch",
                    category="Phase 5",
                    extended=54_800,
                    lineItemType="agency_fee",
                ),
            ],
            lumpSumTotal=279_800,
            agencyRevenueEstimate=279_800,
            agencyFeeSubtotal=279_800,
            totalClientInvoicing=279_800,
        )
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="section-budget-pricing",
                    title="Budget & Cost Efficiency",
                    content=broken,
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=budget,
        )
        import app.services.proposal_fulfill_rfp_budget_kpi as scan_mod
        from app.services.proposal_budget_sync import collect_prose_arithmetic_violations
        from app.services.proposal_fulfill_rfp_accuracy import RfpScoringFacts

        self.assertTrue(collect_prose_arithmetic_violations(broken))

        with patch.object(
            scan_mod,
            "run_budget_editor_pass",
            side_effect=lambda b, **_kw: b,
        ), patch.object(
            scan_mod,
            "extract_rfp_scoring_facts_llm",
            new=AsyncMock(return_value=RfpScoringFacts()),
        ):
            out_draft, _research, logs, meta = asyncio.run(
                run_fulfill_budget_scan(
                    rfp_id=rfp_id,
                    rfp=_rfp(rfp_id),
                    draft=draft,
                    research=research,
                    rfp_text="Cost is 20% of score. Submit a fee table.",
                    use_llm=False,
                    skip_section_ids=set(),
                )
            )

        fixed = out_draft.sections[0].content or ""
        self.assertNotEqual(fixed, broken)
        self.assertTrue(any("arithmetic mismatch" in line for line in logs))
        self.assertTrue(meta.get("budgetChanged"))
        self.assertEqual(collect_prose_arithmetic_violations(fixed), [])
        self.assertIn("$279,800", fixed)
        self.assertNotIn("$261,300", fixed)

    def test_second_scan_restores_pricing_polluted_by_contact_lock_tag(self) -> None:
        from app.services.proposal_fulfill_rfp_budget_kpi import (
            budget_manuscript_needs_restore,
        )

        polluted = (
            "## Request for Qualifications Pricing Form\n\n"
            "### Section I: Contact Information\n\n"
            "CONTACT PERSON: Ron Comer\n"
            "CONTACT EMAIL: ron@zo.agency\n\n"
            "| Description | Extended |\n| --- | ---: |\n| Services | $150,000 |\n\n"
            "GRAND TOTAL (In words): One Hundred Fifty Thousand Dollars\n\n"
            "[MANUAL FILL: SONJA — DETERMINISTIC.MANUSCRIPT_LOCKS.PRIMARY_CONTACT_LOCK_"
            "IS_RON_COMER_BUT_THIS_SECTION_NAMES_SONJA | Primary contact lock is Ron "
            "Comer, but this section names Sonja Anderson as…]"
        )
        budget = ProposalBudget(
            rfpId="rfp-polluted",
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    description="Services",
                    category="Labor",
                    extended=150_000,
                    lineItemType="agency_fee",
                )
            ],
            lumpSumTotal=150_000,
            totalClientInvoicing=150_000,
        )
        self.assertTrue(budget_manuscript_needs_restore(polluted, budget))

        draft = ProposalDraft(
            rfpId="rfp-polluted",
            sections=[
                ProposalSection(
                    id="rfp-pricing",
                    title="Request for Qualifications Pricing Form",
                    content=polluted,
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId="rfp-polluted",
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=budget,
        )
        import app.services.proposal_fulfill_rfp_budget_kpi as scan_mod
        from app.services.proposal_fulfill_rfp_accuracy import RfpScoringFacts

        with patch.object(
            scan_mod,
            "run_budget_editor_pass",
            side_effect=lambda b, **_kw: b,
        ), patch.object(
            scan_mod,
            "extract_rfp_scoring_facts_llm",
            new=AsyncMock(return_value=RfpScoringFacts()),
        ):
            out_draft, _r, logs, meta = asyncio.run(
                run_fulfill_budget_scan(
                    rfp_id="rfp-polluted",
                    rfp=_rfp("rfp-polluted"),
                    draft=draft,
                    research=research,
                    rfp_text="Pricing Proposal Form required.",
                    use_llm=False,
                    skip_section_ids=set(),
                )
            )

        self.assertTrue(meta.get("budgetChanged"))
        body = out_draft.sections[0].content or ""
        self.assertNotIn("MANUSCRIPT_LOCKS", body)
        self.assertIn("$150,000", body)
        self.assertIn("Ron Comer", body)
        self.assertIn("Section I: Contact Information", body)
        self.assertTrue(
            any("preserved official Pricing Form" in line for line in logs)
        )

    def test_fills_contact_placeholders_on_official_form(self) -> None:
        from app.models.proposal import ManuscriptLocks
        from app.services.proposal_fulfill_rfp_budget_kpi import (
            fill_pricing_form_contact_placeholders,
        )

        raw = (
            "CONTACT PERSON: [Contact Name]\n"
            "CONTACT EMAIL: [Contact Email]\n"
            "GRAND TOTAL: $150,000\n"
        )
        filled = fill_pricing_form_contact_placeholders(
            raw, contact_name="Ron Comer", contact_email="ron@zo.agency"
        )
        self.assertIn("Ron Comer", filled)
        self.assertIn("ron@zo.agency", filled)
        self.assertNotIn("[Contact Name]", filled)

        form = (
            "### Section I: Contact Information\n"
            "RFQ NUMBER: 26-070-WIOA\n"
            "CONTACT PERSON: [Contact Name]\n"
            "CONTACT EMAIL: [Contact Email]\n\n"
            "| ITEM | EXTENDED |\n| --- | ---: |\n| Services | $150,000 |\n"
            "GRAND TOTAL (In words): One Hundred Fifty Thousand Dollars\n"
        )
        draft = ProposalDraft(
            rfpId="rfp-contact",
            sections=[
                ProposalSection(
                    id="rfp-pricing",
                    title="Request for Qualifications Pricing Form",
                    content=form,
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId="rfp-contact",
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=ProposalBudget(
                rfpId="rfp-contact",
                updatedAt="2026-08-05T00:00:00+00:00",
                lineItems=[
                    BudgetLineItem(
                        id="L1",
                        description="Services",
                        category="Labor",
                        extended=150_000,
                        lineItemType="agency_fee",
                    )
                ],
                lumpSumTotal=150_000,
                totalClientInvoicing=150_000,
            ),
            manuscriptLocks=ManuscriptLocks(
                primaryContactName="Ron Comer",
                primaryContactTitle="Senior Account Manager",
                updatedAt="2026-08-05T00:00:00+00:00",
            ),
        )
        import app.services.proposal_fulfill_rfp_budget_kpi as scan_mod
        from app.services.proposal_fulfill_rfp_accuracy import RfpScoringFacts

        with patch.object(
            scan_mod,
            "run_budget_editor_pass",
            side_effect=lambda b, **_kw: b,
        ), patch.object(
            scan_mod,
            "extract_rfp_scoring_facts_llm",
            new=AsyncMock(return_value=RfpScoringFacts()),
        ):
            out_draft, _r, logs, meta = asyncio.run(
                run_fulfill_budget_scan(
                    rfp_id="rfp-contact",
                    rfp=_rfp("rfp-contact"),
                    draft=draft,
                    research=research,
                    rfp_text="Complete the RFQ Pricing Form.",
                    use_llm=False,
                    skip_section_ids=set(),
                )
            )
        body = out_draft.sections[0].content or ""
        self.assertIn("Ron Comer", body)
        self.assertNotIn("[Contact Name]", body)
        self.assertIn("$150,000", body)
        self.assertTrue(any("preserved official Pricing Form" in line for line in logs))


class StaleManuscriptRefreshWithoutRegenTests(unittest.TestCase):
    """Providence regression: $500 hollow tab but cached $156k model must re-render, not LLM regen."""

    def test_hollow_tab_with_healthy_model_rerenders_without_phase_3_5(self) -> None:
        hollow = (
            "## Proposed Investment\n\n"
            "**Direct travel / reimbursables: $500**\n"
            "**Total proposed investment: $500**\n"
        )
        healthy_budget = ProposalBudget(
            rfpId="rfp-stale",
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    description="Strategy & creative",
                    category="Labor",
                    extended=120_000,
                    lineItemType="agency_fee",
                ),
                BudgetLineItem(
                    id="T1",
                    description="Travel",
                    category="Travel",
                    extended=500,
                    lineItemType="direct_expense",
                ),
            ],
            lumpSumTotal=120_500,
            agencyRevenueEstimate=120_500,
        )
        draft = ProposalDraft(
            rfpId="rfp-stale",
            sections=[
                ProposalSection(
                    id="section-budget-pricing",
                    title="Budget & Pricing",
                    content=hollow,
                    status="generated",
                )
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId="rfp-stale",
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=healthy_budget,
        )

        import app.services.proposal_fulfill_rfp_budget_kpi as scan_mod
        from app.services.proposal_fulfill_rfp_accuracy import RfpScoringFacts

        with patch.object(
            scan_mod,
            "_regen_budget_via_phase_3_5",
            new=AsyncMock(),
        ) as regen, patch.object(
            scan_mod,
            "run_budget_editor_pass",
            side_effect=lambda b, **_kw: b,
        ), patch.object(
            scan_mod,
            "extract_rfp_scoring_facts_llm",
            new=AsyncMock(return_value=RfpScoringFacts()),
        ):
            out, _r, logs, meta = asyncio.run(
                run_fulfill_budget_scan(
                    rfp_id="rfp-stale",
                    rfp=_rfp("rfp-stale"),
                    draft=draft,
                    research=research,
                    rfp_text="Submit itemized professional fees and travel.",
                    use_llm=False,
                    skip_section_ids=set(),
                )
            )

        regen.assert_not_awaited()
        body = next(s for s in out.sections if s.id == "section-budget-pricing").content or ""
        self.assertIn("120,500", body.replace(",", ","))
        self.assertIn("120,000", body.replace(",", ","))
        self.assertNotIn("$500", body.split("Professional")[0] if "Professional" in body else body[:200])
        self.assertTrue(any("re-rendering from canon" in line for line in logs))
        self.assertTrue(meta["budgetChanged"])


class FailClosedTravelOnlyBudgetTests(unittest.TestCase):
    def test_travel_only_total_does_not_pass_green(self) -> None:
        """Providence regression: $500 travel == total still marked Budget step green."""
        hollow = (
            "## Proposed Investment\n\n"
            "**Direct travel / reimbursables: $500**\n"
            "**Total proposed investment: $500**\n"
            "Rates follow zö's Industry Average pricing guide for comparable "
            "municipal / education marketing engagements.\n"
        )
        travel_budget = ProposalBudget(
            rfpId="rfp-hollow",
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="T1",
                    description="Travel / reimbursables",
                    category="Travel",
                    extended=500,
                    lineItemType="direct_expense",
                )
            ],
            lumpSumTotal=500,
            directExpensesTotal=500,
            agencyRevenueEstimate=0,
        )
        draft = ProposalDraft(
            rfpId="rfp-hollow",
            updatedAt="2026-08-05T00:00:00+00:00",
            sections=[
                ProposalSection(
                    id="section-budget-pricing",
                    title="Budget & Pricing",
                    content=hollow,
                    status="generated",
                )
            ],
        )
        research = ProposalResearchCache(
            rfpId="rfp-hollow",
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=travel_budget,
        )

        import app.services.proposal_fulfill_rfp_budget_kpi as scan_mod
        from app.services.proposal_fulfill_rfp_accuracy import RfpScoringFacts

        # Regen "succeeds" but still returns travel-only — must fail closed.
        with patch.object(
            scan_mod,
            "_regen_budget_via_phase_3_5",
            new=AsyncMock(return_value=(draft, research, travel_budget)),
        ), patch.object(
            scan_mod,
            "run_budget_editor_pass",
            side_effect=lambda b, **_kw: b,
        ), patch.object(
            scan_mod,
            "extract_rfp_scoring_facts_llm",
            new=AsyncMock(return_value=RfpScoringFacts()),
        ):
            _out, _r, logs, meta = asyncio.run(
                run_fulfill_budget_scan(
                    rfp_id="rfp-hollow",
                    rfp=_rfp("rfp-hollow"),
                    draft=draft,
                    research=research,
                    rfp_text="Submit a cost proposal with professional fees.",
                    use_llm=False,
                    skip_section_ids=set(),
                )
            )

        self.assertEqual(meta["budgetStatus"], "needs_human")
        self.assertTrue(any("FAIL CLOSED" in line for line in logs))


class UnresolvedBudgetTokensTests(unittest.TestCase):
    def test_custom_budget_slots_count_as_hollow(self) -> None:
        body = (
            "| Category | Investment |\n"
            "| --- | --- |\n"
            "| Brand | {{budget.brand_development}} |\n"
            "| Media | {{budget.media_placements}} |\n"
        )
        self.assertTrue(manuscript_cost_section_is_hollow(body))

    def test_cost_proposal_slots_rewritten_when_sibling_fee_table_is_healthy(self) -> None:
        rfp_id = "rfp-slots"
        healthy = (
            "## Proposed Investment\n\n"
            "**Professional fees: $50,000**\n"
            "**Total proposed investment: $50,000**\n\n"
            "| Phase | Amount |\n"
            "| --- | ---: |\n"
            "| Strategy | $50,000 |\n"
        )
        slotted = (
            "| Category | Investment |\n"
            "| --- | --- |\n"
            "| Brand development | {{budget.brand_development}} |\n"
            "| Media placements | {{budget.media_placements}} |\n"
        )
        budget = ProposalBudget(
            rfpId=rfp_id,
            updatedAt="2026-08-05T00:00:00+00:00",
            lineItems=[
                BudgetLineItem(
                    id="L1",
                    description="Strategy",
                    category="Labor",
                    extended=50_000,
                    lineItemType="agency_fee",
                )
            ],
            lumpSumTotal=50_000,
            agencyRevenueEstimate=50_000,
            agencyFeeSubtotal=50_000,
            totalClientInvoicing=50_000,
        )
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="section-budget-pricing",
                    title="Budget & Pricing",
                    content=healthy,
                    status="generated",
                ),
                ProposalSection(
                    id="rfp-cost",
                    title="Cost Proposal",
                    content=slotted,
                    status="generated",
                ),
            ],
            updatedAt="2026-08-05T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt="2026-08-05T00:00:00+00:00",
            budget=budget,
        )
        import app.services.proposal_fulfill_rfp_budget_kpi as scan_mod
        from app.services.proposal_fulfill_rfp_accuracy import RfpScoringFacts

        with patch.object(
            scan_mod,
            "run_budget_editor_pass",
            side_effect=lambda b, **_kw: b,
        ), patch.object(
            scan_mod,
            "extract_rfp_scoring_facts_llm",
            new=AsyncMock(return_value=RfpScoringFacts()),
        ), patch.object(
            scan_mod,
            "_regen_budget_via_phase_3_5",
            new=AsyncMock(side_effect=AssertionError("must not regen")),
        ):
            out_draft, _research, _logs, meta = asyncio.run(
                run_fulfill_budget_scan(
                    rfp_id=rfp_id,
                    rfp=_rfp(rfp_id),
                    draft=draft,
                    research=research,
                    rfp_text="Submit a Cost Proposal with the itemized budget.",
                    use_llm=False,
                    skip_section_ids=set(),
                )
            )

        self.assertEqual(len(out_draft.sections), 1)
        cost = out_draft.sections[0]
        self.assertNotIn("{{budget.", cost.content or "")
        self.assertIn("$50,000", cost.content or "")
        self.assertTrue(meta.get("budgetChanged"))


class CollapseDuplicateCostTabsTests(unittest.TestCase):
    def test_keeps_fee_table_drops_slot_shell(self) -> None:
        from app.services.proposal_budget_content import collapse_duplicate_cost_proposal_tabs

        healthy = (
            "## Fee Detail by Phase\n\n"
            "| Phase | Amount |\n"
            "| --- | ---: |\n"
            "| Strategy | $50,000 |\n"
        )
        slotted = (
            "| Channel | Oahu |\n"
            "| --- | --- |\n"
            "| Digital | {{budget.media_oahu_digital}} |\n"
        )
        sections = [
            ProposalSection(
                id="rfp-cost-appendix",
                title=(
                    "Cost Proposal using Appendix A with itemized pricing for all "
                    "services, estimated quantities, budget"
                ),
                content=healthy,
            ),
            ProposalSection(
                id="rfp-cost",
                title="Cost Proposal",
                content=slotted,
            ),
        ]
        kept, logs = collapse_duplicate_cost_proposal_tabs(sections)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].id, "rfp-cost-appendix")
        self.assertIn("$50,000", kept[0].content or "")
        self.assertTrue(any("collapsed duplicate" in x.casefold() for x in logs))

    def test_pricing_titles_are_the_same_ask(self) -> None:
        from app.services.proposal_outline_dedup import outline_titles_near_duplicate

        self.assertTrue(
            outline_titles_near_duplicate(
                "Cost Proposal",
                "Cost Proposal using Appendix A with itemized pricing for all services",
            )
        )


if __name__ == "__main__":
    unittest.main()
