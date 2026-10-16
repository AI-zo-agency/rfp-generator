"""Repairs for the HomeFront, Tacoma, Truckee, and Wyoming review notes.

These tests use the stored failure shapes. They do not call a model.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RfpSectionMap,
)
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.models.rfp import RfpRecord
from app.services.proposal_brand_voice import (
    INTERESTING_PROPOSAL_ANSWER_BLOCK,
    PROCUREMENT_REGISTER_BLOCK,
    brand_voice_is_usable,
    ensure_drafting_brand_voice,
    narrative_opening_problem,
)
from app.services.proposal_client_table_guard import collapse_incomplete_client_tables
from app.services.proposal_coverage_binding import (
    coverage_haystack,
    is_stub_answer,
    rebind_ledger_to_draft,
    rebind_ledger_to_section_ids,
    unbound_requirement_tickets,
)
from app.services.proposal_ending_report import build_proposal_ending_report
from app.services.proposal_intelligence.assembler import (
    amend_outline_for_missing_requirements,
)
from app.services.proposal_intelligence.opportunity_extract.compliance_atomic_split import (
    atomize_compliance_items,
)
from app.services.proposal_presubmit_review import _scan_voice
from app.services.proposal_voice_enforcement import (
    apply_writing_standards_mechanics,
    hard_voice_ban_hits,
)
from app.services.proposal_voice_llm import _INSTRUCTIONS, _PROCUREMENT_EXTRA
from app.services.proposal_zero_fabrication import apply_zero_fabrication_guards

HOMEFRONT_OPEN = (
    "We'll build HomeFront's site so staff can run it, residents can find "
    "documents quickly, and every file, database, and page can leave with "
    "HomeFront at any time."
)
TACOMA_PHILOSOPHY = (
    "Our media buying philosophy centers on matching platform and modality "
    "to the behavioral realities of each target audience, then validating "
    "performance through continuous measurement."
)
TACOMA_PLANS = (
    "Our agency plans and places media across the full spectrum of platforms "
    "and outlets necessary to reach the diverse populations of Tacoma-Pierce County."
)
TRUCKEE_OPEN = (
    "We founded zö agency on August 21, 2013. By 2026 that puts us at thirteen "
    "years of running a design and marketing studio."
)
MARICOPA_OPEN = (
    "We bought media for Maricopa County across public health, judicial services, "
    "libraries, and the sheriff's office under one standing engagement."
)
WYOMING_OPEN = (
    "We'll run this program as seven linked workstreams, each with a single "
    "WDH-PHD approval gate before the next one opens."
)

HOMEFRONT_TABLE = """
Per Table 4, zö agency submits the following public-sector clients.

