"""Tests for MANUAL FILL protection in section chat."""

from __future__ import annotations

import contextlib
import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_common import ProposalError
from app.services.proposal_manual_flags import (
    MANUAL_FILL_TAG_RE,
    extract_manual_fill_tags,
    fill_manual_fill_tags,
    is_manual_fill_request,
    mask_manual_fill_tags,
    missing_manual_fill_placeholders,
    unmask_manual_fill_tags,
)
from app.services.proposal_section_editor import (
    _mask_manual_fill_for_rewrite,
    _unmask_manual_fill_checked,
    improve_proposal_section,
)


def _section(content: str, *, sid: str = "section-budget") -> ProposalSection:
    return ProposalSection(
        id=sid,
        title="4.1 — Pricing Proposal Form",
        content=content,
        source="rfp",
        mode="write",
        wordTarget=400,
        status="generated",
    )


def _draft(content: str) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-mfill",
        sections=[_section(content)],
        updatedAt="2026-07-23T00:00:00+00:00",
    )


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="rfp-mfill",
        title="Test RFP",
        client="Test Client",
        sector="Health",
        source="manual",
        dueDate="2026-08-01",
        receivedDate="2026-07-01",
        lastActivity="2026-07-01",
        lastActivityNote="test",
    )


class ManualFillHelperTests(unittest.TestCase):
    def test_expanded_regex_covers_bare_and_colon_forms(self) -> None:
        text = (
            "| Title | [MANUAL FILL] |\n"
            "| Fax | [MANUAL FILL or N/A] |\n"
            "| Sign | [MANUAL FILL: wet/digital signature] |\n"
        )
        tags = [t.text for t in extract_manual_fill_tags(text)]
        self.assertEqual(
            tags,
            [
                "[MANUAL FILL]",
                "[MANUAL FILL or N/A]",
                "[MANUAL FILL: wet/digital signature]",
            ],
        )
        self.assertEqual(len(MANUAL_FILL_TAG_RE.findall(text)), 3)

    def test_mask_roundtrip(self) -> None:
        text = "Fee: [MANUAL FILL: Title] and [MANUAL FILL]"
        masked, originals = mask_manual_fill_tags(text)
        self.assertIn("«MFILL_0»", masked)
        self.assertIn("«MFILL_1»", masked)
        self.assertNotIn("[MANUAL FILL", masked)
        restored = unmask_manual_fill_tags(masked, originals)
        self.assertEqual(restored, text)

    def test_missing_placeholders_detected(self) -> None:
        originals = ["[MANUAL FILL: Title]", "[MANUAL FILL]"]
        self.assertEqual(
            missing_manual_fill_placeholders("only «MFILL_0» kept", originals),
            ["[MANUAL FILL]"],
        )

    def test_fill_from_user_message(self) -> None:
        content = "Role: [MANUAL FILL: Title]"
        updated, log, remaining = fill_manual_fill_tags(
            content,
            user_message='fill [MANUAL FILL: Title] with Director of Marketing',
        )
        self.assertEqual(updated, "Role: Director of Marketing")
        self.assertEqual(log[0]["source"], "user")
        self.assertEqual(remaining, [])

    def test_fill_from_kb_fein(self) -> None:
        content = "EIN: [MANUAL FILL: FEIN]"
        updated, log, remaining = fill_manual_fill_tags(
            content,
            user_message="fill MANUAL FILL from KB",
            kb_blob="Federal EIN 93-1234567 on file",
        )
        self.assertEqual(updated, "EIN: 93-1234567")
        self.assertEqual(log[0]["source"], "kb")
        self.assertEqual(remaining, [])

    def test_fill_gap_leaves_tag(self) -> None:
        content = "Sign: [MANUAL FILL: wet/digital signature]"
        updated, log, remaining = fill_manual_fill_tags(
            content,
            user_message="fill all MANUAL FILL tags from KB",
            kb_blob="no signature facts here",
        )
        self.assertEqual(updated, content)
        self.assertEqual(log, [])
        self.assertEqual(remaining, ["[MANUAL FILL: wet/digital signature]"])

    def test_is_manual_fill_request(self) -> None:
        self.assertTrue(is_manual_fill_request("Fill MANUAL FILL tags from KB"))
        self.assertFalse(is_manual_fill_request("tighten this paragraph"))


def _structure_edit(section_id: str):
    from app.services.proposal_chat_structure import StructurePlan

    return StructurePlan(action="edit", editSectionId=section_id)


@contextlib.contextmanager
def _common_improve_patches(*, draft, research, section_id: str, chat_json):
    """Shared mocks for improve_proposal_section MANUAL FILL integration tests."""
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
            new=AsyncMock(return_value=research),
        ),
        patch(
            "app.services.proposal_section_editor._persist_section_improve_draft",
            new=AsyncMock(side_effect=lambda d, r, section_title="": d),
        ),
        patch(
            "app.services.proposal_chat_structure.plan_chat_structure_action",
            new=AsyncMock(return_value=_structure_edit(section_id)),
        ),
        patch(
            "app.services.proposal_chat_manuscript_fix.classify_chat_edit_intent",
            new=AsyncMock(
                return_value={
                    "intent": "single_edit",
                    "primarySectionId": section_id,
                }
            ),
        ),
        patch(
            "app.services.proposal_section_editor.should_apply_budget_playbook",
            return_value=False,
        ),
        patch(
            "app.services.proposal_section_editor.resolve_voice_context",
            new=AsyncMock(return_value=({}, "")),
        ),
        patch(
            "app.services.proposal_brand_voice.fetch_zo_voice_excerpt",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "app.services.proposal_knowledge_base_tools.search_knowledge_base",
            new=AsyncMock(return_value=("kb facts about pricing", [])),
        ),
        patch(
            "app.services.proposal_section_editor.llm.chat_json",
            new=AsyncMock(side_effect=chat_json),
        ),
        patch(
            "app.services.proposal_section_editor._plan_section_improve",
            new=AsyncMock(
                return_value=("tighten", "tighten this paragraph", ["zö pricing facts"])
            ),
        ),
    ):
        yield


class ManualFillGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_unrelated_rewrite_preserves_manual_fill(self) -> None:
        content = (
            "We will submit pricing as required.\n\n"
            "| Title | [MANUAL FILL] |\n"
            "| Fax | [MANUAL FILL or N/A] |\n"
        )
        draft = _draft(content)
        draft.sections[0] = draft.sections[0].model_copy(
            update={
                "source": "template",
                "id": "section-1-who",
                "title": "1.1 — Who We Are",
            }
        )
        research = ProposalResearchCache(
            rfpId="rfp-mfill",
            updatedAt="2026-07-23T00:00:00+00:00",
            evidenceCorpus=[],
            rfpSections=[],
        )

        async def fake_chat_json(messages, **kwargs):
            user = messages[-1]["content"]
            # Model tries to "resolve" bare tags but must keep «MFILL_N» if masked.
            if "«MFILL_0»" in user:
                body = (
                    "We submit pricing promptly.\n\n"
                    "| Title | «MFILL_0» |\n"
                    "| Fax | «MFILL_1» |\n"
                )
            else:
                body = "We submit pricing promptly. Title TBD. Fax N/A."
            return {"content": body}, "mock"

        with _common_improve_patches(
            draft=draft,
            research=research,
            section_id="section-1-who",
            chat_json=fake_chat_json,
        ):
            section, _updated, _research, _provider, _msg, changed, _ = await improve_proposal_section(
                "rfp-mfill",
                "section-1-who",
                "tighten this paragraph",
                persist=False,
            )
            self.assertTrue(changed)
            self.assertIn("[MANUAL FILL]", section.content)
            self.assertIn("[MANUAL FILL or N/A]", section.content)

    async def test_explicit_fill_with_user_value(self) -> None:
        content = "Role: [MANUAL FILL: Title]"
        draft = _draft(content)
        research = ProposalResearchCache(
            rfpId="rfp-mfill",
            updatedAt="2026-07-23T00:00:00+00:00",
            evidenceCorpus=[],
        )

        with _common_improve_patches(
            draft=draft,
            research=research,
            section_id="section-budget",
            chat_json=AssertionError("LLM must not run for user fill"),
        ):
            with patch(
                "app.services.proposal_section_editor.enforce_narrative_voice",
                side_effect=AssertionError(
                    "MANUAL FILL fill must not run brand-voice enforcement"
                ),
            ):
                section, _updated, _r, provider, msg, changed, _ = await improve_proposal_section(
                    "rfp-mfill",
                    "section-budget",
                    "fill [MANUAL FILL: Title] with Director of Marketing",
                    persist=False,
                )
            self.assertTrue(changed)
            self.assertEqual(provider, "manual-fill")
            # Exact substitution — no rewording of surrounding sentence.
            self.assertEqual(section.content, "Role: Director of Marketing")
            self.assertNotIn("[MANUAL FILL", section.content)
            self.assertIn("user", msg.lower())

    async def test_explicit_fill_with_no_source_leaves_tag(self) -> None:
        content = "Sign: [MANUAL FILL: wet/digital signature]"
        draft = _draft(content)
        research = ProposalResearchCache(
            rfpId="rfp-mfill",
            updatedAt="2026-07-23T00:00:00+00:00",
            evidenceCorpus=[],
        )

        with _common_improve_patches(
            draft=draft,
            research=research,
            section_id="section-budget",
            chat_json=AssertionError("LLM must not invent fill"),
        ):
            section, _updated, _r, _p, msg, changed, _ = await improve_proposal_section(
                "rfp-mfill",
                "section-budget",
                "fill all MANUAL FILL tags from KB",
                persist=False,
            )
            self.assertFalse(changed)
            self.assertIn("[MANUAL FILL: wet/digital signature]", section.content)
            self.assertIn("could not resolve", msg.lower())

    async def test_rewrite_dropping_tags_retries_then_errors(self) -> None:
        content = "Intro text.\n\n[MANUAL FILL: attach COI]\n\nClosing."
        calls = {"n": 0}

        async def bad_then_still_bad(messages, **kwargs):
            calls["n"] += 1
            # Always drop the placeholder — should fail twice and raise.
            return {"content": "Intro text tightened.\n\nClosing."}, "mock"

        draft = _draft(content)
        draft.sections[0] = draft.sections[0].model_copy(
            update={"source": "template", "id": "section-1-who"}
        )
        research = ProposalResearchCache(
            rfpId="rfp-mfill",
            updatedAt="2026-07-23T00:00:00+00:00",
            evidenceCorpus=[],
        )

        with _common_improve_patches(
            draft=draft,
            research=research,
            section_id="section-1-who",
            chat_json=bad_then_still_bad,
        ):
            with self.assertRaises(ProposalError) as ctx:
                await improve_proposal_section(
                    "rfp-mfill",
                    "section-1-who",
                    "tighten this paragraph",
                    persist=False,
                )
            self.assertIn("MANUAL FILL", str(ctx.exception))
            self.assertGreaterEqual(calls["n"], 2)


class ManualFillUnmaskUnitTests(unittest.TestCase):
    def test_unmask_raises_when_dropped(self) -> None:
        with self.assertRaises(ProposalError):
            _unmask_manual_fill_checked(
                "no placeholders left",
                ["[MANUAL FILL]"],
                attempt=1,
            )


if __name__ == "__main__":
    unittest.main()
