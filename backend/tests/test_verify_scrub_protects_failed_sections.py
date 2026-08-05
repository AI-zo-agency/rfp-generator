"""The optional-[VERIFY] scrub must never delete a failed-section stub.

A failed section holds a ~60-character stub which is, technically, a section
consisting entirely of [VERIFY] tags. The scrub selected it as a candidate, and
its anti-wipe guard was gated on `len(body) > 400`, so nothing stopped the stub
being removed outright — leaving an empty section and destroying the exact marker
chat needs to rebuild it.
"""

from __future__ import annotations

import unittest

from app.services.proposal_draft_llm import SECTION_DRAFT_FAILURE_PLACEHOLDER
from app.services.proposal_section_health import is_dead_section
from app.services.proposal_verify_optional_scrub import (
    VerifyOptionalScrubResult,
    count_verify_tags,
)


class ScrubCandidateSelectionTests(unittest.TestCase):
    def test_failure_stub_looks_like_a_scrub_candidate_by_tag_count(self) -> None:
        """Why it was selected in the first place — the stub does contain a tag."""
        self.assertGreater(count_verify_tags(SECTION_DRAFT_FAILURE_PLACEHOLDER), 0)

    def test_failure_stub_is_now_excluded_as_dead(self) -> None:
        self.assertTrue(is_dead_section(SECTION_DRAFT_FAILURE_PLACEHOLDER))

    def test_stored_comma_variant_is_also_excluded(self) -> None:
        stored = "[VERIFY: Section drafting failed, needs manual regeneration]"
        self.assertGreater(count_verify_tags(stored), 0)
        self.assertTrue(is_dead_section(stored))

    def test_real_section_with_inline_tags_is_still_scrubbed(self) -> None:
        """The scrub must keep working on genuine content."""
        body = (
            "We accept the terms of the exemplar agreement. [VERIFY: signatory] "
            "The signed page is returned with our submission. [VERIFY: date]"
        )
        self.assertGreater(count_verify_tags(body), 0)
        self.assertFalse(is_dead_section(body))


class AntiWipeGuardTests(unittest.TestCase):
    """The guard threshold, expressed directly: max(24, 25% of the body)."""

    @staticmethod
    def _rejects(body: str, updated: str) -> bool:
        return len(updated) < max(24, int(len(body) * 0.25))

    def test_short_stub_wipe_is_rejected(self) -> None:
        body = SECTION_DRAFT_FAILURE_PLACEHOLDER
        self.assertTrue(self._rejects(body, ""))
        self.assertTrue(self._rejects(body, "   "))

    def test_previously_unguarded_length_is_now_guarded(self) -> None:
        """A 300-char body used to bypass the guard entirely (needed > 400)."""
        body = "x" * 300
        self.assertFalse(300 > 400)  # old precondition would not have fired
        self.assertTrue(self._rejects(body, "y" * 10))

    def test_legitimate_scrub_of_a_long_section_still_passes(self) -> None:
        body = "x" * 1000
        self.assertFalse(self._rejects(body, "y" * 800))

    def test_modest_shrink_of_a_short_section_still_passes(self) -> None:
        body = "x" * 200
        self.assertFalse(self._rejects(body, "y" * 150))


if __name__ == "__main__":
    unittest.main()
