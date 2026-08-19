"""Tests for deterministic zö voice / generic-AI prose scrub."""

from __future__ import annotations

import unittest

from app.services.proposal_voice_enforcement import (
    enforce_narrative_voice,
    scrub_generic_ai_prose,
)


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


if __name__ == "__main__":
    unittest.main()