| Client | Contact | Phone | Email | Scope (summary) | Dates of service |
|---|---|---|---|---|---|
| City of Bend | Mickie Derting | [MANUAL FILL: Sonja — phone] | mderting@bendoregon.gov | Brand audit | [MANUAL FILL: Sonja — service dates] |
| Deschutes County | [MANUAL FILL: Sonja — contact name] | [MANUAL FILL: Sonja — phone] | [MANUAL FILL: Sonja — email] | Brand modernization | [MANUAL FILL: Sonja — service dates] |
| City of Bend Water | Dana Wells | 541-555-0100 | dana@bendoregon.gov | Conservation campaign | 2019-2024 |
""".strip()


def _section(sid: str, title: str, content: str) -> ProposalSection:
    return ProposalSection(
        id=sid,
        title=title,
        content=content,
        status="generated",
        mode="write",
    )


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="r1",
        title="Tobacco Prevention Branding",
        client="Wyoming Health Department",
        sector="Public Sector",
        dueDate="2026-10-26",
        receivedDate="2026-09-01",
        lastActivity="2026-10-05",
        lastActivityNote="test",
    )


class OpeningVoiceTests(unittest.TestCase):
    def test_homefront_piled_so_is_flagged(self) -> None:
        self.assertEqual(narrative_opening_problem(HOMEFRONT_OPEN), "piled-so-opening")

    def test_third_person_openers_are_not_a_special_case_list(self) -> None:
        # The drafting prompt bans third-person agency openers for every RFP.
        # There is no phrase list for one buyer's sentences.
        self.assertIsNone(narrative_opening_problem(TACOMA_PHILOSOPHY))
        self.assertIsNone(narrative_opening_problem(TACOMA_PLANS))

    def test_concrete_opens_are_left_alone(self) -> None:
        self.assertIsNone(narrative_opening_problem(TRUCKEE_OPEN))
        self.assertIsNone(narrative_opening_problem(MARICOPA_OPEN))
        self.assertIsNone(narrative_opening_problem(WYOMING_OPEN))

    def test_single_outcome_so_clause_is_left_alone(self) -> None:
        text = "We'll ship the homepage so residents can find forms, minutes, and notices."
        self.assertIsNone(narrative_opening_problem(text))

    def test_heading_is_skipped_and_the_prose_sentence_is_read(self) -> None:
        text = "## Proposed Services\n\n" + HOMEFRONT_OPEN
        self.assertEqual(narrative_opening_problem(text), "piled-so-opening")

    def test_prompt_asks_for_one_promise(self) -> None:
        self.assertIn("one promise", INTERESTING_PROPOSAL_ANSWER_BLOCK)
        self.assertNotIn("flat — and stop", INTERESTING_PROPOSAL_ANSWER_BLOCK)
        self.assertIn("third-person", _INSTRUCTIONS.casefold())
        self.assertNotIn("philosophy centers on", _INSTRUCTIONS)
        self.assertIn("restating the buyer's problem", _INSTRUCTIONS)
        self.assertIn("not a preference", _INSTRUCTIONS)
        self.assertIn("first person", _PROCUREMENT_EXTRA.casefold())
        self.assertIn("first person", PROCUREMENT_REGISTER_BLOCK.casefold())
        self.assertNotIn("formal third-person", PROCUREMENT_REGISTER_BLOCK.casefold())


class AgencySubjectTests(unittest.TestCase):
    def test_price_prose_becomes_first_person(self) -> None:
        text = (
            "zö agency proposes a fixed price for the contract term.\n"
            "zö agency bills a blended rate of $275 an hour.\n"
            "When the form asks for a rate, zö agency gives a blended rate.\n"
            "zö agency carries its own proposal preparation costs.\n"
            "zö agency will hold these terms for 180 days."
        )
        cleaned = apply_writing_standards_mechanics(text)
        self.assertIn("We propose a fixed price", cleaned)
        self.assertIn("We bill a blended rate", cleaned)
        self.assertIn("we give a blended rate", cleaned)
        self.assertIn("We carry our own proposal preparation costs", cleaned)
        self.assertIn("We will hold these terms", cleaned)
        self.assertNotIn("zö agency proposes", cleaned)
        self.assertEqual(hard_voice_ban_hits(text)[0][0], "agency-subject")
        self.assertEqual(hard_voice_ban_hits(cleaned), [])

    def test_form_labels_and_certifications_stay(self) -> None:
        text = (
            "Submitted by\tzö agency\n"
            "zö agency certifies that neither the firm nor its principals are debarred."
        )
        self.assertEqual(apply_writing_standards_mechanics(text), text)
        self.assertEqual(hard_voice_ban_hits(text), [])

    def test_presubmit_flags_the_homefront_sentence(self) -> None:
        draft = ProposalDraft(
            rfpId="hf",
            updatedAt="t",
            sections=[_section("rfp-sec-5", "Proposed Services", HOMEFRONT_OPEN)],
        )
        messages = [issue.message for issue in _scan_voice(draft)]
        self.assertTrue(any("piled-so-opening" in message for message in messages))


class ClientTableTests(unittest.TestCase):
    def test_homefront_matrix_drops_incomplete_rows_and_keeps_the_complete_one(self) -> None:
        text, changed = collapse_incomplete_client_tables(HOMEFRONT_TABLE)
        self.assertTrue(changed)
        self.assertIn("City of Bend Water", text)
        self.assertIn("Dana Wells", text)
        self.assertNotIn("Deschutes County", text)
        self.assertNotIn("Mickie Derting", text)
        self.assertIn("MANUAL FILL: Sonja", text)
        self.assertIn("public-sector clients", text)

    def test_workstream_table_is_not_a_client_list(self) -> None:
        table = (
            "| Workstream | What we'll do | HomeFront gate |\n"
            "|---|---|---|\n"
            "| Planning | Inventory pages | Kickoff |\n"
            "| Design | [MANUAL FILL: Sonja — palette] | Approval |\n"
            "| Build | [MANUAL FILL: Sonja — cms] | Demo |\n"
        )
        text, changed = collapse_incomplete_client_tables(table)
        self.assertFalse(changed)
        self.assertIn("Workstream", text)

    def test_complete_client_table_stays(self) -> None:
        table = (
            "| Client | Contact | Email |\n"
            "|---|---|---|\n"
            "| City of Bend | Mickie Derting | mderting@bendoregon.gov |\n"
            "| City of Medford | Ann Lee | ann@medford.gov |\n"
        )
        text, changed = collapse_incomplete_client_tables(table)
        self.assertFalse(changed)
        self.assertIn("Ann Lee", text)

    def test_half_the_rows_incomplete_does_not_collapse(self) -> None:
        table = (
            "| Client | Contact | Email |\n"
            "|---|---|---|\n"
            "| City of Bend | Mickie Derting | mderting@bendoregon.gov |\n"
            "| City of Medford | Ann Lee | [MANUAL FILL: Sonja — email] |\n"
        )
        _, changed = collapse_incomplete_client_tables(table)
        self.assertFalse(changed)

    def test_guard_stack_rewrites_the_section(self) -> None:
        draft = ProposalDraft(
            rfpId="hf",
            updatedAt="t",
            sections=[_section("tab-7", "Tab 7 — Client Information", HOMEFRONT_TABLE)],
        )
        updated, report = apply_zero_fabrication_guards(draft)
        self.assertNotIn("Deschutes County", updated.sections[0].content)
        self.assertTrue(any("client-reference" in line for line in report.logs))


class CoverageBindingTests(unittest.TestCase):
    def test_stub_form_note_is_not_an_answer(self) -> None:
        stub = (
            "Required form per the RFP submission package.\n\n"
            "[DESIGNER NOTE: Attach the buyer's required PDF/form here.]\n\n"
            "[MANUAL FILL: Sonja, attach signed/completed file before submit.]"
        )
        self.assertTrue(is_stub_answer(stub))
        self.assertFalse(
            is_stub_answer(
                "We'll build the quit-tobacco website on a non-proprietary CMS and "
                "hand WDH-PHD the admin login, the architecture notes, and the "
                "referral tracking setup at acceptance. " * 4
            )
        )

    def test_ending_report_ignores_a_stub_that_repeats_the_requirement(self) -> None:
        stub = _section(
            "rfp-closing-confidential_information_statement",
            "Confidential Information Justification Statement",
            "## Confidential Information Justification Statement\n\n"
            "Required disclosure / exemption affidavit per the RFP submission package.\n\n"
            "[DESIGNER NOTE: Attach the buyer's required PDF/form here.]",
        )
        approach = _section(
            "rfp-sec-4",
            "Proposer's Project Approach",
            (
                "Proposer's project approach for the tobacco website starts with the "
                "quit-tobacco brand and the referral path into treatment. "
            )
            * 6,
        )
        draft = ProposalDraft(rfpId="wy", updatedAt="t", sections=[stub, approach])
        research = ProposalResearchCache(
            rfpId="wy",
            updatedAt="t",
            rfpSections=[
                RfpSectionMap(
                    id=stub.id,
                    title=stub.title,
                    requirements=["Address Confidential Information Justification Statement per RFP"],
                ),
                RfpSectionMap(
                    id=approach.id,
                    title=approach.title,
                    requirements=["Submit the proposer project approach for the tobacco website"],
                ),
            ],
        )
        haystack = coverage_haystack(draft.sections)
        self.assertNotIn("Confidential Information Justification", haystack)
        self.assertIn("tobacco website", haystack)
        report = build_proposal_ending_report(rfp=_rfp(), draft=draft, research=research)
        by_req = {row.requirement: row.covered for row in report.requirement_statuses}
        self.assertFalse(by_req["Address Confidential Information Justification Statement per RFP"])
        self.assertTrue(by_req["Submit the proposer project approach for the tobacco website"])

    def test_ghost_ledger_ids_follow_the_parent_section_only(self) -> None:
        approach = _section(
            "rfp-sec-4",
            "3.4 — Proposer's Project Approach",
            "We'll run the quit-tobacco brand, the website, and the campaign as one team. " * 8,
        )
        background = _section(
            "rfp-sec-2",
            "3.2 — Proposer Background and Experience",
            "We built zö agency around brand, web, and public education. " * 8,
        )
        ledger = RequirementLedger(
            requirements=[
                LedgerRequirement(
                    id="C-24",
                    text="Describe proposed technology hosting security measures and physical location.",
                    source="required_content",
                    satisfiedBy=["rfp-sec-4"],
                ),
                LedgerRequirement(
                    id="C-24-b",
                    text="Describe FIPS 140-3 controls for the hosted environment.",
                    source="required_content",
                    satisfiedBy=["ledger-C-35"],
                    atomOf="C-24",
                ),
                LedgerRequirement(
                    id="C-37",
                    text="Provide three business references for the most relevant projects.",
                    source="required_content",
                    satisfiedBy=["ledger-C-37"],
                ),
                LedgerRequirement(
                    id="C-02",
                    text="Submit the proposal by the published deadline.",
                    source="submission_instruction",
                    satisfiedBy=["ledger-C-02"],
                ),
            ]
        )
        sections = [approach, background]
        tickets = unbound_requirement_tickets(ledger, sections)
        self.assertEqual([t["sectionId"] for t in tickets], [approach.id])
        unmet = " ".join(tickets[0]["unmetRequirements"]).casefold()
        self.assertIn("fips", unmet)
        self.assertNotIn("references", unmet)
        self.assertEqual(tickets[0]["source"], "unbound-requirement")
        rebound = rebind_ledger_to_draft(ledger, sections)
        by_id = {r.id: r for r in rebound.requirements}
        self.assertEqual(by_id["C-24-b"].satisfied_by, [])
        self.assertEqual(by_id["C-37"].satisfied_by, [])
        # Submission instructions are not rewritten as narrative answers.
        self.assertEqual(by_id["C-02"].satisfied_by, [])

    def test_an_answer_already_in_the_draft_is_rebound_not_reticketed(self) -> None:
        section = _section(
            "rfp-sec-5",
            "Proposed Services",
            "The quality assurance program checks each module before HomeFront acceptance. " * 6,
        )
        ledger = RequirementLedger(
            requirements=[
                LedgerRequirement(
                    id="C-18",
                    text="Describe the proposed quality assurance program.",
                    source="required_content",
                    satisfiedBy=["ledger-C-18"],
                )
            ]
        )
        self.assertEqual(unbound_requirement_tickets(ledger, [section]), [])
        rebound = rebind_ledger_to_draft(ledger, [section])
        self.assertEqual(rebound.requirements[0].satisfied_by, ["rfp-sec-5"])

    def test_outline_rebind_forgets_dropped_tabs(self) -> None:
        ledger = RequirementLedger(
            requirements=[
                LedgerRequirement(
                    id="C-24",
                    text="Describe hosting.",
                    source="required_content",
                    satisfiedBy=["ledger-C-24", "rfp-sec-4"],
                )
            ]
        )
        rebound = rebind_ledger_to_section_ids(ledger, {"rfp-sec-4"})
        self.assertEqual(rebound.requirements[0].satisfied_by, ["rfp-sec-4"])

    def test_atoms_do_not_each_mint_a_tab(self) -> None:
        outline = [
            RfpSectionMap(id="rfp-sec-4", title="Project Approach", requirements=["approach"]),
        ]
        ledger = RequirementLedger(
            requirements=[
                LedgerRequirement(
                    id="C-24",
                    text="Describe facility access controls for the hosting center and the support desk.",
                    source="required_content",
                    mandatory=True,
                ),
                LedgerRequirement(
                    id="C-24-b",
                    text="Describe FIPS 140-3 controls for the hosted environment.",
                    source="required_content",
                    mandatory=True,
                    atomOf="C-24",
                ),
                LedgerRequirement(
                    id="C-24-c",
                    text="Describe HIPAA training for new hires and subcontractors on this contract.",
                    source="required_content",
                    mandatory=True,
                ),
            ]
        )
        amended = amend_outline_for_missing_requirements(ledger, outline)
        ids = [s.id for s in amended]
        self.assertIn("ledger-C-24", ids)
        self.assertNotIn("ledger-C-24-b", ids)
        self.assertNotIn("ledger-C-24-c", ids)

    def test_atomizer_marks_children(self) -> None:
        items = [
            {
                "id": "C-24",
                "requirement": (
                    "Describe hosting location, facility access controls, "
                    "and FIPS 140-3 encryption for the public site."
                ),
                "mandatory": True,
            }
        ]
        rows, fixes = atomize_compliance_items(items)
        self.assertTrue(fixes)
        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual(rows[0].get("atomOf") or "", "")
        self.assertTrue(all(row.get("atomOf") == "C-24" for row in rows[1:]))


class BrandVoiceEnsureTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_profile_is_synthesized_for_strict_rfp(self) -> None:
        self.assertFalse(brand_voice_is_usable(None))
        from app.models.proposal import ProposalBrandVoice

        self.assertFalse(brand_voice_is_usable(ProposalBrandVoice()))

        async def fake_fetch(**kwargs: object) -> str:
            return "We'll decide which pages launch first."

        async def fake_synth(state: dict) -> dict:
            return {
                "brand_voice": {
                    "tone": "direct",
                    "zoCoreVoice": "zö agency writes in first person.",
                    "kbZoVoice": state.get("kb_zo_voice") or "",
                    "rfpAdaptationNotes": "Wyoming tobacco program, plain language.",
                }
            }

        with (
            patch(
                "app.services.proposal_brand_voice.fetch_zo_voice_excerpt",
                new=AsyncMock(side_effect=fake_fetch),
            ),
            patch(
                "app.services.proposal_sections_graph._synthesize_proposal_voice",
                new=AsyncMock(side_effect=fake_synth),
            ),
        ):
            voice = await ensure_drafting_brand_voice(
                None,
                rfp_title="Tobacco Prevention Branding",
                rfp_client="Wyoming Health Department",
                rfp_sector="Public Sector",
                rfp_location="WY",
                rfp_context="Quit tobacco website and brand.",
            )
        self.assertEqual(voice.tone, "direct")
        self.assertIn("first person", voice.zo_core_voice)
        self.assertIn("pages launch first", voice.kb_zo_voice)
        self.assertTrue(brand_voice_is_usable(voice))

    async def test_usable_profile_is_not_resynthesized(self) -> None:
        from app.models.proposal import ProposalBrandVoice

        existing = ProposalBrandVoice(
            tone="warm",
            zoCoreVoice="Already synthesized for this RFP.",
        )
        with patch(
            "app.services.proposal_brand_voice.fetch_zo_voice_excerpt",
            new=AsyncMock(),
        ) as fetch:
            voice = await ensure_drafting_brand_voice(
                existing,
                rfp_title="t",
                rfp_client="c",
                rfp_sector="s",
                rfp_location=None,
                rfp_context="x",
            )
        fetch.assert_not_called()
        self.assertEqual(voice.tone, "warm")


if __name__ == "__main__":
    unittest.main()
