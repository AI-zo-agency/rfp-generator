"""Task 4: page-limit parsing must bind at generation, not be inert.

Real symptom #1: an MSU Denver RFP capped quotes at 12 pages (Section IV).
The generated draft ran 22 sections including a full mock insurance
certificate and a full W-9 image. Real symptom #2: a KVCC RFP had a 30-page
cap and shipped insurance content three times.

Root cause (verified against HEAD 04abf01): ``rfp.page_limit`` was populated
ONLY from a manual upload-form field (``app/api/v1/rfps.py:123``). Nothing
parsed "12 pages" out of the RFP text, so ``_remaining_word_budget``
(``proposal_generator.py``) returned ``None``, ``doc_word_budget`` was
``None``, and the entire allocation system downstream was inert.

The overriding constraint on the parser: a FALSE page limit that truncates a
proposal is worse than none. ``parse_page_limit`` must return ``None``
whenever it isn't sure — including when the RFP states two different limits.
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalSection
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.models.rfp import RfpRecord
from app.services.proposal_drafting_graph import (
    MIN_SECTION_WORDS,
    WORDS_PER_PAGE,
    allocate_words_by_points,
    section_weights_from_ledger,
)
from app.services.proposal_generator import _remaining_word_budget
from app.services.rfp_page_limit import parse_page_limit, resolve_page_limit


def _rfp(**overrides) -> RfpRecord:
    fields = dict(
        id="rfp-1",
        title="Test RFP",
        client="Client",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="note",
    )
    fields.update(overrides)
    return RfpRecord(**fields)


def _req(rid: str, points: float | None, **kw) -> LedgerRequirement:
    kw.setdefault("mandatory", True)
    kw.setdefault("satisfiedBy", [])
    return LedgerRequirement(id=rid, text=f"requirement {rid}", points=points, **kw)


class ParsePageLimitMustParseTests(unittest.TestCase):
    def test_quotes_limited_to_12_pages(self) -> None:
        self.assertEqual(parse_page_limit("Quotes are limited to 12 pages"), 12)

    def test_shall_not_exceed_30_pages(self) -> None:
        self.assertEqual(
            parse_page_limit("Proposals shall not exceed 30 pages"), 30
        )

    def test_spelled_out_number_with_parenthetical_digits(self) -> None:
        self.assertEqual(
            parse_page_limit(
                "The proposal is limited to a maximum of twenty (20) pages"
            ),
            20,
        )

    def test_no_more_than_in_length(self) -> None:
        self.assertEqual(
            parse_page_limit("Submissions must be no more than 15 pages in length"),
            15,
        )


class ParsePageLimitMustNotParseTests(unittest.TestCase):
    def test_page_reference_in_a_form(self) -> None:
        self.assertIsNone(parse_page_limit("page 12 of the attached form"))

    def test_page_count_explicitly_excluded_from_limit(self) -> None:
        self.assertIsNone(
            parse_page_limit(
                "12-page portfolio samples are excluded from the page limit"
            )
        )

    def test_page_range_reference(self) -> None:
        self.assertIsNone(
            parse_page_limit("see pages 4-9 for evaluation criteria")
        )

    def test_page_limit_mentioned_with_no_number(self) -> None:
        self.assertIsNone(
            parse_page_limit(
                "Attachments and resumes do not count toward the page limit"
            )
        )

    def test_number_not_about_pages(self) -> None:
        self.assertIsNone(parse_page_limit("Font size 12, single spaced"))

    def test_empty_string(self) -> None:
        self.assertIsNone(parse_page_limit(""))

    def test_none(self) -> None:
        self.assertIsNone(parse_page_limit(None))

    def test_rfq_number_near_the_word_page(self) -> None:
        self.assertIsNone(
            parse_page_limit("RFQ #12-345, see page 3 of 40 for details.")
        )


class ParsePageLimitAmbiguousTests(unittest.TestCase):
    def test_two_different_limits_returns_none(self) -> None:
        text = (
            "The narrative is limited to 10 pages; the full submission "
            "shall not exceed 25 pages."
        )
        # Two distinct, disagreeing limits. Returning either would be a
        # guess about which one governs the document — ruling: None.
        self.assertIsNone(parse_page_limit(text))

    def test_same_limit_repeated_is_not_ambiguous(self) -> None:
        text = (
            "Quotes are limited to 12 pages. The technical response is "
            "limited to 12 pages, excluding appendices."
        )
        self.assertEqual(parse_page_limit(text), 12)


class ParsePageLimitPerAttachmentSubLimitTests(unittest.TestCase):
    """A per-attachment sub-limit is NOT the whole-document page limit.

    Real symptom: ``resolve_page_limit`` returning a resume/cover-letter cap
    (e.g. 2 pages) as the entire proposal's budget truncates the whole
    document to ~595 words. Per-attachment caps are extremely common in real
    RFPs — this must fail closed to None, never return the sub-limit.
    """

    def test_resumes_limited_to_2_pages_each_is_not_the_document_limit(self) -> None:
        self.assertIsNone(
            parse_page_limit("Resumes are limited to 2 pages each")
        )

    def test_cover_letter_limited_to_1_page_is_not_the_document_limit(self) -> None:
        self.assertIsNone(
            parse_page_limit("The cover letter is limited to 1 page")
        )

    def test_letters_of_support_limited_to_1_page_is_not_the_document_limit(
        self,
    ) -> None:
        self.assertIsNone(
            parse_page_limit("Letters of support are limited to 1 page")
        )

    def test_references_shall_not_exceed_one_page_each_is_not_the_document_limit(
        self,
    ) -> None:
        self.assertIsNone(
            parse_page_limit("References shall not exceed one page each")
        )

    def test_resumes_for_key_personnel_shall_not_exceed_two_pages(self) -> None:
        self.assertIsNone(
            parse_page_limit(
                "Résumés for key personnel shall not exceed two (2) pages"
            )
        )

    def test_elliptical_two_limit_sentence_does_not_collapse_to_the_first_number(
        self,
    ) -> None:
        self.assertIsNone(
            parse_page_limit(
                "The technical volume is limited to 10 pages and the cost "
                "volume to 5 pages"
            )
        )

    def test_coincidental_equal_sub_limits_do_not_collapse_to_a_false_certainty(
        self,
    ) -> None:
        text = (
            "The appendix is limited to 10 pages. The technical narrative "
            "is limited to 10 pages."
        )
        self.assertIsNone(parse_page_limit(text))

    # Contrast cases: text that already correctly returns None today,
    # proving the elliptical case above is a parsing gap, not a policy call.
    def test_semicolon_joined_sub_limits_with_repeated_verb_already_none(self) -> None:
        text = (
            "The proposal shall not exceed 12 pages excluding attachments; "
            "attachments shall not exceed 8 pages."
        )
        self.assertIsNone(parse_page_limit(text))

    def test_fully_stated_two_clause_limit_already_none(self) -> None:
        text = (
            "The technical volume shall not exceed 10 pages; the cost "
            "volume shall not exceed 5 pages."
        )
        self.assertIsNone(parse_page_limit(text))


class ResolvePageLimitTests(unittest.TestCase):
    def test_manual_field_overrides_parsed_value_when_both_set(self) -> None:
        self.assertEqual(
            resolve_page_limit(5, "Quotes are limited to 12 pages"), 5
        )

    def test_parsed_value_used_when_manual_unset(self) -> None:
        self.assertEqual(
            resolve_page_limit(None, "Quotes are limited to 12 pages"), 12
        )

    def test_both_unset_is_none(self) -> None:
        self.assertIsNone(resolve_page_limit(None, "no limit mentioned here"))

    def test_manual_zero_treated_as_unset_falls_back_to_parsed(self) -> None:
        self.assertEqual(
            resolve_page_limit(0, "Quotes are limited to 12 pages"), 12
        )


class RemainingWordBudgetFlowsFromParsedTextTests(unittest.TestCase):
    """The exact function proposal_generator.py:1897 calls to compute
    doc_word_budget. Before this task it read only rfp.page_limit, which is
    manual-only and virtually always None for JustWin-synced RFPs.
    """

    def test_before_no_manual_limit_and_no_text_is_none(self) -> None:
        rfp = _rfp(pageLimit=None)
        budget = _remaining_word_budget(
            rfp=rfp, already_written=[], drafting_count=5, rfp_text=None
        )
        self.assertIsNone(budget)

    def test_after_parsed_limit_from_rfp_text_produces_a_real_budget(self) -> None:
        """The MSU Denver scenario: a 12-page cap stated in Section IV, plus
        realistic decoys (a font-size number, a page-range reference) that
        must NOT be mistaken for the limit.
        """
        rfp = _rfp(pageLimit=None)  # never set manually — the real-world default
        rfp_text = (
            "REQUEST FOR PROPOSAL\nMetropolitan State University of Denver\n\n"
            "SECTION I — INTRODUCTION\n...background text...\n\n"
            "SECTION IV — SUBMISSION REQUIREMENTS\n"
            "Quotes are limited to 12 pages, excluding the cover letter, "
            "resumes, insurance certificates, and required forms "
            "(W-9, Vendor Application).\n"
            "Font size 12, single spaced, is required for the narrative body.\n"
            "See pages 40-45 of the attached solicitation for the "
            "evaluation matrix.\n\n"
            "SECTION V — EVALUATION CRITERIA\n...\n"
        )
        before = _remaining_word_budget(
            rfp=rfp, already_written=[], drafting_count=21, rfp_text=None
        )
        after = _remaining_word_budget(
            rfp=rfp, already_written=[], drafting_count=21, rfp_text=rfp_text
        )
        self.assertIsNone(before)
        self.assertIsNotNone(after)
        assert after is not None
        self.assertGreater(after, 0)
        # Sanity: bounded by the parsed 12-page cap, not runaway, and not
        # thrown off by the "Font size 12" / "pages 40-45" decoys.
        self.assertLessEqual(after, 12 * WORDS_PER_PAGE)
        self.assertEqual(after, 3570)  # 12 * 350 words/page * (1 - 0.15 reserve)

    def test_manual_field_overrides_parsed_text(self) -> None:
        rfp = _rfp(pageLimit=5)
        rfp_text = "Quotes are limited to 12 pages"
        budget = _remaining_word_budget(
            rfp=rfp, already_written=[], drafting_count=5, rfp_text=rfp_text
        )
        # 5-page manual cap wins: total words bounded by 5 pages, not 12.
        self.assertIsNotNone(budget)
        assert budget is not None
        self.assertLessEqual(budget, 5 * WORDS_PER_PAGE)


class AllocateWordsByPointsTests(unittest.TestCase):
    def test_higher_points_gets_proportionally_more_words(self) -> None:
        requirements = [_req("r-30", 30.0), _req("r-10", 10.0)]
        budget = 12 * WORDS_PER_PAGE  # 4200

        allocated = allocate_words_by_points(requirements, budget)

        self.assertGreater(allocated["r-30"], allocated["r-10"])
        # Roughly a 3:1 split above the shared floor.
        floor = MIN_SECTION_WORDS
        headroom_30 = allocated["r-30"] - floor
        headroom_10 = allocated["r-10"] - floor
        self.assertGreater(headroom_30, 0)
        self.assertAlmostEqual(headroom_30 / headroom_10, 3.0, delta=0.2)

    def test_no_requirements_returns_empty_dict(self) -> None:
        self.assertEqual(allocate_words_by_points([], 4200), {})

    def test_no_points_anywhere_falls_back_to_even_split(self) -> None:
        requirements = [_req("r-1", None), _req("r-2", None), _req("r-3", None)]
        allocated = allocate_words_by_points(requirements, 3000)
        self.assertEqual(len(allocated), 3)
        values = list(allocated.values())
        self.assertEqual(values[0], values[1])
        self.assertEqual(values[1], values[2])

    def test_zero_total_points_falls_back_to_even_split_no_div_by_zero(self) -> None:
        requirements = [_req("r-1", 0.0), _req("r-2", 0.0)]
        try:
            allocated = allocate_words_by_points(requirements, 3000)
        except ZeroDivisionError:  # pragma: no cover - the bug this guards
            self.fail("allocate_words_by_points divided by zero")
        self.assertEqual(allocated["r-1"], allocated["r-2"])

    def test_no_budget_still_returns_a_value_per_requirement(self) -> None:
        requirements = [_req("r-1", 30.0), _req("r-2", 10.0)]
        allocated = allocate_words_by_points(requirements, None)
        self.assertEqual(set(allocated), {"r-1", "r-2"})

    def test_non_mandatory_requirements_excluded(self) -> None:
        requirements = [
            _req("mand", 20.0, mandatory=True),
            _req("optional", 50.0, mandatory=False),
        ]
        allocated = allocate_words_by_points(requirements, 3000)
        self.assertEqual(set(allocated), {"mand"})


class SectionWeightsFromLedgerTests(unittest.TestCase):
    def test_none_ledger_returns_empty(self) -> None:
        self.assertEqual(section_weights_from_ledger(None, 4200), {})

    def test_empty_ledger_returns_empty(self) -> None:
        self.assertEqual(
            section_weights_from_ledger(RequirementLedger(requirements=[]), 4200),
            {},
        )

    def test_high_point_section_outweighs_low_point_section(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req("scored-tech", 30.0, satisfiedBy=["section-tech"]),
                _req("scored-admin", 10.0, satisfiedBy=["section-admin"]),
            ]
        )
        weights = section_weights_from_ledger(ledger, 12 * WORDS_PER_PAGE)
        self.assertGreater(weights["section-tech"], weights["section-admin"])

    def test_never_raises_when_budget_is_zero_and_points_sum_to_zero(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req("a", 0.0, satisfiedBy=["s1"]),
                _req("b", 0.0, satisfiedBy=["s2"]),
            ]
        )
        try:
            weights = section_weights_from_ledger(ledger, 0)
        except ZeroDivisionError:  # pragma: no cover - the bug this guards
            self.fail("section_weights_from_ledger divided by zero")
        self.assertEqual(set(weights), {"s1", "s2"})


if __name__ == "__main__":
    unittest.main()
