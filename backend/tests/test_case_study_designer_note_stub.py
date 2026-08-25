"""Section 3 hands design the approved case study — it does not rewrite it.

Root cause this replaces: every selected study was redrafted as prose (one LLM
call each producing Challenge / Solution / Client Voice), then scrubbed by
``scrub_case_study_overbuild`` and ``scrub_ungrounded_case_study_percent_metrics``
for the metrics and quotes drafting had invented on the way. The approved case
study PDF already tells that story, and design places it as a card — so the
manuscript copy was invented work competing with the real asset.

Now Section 3 spends its budget on SELECTION (the fit matcher is unchanged) and
emits a designer note, the same Option B shape team bios use. The tests below
pin the two things that break silently: the note must not read as an unfinished
section to any downstream repair pass, and the silent floor must stay at four
studies.
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_case_study_stub import (
    case_study_asset_filename,
    format_case_study_stub_content,
    is_case_study_stub_section,
    looks_like_case_study_stub_body,
)
from app.services.proposal_fulfill_guard import fulfill_scan_preserves_section
from app.services.proposal_rfp_compulsory_content import (
    DEFAULT_CASE_STUDY_PREFERENCE,
    count_usable_case_study_cards,
)
from app.services.proposal_section_health import is_dead_section
from app.services.proposal_sections_graph import _case_study_relevance_map

DRAFTED_PROSE = (
    "**Challenge**\n\nThe city needed a modern website.\n\n"
    "**Solution / Our Approach**\n\nWe rebuilt the sitemap and rewrote the "
    "top thirty pages around resident tasks.\n"
)


def _stub(
    display_name: str = "Hampton Lumber",
    title: str = "03_CS_Hampton_Lumber_Website",
    relevance: str = "Website redesign, navigation and mobile-first UX",
) -> str:
    return format_case_study_stub_content(
        display_name=display_name,
        asset_filename=case_study_asset_filename(title),
        relevance=relevance,
    )


def _section(content: str, sid: str = "section-3-work-01-hampton") -> ProposalSection:
    return ProposalSection(
        id=sid,
        title="3.1 - Hampton Lumber",
        content=content,
        status="generated",
    )


class StubShapeTests(unittest.TestCase):
    def test_stub_names_the_asset_and_writes_no_narrative(self) -> None:
        body = _stub()
        self.assertIn("DESIGNER NOTE", body)
        self.assertIn("03_CS_Hampton_Lumber_Website.pdf", body)
        for invented in ("Challenge", "Client Voice", "Solution / Our Approach"):
            self.assertNotIn(invented, body)

    def test_relevance_is_carried_not_composed(self) -> None:
        body = _stub(relevance="Municipal communications and public engagement")
        self.assertIn("Municipal communications and public engagement", body)
        # Nothing to say means nothing said — never an invented rationale.
        self.assertNotIn("Why this work is relevant", _stub(relevance=""))

    def test_missing_asset_raises_a_manual_fill_not_a_guess(self) -> None:
        body = format_case_study_stub_content(
            display_name="City of Bend", kb_available=False
        )
        self.assertIn("MANUAL FILL", body)
        self.assertIn("City of Bend", body)

    def test_asset_filename_keeps_an_existing_extension(self) -> None:
        self.assertEqual(case_study_asset_filename("03_CS_Bend.pdf"), "03_CS_Bend.pdf")
        self.assertEqual(case_study_asset_filename("03_CS_Bend"), "03_CS_Bend.pdf")
        self.assertEqual(case_study_asset_filename(""), "")


class StubRecognitionTests(unittest.TestCase):
    def test_stub_bodies_are_recognised(self) -> None:
        self.assertTrue(looks_like_case_study_stub_body(_stub()))
        self.assertTrue(is_case_study_stub_section("section-3-work-01-hampton", _stub()))

    def test_drafted_prose_is_not_a_stub(self) -> None:
        self.assertFalse(looks_like_case_study_stub_body(DRAFTED_PROSE))
        self.assertFalse(
            is_case_study_stub_section("section-3-work-01-hampton", DRAFTED_PROSE)
        )

    def test_a_stub_outside_section_3_is_not_protected(self) -> None:
        self.assertFalse(is_case_study_stub_section("section-2-bio-01", _stub()))


class DownstreamPassTests(unittest.TestCase):
    """The stub is a finished section — no pass may treat it as unwritten."""

    def test_stub_is_not_a_dead_section(self) -> None:
        self.assertFalse(is_dead_section(_stub()))

    def test_stub_counts_as_a_usable_case_study_card(self) -> None:
        draft = ProposalDraft(
            rfp_id="rfp-1",
            updatedAt="2026-08-21T00:00:00Z",
            sections=[
                _section(_stub(), sid="section-3-work-01-hampton"),
                _section(_stub("City of Bend"), sid="section-3-work-02-bend"),
            ],
        )
        self.assertEqual(count_usable_case_study_cards(draft), 2)

    def test_hollow_fill_skips_the_stub(self) -> None:
        from app.services.proposal_hollow_kb_fill import _skip_section

        self.assertTrue(_skip_section(_section(_stub())))

    def test_scan_may_not_rewrite_a_short_stub(self) -> None:
        # The 350-char floor exists to catch half-drafted narratives; a stub is
        # shorter than that and still complete.
        body = _stub(relevance="")
        self.assertLess(len(body), 350)
        self.assertTrue(fulfill_scan_preserves_section(_section(body)))

    def test_adversarial_repair_protects_the_stub(self) -> None:
        import inspect

        from app.services import proposal_adversarial_repair as mod

        src = inspect.getsource(mod)
        self.assertIn("is_case_study_stub_section", src)


class RelevanceMapTests(unittest.TestCase):
    def test_map_reads_capability_from_the_fit_report(self) -> None:
        from app.services.proposal_case_study_fit import (
            CaseStudyCandidate,
            CaseStudyFitReport,
            CaseStudyFitResult,
        )

        report = CaseStudyFitReport(
            results=[
                CaseStudyFitResult(
                    capability="Municipal website redesign",
                    candidates=[CaseStudyCandidate(source="03_CS_Bend", excerpt="")],
                    gap=False,
                )
            ]
        )
        self.assertEqual(
            _case_study_relevance_map(report),
            {"03_cs_bend": "Municipal website redesign"},
        )

    def test_missing_report_is_not_an_error(self) -> None:
        self.assertEqual(_case_study_relevance_map(None), {})


class MinimumSelectionTests(unittest.TestCase):
    def test_silent_floor_is_four_studies(self) -> None:
        self.assertEqual(DEFAULT_CASE_STUDY_PREFERENCE, 4)

    def test_section_3_no_longer_drafts_case_study_prose(self) -> None:
        import inspect

        from app.services import proposal_sections_graph as mod

        for fn in (mod._build_section_3, mod._build_case_studies):
            src = inspect.getsource(fn)
            self.assertIn("format_case_study_stub_content", src, fn.__name__)
            self.assertNotIn("Client Voice", src, fn.__name__)
            self.assertNotIn("run_case_study_builder_agent", src, fn.__name__)


if __name__ == "__main__":
    unittest.main()
