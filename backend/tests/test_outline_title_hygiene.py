"""Unit tests for outline title hygiene — humanize_outline_title and its call sites.

Pure unit tests. No network / LLM calls (safe_chat_json is mocked where needed).
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.proposal_closing_ledger import ClosingRequirement
from app.services.proposal_intelligence.agents.checklister import run_proposal_checklister
from app.services.proposal_intelligence.schemas import (
    OutlineSection,
    ProposalExecutionPlan,
)
from app.services.proposal_outline_dedup import humanize_outline_title

_LONG_SENTENCE = (
    "Provide monthly performance dashboards/reports (web traffic, social "
    "engagement, paid media KPIs) and quarterly strategic reviews; strong "
    "proposals include sample reports/dashboards from past work"
)


class TestHumanizeOutlineTitle(unittest.TestCase):
    def test_long_sentence_cut_to_heading(self):
        result = humanize_outline_title(_LONG_SENTENCE)
        self.assertLessEqual(len(result), 72)
        self.assertTrue(result)
        # Cut at a real boundary, not mid-word — the result must be a prefix
        # of the original up to whitespace (no partial trailing word).
        self.assertTrue(
            _LONG_SENTENCE.startswith(result.rstrip(" .,;:—–-"))
            or _LONG_SENTENCE.startswith(result)
        )
        self.assertFalse(result.endswith("..."))
        self.assertNotIn("…", result)
        # No mid-word cut: last char of result must be followed (in the
        # original) by a space, boundary punctuation, or be the end of the RFP text.
        cut_len = len(result)
        if cut_len < len(_LONG_SENTENCE):
            self.assertIn(_LONG_SENTENCE[cut_len], " ;(:")

    def test_machine_key_addenda(self):
        self.assertEqual(
            humanize_outline_title("addenda_acknowledgment"), "Addenda Acknowledgment"
        )

    def test_machine_key_all_caps_token_preserved(self):
        result = humanize_outline_title("w9_form")
        self.assertEqual(result, "W9 Form")

    def test_already_good_heading_passes_through_unchanged(self):
        self.assertEqual(humanize_outline_title("Cover Letter"), "Cover Letter")
        self.assertEqual(
            humanize_outline_title("Vendor Questionnaire"), "Vendor Questionnaire"
        )

    def test_empty_and_whitespace(self):
        self.assertEqual(humanize_outline_title(""), "")
        self.assertEqual(humanize_outline_title("   "), "")
        self.assertEqual(humanize_outline_title(None), "")


class TestChecklisterHumanizesTitles(unittest.IsolatedAsyncioTestCase):
    async def test_long_sentence_title_produces_short_heading_and_full_requirement(self):
        plan = ProposalExecutionPlan()
        plan.writing.proposal_outline.sections = [
            OutlineSection(id="s1", title="Cover Letter", order=1)
        ]

        llm_payload = {
            "missing_sections": [
                {
                    "title": _LONG_SENTENCE,
                    "required": True,
                    "conditionalReason": "",
                    "evaluationWeight": None,
                    "protectFromCap": True,
                    "submissionInstrument": "narrative",
                }
            ],
            "reasoning": "test",
        }

        with patch(
            "app.services.proposal_intelligence.agents.checklister.safe_chat_json",
            new=AsyncMock(return_value=(llm_payload, "test-provider")),
        ):
            updated = await run_proposal_checklister(
                plan=plan, rfp_context="irrelevant rfp text"
            )

        new_sections = updated.writing.proposal_outline.sections
        added = [s for s in new_sections if s.id != "s1"]
        self.assertEqual(len(added), 1)
        section = added[0]
        self.assertLessEqual(len(section.title), 72)
        self.assertNotEqual(section.title, _LONG_SENTENCE)
        self.assertTrue(
            section.conditional_reason.startswith("Full RFP requirement: ")
        )
        self.assertIn(_LONG_SENTENCE, section.conditional_reason)


class TestClosingRequirementTitleHumanized(unittest.TestCase):
    def test_slug_title_is_humanized(self):
        req = ClosingRequirement(id="addenda_acknowledgment", title="addenda_acknowledgment")
        self.assertEqual(req.title, "Addenda Acknowledgment")


if __name__ == "__main__":
    unittest.main()


def test_word_cut_never_leaves_a_dangling_linking_word():
    # A word-boundary truncation used to end "...Cost Proposal Attachment that
    # must be" — grammatically broken, and it shipped as a section heading.
    out = humanize_outline_title(
        "Pricing Table or as a separate Cost Proposal Attachment that must be "
        "downloaded, completed,"
    )
    assert out == "Pricing Table or as a separate Cost Proposal Attachment"
    for tail in ("that", "must", "be", "and", "the", "to", "which", "shall"):
        assert not out.casefold().endswith(" " + tail), out


def test_every_result_is_a_clean_heading():
    for raw in (
        "Provide monthly performance dashboards/reports (web traffic, social "
        "engagement, paid media KPIs) and quarterly strategic reviews; strong "
        "proposals include sample reports/dashboards from past work",
        "Qualifications and Experience of the Respondent(s): describe last 3 "
        "years of relevant projects, emphasize key staff involvement",
        "Vendor Information Attachment (incl. Designation of Confidential and "
        "Proprietary Information)",
    ):
        out = humanize_outline_title(raw)
        assert 0 < len(out) <= 72, (len(out), out)
        assert "…" not in out and "..." not in out, out
        assert not out.endswith((",", ";", ":", "-", "—")), out
