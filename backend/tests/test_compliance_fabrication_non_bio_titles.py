"""run_compliance_fabrication_repairs must never treat a forms/closing tab as a
bio candidate just because its bare title happens to be two Title-Case words.

Regression for: "COST FILE" (real, already-generated budget content) got wiped
during Final checks and replaced with a "[MANUAL FILL: ... A prior pass wrongly
replaced this body with a team-bio stub]" message. Root cause traced end to end:

1. person_name_from_tab_title("Cost File") returns "Cost File" — is_plausible_
   person_name only checks SHAPE (2-3 Title-Case words minus a stopword list),
   and neither "cost" nor "file" is on that list.
2. The only other defense — confirming the "name" is a real org-chart member —
   is skipped whenever org_roles comes back empty for this RFP (by design, so a
   parser miss on Section 1.2 never silently drops a real bio). For this RFP the
   org chart didn't parse, so the check no-oped and "Cost File" sailed through.
3. With `member` set, the section is treated as an ungrounded bio narrative and
   rewritten via _rebuild_bio_stub — replacing the real budget content with
   fresh bio-stub text (a "Role on this engagement" line, a name that isn't a
   person).
4. A later, separate pass (repair_misplaced_bio_stub_sections) correctly
   detects that text doesn't belong on a non-Section-2 tab and blanks it to
   [MANUAL FILL] — by which point the original budget content is already gone
   from the live draft (only recoverable from a pre-scan snapshot, if any).

Fix: require the same dotted section-number prefix ("2.1 — Name") that
is_named_person_bio_tab already requires everywhere else in the codebase,
before ever treating a bare title as a person-name candidate. A forms tab like
"Cost File" never carries that prefix; a real Section 2 bio tab always does.
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_scan_compliance_fabrication import (
    run_compliance_fabrication_repairs,
)

_REAL_BUDGET_CONTENT = (
    "## Cost File\n\n"
    "This budget reflects a fixed-fee engagement covering account strategy, "
    "creative production, and media management for the 12-month contract term. "
    "Rates are held firm through the option-year renewal and include standard "
    "overhead, benefits, and profit consistent with our current government rate "
    "agreement. No travel or reimbursable expenses are included above the fee "
    "shown; those are billed at cost with prior written approval.\n\n"
    "| Line Item | Rate | Hours | Total |\n"
    "|---|---|---|---|\n"
    "| Account strategy | $185/hr | 120 | $22,200 |\n"
    "| Creative production | $150/hr | 200 | $30,000 |\n\n"
    "Total not-to-exceed fee: $52,200 over the 12-month engagement."
)


class ComplianceFabricationNonBioTitleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cost_file_tab_survives_with_no_org_chart_present(self) -> None:
        """Exact repro: no org chart section at all -> org_roles == {} -> the
        roster check alone (pre-fix) would no-op and let "Cost File" through."""
        draft = ProposalDraft(
            rfpId="rfp-1",
            updatedAt="2026-09-01T00:00:00+00:00",
            sections=[
                ProposalSection(
                    id="section-cost-file",
                    title="Cost File",
                    content=_REAL_BUDGET_CONTENT,
                    status="generated",
                ),
            ],
        )
        updated, logs = await run_compliance_fabrication_repairs(draft)
        cost_section = next(s for s in updated.sections if s.id == "section-cost-file")
        self.assertEqual(cost_section.content, _REAL_BUDGET_CONTENT)
        self.assertNotIn("Role on this engagement", cost_section.content)

    async def test_response_file_tab_also_survives(self) -> None:
        """Same shape bug, different forms tab from the same incident's screenshot."""
        draft = ProposalDraft(
            rfpId="rfp-1",
            updatedAt="2026-09-01T00:00:00+00:00",
            sections=[
                ProposalSection(
                    id="section-response-file",
                    title="Response File",
                    content="All required forms are attached in Appendix B.",
                    status="generated",
                ),
            ],
        )
        updated, _logs = await run_compliance_fabrication_repairs(draft)
        section = next(s for s in updated.sections if s.id == "section-response-file")
        self.assertEqual(
            section.content, "All required forms are attached in Appendix B."
        )

    async def test_real_numbered_bio_tab_is_still_processed(self) -> None:
        """The fix must not blind the pipeline to real bios — a numbered "2.1 —
        Name" tab must still reach the org-chart / KB grounding logic."""
        draft = ProposalDraft(
            rfpId="rfp-1",
            updatedAt="2026-09-01T00:00:00+00:00",
            sections=[
                ProposalSection(
                    id="section-2-bio-jamie-rivera",
                    title="2.1 — Jamie Rivera",
                    content=(
                        "### Jamie Rivera\n\n"
                        "**Role on this engagement:** Account Director\n\n"
                        "Jamie has led digital campaigns for a decade."
                    ),
                    status="generated",
                ),
            ],
        )
        # Must not raise, and must not be a no-op purely because we now skip it.
        updated, _logs = await run_compliance_fabrication_repairs(draft)
        section = next(
            s for s in updated.sections if s.id == "section-2-bio-jamie-rivera"
        )
        self.assertIn("Role on this engagement", section.content)


if __name__ == "__main__":
    unittest.main()
