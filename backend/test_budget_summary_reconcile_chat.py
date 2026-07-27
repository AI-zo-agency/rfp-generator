"""Regression test: chat-driven budget summary reconcile must not crash.

improve_proposal_section() previously referenced the local variable `section`
when calling _try_budget_summary_reconcile() before `section` had been
assigned anywhere in the function, raising:

    UnboundLocalError: cannot access local variable 'section' where it is
    not associated with a value

This reproduces that path: a non-selection chat message that asks to
reconcile the investment summary against the fee table, with no canonical
budget cached yet.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_section_editor import improve_proposal_section


def _section() -> ProposalSection:
    return ProposalSection(
        id="section-budget",
        title="Proposed budget and cost structure",
        content="Total proposed investment: $325,242.66 ...",
        source="rfp",
        mode="write",
        wordTarget=400,
        status="generated",
    )


def _draft() -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-budget-reconcile",
        sections=[_section()],
        updatedAt="2026-07-27T00:00:00+00:00",
    )


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="rfp-budget-reconcile",
        title="Test RFP",
        client="Test Client",
        sector="Municipal",
        source="manual",
        dueDate="2026-08-01",
        receivedDate="2026-07-01",
        lastActivity="2026-07-01",
        lastActivityNote="test",
    )


class BudgetSummaryReconcileChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_reconcile_ask_does_not_raise_unbound_local(self) -> None:
        draft = _draft()

        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch(
                "app.services.proposal_section_editor.aload_rfp_for_proposal",
                new=AsyncMock(return_value=(_rfp(), "", "RFP text")),
            ),
            patch(
                "app.services.proposal_section_editor.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            patch(
                "app.services.proposal_section_editor.aget_research_cache",
                new=AsyncMock(return_value=None),
            ),
        ):
            section, updated_draft, research, provider, reply, changed = (
                await improve_proposal_section(
                    "rfp-budget-reconcile",
                    "section-budget",
                    (
                        "Please reconcile the investment summary — the "
                        "totals don't match the fee table."
                    ),
                    persist=False,
                )
            )

        self.assertEqual(section.id, "section-budget")
        self.assertFalse(changed)
        self.assertIn("no canonical fee table", reply)


if __name__ == "__main__":
    unittest.main()
