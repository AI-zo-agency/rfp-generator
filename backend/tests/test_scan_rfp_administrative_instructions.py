"""Task 16: the third instance of the requirement-ledger ADD defect (Task 14
fixed scored_criterion; Task 15 wired that into full-generation) — the
reconciler tried to ADD a new section for administrative submission
instructions, not just scoring-category names.

Root defect, verbatim from a real KVCC proposal scan (HEAD ad3923e):

    "Declined to add 8 section(s) automatically ("Proposal must be received
     no later than August 3, 2026 by 3:00 P.M. (ET)", "Proposal must be
     marked 'Marketing Plan' and submitted to specified address or email",
     "Include contractor's name(s)", "Include contact information (Address,
     phone, Fax, Email)", "Proposal must be valid for at least thirty (30)
     days after proposal due date", +3 more) (would add 8 section(s) to a
     22-section proposal in one pass — over the blast-radius guard...)"

Every one of those 8 items is a submission instruction or administrative
constraint, not a deliverable — nobody writes a proposal section titled with
a deadline. The blast-radius guard declined the whole pass only because 8
exceeded its cap; with 4 such items (or fewer) it would have silently added
them as [MANUAL FILL] stub sections, and worse, a blast-radius trip on a
mixed batch means a GENUINELY missing deliverable riding alongside the
administrative items gets declined too, exactly as this reproduction proves
below.

Fix: proposal_intelligence.assembler._classify_compliance_source now
recognizes administrative/submission-instruction phrasing (deadlines,
delivery/labelling instructions, validity windows, copy counts, format/page
rules, bare contact-field lists) and classifies them source=
"submission_instruction" — excluded from _ADD_ELIGIBLE_SOURCES same as
scored_criterion, but surfaced as its own advisory checklist
(advisory_submission_instructions) so the real obligations (like the August
3 deadline) stay visible rather than silently dropped.
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.models.rfp import RfpRecord
from app.services.proposal_intelligence.assembler import build_requirement_ledger
from app.services.proposal_intelligence.schemas import ComplianceItem
from app.services.proposal_rfp_compliance import reconcile_requirement_ledger


def _rfp(**overrides) -> RfpRecord:
    fields = dict(
        id="rfp-kvcc",
        title="KVCC Marketing Plan RFP",
        client="KVCC",
        dueDate="2026-08-03",
        receivedDate="2026-07-01",
        lastActivity="2026-08-05",
        lastActivityNote="note",
    )
    fields.update(overrides)
    return RfpRecord(**fields)


def _section(sid: str, title: str, content: str = "Real proposal content here.") -> ProposalSection:
    return ProposalSection(id=sid, title=title, content=content)


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-kvcc", sections=list(sections), updatedAt="2026-08-06T00:00:00Z"
    )


def _research(ledger: RequirementLedger | None) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="rfp-kvcc", requirementLedger=ledger, updatedAt="2026-08-06T00:00:00Z"
    )


# The five real administrative items verbatim from the KVCC banner.
REAL_ADMINISTRATIVE_ITEMS_FROM_INCIDENT = [
    "Proposal must be received no later than August 3, 2026 by 3:00 P.M. (ET)",
    "Proposal must be marked 'Marketing Plan' and submitted to specified address or email",
    "Include contractor's name(s)",
    "Include contact information (Address, phone, Fax, Email)",
    "Proposal must be valid for at least thirty (30) days after proposal due date",
]


class AdministrativeItemsAreNeverAutoAddedTests(unittest.TestCase):
    def test_none_of_the_five_real_administrative_items_are_auto_added(self) -> None:
        compliance_items = [
            ComplianceItem(id=f"admin-{i}", requirement=text, mandatory=True)
            for i, text in enumerate(REAL_ADMINISTRATIVE_ITEMS_FROM_INCIDENT)
        ]
        ledger = build_requirement_ledger(compliance_items, [], [])
        # Confirm the classifier actually produced the defect condition
        # (all five classified administrative, none required_content) before
        # asserting on reconcile behavior.
        sources = {r.text: r.source for r in ledger.requirements}
        for text in REAL_ADMINISTRATIVE_ITEMS_FROM_INCIDENT:
            self.assertEqual(sources[text], "submission_instruction", text)

        draft = _draft(_section("s1", "Approach"))
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertEqual(
            result.applied_additions,
            [],
            "an administrative submission instruction must never be auto-added",
        )
        self.assertEqual(len(result.draft.sections), 1, "no stub sections were created")

    def test_each_administrative_item_is_reported_as_a_compliance_checklist_entry(
        self,
    ) -> None:
        compliance_items = [
            ComplianceItem(id=f"admin-{i}", requirement=text, mandatory=True)
            for i, text in enumerate(REAL_ADMINISTRATIVE_ITEMS_FROM_INCIDENT)
        ]
        ledger = build_requirement_ledger(compliance_items, [], [])
        draft = _draft(_section("s1", "Approach"))
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        advisory_texts = {a.requirement_text for a in result.advisory_submission_instructions}
        self.assertEqual(advisory_texts, set(REAL_ADMINISTRATIVE_ITEMS_FROM_INCIDENT))
        self.assertTrue(
            any(
                "submission-instructions" in line and "comply with" in line
                for line in result.logs
            ),
            f"expected a submission-instructions checklist log line, got: {result.logs}",
        )


class KvccReproductionTests(unittest.TestCase):
    """Required reproduction: a 22-section proposal whose compliance matrix
    contains the five real administrative items plus one genuine missing
    deliverable. The administrative items must never be added; the
    deliverable SHOULD be — proving the fix doesn't just suppress the
    administrative items but also stops them from poisoning the blast-radius
    guard against a real, unrelated missing section riding in the same
    batch."""

    def test_administrative_items_declined_genuine_deliverable_added(self) -> None:
        existing_sections = [_section(f"sec-{i}", f"Existing Section {i}") for i in range(22)]
        compliance_items = [
            ComplianceItem(id=f"admin-{i}", requirement=text, mandatory=True)
            for i, text in enumerate(REAL_ADMINISTRATIVE_ITEMS_FROM_INCIDENT)
        ] + [
            ComplianceItem(
                id="deliverable-1",
                requirement="Provide a detailed project schedule with milestones",
                mandatory=True,
            )
        ]
        ledger = build_requirement_ledger(compliance_items, [], [])
        draft = _draft(*existing_sections)
        research = _research(ledger)

        before_count = len(draft.sections)
        self.assertEqual(before_count, 22)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        after_count = len(result.draft.sections)

        # The five administrative items were never candidates for addition
        # at all (not even declined-by-blast-radius) — they are reported as
        # a compliance checklist instead.
        self.assertEqual(
            {a.requirement_text for a in result.advisory_submission_instructions},
            set(REAL_ADMINISTRATIVE_ITEMS_FROM_INCIDENT),
        )
        self.assertEqual(
            result.declined_addition_count,
            0,
            "with administrative items correctly excluded, only the genuine "
            "deliverable remains eligible — far under the blast-radius guard, "
            "so nothing is declined",
        )

        # The one genuine deliverable WAS added.
        self.assertEqual(len(result.applied_additions), 1)
        self.assertEqual(
            result.applied_additions[0].requirement_text,
            "Provide a detailed project schedule with milestones",
        )
        self.assertEqual(after_count, before_count + 1, "22 sections before, 23 after")

        added_titles = {s.title for s in result.draft.sections} - {
            s.title for s in existing_sections
        }
        self.assertEqual(added_titles, {"Provide a detailed project schedule with milestones"})


if __name__ == "__main__":
    unittest.main()
