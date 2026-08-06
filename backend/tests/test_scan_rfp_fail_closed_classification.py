"""Task 19: the FOURTH instance of the requirement-ledger ADD defect class
(see proposal_rfp_compliance.py's ``_ADD_ELIGIBLE_SOURCES`` module note for
the first three — scored_criterion, the blast-radius guard, then the
submission-instruction deny list). Root defect, verbatim from a real KVCC
proposal scan:

    "Added 3 missing required section(s) ("Comply with all applicable
     federal, state and local statutes, laws, codes, rules, regulations,
     ordinances and orders", "Identify trade secret exemptions from Maine's
     Freedom of Access Act at time of submission", "Submit contractual
     documents in 12 point black font on white background in single Word or
     PDF document")"

A font rule and a blanket statutory-compliance clause became client-facing
proposal sections. None of the three matched the existing
``_ADMIN_INSTRUCTION_PATTERNS`` deny list (deadlines, delivery/labelling
instructions, validity windows, copy counts, page limits, and a narrow
font/margin pattern that requires "N-point font" as a contiguous phrase —
"12 point black font" has "black" in between and does not match).

Root cause: ``_classify_compliance_source`` defaulted to "required_content"
(the add-eligible source) for anything it did not positively recognise as
administrative or a form. Adding a fifth deny-list pattern set fixes only
today's three phrasings; RFP prose is unbounded.

Fix: the default is inverted. A compliance-matrix item is add-eligible ONLY
when it positively reads as a narrative deliverable — an authoring verb
addressed to the vendor, or a deliverable noun naming a standard proposal
section (``_looks_like_narrative_deliverable`` in
proposal_intelligence/assembler.py). Everything else — including every
future phrasing nobody has anticipated — falls through to
"submission_instruction": still visible on the compliance checklist, never
silently turned into a section.
"""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.models.rfp import RfpRecord
from app.services.proposal_intelligence.assembler import build_requirement_ledger
from app.services.proposal_intelligence.schemas import ComplianceItem
from app.services.proposal_rfp_compliance import reconcile_requirement_ledger

# ---------------------------------------------------------------------------
# The two required lists, verbatim from the live incident and from the task.
# ---------------------------------------------------------------------------

# All 8 are real, from this user's live RFPs. None may ever classify as
# "required_content" (add-eligible) — three are the live-incident items that
# slipped through the pre-existing deny list; five are the third-instance
# administrative items the deny list already caught (kept here so this file
# is a complete verbatim record of both defect rounds in one place).
NON_ADDABLE_REQUIREMENTS = [
    "Comply with all applicable federal, state and local statutes, laws, "
    "codes, rules, regulations, ordinances and orders",
    "Identify trade secret exemptions from Maine's Freedom of Access Act "
    "at time of submission",
    "Submit contractual documents in 12 point black font on white "
    "background in single Word or PDF document",
    "Proposal must be received no later than August 3, 2026 by 3:00 P.M. (ET)",
    "Proposal must be marked 'Marketing Plan' and submitted to specified "
    "address or email",
    "Include contractor's name(s)",
    "Include contact information (Address, phone, Fax, Email)",
    "Proposal must be valid for at least thirty (30) days after proposal "
    "due date",
]

# All 9 are real, from this user's live RFPs. Every one must stay addable
# (or, for the W-9, stay classified "form" — a separate, already-working
# category the fix must not disturb).
ADDABLE_REQUIREMENTS = [
    "Provide a detailed project schedule with milestones",
    "Describe your approach to content migration",
    "Provide three client references with contact information",
    "Include a cover letter signed by an authorized representative",
    "Submit a completed and signed W-9 form",
    "Provide a summary of the qualifications and experience of each "
    "proposed team member",
    "Describe your firm's technical approach to meeting the requirements "
    "in Section 3",
    # Round-2 additions: the first cut of the positive signal list was too
    # tight and swallowed these two genuine deliverables onto the compliance
    # checklist. Both are real proposal SECTIONS a vendor must write/assemble.
    "Provide information about your firm",
    "Key Personnel Matrix",
]

_FORM_TEXT = "Submit a completed and signed W-9 form"


def _rfp(**overrides) -> RfpRecord:
    fields = dict(
        id="rfp-kvcc-fail-closed",
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
        rfpId="rfp-kvcc-fail-closed", sections=list(sections),
        updatedAt="2026-08-06T00:00:00Z",
    )


def _research(ledger: RequirementLedger | None) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="rfp-kvcc-fail-closed", requirementLedger=ledger,
        updatedAt="2026-08-06T00:00:00Z",
    )


