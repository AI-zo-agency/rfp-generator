"""Integrity VERIFY flagging must count as an improvement (not a rejection)."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalSection
from app.services.proposal_section_quality import (
    is_integrity_verify_flagging,
    is_strict_improvement,
)


def _sec(content: str) -> ProposalSection:
    return ProposalSection(
        id="agency-team",
        title="Agency team qualifications",
        content=content,
        status="generated",
        wordTarget=400,
    )


class IntegrityVerifyFlaggingTests(unittest.TestCase):
    def test_percent_time_to_verify_is_improvement(self) -> None:
        before = _sec(
            "| Role | Name | Percent-Time |\n"
            "| Executive Sponsor | Sonja Anderson | 10% |\n"
            "| Account Manager | Ron Comer | 35% |\n"
            "| Creative Lead | Curt Schultz | 25% |\n"
        )
        after = _sec(
            "| Role | Name | Percent-Time |\n"
            "| Executive Sponsor | Sonja Anderson | [VERIFY: percent time] |\n"
            "| Account Manager | Ron Comer | [VERIFY: percent time] |\n"
            "| Creative Lead | Curt Schultz | [VERIFY: percent time] |\n"
        )
        self.assertTrue(is_integrity_verify_flagging(before, after))
        self.assertTrue(is_strict_improvement(before, after))

    def test_identical_content_not_flagging(self) -> None:
        s = _sec("Ron Comer at 35% time.")
        self.assertFalse(is_integrity_verify_flagging(s, s))


if __name__ == "__main__":
    unittest.main()
