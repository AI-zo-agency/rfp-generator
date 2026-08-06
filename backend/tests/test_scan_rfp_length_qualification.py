"""Qualification length guard: drop padding / unneeded sections, keep trust."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.models.rfp import RfpRecord
from app.services.proposal_rfp_compliance import reconcile_requirement_ledger


def _rfp(**kw) -> RfpRecord:
    base = dict(
        id="rfp-len",
        title="T",
        client="C",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="n",
        pageLimit=5,
    )
    base.update(kw)
    return RfpRecord(**base)


class PaddingAndLengthGuardTests(unittest.TestCase):
    def test_removes_additional_supporting_material_checklist(self) -> None:
        checklist = "\n".join(
            f"[MANUAL FILL: Sonja — attach item {i}]" for i in range(6)
        )
        draft = ProposalDraft(
            rfpId="rfp-len",
            updatedAt="2026-08-05T00:00:00+00:00",
            sections=[
                ProposalSection(
                    id="section-1-1",
                    title="1.1 — Who We Are",
                    content="zö agency builds trust with public institutions. " * 20,
                    status="generated",
                ),
                ProposalSection(
                    id="sec-padding",
                    title="Additional Supporting Material",
                    content=checklist + "\nCompliance Checklist filler.",
                    status="generated",
                ),
            ],
        )
        research = ProposalResearchCache(
            rfpId="rfp-len",
            updatedAt="2026-08-05T00:00:00+00:00",
            requirementLedger=RequirementLedger(
                requirements=[
                    LedgerRequirement(
                        id="r1",
                        text="Provide information about your firm",
                        source="required_content",
                        mandatory=True,
                        satisfiedBy=["section-1-1"],
                    )
                ]
            ),
        )
        result = reconcile_requirement_ledger(
            draft=draft, research=research, rfp=_rfp(pageLimit=None), rfp_text="x" * 200
        )
        ids = {s.id for s in result.draft.sections}
        self.assertIn("section-1-1", ids)
        self.assertNotIn("sec-padding", ids)
        self.assertTrue(any("remove-padding" in line for line in result.logs))

    def test_drops_unneeded_sections_to_fit_page_budget_keeps_trust(self) -> None:
        fluff = ("Generic process paragraph about methodology phases. " * 40) + "\n\n"
        draft = ProposalDraft(
            rfpId="rfp-len",
            updatedAt="2026-08-05T00:00:00+00:00",
            sections=[
                ProposalSection(
                    id="section-1-1",
                    title="1.1 — Who We Are",
                    content="Trusted agency narrative for KVCC. " * 30,
                    status="generated",
                ),
                ProposalSection(
                    id="section-2-bio-sonja",
                    title="2.1 — Sonja Anderson",
                    content="Sonja Anderson bio with decades of experience. " * 25,
                    status="generated",
                ),
                ProposalSection(
                    id="sec-fluff-a",
                    title="Generic Process Overview Phase Diagram Notes",
                    content=fluff * 8,
                    status="generated",
                ),
                ProposalSection(
                    id="sec-fluff-b",
                    title="Optional Internal Workflow Digression",
                    content=fluff * 8,
                    status="generated",
                ),
            ],
        )
        research = ProposalResearchCache(
            rfpId="rfp-len",
            updatedAt="2026-08-05T00:00:00+00:00",
            requirementLedger=RequirementLedger(
                requirements=[
                    LedgerRequirement(
                        id="r1",
                        text="Provide information about your firm",
                        source="required_content",
                        mandatory=True,
                        satisfiedBy=["section-1-1"],
                        points=20,
                    )
                ]
            ),
        )
        # 5 pages * 350 * 0.92 ≈ 1610 words — fluff alone is huge.
        result = reconcile_requirement_ledger(
            draft=draft,
            research=research,
            rfp=_rfp(pageLimit=5),
            rfp_text="Proposal limited to 5 pages total submission.",
        )
        ids = {s.id for s in result.draft.sections}
        self.assertIn("section-1-1", ids)
        self.assertIn("section-2-bio-sonja", ids)
        self.assertTrue(
            "sec-fluff-a" not in ids or "sec-fluff-b" not in ids,
            "at least one fluff section must be dropped for page budget",
        )
        self.assertTrue(
            any("cut-sections" in line or "ledger:cut" in line for line in result.logs),
            result.logs,
        )


if __name__ == "__main__":
    unittest.main()
