"""Proposal-submission content is not a vendor capability, on any RFP.

Root cause this guards (Gilroy Garlic Festival Association, Sep 2026): the RFP's
"what your proposal must contain" section was decomposed into capability
requirements alongside the actual scope of work. Five of twenty matrix rows were
proposal *attachments* — two case studies, three client references, an itemized
budget, an agency philosophy statement, an operational plan — and two more
restated the RFP's evaluation criteria, duplicating scope rows already present.

None of those can be evidenced by a knowledge base of zö's own past work: no
document in any KB says "here are three references". So they sat as permanent
zero-earned rows in the Technical Capability denominator, and the planner
additionally flagged "Three client references" disqualifying=true because the
prompt listed "a mandatory reference count" as a pass/fail threshold.

That single flag did three things at once: forced recommendation="no_go"
outright, suppressed every floor in calibrate_technical_capability_score, and
suppressed the Win Probability floor. The live run returned NO-GO at
Technical 1/5 on an RFP whose scope matched a won proposal already in the KB.

The distinction that matters, and it is general to every RFP:

  - "at least five comparable municipal projects completed in the past five
    years" is a track record you either HAVE or do not. Pass/fail. Disqualifying.
  - "attach three client references" is a form you fill out. Any agency with
    clients can satisfy it. It is a submission deliverable, never a disqualifier
    and never a capability score input.
"""

from __future__ import annotations

import unittest

from app.models.go_no_go import GoNoGoCapabilityRow
from app.services.go_no_go_capability import (
    calibrate_technical_capability_score,
    core_craft_rows,
    derive_resource_capability_score,
    derive_technical_capability_score,
    unmet_disqualifying_requirements,
    unverified_core_requirements,
)
from app.services.go_no_go_requirements import parse_requirements


def _row(
    requirement: str,
    status: str,
    *,
    category: str = "service",
    disqualifying: bool = False,
    is_core: bool = True,
) -> GoNoGoCapabilityRow:
    return GoNoGoCapabilityRow(
        requirement=requirement,
        status=status,
        isCore=is_core,
        category=category,
        disqualifying=disqualifying,
        kbSource="03_CS_City_of_Umatilla.pdf" if status != "gap" else "",
    )


# The twenty rows the live Gilroy run produced, with the seven mis-typed rows
# carrying the category the planner should now assign them.
GILROY_ROWS: list[GoNoGoCapabilityRow] = [
    _row("Ongoing website management and security updates", "gap", category="technical"),
    _row("Website performance optimization for ticket sales", "partial", category="technical"),
    _row("Website content management and updates", "partial"),
    _row("Social media account management across Instagram, Facebook", "partial"),
    _row("Strategic social media content planning and calendar development", "verified"),
    _row("Social media community engagement and audience interaction", "partial"),
    _row("Pre-event promotional campaign strategy and execution", "gap"),
    _row("Data analytics and attendee data capture strategy", "partial", category="technical"),
    _row("Sponsorship package development and tiered structure creation", "gap"),
    _row("Sponsor collateral and marketing materials modernization", "verified"),
    _row("Digital asset integration and sponsor activation", "gap"),
    _row("Charitable impact storytelling and campaign development", "gap"),
    _row("Regional partnership and community collaboration strategy", "gap"),
    # Proposal attachments — things the document must contain, not capabilities.
    _row(
        "Demonstrated case studies of scaling existing website/brand infrastructure",
        "gap",
        category="submission",
        disqualifying=True,
    ),
    _row(
        "Three client references (current or former)",
        "gap",
        category="submission",
        disqualifying=True,
    ),
    _row("Itemized budget and cost breakdown", "gap", category="submission"),
    _row(
        "Agency philosophy on working with established legacy brands",
        "gap",
        category="submission",
    ),
    _row(
        "High-level operational plan addressing five strategic pillars",
        "gap",
        category="submission",
    ),
    # Restatements of the RFP's evaluation criteria; duplicate scope rows above.
    _row(
        "Data-driven optimization capabilities (social media growth, website conversion)",
        "gap",
        category="submission",
    ),
    _row(
        "Corporate sponsorship elevation and B2B marketing collateral expertise",
        "gap",
        category="submission",
    ),
]


