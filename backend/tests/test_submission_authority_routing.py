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
    apply_submission_authority_pass,
    instrument_supports_budget_build,
    phase35_budget_gate,
    unresolved_reference_block,
)
from app.models.pricing_instrument import PricingInstrument, PricingTrack


class UnresolvedReferencePromptTests(unittest.TestCase):
    def test_block_quotes_the_stored_reference(self) -> None:
        block = unresolved_reference_block(
            [
                {
                    "target": "Section 7: Submission Instructions",
                    "quote": "Refer to Section 7: Submission Instructions for details.",
                }
            ]
        )
        self.assertIn("Section 7: Submission Instructions", block)
        self.assertIn("Refer to Section 7: Submission Instructions for details.", block)

    def test_empty_references_add_nothing(self) -> None:
        self.assertEqual(unresolved_reference_block([]), "")


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
        from tests.test_pricing_plan_service import new_budget, svc

        budget = new_budget()
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
        content = already[0].content or ""
        self.assertIn("MANUAL FILL", content)
        self.assertIn("Attachment C — Certification", content)
        self.assertNotIn("required PDF/form here", content)
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

    def test_form_stubs_name_the_section(self) -> None:
        from app.services.proposal_submission_authority import attachment_checklist_stub

        vendor = attachment_checklist_stub("Vendor Questionnaire", "form")
        insurance = attachment_checklist_stub(
            "Insurance Requirements",
            "form",
            requirement="Certificate of insurance meeting the stated limits.",
        )
        self.assertIn("Vendor Questionnaire", vendor)
        self.assertIn("Insurance Requirements", insurance)
        self.assertIn("Certificate of insurance meeting the stated limits.", insurance)
        self.assertNotEqual(vendor, insurance)

    def test_attach_only_when_rfp_gave_no_form_fields(self) -> None:
        from app.services.proposal_submission_authority import attachment_checklist_stub

        body = attachment_checklist_stub(
            "Campaign Contribution Disclosure Form",
            "disclosure",
            requirement="Attach the signed disclosure form with the proposal.",
        )
        self.assertIn("DESIGNER NOTE", body)
        self.assertIn("MANUAL FILL", body)
        self.assertNotIn("| Field | Response |", body)

    def test_scaffolds_field_table_when_rfp_format_labels_present(self) -> None:
        from app.services.proposal_submission_authority import attachment_checklist_stub

        body = attachment_checklist_stub(
            "Campaign Contribution Disclosure Form",
            "disclosure",
            form_fields=[
                "Offeror Name",
                "Authorized Representative",
                "Date",
                "Signature",
            ],
            field_values={
                "Offeror Name": "Z'Onion Creative Group LLC dba zö agency",
            },
        )
        self.assertIn("| Field | Response |", body)
        self.assertIn("| Offeror Name |", body)
        self.assertIn("Z'Onion Creative Group LLC dba zö agency", body)
        self.assertIn("| Date | [MANUAL FILL:", body)
        self.assertIn("| Signature | [MANUAL FILL:", body)
        # Signed buyer PDF still needed
        self.assertIn("DESIGNER NOTE", body)
        self.assertIn("attach the signed", body.casefold())

    def test_phase3_form_with_structured_fields_scaffolds_table(self) -> None:
        mapped = [
            RfpSectionMap(
                id="f1",
                title="Campaign Contribution Disclosure Form",
                submissionInstrument="disclosure",
                requirements=["Attach signed disclosure with submission."],
                formFields=["Offeror Name", "Signature"],
            ),
        ]
        _to_draft, already = partition_phase3_sections(
            mapped,
            {},
            form_field_values={"Offeror Name": "zö agency"},
        )
        self.assertEqual(len(already), 1)
        content = already[0].content or ""
        self.assertIn("| Field | Response |", content)
        self.assertIn("| Offeror Name | zö agency |", content)
        self.assertIn("| Signature | [MANUAL FILL:", content)

    def test_phase3_form_without_structured_fields_stays_attach_only(self) -> None:
        mapped = [
            RfpSectionMap(
                id="f1",
                title="Campaign Contribution Disclosure Form",
                submissionInstrument="disclosure",
                requirements=["Attach signed disclosure with submission."],
            ),
        ]
        _to_draft, already = partition_phase3_sections(mapped, {})
        content = already[0].content or ""
        self.assertNotIn("| Field | Response |", content)
        self.assertIn("DESIGNER NOTE", content)


if __name__ == "__main__":
    unittest.main()


class CostEvidenceOverridesAbsentTests(unittest.IsolatedAsyncioTestCase):
    """OCTA: the model said "absent" while quoting the cost section and stamping a cost tab."""

    def _plan(self, instrument: str | None, status: str = "absent") -> ProposalExecutionPlan:
        return ProposalExecutionPlan(
            writing=WritingIntelligence(
                cost_requirement_status=status,
                proposal_outline=ProposalOutline(
                    sections=[
                        OutlineSection(
                            id="rfp-sec-6",
                            title="Cost and Price Proposal",
                            submission_instrument=instrument,
                        ),
                        OutlineSection(id="rfp-sec-1", title="Approach"),
                    ]
                ),
            )
        )

    def test_saved_plan_with_a_stamped_cost_tab_builds_the_budget(self) -> None:
        self.assertEqual(phase35_budget_gate(self._plan("cost"))[0], "proceed")

    def test_absent_with_no_cost_tab_still_skips(self) -> None:
        self.assertEqual(phase35_budget_gate(self._plan("narrative"))[0], "skip")

    def test_ambiguous_with_a_cost_tab_still_skips(self) -> None:
        plan = self._plan("cost", status="ambiguous")
        self.assertEqual(phase35_budget_gate(plan)[0], "skip")

    async def _run_pass(self, model_status: str, quotes: list[str]) -> str:
        plan = self._plan(None)
        raw = {"costRequirementStatus": model_status, "sectionUpdates": [], "confidence": 0.8}
        with (
            patch(
                "app.services.proposal_submission_authority.collect_cost_submittal_quotes",
                new=AsyncMock(return_value=quotes),
            ),
            patch(
                "app.services.proposal_intelligence.agent_base.safe_chat_json",
                new=AsyncMock(return_value=(raw, "test")),
            ),
            patch(
                "app.services.proposal_submission_authority._load_unresolved_references",
                return_value=[],
            ),
            patch(
                "app.services.proposal_submission_authority.load_rfp_body_for_quotes",
                return_value="rfp body",
            ),
        ):
            out = await apply_submission_authority_pass(plan, "rfp body")
        return out.writing.cost_requirement_status

    async def test_copied_cost_quotes_overrule_an_absent_answer(self) -> None:
        quote = "The Offeror shall complete the Price Summary Sheet form included with this RFP."
        self.assertEqual(await self._run_pass("absent", [quote]), "confirmed")

    async def test_absent_answer_stands_with_no_quotes(self) -> None:
        self.assertEqual(await self._run_pass("absent", []), "absent")

    async def test_copied_quotes_do_not_overrule_a_contradiction(self) -> None:
        self.assertEqual(await self._run_pass("ambiguous", ["Submit a cost proposal."]), "ambiguous")
