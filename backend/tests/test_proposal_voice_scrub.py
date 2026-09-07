"""Tests for deterministic zö voice / generic-AI prose scrub."""

from __future__ import annotations

import unittest

from app.services.proposal_voice_enforcement import (
    enforce_narrative_voice,
    scrub_generic_ai_prose,
    scrub_rev6_voice_patterns,
    apply_chat_rev6_voice_to_draft,
)
from app.models.proposal import ProposalDraft, ProposalSection


class GenericAiScrubTests(unittest.TestCase):
    def test_strips_hype_words(self) -> None:
        raw = (
            "We leverage a robust, seamless approach to unlock impactful outcomes "
            "for this passionate partnership."
        )
        cleaned = scrub_generic_ai_prose(raw)
        for banned in (
            "leverage",
            "robust",
            "seamless",
            "unlock",
            "impactful",
            "passionate",
        ):
            self.assertNotIn(banned, cleaned.casefold())

    def test_strips_generic_openers(self) -> None:
        raw = "At the end of the day, we built the campaign in six weeks."
        cleaned = scrub_generic_ai_prose(raw)
        self.assertNotIn("at the end of the day", cleaned.casefold())
        self.assertIn("six weeks", cleaned)

    def test_enforce_narrative_voice_applies_scrub(self) -> None:
        raw = "The Vendor delivers a seamless solution for Denver Health."
        cleaned = enforce_narrative_voice(
            raw,
            section_id="section-1-who-we-are",
            title="1.1 — Who We Are",
            register="narrative",
        )
        self.assertIn("We deliver", cleaned)
        self.assertNotIn("seamless", cleaned.casefold())
        self.assertNotIn("The Vendor", cleaned)


class Rev6VoicePatternTests(unittest.TestCase):
    def test_rather_than_keeps_affirmative(self) -> None:
        raw = (
            "we'd rather spend four weeks confirming what's working in your "
            "current site and social presence than guess and rebuild something "
            "that didn't need it."
        )
        cleaned, logs = scrub_rev6_voice_patterns(raw)
        self.assertNotIn("rather", cleaned.casefold())
        self.assertNotIn("than guess", cleaned.casefold())
        self.assertIn("spend four weeks", cleaned.casefold())
        self.assertTrue(any("rather" in line.casefold() for line in logs))

    def test_gilroy_six_negation_contrasts(self) -> None:
        cases = [
            (
                "with fixes shipped inside the existing site rather than a rebuild",
                "existing site",
                "rather than",
            ),
            (
                "finalized ahead of festival week, not improvised on-site",
                "festival week",
                "not improvised",
            ),
            (
                "an itemized fee structure, not a flat retainer that hides where the hours go",
                "itemized fee structure",
                "not a flat",
            ),
            (
                "sized to the work in front of us rather than padded for margin",
                "work in front of us",
                "rather than",
            ),
            (
                "small, dated moves that compound across a year, not a single campaign burst in July",
                "compound across a year",
                "not a single",
            ),
            (
                "We'd rather spend four weeks confirming what's working in your current "
                "site and social presence than guess and rebuild something that didn't need it",
                "spend four weeks",
                "rather",
            ),
        ]
        for raw, keep, banned in cases:
            cleaned, _ = scrub_rev6_voice_patterns(raw)
            self.assertIn(keep.casefold(), cleaned.casefold(), raw)
            self.assertNotIn(banned.casefold(), cleaned.casefold(), cleaned)

    def test_table_cells_scrubbed(self) -> None:
        raw = (
            "| Area | Commitment |\n"
            "| --- | --- |\n"
            "| Website | fixes shipped inside the existing site rather than a rebuild |\n"
            "| Fees | an itemized fee structure, not a flat retainer that hides hours |\n"
        )
        cleaned, logs = scrub_rev6_voice_patterns(raw)
        self.assertNotIn("rather than", cleaned.casefold())
        self.assertNotIn("not a flat", cleaned.casefold())
        self.assertIn("existing site", cleaned.casefold())
        self.assertIn("itemized fee structure", cleaned.casefold())
        self.assertTrue(any("Rev6:" in line for line in logs))

    def test_dangling_before_cut(self) -> None:
        raw = (
            "We've built sponsorship structures and content calendars around brands "
            "that carry decades of community trust before."
        )
        cleaned, logs = scrub_rev6_voice_patterns(raw)
        self.assertNotRegex(cleaned, r"(?i)\bbefore\s*\.?$")
        self.assertIn("community trust", cleaned.casefold())
        self.assertTrue(any("dangling" in line for line in logs))

    def test_x_not_y_trailing_cut(self) -> None:
        raw = (
            "Small, dated moves that compound across a year, not a single "
            "campaign burst in July."
        )
        cleaned, _ = scrub_rev6_voice_patterns(raw)
        self.assertNotIn("not a single", cleaned.casefold())
        self.assertIn("compound across a year", cleaned.casefold())

    def test_instead_of_cut(self) -> None:
        raw = (
            "Build on what a client already has instead of starting from a blank page."
        )
        cleaned, _ = scrub_rev6_voice_patterns(raw)
        self.assertNotIn("instead of", cleaned.casefold())
        self.assertIn("build on what a client already has", cleaned.casefold())
        self.assertNotIn("blank page", cleaned.casefold())

    def test_significance_close_cut(self) -> None:
        raw = (
            "We fix the broken form on the donate page. "
            "That's the kind of specific fix that runs through every pillar below."
        )
        cleaned, _ = scrub_rev6_voice_patterns(raw)
        self.assertNotIn("that's the kind of", cleaned.casefold())
        self.assertIn("donate page", cleaned.casefold())

    def test_hedge_worth_naming_cut(self) -> None:
        raw = (
            "That's a real tradeoff worth naming: The regional partnership "
            "outreach moves slower than social."
        )
        cleaned, _ = scrub_rev6_voice_patterns(raw)
        self.assertNotIn("worth naming", cleaned.casefold())
        self.assertIn("regional partnership", cleaned.casefold())

    def test_tagline_exempt(self) -> None:
        raw = "We are more than your agency. We are your strongest advocate."
        cleaned, _ = scrub_rev6_voice_patterns(raw)
        self.assertIn("more than your agency", cleaned.casefold())

    def test_chat_persist_voice_enforces_rev6_on_draft(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-chat-voice",
            sections=[
                ProposalSection(
                    id="approach",
                    title="6. Strategic Growth Plan",
                    content=(
                        "We'd rather spend four weeks confirming what's working "
                        "than guess and rebuild. Build on what exists instead of "
                        "starting from a blank page."
                    ),
                    wordTarget=400,
                    status="generated",
                )
            ],
            updatedAt="2026-09-07T00:00:00Z",
        )
        out, logs = apply_chat_rev6_voice_to_draft(draft)
        body = out.sections[0].content or ""
        self.assertNotIn("rather", body.casefold())
        self.assertNotIn("instead of", body.casefold())
        self.assertTrue(any("Rev6 chat voice" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
