"""A whole RFP-required section that was never drafted must block readiness —
not sit at the same "warning" severity as an ordinary human-input placeholder
(a signature, a date) inside an otherwise-complete section."""

from __future__ import annotations

import unittest

from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RfpSectionMap,
)
from app.models.rfp import RfpRecord
from app.services.proposal_presubmit_review import _compliance_checklist, _scan_copy_paste


def _rfp(rfp_id: str = "rfp-presubmit-severity") -> RfpRecord:
    return RfpRecord(
        id=rfp_id,
        title="Test RFP",
        client="Test Client",
        sector="Health",
        source="manual",
        dueDate="2026-08-01",
        receivedDate="2026-07-01",
        lastActivity="2026-07-01",
        lastActivityNote="test",
    )


class UndraftedSectionSeverityTests(unittest.TestCase):
    def test_whole_section_stub_manual_fill_is_critical(self) -> None:
        section = ProposalSection(
            id="rfp-sec-data-security",
            title="Data Security Plan",
            content=(
                "## Data Security Plan\n\n"
                "[MANUAL FILL: Draft this RFP-required section — Data Security Plan]\n\n"
                "RFP-required outline:\n- Encryption\n- Access Control\n"
            ),
            status="generated",
        )
        draft = ProposalDraft(
            rfpId="rfp-presubmit-severity", sections=[section], updatedAt="2026-01-01T00:00:00Z"
        )
        issues = _scan_copy_paste(draft=draft, rfp=_rfp())
        placeholder_issues = [i for i in issues if i.category == "placeholder"]
        self.assertTrue(placeholder_issues, "expected at least one placeholder issue")
        self.assertTrue(
            all(i.severity == "critical" for i in placeholder_issues),
            f"expected all tags in an undrafted stub section to be critical, got: "
            f"{[(i.excerpt, i.severity) for i in placeholder_issues]}",
        )

    def test_ordinary_manual_fill_in_a_complete_section_stays_a_warning(self) -> None:
        section = ProposalSection(
            id="rfp-sec-signature",
            title="Authorized Signature",
            content=(
                "## Authorized Signature\n\n"
                "By signing below, the undersigned certifies that the information "
                "provided in this proposal is accurate and complete, and that the "
                "offeror agrees to all terms and conditions of this RFP. This "
                "section is otherwise fully drafted with real, substantive prose "
                "covering every requirement the RFP names for this tab, well past "
                "any length that would look like an unwritten stub to a reader.\n\n"
                "| Field | Response |\n| --- | --- |\n"
                "| Authorized Representative | [MANUAL FILL: wet/digital signature] |\n"
            ),
            status="generated",
        )
        draft = ProposalDraft(
            rfpId="rfp-presubmit-severity", sections=[section], updatedAt="2026-01-01T00:00:00Z"
        )
        issues = _scan_copy_paste(draft=draft, rfp=_rfp())
        placeholder_issues = [i for i in issues if i.category == "placeholder"]
        self.assertTrue(placeholder_issues, "expected at least one placeholder issue")
        self.assertTrue(
            all(i.severity == "warning" for i in placeholder_issues),
            f"expected an ordinary in-context MANUAL FILL to stay a warning, got: "
            f"{[(i.excerpt, i.severity) for i in placeholder_issues]}",
        )


class ComplianceChecklistTitleMatchTests(unittest.TestCase):
    def test_renamed_section_is_not_reported_missing(self) -> None:
        """research.rfp_sections was computed against one title; a later pass
        (mandated-title relabel, reorder) renamed the actual draft tab — the
        compliance checklist must still find it by fuzzy match, not report a
        real, present section as "No draft content — generate or attach form"."""
        section = ProposalSection(
            id="rfp-sec-refs",
            title="3 — References and Past Performance",
            content=(
                "| # | Client | Contact | Project |\n"
                "|---|--------|---------|----------|\n"
                "| 1 | City of Bend | Jane Doe | Brand campaign |\n"
                "| 2 | McMinnville Library | John Smith | Bilingual outreach |\n"
            ),
            status="generated",
        )
        draft = ProposalDraft(
            rfpId="rfp-presubmit-severity", sections=[section], updatedAt="2026-01-01T00:00:00Z"
        )
        research = ProposalResearchCache(
            rfpId="rfp-presubmit-severity",
            updatedAt="2026-01-01T00:00:00Z",
            rfpSections=[
                RfpSectionMap(
                    id="rfp-sec-refs",
                    title="References And Past Performance",
                )
            ],
        )
        items = _compliance_checklist(draft=draft, research=research, rfp=_rfp())
        matching = [i for i in items if "references and past performance" in i.item.casefold()]
        self.assertTrue(matching, f"expected a checklist item for References, got: {items}")
        self.assertTrue(
            all(i.status == "pass" for i in matching),
            f"expected the renamed-but-present section to read as covered, got: {items}",
        )


if __name__ == "__main__":
    unittest.main()
