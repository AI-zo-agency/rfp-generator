"""Apply the fix must reuse prior audit context — no KB re-plan."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import proposal_section_editor as editor


class ApplySuggestedFixFastPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_fix_skips_kb_and_planners(self) -> None:
        section = ProposalSection(
            id="section-1-business-info",
            title="1.3 — Business Information",
            content="| Email | info@zo.agency |",
            mode="write",
            word_target=187,
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-08-10T00:00:00Z",
            sections=[section],
        )
        rfp = RfpRecord(
            id="r1",
            title="RFP",
            client="DuPage",
            sector="Government",
            source="manual",
            dueDate="2026-09-01",
            receivedDate="2026-08-01",
            lastActivity="2026-08-01",
            lastActivityNote="t",
        )
        history = [
            {"role": "user", "content": "here its wrong"},
            {
                "role": "assistant",
                "content": (
                    "**Incorrect** — KB source 01_companyfacts verified.docx: "
                    "Email: connect@zo.agency"
                ),
            },
        ]
        instruction = (
            "Change Email from info@zo.agency to connect@zo.agency per 01_companyfacts."
        )

        async def fake_chat(messages, **kwargs):
            self.assertEqual(kwargs.get("node_name"), "chat_apply_suggested_fix")
            user = messages[-1]["content"]
            self.assertIn("connect@zo.agency", user)
            self.assertIn("Prior chat", user)
            self.assertNotIn("KB excerpts", user)
            self.assertNotIn("04_Bio KB", user)
            return {"content": "| Email | connect@zo.agency |"}, "stub"

        with (
            patch.object(editor.llm, "is_configured", return_value=True),
            patch.object(editor.llm, "chat_json", side_effect=fake_chat),
            patch.object(editor, "_plan_edit_scope", new_callable=AsyncMock) as plan_scope,
            patch.object(editor, "_plan_section_improve", new_callable=AsyncMock) as plan_improve,
            patch.object(editor.supermemory, "search_hybrid", new_callable=AsyncMock) as search,
            patch.object(
                editor,
                "_persist_section_improve_draft",
                side_effect=lambda d, r, **kw: d,
            ),
        ):
            working, updated, *_rest, changed = await editor._apply_suggested_fix_to_section(
                rfp_id="r1",
                section=section,
                draft=draft,
                research=None,
                rfp=rfp,
                instruction=instruction,
                conversation_history=history,
                persist=True,
            )

        plan_scope.assert_not_called()
        plan_improve.assert_not_called()
        search.assert_not_called()
        self.assertTrue(changed)
        self.assertIn("connect@zo.agency", working.content or "")

    async def test_improve_proposal_section_apply_fix_uses_fast_path(self) -> None:
        section = ProposalSection(
            id="section-1-business-info",
            title="1.3 — Business Information",
            content="| Email | info@zo.agency |",
            mode="write",
            word_target=187,
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-08-10T00:00:00Z",
            sections=[section],
        )

        async def fake_apply(**kwargs):
            return (
                section.model_copy(update={"content": "| Email | connect@zo.agency |"}),
                draft,
                None,
                "stub",
                "Applied.",
                True,
            )

        with (
            patch.object(editor, "aload_rfp_for_proposal", new_callable=AsyncMock) as load_rfp,
            patch.object(editor, "aget_proposal_draft", new_callable=AsyncMock, return_value=draft),
            patch.object(editor, "aget_research_cache", new_callable=AsyncMock, return_value=None),
            patch.object(editor.llm, "is_configured", return_value=True),
            patch.object(editor, "_apply_suggested_fix_to_section", side_effect=fake_apply) as apply_fn,
            patch.object(editor, "_plan_edit_scope", new_callable=AsyncMock) as plan_scope,
        ):
            load_rfp.return_value = (
                RfpRecord(
                    id="r1",
                    title="RFP",
                    client="DuPage",
                    sector="Government",
                    source="manual",
                    dueDate="2026-09-01",
                    receivedDate="2026-08-01",
                    lastActivity="2026-08-01",
                    lastActivityNote="t",
                ),
                "",
                "",
            )
            await editor.improve_proposal_section(
                "r1",
                "section-1-business-info",
                "Change email to connect@zo.agency",
                apply_fix=True,
            )

        apply_fn.assert_called_once()
        plan_scope.assert_not_called()


if __name__ == "__main__":
    unittest.main()
