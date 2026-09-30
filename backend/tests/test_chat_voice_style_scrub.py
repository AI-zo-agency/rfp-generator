"""Voice/align asks must not full-rewrite Budget or scrub untouched bios."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.services.proposal_section_editor import (
    _try_voice_style_only_pass,
    _user_asks_voice_or_style_only,
)


class VoiceStyleAskDetectionTests(unittest.TestCase):
    def test_short_align_prompt(self) -> None:
        self.assertTrue(_user_asks_voice_or_style_only("Align with zo agency voice"))

    def test_expanded_budget_align_prompt(self) -> None:
        ask = (
            "Align the Budget & Cost Breakdown section with zö agency's "
            "established voice, tone, and writing standards"
        )
        self.assertTrue(_user_asks_voice_or_style_only(ask))

    def test_fee_mutation_not_voice_only(self) -> None:
        self.assertFalse(
            _user_asks_voice_or_style_only(
                "Add hourly rates from the pricing guide and align voice"
            )
        )

    def test_restore_caveat_with_rev6_context_is_not_voice_only(self) -> None:
        ask = (
            'Section 22 — lost a legitimate caveat, not just hedging. The old line '
            "explained why regional partnerships move slower (tourism boards run "
            "their own calendars) — that's useful commercial qualification, not fluff, "
            "and rev6 says keep that kind of thing. Recommend restoring the \"why\": "
            '"Regional partnership outreach moves slower than social or sponsorship, '
            'because tourism boards and downtown business groups run on their own '
            "meeting calendars. We'll start those conversations early and treat the "
            'first agreements as a foundation to build from."'
        )
        self.assertFalse(_user_asks_voice_or_style_only(ask))


class VoiceStyleOnlyPassTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_voice_ask_restores_missing_verbatim(self) -> None:
        long_body = (
            "## Proposed Investment\n\n**Total proposed investment: $122,000.00**\n\n"
            "## Terms\n\n### Investment Framing\n\nParaphrase only.\n\n"
            "## Fee Detail by Phase\n\n"
            "| Phase | Deliverable | Amount |\n"
            "| --- | --- | ---: |\n"
            "| Discovery | Plan | $10,000 |\n"
            "| Creative | Assets | $20,000 |\n"
            "| Digital | Site | $31,900 |\n"
            "| Media | Buy | $60,100 |\n\n"
            + ("Evaluator-facing phase narrative. " * 40)
        )
        section = ProposalSection(
            id="rfp-structure-budget-cost-breakdown",
            title="Budget & Cost Breakdown",
            content=long_body,
            status="generated",
        )
        draft = ProposalDraft(rfpId="r1", updatedAt="t", sections=[section])
        research = ProposalResearchCache(rfpId="r1", updatedAt="t")

        result = await _try_voice_style_only_pass(
            rfp_id="r1",
            section=section,
            section_id=section.id,
            draft=draft,
            research=research,
            user_message="Align with zo agency voice",
            persist=False,
            selection_mode=False,
        )

        self.assertIsNotNone(result)
        focus, _, _, _, reply, changed = result
        self.assertTrue(changed)
        self.assertIn("mileage at current irs rate", (focus.content or "").casefold())
        self.assertIn("$122,000", focus.content or "")
        self.assertIn("safe rfp/compliance", reply.casefold())


if __name__ == "__main__":
    unittest.main()
