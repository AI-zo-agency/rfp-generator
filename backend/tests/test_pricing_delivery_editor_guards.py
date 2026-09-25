"""Editor / QA guards when PricingInstrument.kind is buyer_pricing_form."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.models.pricing_instrument import PricingInstrument
from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.pricing_delivery_context import (
    format_pricing_delivery_constraints_block,
    is_buyer_pricing_form_instrument,
)
from app.services.proposal_self_edit_loop import (
    _maybe_append_delivery_constraint_reminder,
    _pricing_delivery_block_for_repair,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dupage_pricing_instrument.json"

# A filled buyer Pricing Form body (static; the guards key off the instrument).
BUYER_FORM_MD = (
    "## Proposal Pricing Form\n\n"
    "| Track | Hourly rate | Hours | Annual NTE |\n"
    "| --- | --- | --- | --- |\n"
    "| Part A | [MANUAL FILL: SONJA — hourly rate] | — | $75,000 |\n"
)


def _instrument() -> PricingInstrument:
    return PricingInstrument.model_validate(json.loads(FIXTURE.read_text()))


def _sec(sid: str, title: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content=content,
        status="generated",
        wordTarget=400,
    )


class BuyerFormHollowAndTermsGuards(unittest.TestCase):
    def test_is_buyer_pricing_form_helper(self) -> None:
        self.assertTrue(is_buyer_pricing_form_instrument(instrument=_instrument()))
        self.assertFalse(
            is_buyer_pricing_form_instrument(
                instrument=PricingInstrument(kind="phased_fee_schedule", confidence=0.5)
            )
        )

class RepairPromptInjectionGuards(unittest.TestCase):
    def test_pricing_delivery_block_for_repair(self) -> None:
        research = ProposalResearchCache(
            rfpId="rfp-t",
            updatedAt="2026-09-23T00:00:00Z",
            pricingInstrument=_instrument().model_dump(by_alias=True),
        )
        block = _pricing_delivery_block_for_repair(research)
        self.assertIn("buyer_pricing_form", block)
        self.assertIn("deterministic render only", block.casefold())

    def test_sow_expand_ticket_gets_constraint_reminder(self) -> None:
        brief = "Expand the Statement of Work to cover County-wide website redesign."
        out = _maybe_append_delivery_constraint_reminder(
            brief, section_title="Statement of Work"
        )
        self.assertIn("CONSTRAINT REMINDER", out)
        self.assertIn("locked delivery facts", out.casefold())
        # Reminder appended; original brief still present
        self.assertIn("Expand the Statement of Work", out)

    def test_timeline_restructure_gets_reminder(self) -> None:
        brief = "Restructure the project timeline into more phases."
        out = _maybe_append_delivery_constraint_reminder(
            brief, section_title="Project Timeline"
        )
        self.assertIn("CONSTRAINT REMINDER", out)

    def test_non_expand_sow_ticket_unchanged(self) -> None:
        brief = "Surgical patch — fix the typo in deliverable 2."
        out = _maybe_append_delivery_constraint_reminder(
            brief, section_title="Statement of Work"
        )
        self.assertEqual(out, brief)

    def test_expand_on_cover_letter_unchanged(self) -> None:
        brief = "Expand the cover letter with more warmth."
        out = _maybe_append_delivery_constraint_reminder(
            brief, section_title="Cover Letter"
        )
        self.assertEqual(out, brief)

    def test_format_block_matches_canonical(self) -> None:
        block = format_pricing_delivery_constraints_block(instrument=_instrument())
        self.assertIn("buyer_pricing_form", block)


class SeniorEditorCoverageBuyerForm(unittest.IsolatedAsyncioTestCase):
    async def test_coverage_audit_skips_terms_reformat(self) -> None:
        from app.services.proposal_senior_editor_coverage import (
            apply_senior_editor_section_coverage_audit,
        )

        inst = _instrument()
        form_md = BUYER_FORM_MD
        with_terms = (
            form_md
            + "\n\n## Terms\n\n"
            "Allocation is 40% strategy, 35% creative, and 25% media.\n"
        )
        cost = _sec("cost", "Cost Proposal", with_terms)
        draft = ProposalDraft(rfpId="r1", sections=[cost], updatedAt="t")
        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="t",
            pricingInstrument=inst.model_dump(by_alias=True),
        )
        out, logs, _tickets = await apply_senior_editor_section_coverage_audit(
            draft,
            research=research,
            rfp_text="pricing form",
            use_llm_toc=False,
        )
        cost_out = next(s for s in out.sections if s.id == "cost")
        self.assertEqual(cost_out.content, with_terms)
        self.assertFalse(any("Reformatted Terms" in x for x in logs))


class QualityGatePatchPromptGuards(unittest.IsolatedAsyncioTestCase):
    async def test_patch_section_prompt_includes_constraints_header(self) -> None:
        """Scan QA _patch_section must prepend canonical pack when research has form."""
        from unittest.mock import AsyncMock, patch

        from app.models.rfp import RfpRecord
        from app.models.proposal import GateTicket
        from app.services import proposal_quality_gate as gate

        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="t",
            pricingInstrument=_instrument().model_dump(by_alias=True),
        )
        rfp = RfpRecord(
            id="r1",
            title="RFP",
            client="Acme",
            due_date="2026-09-01",
            received_date="2026-08-01",
            last_activity="2026-08-13",
            last_activity_note="scan",
        )
        ticket = GateTicket(
            sectionId="sow",
            code="slop.filler",
            detector="slop",
            message="Tighten filler sentence",
            guidance="Replace with concrete deliverable language",
            requiresEvidence=False,
        )
        captured: dict[str, str] = {}

        async def _capture_agent(**kwargs):
            captured["user_content"] = str(kwargs.get("user_content") or "")
            return {"content": "patched body"}, "mock", []

        with patch(
            "app.services.proposal_langchain_agents.run_tool_json_agent",
            new=AsyncMock(side_effect=_capture_agent),
        ):
            after, note = await gate._patch_section(
                rfp=rfp,
                section_id="sow",
                section_title="Statement of Work",
                content="We will deliver the scope as written.",
                ticket=ticket,
                research=research,
            )

        self.assertEqual(after, "patched body")
        self.assertEqual(note, "patched")
        prompt = captured.get("user_content") or ""
        self.assertIn("=== PRICING + DELIVERY CONSTRAINTS (canonical) ===", prompt)
        self.assertIn("buyer_pricing_form", prompt)
        self.assertIn("CONSTRAINT REMINDER", prompt)


if __name__ == "__main__":
    unittest.main()
