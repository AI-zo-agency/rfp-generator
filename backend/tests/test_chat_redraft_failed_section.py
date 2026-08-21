"""Chat must be able to rebuild a section whose Phase 3 draft failed.

Observed: sections 15 and 16 held only
"[VERIFY: Section drafting failed - needs manual regeneration]". Recovery
required pressing Continue Proposal, which is impossible while a later phase
(budget) is still running. The normal chat edit paths cannot help either --
there is no prose to improve, so they would rewrite the placeholder itself.

Detection is on the section's STATE, not the user's wording: a placeholder
section plus any edit request means "draft this properly".
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_draft_llm import SECTION_DRAFT_FAILURE_PLACEHOLDER
from app.services.proposal_section_editor import (
    _try_draft_empty_section,
    _try_redraft_failed_section,
)


def _section(sid: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=sid, title="Standard Contract Acknowledgment",
        content=content, required=True, custom=False,
    )


def _draft(section: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-1", sections=[section],
        updatedAt="2026-01-01T00:00:00Z", generatedAt="2026-01-01T00:00:00Z",
    )


class RedraftFailedSectionTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, content: str, redraft_result):
        section = _section("s15", content)
        draft = _draft(section)
        with mock.patch(
            "app.services.proposal_self_edit_loop._redraft_section_via_phase3_isolated",
            new=mock.AsyncMock(return_value=redraft_result),
        ) as spy:
            out = await _try_redraft_failed_section(
                rfp_id="rfp-1", section=section, draft=draft, research=None,
                user_message="write this section properly", rfp=mock.Mock(),
            )
        return out, spy

    async def test_healthy_section_is_left_to_the_normal_edit_paths(self) -> None:
        out, spy = await self._run("Real drafted prose about the contract.", None)
        self.assertIsNone(out)
        spy.assert_not_awaited()

    async def test_placeholder_section_triggers_a_phase3_rebuild(self) -> None:
        rebuilt = _section("s15", "Full acknowledgment prose.")
        out, spy = await self._run(
            SECTION_DRAFT_FAILURE_PLACEHOLDER,
            (_draft(rebuilt), None, True, "ok"),
        )
        self.assertIsNotNone(out)
        spy.assert_awaited_once()
        section, _draft_out, _research, mode, reply, changed = out
        self.assertEqual(section.content, "Full acknowledgment prose.")
        self.assertEqual(mode, "phase3")
        self.assertTrue(changed)
        self.assertIn("Rebuilt", reply)

    async def test_user_message_is_passed_as_the_rewrite_brief(self) -> None:
        rebuilt = _section("s15", "Prose.")
        section = _section("s15", SECTION_DRAFT_FAILURE_PLACEHOLDER)
        draft = _draft(section)
        with mock.patch(
            "app.services.proposal_self_edit_loop._redraft_section_via_phase3_isolated",
            new=mock.AsyncMock(return_value=(_draft(rebuilt), None, True, "ok")),
        ) as spy:
            await _try_redraft_failed_section(
                rfp_id="rfp-1", section=section, draft=draft, research=None,
                user_message="keep it to two short paragraphs", rfp=mock.Mock(),
            )
        self.assertEqual(
            spy.await_args.kwargs["rewrite_brief"], "keep it to two short paragraphs"
        )

    async def test_failed_rebuild_explains_itself_and_changes_nothing(self) -> None:
        section = _section("s15", SECTION_DRAFT_FAILURE_PLACEHOLDER)
        out, _spy = await self._run(
            SECTION_DRAFT_FAILURE_PLACEHOLDER,
            (_draft(section), None, False, "phase3_failed: 429 rate limited"),
        )
        _sec, _d, _r, mode, reply, changed = out
        self.assertFalse(changed)
        self.assertEqual(mode, "none")
        self.assertIn("rate-limited", reply)

    async def test_empty_section_is_not_hijacked(self) -> None:
        """Only the failure placeholder routes here, not any empty section."""
        out, spy = await self._run("", None)
        self.assertIsNone(out)
        spy.assert_not_awaited()

    async def test_stored_comma_variant_triggers_a_rebuild(self) -> None:
        """The exact string stored for San Benito sections 9, 15 and 16.

        It differs from SECTION_DRAFT_FAILURE_PLACEHOLDER by one character (comma
        vs em dash). Exact-equality matching skipped it, so chat fell through to
        the advisory router and answered "I cannot improve this section" instead
        of rebuilding it.
        """
        stored = "[VERIFY: Section drafting failed, needs manual regeneration]"
        self.assertNotEqual(stored, SECTION_DRAFT_FAILURE_PLACEHOLDER.strip())

        rebuilt = _section("s15", "Full acknowledgment prose.")
        out, spy = await self._run(stored, (_draft(rebuilt), None, True, "ok"))

        self.assertIsNotNone(out)
        spy.assert_awaited_once()
        self.assertTrue(out[5])

    async def test_inline_verify_chip_is_left_to_the_normal_edit_paths(self) -> None:
        """A drafted section that merely contains a VERIFY chip must not be rebuilt.

        Rebuilding it would discard finished prose.
        """
        drafted = (
            "We accept the terms of the exemplar agreement without exception. "
            "[VERIFY: authorized signatory] The signed page is returned with our bid."
        )
        out, spy = await self._run(drafted, None)
        self.assertIsNone(out)
        spy.assert_not_awaited()

    async def test_no_evidence_stub_explains_the_corpus_gap(self) -> None:
        """Family B must not tell the user to 'try again shortly' — that loops."""
        stub = "[VERIFY: Draft content for Exhibit A — insufficient evidence in corpus.]"
        section = _section("s15", stub)
        out, _spy = await self._run(
            stub, (_draft(section), None, False, "phase3_failed: no evidence")
        )
        _sec, _d, _r, mode, reply, changed = out
        self.assertFalse(changed)
        self.assertEqual(mode, "none")
        self.assertIn("knowledge base", reply)
        self.assertNotIn("rate-limited", reply)


class DraftEmptySectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_business_info_seeds_companyfacts_without_phase3(self) -> None:
        section = ProposalSection(
            id="section-1-business-info",
            title="1.3 — Business Information",
            content="",
            required=True,
            custom=False,
            source="template",
        )
        draft = _draft(section)
        rfp = mock.Mock(id="rfp-1", client="PSU", sector="Higher Ed", title="RFQ")
        with mock.patch(
            "app.services.proposal_self_edit_loop._redraft_section_via_phase3_isolated",
            new=mock.AsyncMock(),
        ) as spy, mock.patch(
            "app.services.proposal_section_editor._persist_section_improve_draft",
            new=mock.AsyncMock(side_effect=lambda d, _r, section_title="": d),
        ):
            out = await _try_draft_empty_section(
                rfp_id="rfp-1",
                section=section,
                draft=draft,
                research=None,
                user_message="Improve this section for the RFP.",
                rfp=rfp,
                rfp_context="RFP text",
                improve_section_pinned=True,
                apply_fix=False,
            )
        self.assertIsNotNone(out)
        spy.assert_not_awaited()
        rebuilt, _draft_out, _research, _mode, reply, changed = out
        self.assertTrue(changed)
        self.assertIn("Drafted", reply)
        self.assertIn("Legal Name", rebuilt.content or "")
        self.assertIn("Z'Onion", rebuilt.content or "")

    async def test_empty_section_without_improve_pin_is_not_hijacked(self) -> None:
        section = _section("section-1-business-info", "")
        draft = _draft(section)
        with mock.patch(
            "app.services.proposal_self_edit_loop._redraft_section_via_phase3_isolated",
            new=mock.AsyncMock(),
        ) as spy:
            out = await _try_draft_empty_section(
                rfp_id="rfp-1",
                section=section,
                draft=draft,
                research=None,
                user_message="what is missing from this section?",
                rfp=mock.Mock(id="rfp-1"),
                rfp_context="",
                improve_section_pinned=False,
                apply_fix=False,
            )
        self.assertIsNone(out)
        spy.assert_not_awaited()

    async def test_empty_rfp_section_uses_phase3_isolated(self) -> None:
        section = ProposalSection(
            id="rfp-sec-9",
            title="Exhibit B",
            content="",
            required=True,
            custom=False,
            source="rfp",
        )
        draft = _draft(section)
        rebuilt = section.model_copy(update={"content": "Certification prose."})
        with mock.patch(
            "app.services.proposal_self_edit_loop._redraft_section_via_phase3_isolated",
            new=mock.AsyncMock(return_value=(_draft(rebuilt), None, True, "ok")),
        ) as spy:
            out = await _try_draft_empty_section(
                rfp_id="rfp-1",
                section=section,
                draft=draft,
                research=None,
                user_message="Improve this section for the RFP.",
                rfp=mock.Mock(id="rfp-1"),
                rfp_context="RFP",
                improve_section_pinned=True,
                apply_fix=False,
            )
        self.assertIsNotNone(out)
        spy.assert_awaited_once()
        self.assertTrue(out[5])
        self.assertIn("Drafted", out[4])

    async def test_empty_section_skipped_when_selection_pinned(self) -> None:
        section = _section("section-1-business-info", "")
        draft = _draft(section)
        with mock.patch(
            "app.services.proposal_self_edit_loop._redraft_section_via_phase3_isolated",
            new=mock.AsyncMock(),
        ) as spy:
            out = await _try_draft_empty_section(
                rfp_id="rfp-1",
                section=section,
                draft=draft,
                research=None,
                user_message="fill this",
                rfp=mock.Mock(id="rfp-1"),
                rfp_context="",
                improve_section_pinned=False,
                apply_fix=False,
                selection_mode=True,
            )
        self.assertIsNone(out)
        spy.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
