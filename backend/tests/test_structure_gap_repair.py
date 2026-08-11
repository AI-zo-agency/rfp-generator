"""Structural gap detection for empty ## and truncated case-study prose."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalSection
from app.services.proposal_structure_gap_repair import (
    find_empty_subheadings,
    find_truncated_passages,
    section_structure_issues,
)


class StructureGapRepairTests(unittest.TestCase):
    def test_finds_empty_transportation_subheads(self) -> None:
        content = """# Transportation Experience

## Procurement Compliance Fluency

## Government Client Roster

We have delivered brand work for multiple public agencies.

## State Agencies
"""
        empty = find_empty_subheadings(content)
        self.assertIn("Procurement Compliance Fluency", empty)
        self.assertIn("State Agencies", empty)
        self.assertNotIn("Government Client Roster", empty)

    def test_finds_truncated_infinite_assets_style(self) -> None:
        trunc = find_truncated_passages(
            "Infinite Assets engaged zö agency to modernize their brand and to"
        )
        self.assertTrue(trunc)

    def test_section_structure_issues_client_voice(self) -> None:
        section = ProposalSection(
            id="cs-11",
            title="Case Study 11",
            content="## Challenge\n\nWe rebranded the title company.\n\n## Client Voice\n\n",
        )
        issues = section_structure_issues(section)
        self.assertTrue(any("Client Voice" in i for i in issues))


if __name__ == "__main__":
    unittest.main()