class FreshClassificationTests(unittest.TestCase):
    """Direct unit coverage: every required string, classified fresh via
    build_requirement_ledger (the real Phase 2 path), asserted explicitly —
    so the next unanticipated phrasing fails safe instead of shipping."""

    def _source_for(self, requirement: str) -> str:
        ledger = build_requirement_ledger(
            [ComplianceItem(id="c1", requirement=requirement, mandatory=True)], [], []
        )
        return ledger.requirements[0].source

    def test_comply_with_all_applicable_statutes_is_not_addable(self) -> None:
        self.assertEqual(
            self._source_for(NON_ADDABLE_REQUIREMENTS[0]), "submission_instruction"
        )

    def test_identify_trade_secret_exemptions_is_not_addable(self) -> None:
        self.assertEqual(
            self._source_for(NON_ADDABLE_REQUIREMENTS[1]), "submission_instruction"
        )

    def test_twelve_point_font_rule_is_not_addable(self) -> None:
        self.assertEqual(
            self._source_for(NON_ADDABLE_REQUIREMENTS[2]), "submission_instruction"
        )

    def test_hard_deadline_is_not_addable(self) -> None:
        self.assertEqual(
            self._source_for(NON_ADDABLE_REQUIREMENTS[3]), "submission_instruction"
        )

    def test_labelling_and_delivery_instruction_is_not_addable(self) -> None:
        self.assertEqual(
            self._source_for(NON_ADDABLE_REQUIREMENTS[4]), "submission_instruction"
        )

    def test_bare_contractor_name_field_is_not_addable(self) -> None:
        self.assertEqual(
            self._source_for(NON_ADDABLE_REQUIREMENTS[5]), "submission_instruction"
        )

    def test_bare_contact_information_field_is_not_addable(self) -> None:
        self.assertEqual(
            self._source_for(NON_ADDABLE_REQUIREMENTS[6]), "submission_instruction"
        )

    def test_proposal_validity_window_is_not_addable(self) -> None:
        self.assertEqual(
            self._source_for(NON_ADDABLE_REQUIREMENTS[7]), "submission_instruction"
        )

    def test_project_schedule_deliverable_is_addable(self) -> None:
        self.assertEqual(
            self._source_for(ADDABLE_REQUIREMENTS[0]), "required_content"
        )

    def test_describe_your_approach_is_addable(self) -> None:
        self.assertEqual(
            self._source_for(ADDABLE_REQUIREMENTS[1]), "required_content"
        )

    def test_references_with_contact_information_is_addable(self) -> None:
        self.assertEqual(
            self._source_for(ADDABLE_REQUIREMENTS[2]), "required_content"
        )

    def test_signed_cover_letter_is_addable(self) -> None:
        self.assertEqual(
            self._source_for(ADDABLE_REQUIREMENTS[3]), "required_content"
        )

    def test_signed_w9_stays_a_form_not_administrative_or_required_content(self) -> None:
        self.assertEqual(self._source_for(ADDABLE_REQUIREMENTS[4]), "form")

    def test_qualifications_and_experience_summary_is_addable(self) -> None:
        self.assertEqual(
            self._source_for(ADDABLE_REQUIREMENTS[5]), "required_content"
        )

    def test_describe_your_firms_technical_approach_is_addable(self) -> None:
        self.assertEqual(
            self._source_for(ADDABLE_REQUIREMENTS[6]), "required_content"
        )

    def test_provide_information_about_your_firm_is_addable(self) -> None:
        self.assertEqual(
            self._source_for(ADDABLE_REQUIREMENTS[7]), "required_content"
        )

    def test_key_personnel_matrix_is_addable(self) -> None:
        self.assertEqual(
            self._source_for(ADDABLE_REQUIREMENTS[8]), "required_content"
        )

    def test_every_non_addable_requirement_is_covered(self) -> None:
        """Guard against silently dropping an item from the required list."""
        self.assertEqual(len(NON_ADDABLE_REQUIREMENTS), 8)

    def test_every_addable_requirement_is_covered(self) -> None:
        self.assertEqual(len(ADDABLE_REQUIREMENTS), 9)


