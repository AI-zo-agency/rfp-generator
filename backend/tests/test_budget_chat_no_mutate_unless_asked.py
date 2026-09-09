"""Budget chat: no mutate unless explicit change; otherwise RFP coverage check."""

from __future__ import annotations

import unittest

from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RfpSectionMap,
)
from app.services.proposal_budget_playbook import (
    budget_ask_allows_freeform_narrative,
    user_explicitly_asks_to_change_budget,
)
from app.services.proposal_section_editor import (
    _try_budget_section_rfp_coverage_check,
    _user_asks_voice_or_style_only,
)


class BudgetChangeGateTests(unittest.TestCase):
    def test_voice_align_is_not_a_budget_change(self) -> None:
        ask = (
            "Align the Budget & Cost Breakdown section with zö agency's "
            "established voice"
        )
        self.assertTrue(_user_asks_voice_or_style_only(ask))
        self.assertFalse(user_explicitly_asks_to_change_budget(ask))
        self.assertFalse(budget_ask_allows_freeform_narrative(ask))

    def test_soft_improve_is_not_a_budget_change(self) -> None:
        self.assertFalse(user_explicitly_asks_to_change_budget("Improve this section"))
        self.assertFalse(budget_ask_allows_freeform_narrative("Improve this section"))

    def test_improve_budget_align_rfp_is_coverage_not_freeform(self) -> None:
        from app.services.proposal_budget_playbook import (
            user_asks_budget_improve_if_needed,
        )

        ask = "improve budget and align with rfp"
        self.assertTrue(user_asks_budget_improve_if_needed(ask))
        self.assertFalse(user_explicitly_asks_to_change_budget(ask))
        self.assertFalse(budget_ask_allows_freeform_narrative(ask))

    def test_explicit_fee_and_terms_asks_are_changes(self) -> None:
        self.assertTrue(
            user_explicitly_asks_to_change_budget(
                "Add hourly rates from the pricing guide"
            )
        )
        self.assertTrue(
            user_explicitly_asks_to_change_budget(
                "Remove Investment Framing table and keep Fee Detail only"
            )
        )
        # Verbatim Terms restore is the coverage / safe-fix path (not freeform).
        from app.services.proposal_budget_playbook import (
            user_asks_budget_improve_if_needed,
        )

        self.assertTrue(
            user_asks_budget_improve_if_needed("Restore verbatim Terms from guide")
        )
        self.assertFalse(
            user_explicitly_asks_to_change_budget("Restore verbatim Terms from guide")
        )
        self.assertTrue(
            user_explicitly_asks_to_change_budget("Rebuild budget from the pricing guide")
        )


class BudgetCoverageCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_ask_reports_gaps_without_mutating_when_verbatim_ok(self) -> None:
        from app.services.proposal_budget_content import (
            force_pricing_guide_verbatim_qualifying_language,
        )

        ql = force_pricing_guide_verbatim_qualifying_language("")
        section = ProposalSection(
            id="rfp-structure-budget-cost-breakdown",
            title="Budget & Cost Breakdown",
            content=(
                "## Fee Detail by Phase\n\n"
                "| Phase | Amount |\n| --- | ---: |\n| Discovery | $10,000 |\n\n"
                f"## Terms\n\n{ql}\n\n"
                + ("Phase narrative for evaluators. " * 30)
            ),
            status="generated",
        )
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[section])
        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="t",
            rfpSections=[
                RfpSectionMap(
                    id="rfp-structure-budget-cost-breakdown",
                    title="Budget & Cost Breakdown",
                    requirements=[
                        "Address Budget & Cost Breakdown per RFP",
                        "Include reimbursable expense policy with IRS mileage",
                    ],
                )
            ],
        )
        before = section.content
        result = await _try_budget_section_rfp_coverage_check(
            rfp_id="r1",
            section=section,
            section_id=section.id,
            draft=draft,
            research=research,
            user_message="Align with zo agency voice",
            selection_mode=False,
            persist=False,
        )
        self.assertIsNotNone(result)
        focus, out_draft, _, _, reply, changed = result
        self.assertFalse(changed)
        self.assertEqual(focus.content, before)
        self.assertNotIn("Address Budget & Cost Breakdown per RFP", reply)
        self.assertIn("left unchanged", reply.casefold())

    async def test_improve_if_needed_restores_verbatim(self) -> None:
        section = ProposalSection(
            id="rfp-structure-budget-cost-breakdown",
            title="Budget & Cost Breakdown",
            content=(
                "## Proposed Investment\n\n**Total proposed investment: $122,000.00**\n\n"
                "## Terms\n\n### Investment Framing\n\nParaphrased only.\n\n"
                "## Fee Detail by Phase\n\n"
                "| Phase | Amount |\n| --- | ---: |\n| Discovery | $10,000 |\n"
            ),
            status="generated",
        )
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[section])
        research = ProposalResearchCache(rfpId="r1", updatedAt="t")
        result = await _try_budget_section_rfp_coverage_check(
            rfp_id="r1",
            section=section,
            section_id=section.id,
            draft=draft,
            research=research,
            user_message=(
                "Make it align with zo agency voice and improve budget if needed "
                "according to rfp"
            ),
            selection_mode=False,
            persist=False,
        )
        self.assertIsNotNone(result)
        focus, _, _, _, reply, changed = result
        self.assertTrue(changed)
        self.assertIn("mileage at current irs rate", (focus.content or "").casefold())
        self.assertIn("we abide by those terms", (focus.content or "").casefold())
        self.assertIn("rfp", reply.casefold())
        self.assertIn("compliance", reply.casefold())
        self.assertIn("$10,000", focus.content or "")

    async def test_explicit_change_skips_coverage_only_path(self) -> None:
        section = ProposalSection(
            id="section-cost",
            title="Cost Proposal",
            content="## Fee Detail\n\n|$1|",
            status="generated",
        )
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[section])
        result = await _try_budget_section_rfp_coverage_check(
            rfp_id="r1",
            section=section,
            section_id=section.id,
            draft=draft,
            research=None,
            user_message="Rebuild budget from the pricing guide",
            selection_mode=False,
        )
        self.assertIsNone(result)


def _find_content(draft: ProposalDraft, section_id: str) -> str:
    for s in draft.sections:
        if s.id == section_id:
            return s.content or ""
    return ""


if __name__ == "__main__":
    unittest.main()
