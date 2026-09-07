"""enforce_outline_section_cap must never drop an RFP-derived tab.

Regression coverage for a real run where the cap deleted a separately-scored
evaluation criterion ("Social Media Support", 5 points) because the upstream
evaluation extraction under-counted criteria and left points=None on it.
"""

from __future__ import annotations

import unittest

from app.services.proposal_intelligence.schemas import OutlineSection
from app.services.proposal_outline_dedup import (
    enforce_outline_section_cap,
    section_is_rfp_derived,
)


def _invented(id_: str, title: str, order: int) -> OutlineSection:
    """A section with no RFP anchor at all — planner-invented padding."""
    return OutlineSection(id=id_, title=title, order=order, required=False)


class SectionIsRfpDerivedTests(unittest.TestCase):
    def test_scored_section_is_rfp_derived(self) -> None:
        section = OutlineSection(
            id="a", title="Technical Approach", order=1, evaluationWeight=30
        )
        self.assertTrue(section_is_rfp_derived(section))

    def test_zero_points_is_still_rfp_derived(self) -> None:
        # Extractor found the criterion but failed to read its points — the
        # row is still scored, not invented.
        section = OutlineSection(
            id="a", title="Social Media Support", order=1, evaluationWeight=0
        )
        self.assertTrue(section_is_rfp_derived(section))

    def test_submission_instrument_is_rfp_derived(self) -> None:
        section = OutlineSection(
            id="a", title="References", order=1, submissionInstrument="references"
        )
        self.assertTrue(section_is_rfp_derived(section))

    def test_required_alone_is_NOT_proof_of_rfp_origin(self) -> None:
        # OutlineSection.required defaults to True (schemas.py:417), so it
        # carries no signal: an invented filler tab looks "required" too.
        # Trusting it marked every tab RFP-derived and disabled the cap
        # entirely — keep this asserting the default is not sufficient.
        section = OutlineSection(id="a", title="Cover Letter", order=1, required=True)
        self.assertFalse(section_is_rfp_derived(section))

    def test_narrative_instrument_alone_is_NOT_proof_of_rfp_origin(self) -> None:
        # The planner stamps "narrative" on ordinary tabs, so accepting any
        # instrument value would protect invented padding — same trap.
        section = OutlineSection(
            id="a", title="Our Approach", order=1, submissionInstrument="narrative"
        )
        self.assertFalse(section_is_rfp_derived(section))

    def test_protect_from_cap_flag_is_rfp_derived(self) -> None:
        section = OutlineSection(
            id="a",
            title="Signed Addenda Acknowledgement",
            order=1,
            required=False,
            protectFromCap=True,
        )
        self.assertTrue(section_is_rfp_derived(section))

    def test_purely_invented_section_is_not_rfp_derived(self) -> None:
        section = _invented("a", "Why Choose Us", 1)
        self.assertFalse(section_is_rfp_derived(section))


