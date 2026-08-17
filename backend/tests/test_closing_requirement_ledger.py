"""Closing requirement ledger — audit states and fixture-based detection."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_closing_ledger import (
    audit_draft_against_closing_ledger,
    classify_closing_requirement_state,
    ledger_from_fixture,
)
from app.services.proposal_closing_package import (
    detect_closing_components,
    draft_already_covers_component,
)
from app.services.proposal_outline_dedup import merge_closing_components_into_outline


class ClosingLedgerAuditTests(unittest.TestCase):
    def test_missing_when_no_section(self) -> None:
        ledger = ledger_from_fixture(
            [
                {
                    "id": "attachment_02",
                    "title": "Attachment 02 — Conflict of Interest",
                    "kind": "attachment",
                    "rfpLabel": "Attachment 02",
                    "sectionId": "rfp-closing-attachment-02",
                }
            ]
        )
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[])
        audits = audit_draft_against_closing_ledger(draft, ledger)
        self.assertEqual(audits[0].state, "missing")

    def test_fabricated_ready_without_manual_fill(self) -> None:
        ledger = ledger_from_fixture(
            [
                {
                    "id": "w9",
                    "title": "W-9",
                    "kind": "attachment",
                    "sectionId": "rfp-closing-w9",
                }
            ]
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="rfp-closing-w9",
                    title="W-9",
                    content=(
                        "| Document | Status |\n| --- | --- |\n| W-9 | Ready |\n"
                    ),
                )
            ],
        )
        row = classify_closing_requirement_state(ledger.requirements[0], draft)
        self.assertEqual(row.state, "fabricated_ready")

    def test_manual_fill_when_handoff_present(self) -> None:
        ledger = ledger_from_fixture(
            [
                {
                    "id": "coi",
                    "title": "Certificate of Insurance",
                    "kind": "attachment",
                    "sectionId": "rfp-closing-coi",
                }
            ]
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="rfp-closing-coi",
                    title="Certificate of Insurance",
                    content=(
                        "Return the COI with the proposal.\n\n"
                        "[MANUAL FILL: attach PDF of current COI before export.]\n"
                    ),
                )
            ],
        )
        row = classify_closing_requirement_state(ledger.requirements[0], draft)
        self.assertEqual(row.state, "manual_fill")

    def test_detect_without_ledger_is_empty(self) -> None:
        self.assertEqual(
            detect_closing_components(
                "Offerors must submit three client references with the proposal."
            ),
            [],
        )

    def test_detect_with_fixture_ledger(self) -> None:
        ledger = ledger_from_fixture(
            [
                {
                    "id": "references",
                    "title": "References",
                    "kind": "form",
                    "sectionId": "rfp-closing-references",
                    "draftInstructions": "Provide three references.",
                }
            ]
        )
        comps = detect_closing_components("ignored", ledger=ledger)
        self.assertEqual([c.id for c in comps], ["references"])

    def test_merge_outline_uses_ledger_only(self) -> None:
        ledger = ledger_from_fixture(
            [
                {
                    "id": "addenda_acknowledgement",
                    "title": "Acknowledgement of Addenda",
                    "kind": "form",
                    "sectionId": "rfp-closing-addenda",
                }
            ]
        )
        # Procedural RFP text must not matter — ledger is authority.
        merged, added = merge_closing_components_into_outline(
            [],
            rfp_context="The County may issue any addenda prior to the due date.",
            ledger=ledger,
        )
        self.assertEqual(len(added), 1)
        self.assertTrue(any(getattr(s, "title", "") == "Acknowledgement of Addenda" for s in merged))

    def test_merge_without_ledger_adds_nothing(self) -> None:
        merged, added = merge_closing_components_into_outline(
            [],
            rfp_context=(
                "All addenda must be acknowledged and returned with your proposal."
            ),
        )
        self.assertEqual(added, [])
        self.assertEqual(merged, [])

    def test_draft_covers_by_section_id(self) -> None:
        ledger = ledger_from_fixture(
            [
                {
                    "id": "w9",
                    "title": "W-9 Form",
                    "kind": "attachment",
                    "sectionId": "rfp-closing-w9",
                }
            ]
        )
        comps = detect_closing_components("", ledger=ledger)
        self.assertTrue(
            draft_already_covers_component(
                draft_section_ids={"rfp-closing-w9"},
                draft_titles=["Other"],
                component=comps[0],
            )
        )


class ClosingLedgerCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_hit_skips_extract(self) -> None:
        from unittest import mock

        from app.models.proposal import ProposalResearchCache
        from app.services.proposal_closing_ledger import get_or_extract_closing_ledger

        cached = ledger_from_fixture(
            [
                {
                    "id": "w9",
                    "title": "W-9",
                    "kind": "attachment",
                    "sectionId": "rfp-closing-w9",
                }
            ]
        )
        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="t",
            closingRequirementLedger=cached.model_dump(by_alias=True),
        )
        with mock.patch(
            "app.services.proposal_closing_ledger.extract_closing_requirement_ledger",
            new=mock.AsyncMock(side_effect=AssertionError("should not extract")),
        ):
            ledger, out = await get_or_extract_closing_ledger(
                "ignored rfp text", research=research
            )
        self.assertEqual([r.id for r in ledger.requirements], ["w9"])
        self.assertIs(out, research)

    async def test_extract_persists_when_uncached(self) -> None:
        from unittest import mock

        from app.models.proposal import ProposalResearchCache
        from app.services.proposal_closing_ledger import (
            ClosingRequirementLedger,
            get_or_extract_closing_ledger,
        )

        research = ProposalResearchCache(rfpId="r1", updatedAt="t")
        fresh = ledger_from_fixture(
            [{"id": "references", "title": "References", "kind": "form"}]
        )
        saved: list[ProposalResearchCache] = []

        async def _save(r: ProposalResearchCache) -> None:
            saved.append(r)

        with (
            mock.patch(
                "app.services.proposal_closing_ledger.extract_closing_requirement_ledger",
                new=mock.AsyncMock(return_value=fresh),
            ),
            mock.patch(
                "app.services.proposal_repository.asave_research_cache",
                new=_save,
            ),
        ):
            ledger, out = await get_or_extract_closing_ledger(
                "RFP must submit references", research=research
            )
        self.assertEqual([r.id for r in ledger.requirements], ["references"])
        self.assertIsNotNone(out)
        self.assertIsNotNone(out.closing_requirement_ledger)
        self.assertEqual(len(saved), 1)
        restored = ClosingRequirementLedger.model_validate(
            out.closing_requirement_ledger
        )
        self.assertEqual(restored.requirements[0].id, "references")


class FabricatedReadyRepairTests(unittest.TestCase):
    def test_demotes_ready_to_manual_fill(self) -> None:
        from app.services.proposal_closing_ledger import repair_fabricated_ready_in_draft

        ledger = ledger_from_fixture(
            [
                {
                    "id": "w9",
                    "title": "W-9",
                    "kind": "attachment",
                    "sectionId": "rfp-closing-w9",
                }
            ]
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="rfp-closing-w9",
                    title="W-9",
                    content="| Document | Status |\n| --- | --- |\n| W-9 | Ready |\n",
                )
            ],
        )
        updated, logs = repair_fabricated_ready_in_draft(draft, ledger)
        self.assertTrue(logs)
        body = updated.sections[0].content or ""
        self.assertIn("MANUAL FILL", body)
        self.assertNotIn("| Ready |", body)

    def test_stubs_missing_requirement(self) -> None:
        from app.services.proposal_closing_ledger import ensure_missing_closing_stubs

        ledger = ledger_from_fixture(
            [
                {
                    "id": "attachment_02",
                    "title": "Attachment 02",
                    "kind": "attachment",
                    "sectionId": "rfp-closing-attachment-02",
                    "rfpLabel": "Attachment 02 — Conflict",
                }
            ]
        )
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[])
        updated, logs = ensure_missing_closing_stubs(draft, ledger)
        self.assertTrue(logs)
        self.assertEqual(updated.sections[0].id, "rfp-closing-attachment-02")
        self.assertIn("MANUAL FILL", updated.sections[0].content or "")


class BudgetFormatAlignTests(unittest.TestCase):
    def test_confident_judge_overrides_phased(self) -> None:
        from app.services.proposal_budget_format_judge import (
            BudgetFormatJudgment,
            align_budget_format_to_judgment,
        )

        judgment = BudgetFormatJudgment(
            budgetFormat="personnel_loading",
            reason="Hourly rates by role",
            confidence=0.9,
        )
        fmt, changed = align_budget_format_to_judgment("phased", judgment)
        self.assertTrue(changed)
        self.assertEqual(fmt, "personnel_loading")

    def test_low_confidence_leaves_pricing_agent(self) -> None:
        from app.services.proposal_budget_format_judge import (
            BudgetFormatJudgment,
            align_budget_format_to_judgment,
        )

        judgment = BudgetFormatJudgment(
            budgetFormat="personnel_loading",
            reason="guess",
            confidence=0.2,
        )
        fmt, changed = align_budget_format_to_judgment("phased", judgment)
        self.assertFalse(changed)
        self.assertEqual(fmt, "phased")


if __name__ == "__main__":
    unittest.main()
