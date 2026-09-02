"""A citation typo must not destroy evidence that was actually retrieved.

Root cause this guards (Gilroy Garlic Festival Association, Sep 2026): the
capability row "Pre-event promotional campaign strategy and execution" came back
a hard gap with the reason

    quoted evidence does not appear in
    '03_CS_City of Umatilla_Digital Campaign_2006.pdf'

The evidence was not missing. zö's Umatilla work is in the KB twice — a case
study AND a won proposal — and the phased-campaign language the adjudicator
quoted lives in the won proposal, not the case study it named. ``_ground_quote``
checks the quote against ONLY the document the model cited, so a misattribution
between two documents about the same client discarded real, verbatim proof and
froze the row at gap.

This is general to any RFP whose KB has several documents for one client, which
is the normal case: 03_CS_*, 06_WON_* and 04_Bio_* often cover the same
engagement.

The repair keeps the verbatim standard exactly as strict — the quote must still
appear word-for-word in a real retrieved document. It only stops the check from
insisting the model named the right file. When the quote grounds in a sibling
document retrieved for the SAME requirement, the row survives and kb_source is
corrected to the document that actually contains the text, so the report cites
the right source.
"""

from __future__ import annotations

import unittest

from app.services.go_no_go_adjudicator import rows_from_assessments
from app.services.go_no_go_requirements import RfpRequirement

REQUIREMENT = "Pre-event promotional campaign strategy and execution"

CASE_STUDY = "03_CS_City of Umatilla_Digital Campaign_2006.pdf"
WON_PROPOSAL = "06_WON_CityofUmatilla_RockTheLocks.pdf"

# The sentence the adjudicator quoted. It is verbatim in the won proposal.
QUOTE = (
    "We build momentum early, shift to urgency and conversion as the date "
    "approaches, and run event-week coverage that keeps attendance climbing."
)

CASE_STUDY_TEXT = (
    "City of Umatilla engaged zo for a digital campaign supporting the Rock "
    "the Locks festival. Deliverables included brand graphics and a refreshed "
    "event landing page."
)

WON_PROPOSAL_TEXT = (
    "Rock the Locks — phased promotional campaign. "
    + QUOTE
    + " Sponsor integration spans recognition across materials, social "
    "highlights, website and email visibility, and on-site activation support."
)


def _requirement(name: str = REQUIREMENT) -> RfpRequirement:
    return RfpRequirement(
        requirement=name,
        category="service",
        isCore=True,
        kbQueries=["zö agency pre-event campaign"],
    )


class QuoteMisattributionTests(unittest.TestCase):
    def test_quote_grounded_in_sibling_document_survives(self) -> None:
        """Both docs retrieved for the requirement; model named the wrong one."""
        rows, rejected, _recoverable = rows_from_assessments(
            [_requirement()],
            [
                {
                    "requirement": REQUIREMENT,
                    "status": "verified",
                    "kbSource": CASE_STUDY,  # wrong file
                    "quote": QUOTE,  # verbatim in WON_PROPOSAL
                }
            ],
            {
                REQUIREMENT: {
                    CASE_STUDY: CASE_STUDY_TEXT,
                    WON_PROPOSAL: WON_PROPOSAL_TEXT,
                }
            },
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0].status,
            "verified",
            f"row was discarded despite verbatim proof; rejected={rejected}",
        )

    def test_kb_source_is_corrected_to_the_real_document(self) -> None:
        rows, _rejected, _recoverable = rows_from_assessments(
            [_requirement()],
            [
                {
                    "requirement": REQUIREMENT,
                    "status": "verified",
                    "kbSource": CASE_STUDY,
                    "quote": QUOTE,
                }
            ],
            {
                REQUIREMENT: {
                    CASE_STUDY: CASE_STUDY_TEXT,
                    WON_PROPOSAL: WON_PROPOSAL_TEXT,
                }
            },
        )
        self.assertEqual(
            rows[0].kb_source,
            WON_PROPOSAL,
            "report must cite the document the quote is actually in",
        )

    def test_invented_quote_still_rejected(self) -> None:
        """The verbatim standard is unchanged — no document contains this."""
        rows, rejected, _recoverable = rows_from_assessments(
            [_requirement()],
            [
                {
                    "requirement": REQUIREMENT,
                    "status": "verified",
                    "kbSource": CASE_STUDY,
                    "quote": (
                        "zo ran a national multi-channel campaign delivering "
                        "four million verified impressions for the festival."
                    ),
                }
            ],
            {
                REQUIREMENT: {
                    CASE_STUDY: CASE_STUDY_TEXT,
                    WON_PROPOSAL: WON_PROPOSAL_TEXT,
                }
            },
        )
        self.assertEqual(rows[0].status, "gap")
        self.assertTrue(rejected)

    def test_does_not_reach_into_another_requirements_documents(self) -> None:
        """Reattribution is scoped to docs retrieved for THIS requirement."""
        other = "Bond and insurance requirements"
        rows, _rejected, _recoverable = rows_from_assessments(
            [_requirement()],
            [
                {
                    "requirement": REQUIREMENT,
                    "status": "verified",
                    "kbSource": CASE_STUDY,
                    "quote": QUOTE,
                }
            ],
            {
                REQUIREMENT: {CASE_STUDY: CASE_STUDY_TEXT},
                other: {WON_PROPOSAL: WON_PROPOSAL_TEXT},
            },
        )
        self.assertEqual(rows[0].status, "gap")

    def test_pricing_document_never_becomes_the_corrected_source(self) -> None:
        """A rate card cannot evidence delivery, even if the words match."""
        pricing = "00_Guide_Pricing_2026.pdf"
        rows, _rejected, _recoverable = rows_from_assessments(
            [_requirement()],
            [
                {
                    "requirement": REQUIREMENT,
                    "status": "verified",
                    "kbSource": CASE_STUDY,
                    "quote": QUOTE,
                }
            ],
            {
                REQUIREMENT: {
                    CASE_STUDY: CASE_STUDY_TEXT,
                    pricing: WON_PROPOSAL_TEXT,
                }
            },
        )
        self.assertEqual(rows[0].status, "gap")


if __name__ == "__main__":
    unittest.main()
