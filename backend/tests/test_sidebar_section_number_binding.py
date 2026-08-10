"""Sidebar 'section N' must not be answered as an RFP clause with the same number.

Reported: user asked 'what is section 11 about?' while viewing PDF submission
(sidebar 11). Status said PDF. Reply described Understanding of Island County
(sidebar 19) because the RFP text also numbers that topic as section 11.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import proposal_section_editor as editor


def _sec(i: int, title: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=f"s{i}",
        title=title,
        content=content,
        mode="write",
        word_target=100,
    )


def _island_draft() -> ProposalDraft:
    sections: list[ProposalSection] = []
    for i in range(1, 21):
        if i == 11:
            sections.append(
                _sec(
                    i,
                    f"{i}. PDF format proposal submission",
                    "This RFP requires PDF format proposal submission emailed to Lee Ann Mozes.",
                )
            )
        elif i == 19:
            sections.append(
                _sec(
                    i,
                    f"{i}. Understanding of Island County and Tourism Context",
                    "[MANUAL FILL] Whidbey Island knowledge placeholder.",
                )
            )
        else:
            sections.append(_sec(i, f"{i}. Placeholder {i}", f"content {i}"))
    return ProposalDraft(rfpId="r1", updatedAt="t", sections=sections)


class SidebarSectionBindingTests(unittest.TestCase):
    def test_resolve_section_11_is_pdf_not_understanding(self) -> None:
        draft = _island_draft()
        hit = editor._resolve_section_from_message(
            draft, "what is section 11 about?", "s1"
        )
        assert hit is not None
        self.assertEqual(hit.id, "s11")
        self.assertIn("PDF", hit.title)

    def test_binding_names_sidebar_position_and_pdf_title(self) -> None:
        draft = _island_draft()
        pdf = draft.sections[10]
        block = editor._advisory_target_binding(draft, pdf)
        self.assertIn("11 of 20", block)
        self.assertIn("PDF format proposal submission", block)
        self.assertIn("AUTHORITATIVE TARGET TAB", block)

    def test_manuscript_digest_labels_sidebar_index(self) -> None:
        draft = _island_draft()
        digest = editor._manuscript_digest(draft, titles_only=True)
        self.assertIn("Sidebar 11/20 — 11. PDF format proposal submission", digest)
        self.assertIn(
            "Sidebar 19/20 — 19. Understanding of Island County and Tourism Context",
            digest,
        )


class SidebarSectionAdvisoryPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_section_11_ask_prompt_leads_with_pdf_draft(self) -> None:
        draft = _island_draft()
        pdf = draft.sections[10]
        rfp = RfpRecord(
            id="r1",
            title="Social Media Management for Island County Tourism",
            client="Island County",
            sector="tourism",
            source="manual",
            dueDate="2026-09-01",
            receivedDate="2026-08-01",
            lastActivity="2026-08-01",
            lastActivityNote="t",
        )
        # Poison: RFP text that would make a naive model answer Understanding.
        rfp_context = (
            "Section 11 — Understanding of Island County and its communities\n"
            "Offerors must demonstrate knowledge of Whidbey Island tourism.\n"
        )
        captured: dict[str, str] = {}

        async def fake_chat(messages, **kwargs):
            captured["user"] = messages[-1]["content"]
            return {"reply": "ok"}, "stub"

        with patch.object(editor, "chat_json_with_repair", side_effect=fake_chat):
            await editor._section_chat_advisory_reply(
                section=pdf,
                rfp=rfp,
                rfp_context=rfp_context,
                user_message="what is section 11 about?",
                conversation_history=[],
                selection_text=None,
                requirements_block="",
                manuscript_digest="",
                draft=draft,
            )

        prompt = captured["user"]
        self.assertIn("AUTHORITATIVE TARGET TAB", prompt)
        self.assertIn("PDF format proposal submission", prompt)
        self.assertIn("Lee Ann Mozes", prompt)
        # Target draft must appear before the poisoned RFP clause.
        self.assertLess(
            prompt.index("Lee Ann Mozes"),
            prompt.index("Understanding of Island County and its communities"),
        )
        self.assertIn("do not remap section numbers", prompt.casefold())


if __name__ == "__main__":
    unittest.main()
