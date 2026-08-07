"""Anti-duplication digests for Phase 3 drafting prompts."""

from __future__ import annotations

import unittest

from app.services.proposal_section_dedup import (
    ANTI_DUPLICATION_RULES,
    format_prior_sections_block,
)


class SectionDedupTests(unittest.TestCase):
    def test_anti_duplication_rules_forbid_restating_owned_facts(self) -> None:
        self.assertIn("ZERO repetition", ANTI_DUPLICATION_RULES)
        self.assertIn("Do NOT re-explain", ANTI_DUPLICATION_RULES)

    def test_prior_block_prefers_later_sections_when_over_cap(self) -> None:
        prior = [
            {
                "id": f"s-{i}",
                "title": f"Section {i}",
                "content": f"Unique content for section {i} about topic {i}.",
            }
            for i in range(30)
        ]
        block = format_prior_sections_block(prior, max_sections=5, max_chars_each=200)
        self.assertIn("ALREADY COVERED", block)
        self.assertIn("Section 29", block)
        self.assertIn("Section 25", block)
        self.assertNotIn("Section 0", block)
        self.assertNotIn("Section 10", block)

    def test_prior_block_excludes_batch_ids(self) -> None:
        prior = [
            {"id": "a", "title": "Approach", "content": "Our approach uses discovery."},
            {"id": "b", "title": "Timeline", "content": "Phase 1 starts in Q1."},
        ]
        block = format_prior_sections_block(prior, exclude_ids={"a"})
        self.assertIn("Timeline", block)
        self.assertNotIn("Approach", block)


if __name__ == "__main__":
    unittest.main()
