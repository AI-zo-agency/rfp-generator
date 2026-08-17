"""Kills the insurance-x3 duplication.

Observed on a 30-page-limited RFP: insurance content appeared in THREE
places — the static Section 1.5 Insurance Information, the closing
component ``insurance_attachments`` ("Required Submission Attachments —
Document Checklist"), and the closing component ``exemplar_agreement``
("Contract / Agreement Acknowledgment"). Nothing owned the mapping:
``proposal_overlap_detector`` is 5-gram Jaccard and cannot see three
paraphrases of the same coverage; title dedup explicitly whitelists
"insurance" so an insurance tab is exempt from the static-duplicate drop;
and ``insurance_attachments`` alone carried a "do not restate" guard —
its sibling ``exemplar_agreement`` had none and restated insurance
obligations.

Two things close the gap:
  1. ``resolve_duplicate_owners`` — reads ``RequirementLedger.duplicated()``
     in reverse: exactly one section owns a requirement's substance
     (highest evaluation points, ties to earliest RFP order); every other
     section that matched keeps the topic only as a cross-reference.
  2. ``exemplar_agreement`` gets the same non-restatement guard its
     sibling ``insurance_attachments`` already carries.
"""

from __future__ import annotations

import unittest

from app.models.proposal import RfpSectionMap
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.services.proposal_closing_package import (
    detect_closing_components,
    requirement_to_closing_component,
)
from app.services.proposal_closing_ledger import ClosingRequirement, ledger_from_fixture

from app.services.proposal_intelligence.assembler import (
    DuplicateOwnerResolution,
    resolve_duplicate_owners,
)


def _req(rid: str, text: str, satisfied_by: list[str], **kw) -> LedgerRequirement:
    kw.setdefault("source", "required_content")
    kw.setdefault("mandatory", True)
    return LedgerRequirement(id=rid, text=text, satisfiedBy=satisfied_by, **kw)


class ThreeSectionsCollapseToOneOwnerTests(unittest.TestCase):
    """The observed defect, reproduced at the ledger level."""

    def test_insurance_claimed_by_three_sections_gets_one_owner(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r-insurance",
                    "Proof of general liability insurance coverage",
                    satisfied_by=["sec-1-5", "sec-attachments", "sec-contract"],
                )
            ]
        )
        outline_sections = [
            RfpSectionMap(id="sec-1-5", title="1.5 — Insurance Information"),
            RfpSectionMap(
                id="sec-attachments",
                title="Required Submission Attachments",
            ),
            RfpSectionMap(
                id="sec-contract",
                title="Contract / Agreement Acknowledgment",
            ),
        ]

        new_ledger, resolutions = resolve_duplicate_owners(ledger, outline_sections)

        # The ledger's own data is untouched — satisfied_by remains the
        # factual matcher evidence, never pruned.
        self.assertEqual(
            new_ledger.requirements[0].satisfied_by,
            ["sec-1-5", "sec-attachments", "sec-contract"],
        )

        self.assertEqual(len(resolutions), 1)
        resolution = resolutions[0]
        self.assertIsInstance(resolution, DuplicateOwnerResolution)
        self.assertEqual(resolution.requirement_id, "r-insurance")
        # No evaluation points anywhere -> tie -> earliest RFP order wins,
        # which is Section 1.5 (first in outline order).
        self.assertEqual(resolution.owner_section_id, "sec-1-5")
        self.assertEqual(
            sorted(resolution.cross_reference_section_ids),
            ["sec-attachments", "sec-contract"],
        )
        # Cross-reference guidance must never tell a non-owner to restate.
        for section_id in resolution.cross_reference_section_ids:
            self.assertNotIn(section_id, resolution.owner_section_id)
        self.assertIn("do not restate", resolution.note.lower())

    def test_owner_is_the_section_with_the_highest_evaluation_points(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r-insurance",
                    "Proof of general liability insurance coverage",
                    satisfied_by=["sec-1-5", "sec-attachments", "sec-contract"],
                )
            ]
        )
        outline_sections = [
            RfpSectionMap(id="sec-1-5", title="1.5 — Insurance Information"),
            RfpSectionMap(
                id="sec-attachments",
                title="Required Submission Attachments",
            ),
            # Scored highest even though it appears last in RFP order —
            # evaluation weight must beat position.
            RfpSectionMap(
                id="sec-contract",
                title="Contract / Agreement Acknowledgment",
                evaluationWeight=20,
            ),
        ]

        _, resolutions = resolve_duplicate_owners(ledger, outline_sections)
        self.assertEqual(resolutions[0].owner_section_id, "sec-contract")
        self.assertEqual(
            sorted(resolutions[0].cross_reference_section_ids),
            ["sec-1-5", "sec-attachments"],
        )

    def test_ties_break_toward_the_earliest_rfp_order(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r-insurance",
                    "Proof of general liability insurance coverage",
                    satisfied_by=["sec-b", "sec-a"],
                )
            ]
        )
        # Both carry the same weight; "sec-a" comes first in RFP order.
        outline_sections = [
            RfpSectionMap(id="sec-a", title="A", evaluationWeight=10),
            RfpSectionMap(id="sec-b", title="B", evaluationWeight=10),
        ]
        _, resolutions = resolve_duplicate_owners(ledger, outline_sections)
        self.assertEqual(resolutions[0].owner_section_id, "sec-a")


