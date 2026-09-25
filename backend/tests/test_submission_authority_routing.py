"""Submission authority + Phase 3 / budget routing."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection, RfpSectionMap
from app.services.proposal_drafting_graph import partition_phase3_sections
from app.services.proposal_intelligence.schemas import (
    OutlineSection,
    ProposalExecutionPlan,
    ProposalOutline,
    WritingIntelligence,
)
from app.services.proposal_submission_authority import (
    apply_authority_from_raw,
    instrument_supports_budget_build,
    phase35_budget_gate,
)
from app.models.pricing_instrument import PricingInstrument, PricingTrack
from app.services.proposal_budget_content import (
    ensure_instrument_nte_total_block,
    primary_instrument_nte,
)


class SubmissionAuthorityApplyTests(unittest.TestCase):
    def test_cost_contradiction_becomes_clarify_and_skips_budget(self) -> None:
        plan = ProposalExecutionPlan(
            writing=WritingIntelligence(
                proposal_outline=ProposalOutline(
                    sections=[
                        OutlineSection(
                            id="cost-1",
                            title="Cost Sheet",
                            order=1,
                            submission_instrument="cost",
                            required=True,
                        ),
                        OutlineSection(
                            id="wrap",
                            title="Offer Response (Offers Content Requirements)",
                            order=2,
                        ),
                    ]
                )
            )
        )
        raw = {
            "removeSectionIds": ["wrap"],
            "sectionUpdates": [
                {
                    "id": "cost-1",
                    "submissionInstrument": "clarify",
                    "required": False,
                }
            ],
            "submissionConstraints": [
                {
                    "kind": "page_limit",
                    "text": "Offer content limited to no more than five pages.",
                    "required": True,
                }
            ],
            "ambiguities": [
                {
                    "topic": "Cost / pricing deliverable",
                    "status": "unresolved",
                    "evidenceFor": "TOC lists Attachment E Cost Sheet",
                    "evidenceAgainst": "Section 6 lists Attachment E RESERVED",
                    "recommendedAction": "Confirm OregonBuys attachments before pricing.",
                    "blocksBudget": True,
                }
            ],
            "costRequirementStatus": "ambiguous",
        }
        updated = apply_authority_from_raw(plan, raw)
        titles = [s.title for s in updated.writing.proposal_outline.sections]
        self.assertEqual(titles, ["Cost Sheet"])
        self.assertEqual(
            updated.writing.proposal_outline.sections[0].submission_instrument, "clarify"
        )
        self.assertEqual(updated.writing.cost_requirement_status, "ambiguous")
        gate, detail = phase35_budget_gate(updated)
        self.assertEqual(gate, "skip")
        self.assertIn("OregonBuys", detail or "")

    def test_confirmed_cost_proceeds(self) -> None:
        plan = ProposalExecutionPlan(
            writing=WritingIntelligence(cost_requirement_status="confirmed")
        )
        self.assertEqual(phase35_budget_gate(plan)[0], "proceed")

    def test_absent_cost_skips(self) -> None:
        plan = ProposalExecutionPlan(
            writing=WritingIntelligence(cost_requirement_status="absent")
        )
        self.assertEqual(phase35_budget_gate(plan)[0], "skip")

    def test_ambiguous_with_hourly_nte_instrument_proceeds(self) -> None:
        plan = ProposalExecutionPlan(
            writing=WritingIntelligence(
                cost_requirement_status="ambiguous",
                ambiguities=[],
            )
        )
        instrument = PricingInstrument(
            kind="personnel_loading",
            tracks=[
                PricingTrack(
                    id="t1",
                    label="Campaign",
                    nteAnnual=950_000.0,
                    asksHourly=True,
                    asksHours=True,
                )
            ],
        )
        self.assertTrue(instrument_supports_budget_build(instrument))
        gate, detail = phase35_budget_gate(plan, instrument)
        self.assertEqual(gate, "proceed")
        self.assertIn("hourly", (detail or "").casefold())

    def test_ambiguous_without_instrument_still_skips(self) -> None:
        plan = ProposalExecutionPlan(
            writing=WritingIntelligence(cost_requirement_status="ambiguous")
        )
        gate, _ = phase35_budget_gate(plan, None)
        self.assertEqual(gate, "skip")


class InstrumentNteTotalTests(unittest.TestCase):
    def test_appends_nte_when_missing(self) -> None:
        instrument = PricingInstrument(
            kind="personnel_loading",
            tracks=[
                PricingTrack(
                    id="t1",
                    label="Campaign",
                    nteAnnual=950_000.0,
                    asksHourly=True,
                )
            ],
        )
        self.assertEqual(primary_instrument_nte(instrument), 950_000.0)
        out, logs = ensure_instrument_nte_total_block(
            "### Fees\n\n| Task | $ |\n| --- | --- |\n| A1 | $10,000 |\n",
            instrument,
        )
        self.assertTrue(logs)
        self.assertIn("950,000", out)
        self.assertIn("Not-to-Exceed", out)

    def test_idempotent_when_nte_already_present(self) -> None:
        instrument = PricingInstrument(
            kind="personnel_loading",
            tracks=[
                PricingTrack(
                    id="t1",
                    label="Campaign",
                    nteAnnual=950_000.0,
                    asksHourly=True,
                )
            ],
        )
        body = (
            "| **Total Not-to-Exceed** | **$950,000** |\n"
        )
        out, logs = ensure_instrument_nte_total_block(body, instrument)
        self.assertEqual(logs, [])
        self.assertEqual(out.count("950,000"), 1)


class IncorporateBudgetSkipsInstrumentNteForPlanTests(unittest.IsolatedAsyncioTestCase):
    """Bug B: a v2 (pricing_plan) budget must never get a second, contradicting
    Total Not-to-Exceed row from the pricing_instrument passthrough.

    incorporate_budget_into_draft is called with pricing_instrument on the
    budget-before-drafting re-land (proposal_generator.py) and saved directly,
    without the zero-fabrication guard's re-render — so an appended NTE row
    that disagrees with the plan's own ceiling would persist uncorrected.
    """

    async def test_v2_budget_output_equals_plan_render_no_extra_nte_row(self) -> None:
        from app.models.pricing_instrument import PricingInstrument, PricingTrack
        from app.services import proposal_budget_content as pbc
        from tests.test_pricing_plan_service import _newport_budget, svc

        budget = _newport_budget()
        plan_render = svc.render_pricing_plan_budget(budget)

        # Instrument NTE deliberately differs from the plan's own ceiling.
        instrument = PricingInstrument(
            kind="personnel_loading",
            tracks=[
                PricingTrack(
                    id="t1",
                    label="Campaign",
                    nteAnnual=1.0,  # far from the plan's real ceiling
                    asksHourly=True,
                )
            ],
        )

        draft = ProposalDraft(
            rfpId="r-newport",
            updatedAt="t",
            sections=[
                ProposalSection(id="s2", title="Budget & Pricing", content="old", status="generated"),
            ],
        )
        saved = AsyncMock()
        with (
            patch.object(pbc, "aget_proposal_draft", AsyncMock(return_value=draft)),
            patch.object(pbc, "asave_proposal_draft", saved),
        ):
            out_draft = await pbc.incorporate_budget_into_draft(
                "r-newport", budget, pricing_instrument=instrument
            )
        cost = next(s for s in out_draft.sections if s.id == "s2")
        self.assertEqual(cost.content.strip(), plan_render.strip())
        self.assertEqual(cost.content.count("Not-to-Exceed") + cost.content.count("not-to-exceed"),
                          plan_render.count("Not-to-Exceed") + plan_render.count("not-to-exceed"))
        saved.assert_awaited_once()


class Phase3PartitionRoutingTests(unittest.TestCase):
    def test_form_tab_routes_to_stub_not_full_draft(self) -> None:
        mapped = [
            RfpSectionMap(
                id="f1",
                title="Attachment C — Certification",
                submissionInstrument="form",
            ),
            RfpSectionMap(
                id="n1",
                title="Technical Approach",
                submissionInstrument="narrative",
            ),
        ]
        to_draft, already = partition_phase3_sections(mapped, {})
        self.assertEqual(len(to_draft), 1)
        self.assertEqual(to_draft[0].id, "n1")
        self.assertEqual(len(already), 1)
        self.assertIn("MANUAL FILL", already[0].content or "")
        self.assertIn("f1", already[0].id)

    def test_clarify_tab_routes_to_manual_fill_stub(self) -> None:
        mapped = [
            RfpSectionMap(
                id="c1",
                title="Cost Sheet",
                submissionInstrument="clarify",
                requirements=["Attachment E contradictory"],
            ),
        ]
        to_draft, already = partition_phase3_sections(mapped, {})
        self.assertEqual(to_draft, [])
        self.assertEqual(len(already), 1)
        self.assertIn("ambiguity", (already[0].content or "").casefold())


if __name__ == "__main__":
    unittest.main()
