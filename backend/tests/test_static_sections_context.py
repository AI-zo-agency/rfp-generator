"""Phase 3 must know Sections 1-3 are already written and not repeat them.

Observed: a tab titled "A brief description of the firm, including the year the
firm was established, type of firm (partnership, corporation, etc.)" restated
the founding date and entity type already covered by 1.1 Who We Are and
1.3 Business Information.

Two causes, both here:
  * _zo_sections_context truncated with sections[:3], but "Sections 1-3" is 8
    subsections, so certifications/insurance/bios/case studies were invisible.
  * the prompt labelled the block a "reference ... do not duplicate verbatim",
    which permits paraphrasing the very facts it was meant to protect.
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalSection
from app.services.proposal_drafting_graph import _zo_sections_context


def _section(sid: str, title: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=sid, title=title, content=content, required=True, custom=False
    )


STATIC = [
    _section("1.1", "Who We Are", "zö agency was established in 2013."),
    _section("1.2", "Organizational Structure", "Departments and reporting."),
    _section("1.3", "Business Information", "Limited liability company, FEIN on file."),
    _section("1.4", "Certifications", "WBENC and WOSB certified."),
    _section("1.5", "Insurance Information", "General liability limits held."),
    _section("2.1", "Sonja Anderson", "Principal, 20 years experience."),
    _section("3.1", "City of Umatilla", "Digital campaign case study."),
    _section("3.2", "Oregon Employment", "Geofencing campaign case study."),
]


class ZoSectionsContextTests(unittest.TestCase):
    def test_every_static_subsection_is_included(self) -> None:
        ctx = _zo_sections_context(STATIC)
        for section in STATIC:
            self.assertIn(section.title, ctx, section.title)

    def test_content_past_the_first_three_is_not_lost(self) -> None:
        """The exact truncation bug: 1.4 onwards were invisible to the drafter."""
        ctx = _zo_sections_context(STATIC)
        self.assertIn("WBENC and WOSB certified", ctx)
        self.assertIn("General liability limits held", ctx)
        self.assertIn("Sonja Anderson", ctx)
        self.assertIn("Oregon Employment", ctx)

    def test_empty_sections_are_skipped(self) -> None:
        sections = [*STATIC, _section("4.1", "Blank", "   ")]
        self.assertNotIn("### Blank", _zo_sections_context(sections))

    def test_no_sections_yields_empty(self) -> None:
        self.assertEqual(_zo_sections_context([]), "")


class PromptFramingTests(unittest.TestCase):
    """The instruction must forbid restating, not merely copying."""

    def _prompt_block(self) -> str:
        import inspect
        from app.services import proposal_drafting_graph as graph

        return inspect.getsource(graph)

    def test_block_is_framed_as_already_written(self) -> None:
        src = self._prompt_block()
        self.assertIn("ALREADY WRITTEN", src)

    def test_paraphrase_loophole_is_closed(self) -> None:
        src = self._prompt_block()
        self.assertNotIn("do not duplicate verbatim", src)
        self.assertIn("paraphrase", src)

    def test_protected_facts_are_named(self) -> None:
        src = self._prompt_block()
        for fact in ("founding", "entity type", "certifications", "insurance"):
            self.assertIn(fact, src, fact)


if __name__ == "__main__":
    unittest.main()