class ReverseGuardTests(unittest.TestCase):
    """Merging distinct requirements would lose a required response —
    worse than the duplication resolve_duplicate_owners is meant to fix."""

    def test_two_distinct_insurance_requirements_are_never_merged(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r-coi",
                    "Provide proof of general liability insurance",
                    satisfied_by=["sec-attachments"],
                ),
                _req(
                    "r-contract-ack",
                    "Acknowledge the insurance provisions of the standard contract",
                    satisfied_by=["sec-contract"],
                ),
            ]
        )
        outline_sections = [
            RfpSectionMap(id="sec-attachments", title="Attachments"),
            RfpSectionMap(id="sec-contract", title="Contract Acknowledgment"),
        ]

        new_ledger, resolutions = resolve_duplicate_owners(ledger, outline_sections)

        # Neither requirement has more than one satisfying section, so
        # neither is "duplicated" — no resolution is produced for either,
        # and both keep their own distinct id/text/satisfied_by.
        self.assertEqual(resolutions, [])
        self.assertEqual(len(new_ledger.requirements), 2)
        ids = {r.id for r in new_ledger.requirements}
        self.assertEqual(ids, {"r-coi", "r-contract-ack"})
        by_id = {r.id: r for r in new_ledger.requirements}
        self.assertEqual(
            by_id["r-coi"].text, "Provide proof of general liability insurance"
        )
        self.assertEqual(
            by_id["r-contract-ack"].text,
            "Acknowledge the insurance provisions of the standard contract",
        )

    def test_two_distinct_requirements_each_independently_duplicated_stay_separate(
        self,
    ) -> None:
        """Even when BOTH requirements are independently duplicated and share
        a section in common, resolution never crosses requirement identity —
        each gets its own resolution keyed to its own requirement id."""
        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r-coi",
                    "Provide proof of general liability insurance",
                    satisfied_by=["sec-1-5", "sec-attachments"],
                ),
                _req(
                    "r-contract-ack",
                    "Acknowledge the insurance provisions of the standard contract",
                    satisfied_by=["sec-attachments", "sec-contract"],
                ),
            ]
        )
        outline_sections = [
            RfpSectionMap(id="sec-1-5", title="1.5 — Insurance Information"),
            RfpSectionMap(id="sec-attachments", title="Attachments"),
            RfpSectionMap(id="sec-contract", title="Contract Acknowledgment"),
        ]

        _, resolutions = resolve_duplicate_owners(ledger, outline_sections)
        self.assertEqual(len(resolutions), 2)
        by_req = {r.requirement_id: r for r in resolutions}
        self.assertEqual(
            by_req["r-coi"].requirement_text,
            "Provide proof of general liability insurance",
        )
        self.assertEqual(
            by_req["r-contract-ack"].requirement_text,
            "Acknowledge the insurance provisions of the standard contract",
        )
        # Each requirement's owner is drawn only from its own satisfied_by.
        self.assertIn(by_req["r-coi"].owner_section_id, ["sec-1-5", "sec-attachments"])
        self.assertIn(
            by_req["r-contract-ack"].owner_section_id,
            ["sec-attachments", "sec-contract"],
        )