class HardBoundaryTests(unittest.TestCase):
    """The boundary the round-2 widening must not cross: a bare identity/
    contact FIELD inside a section vs a SECTION about the firm. Both sides
    pinned explicitly, because widening the positive signals to recognise
    "information about your firm" is exactly the change that could have
    swallowed "Include contact information" back into addability.

    Held by two independent mechanisms: _ADMIN_INCLUDE_FIELD_RE runs BEFORE
    the narrative test and catches the bare-field phrasings outright, and
    the "information"/"details"/"list"/"statement" nouns are anchored to a
    following "about|on|regarding|of" so a bare "contact information" can
    never reach them anyway."""

    def _source_for(self, requirement: str) -> str:
        ledger = build_requirement_ledger(
            [ComplianceItem(id="c1", requirement=requirement, mandatory=True)], [], []
        )
        return ledger.requirements[0].source

    def test_a_bare_contractor_name_field_is_a_field_not_a_section(self) -> None:
        self.assertEqual(
            self._source_for("Include contractor's name(s)"), "submission_instruction"
        )

    def test_a_bare_contact_information_field_is_a_field_not_a_section(self) -> None:
        self.assertEqual(
            self._source_for("Include contact information (Address, phone, Fax, Email)"),
            "submission_instruction",
        )

    def test_information_about_your_firm_is_a_section_not_a_field(self) -> None:
        self.assertEqual(
            self._source_for("Provide information about your firm"), "required_content"
        )

    def test_the_two_sides_of_the_boundary_classify_differently(self) -> None:
        """Stated as one assertion so a future widening that collapses the
        boundary fails here loudly, not just in one of the two tests above."""
        self.assertNotEqual(
            self._source_for("Include contact information (Address, phone, Fax, Email)"),
            self._source_for("Provide information about your firm"),
        )


class KvccLiveIncidentReproductionTests(unittest.TestCase):
    """Required reproduction: the exact banner clause from the live incident
    must never recur. Reconciling a fresh ledger built from the three
    live-incident items must add zero sections and report all three on the
    compliance checklist instead."""

    def test_none_of_the_three_live_incident_items_are_auto_added(self) -> None:
        live_incident_items = NON_ADDABLE_REQUIREMENTS[:3]
        compliance_items = [
            ComplianceItem(id=f"kvcc-{i}", requirement=text, mandatory=True)
            for i, text in enumerate(live_incident_items)
        ]
        ledger = build_requirement_ledger(compliance_items, [], [])
        sources = {r.text: r.source for r in ledger.requirements}
        for text in live_incident_items:
            self.assertEqual(sources[text], "submission_instruction", text)

        draft = _draft(_section("s1", "Approach"))
        research = _research(ledger)
        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertEqual(
            result.applied_additions,
            [],
            "a font rule and a statutory-compliance clause must never become "
            "client-facing sections",
        )
        self.assertEqual(len(result.draft.sections), 1, "no stub sections were created")
        advisory_texts = {a.requirement_text for a in result.advisory_submission_instructions}
        self.assertEqual(advisory_texts, set(live_incident_items))