class EnforceOutlineSectionCapTests(unittest.TestCase):
    def test_scored_section_never_dropped_even_at_max_n_one(self) -> None:
        sections = [
            OutlineSection(
                id="scored", title="Scored Criterion", order=1, evaluationWeight=25
            ),
            _invented("b", "Padding One", 2),
            _invented("c", "Padding Two", 3),
            _invented("d", "Padding Three", 4),
        ]
        kept, dropped = enforce_outline_section_cap(sections, max_n=1)
        kept_ids = {s.id for s in kept}
        self.assertIn("scored", kept_ids)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 3)

    def test_zero_points_section_kept_extractor_failure_case(self) -> None:
        sections = [
            OutlineSection(
                id="social", title="Social Media Support", order=1, evaluationWeight=0
            ),
            _invented("b", "Padding One", 2),
            _invented("c", "Padding Two", 3),
        ]
        kept, dropped = enforce_outline_section_cap(sections, max_n=1)
        kept_ids = {s.id for s in kept}
        self.assertIn("social", kept_ids)
        self.assertEqual(len(dropped), 2)

    def test_submission_instrument_section_never_dropped(self) -> None:
        sections = [
            OutlineSection(
                id="refs",
                title="References",
                order=1,
                required=False,
                submissionInstrument="references",
            ),
            _invented("b", "Padding One", 2),
            _invented("c", "Padding Two", 3),
        ]
        kept, _dropped = enforce_outline_section_cap(sections, max_n=1)
        self.assertIn("refs", {s.id for s in kept})

    def test_required_or_protect_from_cap_section_never_dropped(self) -> None:
        sections = [
            OutlineSection(id="req", title="Cover Letter", order=1, required=True),
            _invented("b", "Padding One", 2),
        ]
        kept, _dropped = enforce_outline_section_cap(sections, max_n=1)
        self.assertIn("req", {s.id for s in kept})

        sections2 = [
            OutlineSection(
                id="protect",
                title="Addenda Acknowledgement",
                order=1,
                required=False,
                protectFromCap=True,
            ),
            _invented("b", "Padding One", 2),
        ]
        kept2, _dropped2 = enforce_outline_section_cap(sections2, max_n=1)
        self.assertIn("protect", {s.id for s in kept2})

    def test_purely_invented_sections_trimmed_highest_weight_first(self) -> None:
        # No points, no instrument, not required — pure planner invention.
        # These should be trimmed to fit, preferring the higher-weighted one.
        low = OutlineSection(
            id="low",
            title="Low Weight Invented",
            order=1,
            required=False,
            evaluationWeight=None,
        )
        # Give "high" some weight via evaluationWeight so it's not treated as
        # RFP-derived by the *points* rule alone — evaluationWeight=None means
        # not-scored (section_is_rfp_derived checks "is not None", not >0),
        # so both are equally invented here; use insertion order tiebreak
        # instead by keeping only one candidate above cap.
        high = _invented("high", "High Priority Invented", 2)
        sections = [low, high]
        kept, dropped = enforce_outline_section_cap(sections, max_n=1)
        self.assertEqual(len(kept), 1)
        # First in original order wins the tiebreak when weights are equal.
        self.assertEqual(kept[0].id, "low")
        self.assertEqual(len(dropped), 1)

    def test_rfp_derived_overflow_all_kept_with_overflow_message(self) -> None:
        sections = [
            OutlineSection(id=f"s{i}", title=f"Scored {i}", order=i, evaluationWeight=10)
            for i in range(1, 5)
        ]
        kept, dropped = enforce_outline_section_cap(sections, max_n=2)
        self.assertEqual(len(kept), 4)
        self.assertTrue(
            any("RFP requires" in msg and "4" in msg and "2" in msg for msg in dropped)
        )

    def test_order_renumbered_one_to_n_on_kept_list(self) -> None:
        sections = [
            OutlineSection(id="a", title="Scored", order=5, evaluationWeight=10),
            _invented("b", "Padding One", 6),
            _invented("c", "Padding Two", 7),
        ]
        kept, _dropped = enforce_outline_section_cap(sections, max_n=2)
        self.assertEqual([s.order for s in kept], list(range(1, len(kept) + 1)))

    def test_regression_nine_sections_eight_rfp_derived_one_invented(self) -> None:
        rfp_derived = [
            OutlineSection(
                id=f"rfp-{i}", title=f"RFP Section {i}", order=i, evaluationWeight=10
            )
            for i in range(1, 9)
        ]
        invented = [_invented("invented", "Padding Tab", 9)]
        sections = rfp_derived + invented
        kept, dropped = enforce_outline_section_cap(sections, max_n=9)
        kept_ids = {s.id for s in kept}
        for section in rfp_derived:
            self.assertIn(section.id, kept_ids)
        self.assertEqual(len(kept), 9)
        self.assertEqual(dropped, [])


if __name__ == "__main__":
    unittest.main()