class UntouchedCasesTests(unittest.TestCase):
    def test_a_requirement_with_exactly_one_section_is_untouched(self) -> None:
        ledger = RequirementLedger(
            requirements=[_req("r1", "A cover letter", satisfied_by=["sec-1"])]
        )
        new_ledger, resolutions = resolve_duplicate_owners(
            ledger, [RfpSectionMap(id="sec-1", title="Cover Letter")]
        )
        self.assertEqual(resolutions, [])
        self.assertEqual(new_ledger.requirements[0].satisfied_by, ["sec-1"])

    def test_a_requirement_with_zero_sections_is_untouched(self) -> None:
        """That is Task 2's business (missing()), not this task's."""
        ledger = RequirementLedger(
            requirements=[_req("r1", "A cover letter", satisfied_by=[])]
        )
        new_ledger, resolutions = resolve_duplicate_owners(ledger, [])
        self.assertEqual(resolutions, [])
        self.assertEqual(new_ledger.requirements[0].satisfied_by, [])


class DegradeGracefullyTests(unittest.TestCase):
    def test_none_ledger_does_not_raise(self) -> None:
        new_ledger, resolutions = resolve_duplicate_owners(None, None)
        self.assertEqual(new_ledger.requirements, [])
        self.assertEqual(resolutions, [])

    def test_empty_ledger_does_not_raise(self) -> None:
        new_ledger, resolutions = resolve_duplicate_owners(
            RequirementLedger(requirements=[]), None
        )
        self.assertEqual(new_ledger.requirements, [])
        self.assertEqual(resolutions, [])

    def test_none_outline_sections_does_not_raise(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r1",
                    "Proof of insurance",
                    satisfied_by=["sec-a", "sec-b"],
                )
            ]
        )
        new_ledger, resolutions = resolve_duplicate_owners(ledger, None)
        self.assertEqual(len(resolutions), 1)
        # No outline metadata for either candidate -> falls back to the
        # original satisfied_by order.
        self.assertEqual(resolutions[0].owner_section_id, "sec-a")


class ExemplarAgreementGuardTests(unittest.TestCase):
    """Insurance / agreement closing rows must defer to Section 1.5."""

    def test_exemplar_agreement_gets_the_guard(self) -> None:
        comp = requirement_to_closing_component(
            ClosingRequirement(
                id="exemplar_agreement",
                title="Contract / Agreement Acknowledgment",
                kind="form",
                draftInstructions="Acknowledge the exemplar agreement.",
            )
        )
        instructions = comp.draft_instructions.lower()
        self.assertIn("do not restate", instructions)
        self.assertIn("limits", instructions)
        self.assertIn("carriers", instructions)

    def test_insurance_attachments_gets_the_guard(self) -> None:
        comp = requirement_to_closing_component(
            ClosingRequirement(
                id="insurance_attachments",
                title="Required Submission Attachments — Document Checklist",
                kind="attachment",
                draftInstructions="Checklist of documents to return.",
            )
        )
        instructions = comp.draft_instructions.lower()
        self.assertIn("do not restate", instructions)
        self.assertIn("limits", instructions)
        self.assertIn("carriers", instructions)


class EndToEndThreeSurfacesProofTests(unittest.TestCase):
    """Both insurance surfaces defer to Section 1.5 when present on the ledger."""

    def test_rfp_ledger_rows_get_the_guard_on_both(self) -> None:
        ledger = ledger_from_fixture(
            [
                {
                    "id": "insurance_attachments",
                    "title": "Required Submission Attachments",
                    "kind": "attachment",
                    "draftInstructions": "Checklist: COI, W-9.",
                },
                {
                    "id": "exemplar_agreement",
                    "title": "Contract / Agreement Acknowledgment",
                    "kind": "form",
                    "draftInstructions": "Acknowledge exemplar agreement.",
                },
            ]
        )
        comps = {
            c.id: c for c in detect_closing_components("ignored", ledger=ledger)
        }
        self.assertIn("insurance_attachments", comps)
        self.assertIn("exemplar_agreement", comps)

        for comp_id in ("insurance_attachments", "exemplar_agreement"):
            instructions = comps[comp_id].draft_instructions.lower()
            self.assertIn(
                "do not restate",
                instructions,
                f"{comp_id} must defer to Section 1.5, not restate coverage",
            )
            self.assertIn("1.5", instructions)


if __name__ == "__main__":
    unittest.main()