class SubmissionCategoryTests(unittest.TestCase):
    """The planner must be able to type a row as submission content."""

    def test_parser_preserves_submission_category(self) -> None:
        out = parse_requirements(
            {
                "requirements": [
                    {
                        "requirement": "Three client references",
                        "category": "submission",
                        "isCore": True,
                    }
                ]
            }
        )
        self.assertEqual(out[0].category, "submission")

    def test_parser_never_lets_submission_be_disqualifying(self) -> None:
        """A form you fill out cannot be a responsiveness threshold."""
        out = parse_requirements(
            {
                "requirements": [
                    {
                        "requirement": "Three client references",
                        "category": "submission",
                        "isCore": True,
                        "disqualifying": True,
                    }
                ]
            }
        )
        self.assertFalse(out[0].disqualifying)

    def test_parser_keeps_genuine_track_record_disqualifier(self) -> None:
        out = parse_requirements(
            {
                "requirements": [
                    {
                        "requirement": (
                            "At least five comparable municipal projects "
                            "completed within the past five years"
                        ),
                        "category": "service",
                        "isCore": True,
                        "disqualifying": True,
                    }
                ]
            }
        )
        self.assertTrue(out[0].disqualifying)


class SubmissionRowsAreNotScoredTests(unittest.TestCase):
    def test_excluded_from_technical_capability(self) -> None:
        craft = [r for r in GILROY_ROWS if r.category != "submission"]
        self.assertEqual(
            derive_technical_capability_score(GILROY_ROWS),
            derive_technical_capability_score(craft),
            "submission rows must not dilute the Technical denominator",
        )

    def test_excluded_from_resource_availability(self) -> None:
        rows = [
            _row("Assign a project manager", "verified", category="role"),
            _row("Three client references", "gap", category="submission"),
        ]
        self.assertEqual(derive_resource_capability_score(rows), 5)

    def test_excluded_from_core_craft_rows(self) -> None:
        for row in core_craft_rows(GILROY_ROWS):
            self.assertNotEqual(row.category, "submission")

    def test_excluded_from_core_gap_list(self) -> None:
        gaps = unverified_core_requirements(GILROY_ROWS)
        self.assertNotIn("Three client references (current or former)", gaps)

    def test_never_counts_as_an_unmet_disqualifier(self) -> None:
        """Even if a row arrives already flagged, it cannot gate the pursuit."""
        rows = [
            _row(
                "Three client references",
                "gap",
                category="submission",
                disqualifying=True,
            )
        ]
        self.assertEqual(unmet_disqualifying_requirements(rows), [])

    def test_genuine_track_record_disqualifier_still_fires(self) -> None:
        rows = [
            _row(
                "At least five comparable municipal projects in five years",
                "gap",
                category="service",
                disqualifying=True,
            )
        ]
        self.assertEqual(len(unmet_disqualifying_requirements(rows)), 1)


class GilroyRegressionTests(unittest.TestCase):
    """The whole matrix, end to end, at the numbers the live run produced."""

    def test_technical_capability_is_not_collapsed_to_one(self) -> None:
        score = calibrate_technical_capability_score(GILROY_ROWS)
        self.assertIsNotNone(score)
        self.assertGreater(
            score,
            1,
            "Technical 1/5 triggers the hard NO-GO path; scope evidence "
            "does not support a capability collapse here",
        )

    def test_no_disqualifier_blocks_the_pursuit(self) -> None:
        self.assertEqual(unmet_disqualifying_requirements(GILROY_ROWS), [])

    def test_calibration_floors_are_reachable(self) -> None:
        """With no bogus disqualifier, calibration is no longer short-circuited."""
        base = derive_technical_capability_score(GILROY_ROWS)
        calibrated = calibrate_technical_capability_score(GILROY_ROWS)
        self.assertGreaterEqual(calibrated, base)


if __name__ == "__main__":
    unittest.main()