class StaleLedgerReclassificationTests(unittest.TestCase):
    """BEFORE-REPORTING-DONE requirement: a ledger persisted with the OLD
    labels (every item source="required_content" — the user's real
    pre-fix state, since every real proposal's ledger predates this fix) must
    have all 8 non-addable items reclassified to the compliance checklist and
    all 7 addable items remain addable, on the very next Scan-RFP click —
    with zero new LLM calls (reconcile_requirement_ledger and
    _reclassify_persisted_ledger are pure Python)."""

    def _stale_ledger(self) -> RequirementLedger:
        requirements = [
            LedgerRequirement(
                id=f"non-addable-{i}", text=text, source="required_content",
                mandatory=True, satisfiedBy=[],
            )
            for i, text in enumerate(NON_ADDABLE_REQUIREMENTS)
        ] + [
            LedgerRequirement(
                id=f"addable-{i}", text=text, source="required_content",
                mandatory=True, satisfiedBy=[],
            )
            for i, text in enumerate(ADDABLE_REQUIREMENTS)
            if text != _FORM_TEXT
        ] + [
            # The W-9 form item was already correctly labelled "form" before
            # this fix (that classification predates and is untouched by
            # this change) — persisted as such, exactly like a real ledger.
            LedgerRequirement(
                id="addable-form", text=_FORM_TEXT, source="form",
                mandatory=True, satisfiedBy=[],
            )
        ]
        return RequirementLedger(requirements=requirements)

    def test_stale_ledger_reclassifies_all_15_items_correctly_on_reconcile(self) -> None:
        ledger = self._stale_ledger()
        draft = _draft(_section("s1", "Approach"))
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        self.assertIsNotNone(
            result.built_ledger,
            "a stale required_content label must be corrected and handed back "
            "for the caller to persist",
        )
        corrected_sources = {r.text: r.source for r in result.built_ledger.requirements}

        for text in NON_ADDABLE_REQUIREMENTS:
            self.assertEqual(
                corrected_sources[text], "submission_instruction",
                f"non-addable item reclassified wrong: {text!r}",
            )
        for text in ADDABLE_REQUIREMENTS:
            expected = "form" if text == _FORM_TEXT else "required_content"
            self.assertEqual(
                corrected_sources[text], expected,
                f"addable item reclassified wrong: {text!r}",
            )

        # All 8 non-addable items land on the compliance checklist.
        advisory_texts = {a.requirement_text for a in result.advisory_submission_instructions}
        self.assertEqual(advisory_texts, set(NON_ADDABLE_REQUIREMENTS))

        # All 7 addable items (6 required_content + 1 form) were genuinely
        # missing (satisfiedBy=[]) and so were auto-added as sections — never
        # declined, since 7 additions on a 1-section draft trips neither the
        # absolute cap (5) alone... it actually exceeds 5, so confirm via the
        # blast-radius accounting instead of assuming unconditional success.
        added_or_declined = {a.requirement_text for a in result.applied_additions} | set(
            result.declined_addition_titles
        )
        self.assertEqual(added_or_declined, set(ADDABLE_REQUIREMENTS))
        # Whichever path (applied or declined-by-blast-radius), no addable
        # item may ever appear on the non-addable compliance checklist.
        self.assertFalse(added_or_declined & advisory_texts)

    def test_reclassification_is_idempotent_on_a_second_scan(self) -> None:
        ledger = self._stale_ledger()
        draft = _draft(_section("s1", "Approach"))
        research = _research(ledger)

        first = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())
        self.assertIsNotNone(first.built_ledger)

        research_after_first_scan = _research(first.built_ledger)
        second = reconcile_requirement_ledger(
            draft=first.draft, research=research_after_first_scan, rfp=_rfp()
        )

        self.assertIsNone(
            second.built_ledger,
            "already-correct source classifications must not be reported as "
            "changed or re-persisted on a second scan",
        )


class BannerClauseTests(unittest.TestCase):
    """Reproduces the exact banner clause the frontend's buildScanRfpBanner
    (frontend/src/lib/proposal-scan-report.ts) renders from these backend
    counts, so the fix is verified end-to-end rather than just at the
    classifier. The template (namedList, MAX_NAMED_TITLES=5) is reproduced
    here verbatim since that module is pure TypeScript and not importable
    from a Python test."""

    _MAX_NAMED_TITLES = 5

    def _named_list(self, titles: list[str]) -> str:
        names = [t for t in titles if t]
        if not names:
            return ""
        shown = names[: self._MAX_NAMED_TITLES]
        quoted = ", ".join(f'"{t}"' for t in shown)
        extra = len(names) - len(shown)
        return f" ({quoted}, +{extra} more)" if extra > 0 else f" ({quoted})"

    def test_banner_never_reports_an_added_section_for_the_kvcc_incident(self) -> None:
        live_incident_items = NON_ADDABLE_REQUIREMENTS[:3]
        compliance_items = [
            ComplianceItem(id=f"kvcc-{i}", requirement=text, mandatory=True)
            for i, text in enumerate(live_incident_items)
        ]
        ledger = build_requirement_ledger(compliance_items, [], [])
        draft = _draft(_section("s1", "Approach"))
        research = _research(ledger)

        result = reconcile_requirement_ledger(draft=draft, research=research, rfp=_rfp())

        added = len(result.applied_additions)
        self.assertEqual(added, 0)

        submission_count = len(result.advisory_submission_instructions)
        submission_titles = [
            a.requirement_text for a in result.advisory_submission_instructions
        ]
        clause = (
            f"{submission_count} submission requirement(s) to comply with"
            f"{self._named_list(submission_titles)}"
        )
        expected_clause = (
            '3 submission requirement(s) to comply with ('
            '"Comply with all applicable federal, state and local statutes, '
            'laws, codes, rules, regulations, ordinances and orders", '
            '"Identify trade secret exemptions from Maine\'s Freedom of '
            'Access Act at time of submission", '
            '"Submit contractual documents in 12 point black font on white '
            'background in single Word or PDF document")'
        )
        self.assertEqual(clause, expected_clause)
        self.assertNotIn("Added", clause)


if __name__ == "__main__":
    unittest.main()
