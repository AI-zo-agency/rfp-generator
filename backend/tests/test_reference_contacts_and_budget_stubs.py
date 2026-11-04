"""Reference tabs must reach real 06_WON contacts; budget criteria must not leave empty tabs."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import EvidenceItem, ProposalDraft, ProposalSection
from app.services.evidence_trust.client_list import ClientListEntry, ClientListRegistry
from app.services.proposal_client_table_guard import collapse_incomplete_client_tables
from app.services.proposal_draft_structure_stubs import (
    repair_prose_disguised_as_table_rows,
    section_needs_presubmit_fill,
)
from app.services.proposal_edge_case_guards import ensure_references_tabs_have_tables
from app.services.proposal_fulfill_rfp_structure import (
    RfpSectionSpec,
    ensure_missing_scored_section_stubs,
)
from app.services.proposal_integrity_guards import apply_reference_contact_evidence_guard
from app.services.proposal_intelligence.agents.retrieval_planner import (
    section_wants_client_references,
)
from app.services.proposal_intelligence.jit_retrieval import retrieve_for_section
from app.services.proposal_intelligence.schemas import RetrievalEntry
from app.services.proposal_manuscript import scrub_broken_reference_pipe_rows
from app.services.proposal_reference_contacts import (
    add_reference_contacts_to_corpus,
    clear_reference_contact_cache,
    load_won_reference_contacts,
    parse_reference_contacts,
)
from app.services.proposal_section_editor import (
    _mask_manual_fill_for_rewrite,
    _merge_bio_kb_into_blobs,
)

COST_BODY = "## Cost proposal\n\n| Task | Fee |\n|---|---|\n| A1 | $3,000 |\n\n" + "word " * 60


def _section(section_id: str, title: str, content: str) -> ProposalSection:
    return ProposalSection(id=section_id, title=title, content=content, status="generated")


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-test",
        sections=list(sections),
        updatedAt="2026-11-03T00:00:00Z",
    )


def _budget_stub(title: str = "Evaluation Criteria 3 — Project Budget") -> ProposalSection:
    return _section(
        "rfp-structure-evaluation-criteria-3-project-budget",
        title,
        f"[MANUAL FILL: Draft this RFP-required section, {title}]",
    )


def _weighted_spec(title: str) -> RfpSectionSpec:
    return RfpSectionSpec(rfp_title=title, evaluation_weight="25 points")


class BudgetCriterionStubTests(unittest.TestCase):
    def _titles_after(self, spec_title: str, *sections: ProposalSection) -> list[str]:
        out, _ = ensure_missing_scored_section_stubs(
            _draft(*sections), [_weighted_spec(spec_title)]
        )
        return [s.title for s in out.sections]

    def test_budget_criterion_mints_no_stub_when_fee_tab_is_drafted(self) -> None:
        titles = self._titles_after(
            "Evaluation Criteria 3 — Project Budget",
            _section("rfp-structure-cost-proposal", "Cost proposal", COST_BODY),
        )
        self.assertEqual(titles, ["Cost proposal"])

    def test_budget_criterion_still_mints_a_stub_with_no_fee_tab(self) -> None:
        titles = self._titles_after(
            "Evaluation Criteria 3 — Project Budget",
            _section("rfp-sec-1", "Implementation plan", "word " * 60),
        )
        self.assertIn("Evaluation Criteria 3 — Project Budget", titles)

    def test_budget_management_ask_is_a_real_section_not_a_fee_duplicate(self) -> None:
        titles = self._titles_after(
            "Budget Management Approach",
            _section("rfp-structure-cost-proposal", "Cost proposal", COST_BODY),
        )
        self.assertIn("Budget Management Approach", titles)

    def test_saved_empty_budget_stub_is_removed_beside_a_drafted_fee_tab(self) -> None:
        cost = _section("rfp-structure-cost-proposal", "Cost proposal", COST_BODY)
        out, logs = ensure_missing_scored_section_stubs(_draft(cost, _budget_stub()), [])
        self.assertEqual([s.id for s in out.sections], ["rfp-structure-cost-proposal"])
        self.assertEqual(out.sections[0].content, COST_BODY)
        self.assertTrue(logs)

    def test_only_budget_tab_is_kept_even_when_empty(self) -> None:
        stub = _budget_stub()
        out, _ = ensure_missing_scored_section_stubs(_draft(stub), [])
        self.assertEqual([s.id for s in out.sections], [stub.id])

    def test_budget_stub_holding_written_content_is_kept(self) -> None:
        cost = _section("rfp-structure-cost-proposal", "Cost proposal", COST_BODY)
        written = _budget_stub().model_copy(
            update={"content": _budget_stub().content + "\n\n" + "prose " * 120}
        )
        out, _ = ensure_missing_scored_section_stubs(_draft(cost, written), [])
        self.assertEqual(len(out.sections), 2)


LIST_WITH_TITLES = """
# REFERENCES

Please reach out to any of these references.

## zö agency

- Pat Lee, Parks Director,
  City of Bend, pat@bendoregon.gov | 541-555-0101
  Project: Municipal brand refresh (2017-2024)

Additional References Available Upon Request
"""

LIST_SINGLE_LINE = """
# REFERENCES

- Pat Lee, City of Bend Strategic Initiatives | pat@bendoregon.gov | 541-555-0199
- Ana Ruiz, City of Medford | ana@cityofmedford.org
"""


class _SupermemoryConfigured(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_reference_contact_cache()
        self.addCleanup(clear_reference_contact_cache)
        patcher = patch("app.services.supermemory.is_configured", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class ParseReferenceContactsTests(unittest.TestCase):
    def test_reads_two_line_entry_with_title_and_project(self) -> None:
        [contact] = parse_reference_contacts(LIST_WITH_TITLES)
        self.assertEqual(contact.name, "Pat Lee")
        self.assertEqual(contact.title, "Parks Director")
        self.assertEqual(contact.organization, "City of Bend")
        self.assertEqual(contact.email, "pat@bendoregon.gov")
        self.assertEqual(contact.phone, "541-555-0101")
        self.assertEqual(contact.project, "Municipal brand refresh (2017-2024)")

    def test_reads_single_line_entries_with_and_without_phone(self) -> None:
        contacts = parse_reference_contacts(LIST_SINGLE_LINE)
        self.assertEqual(
            [(c.name, c.organization, c.phone) for c in contacts],
            [
                ("Pat Lee", "City of Bend Strategic Initiatives", "541-555-0199"),
                ("Ana Ruiz", "City of Medford", ""),
            ],
        )

    def test_ignores_emails_outside_a_references_list(self) -> None:
        text = "Procurement officer: buyer@city.gov | 541-555-0111\n\nCompany overview."
        self.assertEqual(parse_reference_contacts(text), [])

    def test_ignores_agency_staff_inside_the_list(self) -> None:
        text = "# REFERENCES\n\n- Sonja Anderson, zö agency | sonja@zo.agency | 541-555-0100\n"
        self.assertEqual(parse_reference_contacts(text), [])


def _doc(file_name: str) -> dict:
    return {
        "id": file_name,
        "customId": f"drive:{file_name}",
        "title": file_name,
        "metadata": {"fileName": file_name},
    }


REGISTRY = ClientListRegistry(
    entries=[
        ClientListEntry(name="City of Bend", sector="Gov", work_type="Brand", public="Yes"),
        ClientListEntry(name="City of Medford", sector="Gov", work_type="Brand", public="Yes"),
        ClientListEntry(name="Thrive Guides", sector="Biz", work_type="Web", public="Confirm"),
    ]
)


class LoadWonReferenceContactsTests(_SupermemoryConfigured):
    async def _load(self, docs_by_name: dict[str, str]) -> list:
        async def content(*, custom_id: str = "", document_id: str = "") -> str:
            return docs_by_name[custom_id.removeprefix("drive:")]

        with (
            patch(
                "app.services.supermemory.list_all_container_documents",
                new=AsyncMock(return_value=[_doc(n) for n in docs_by_name]),
            ),
            patch("app.services.supermemory.get_document_content", new=content),
            patch(
                "app.services.proposal_reference_contacts.load_client_list_registry",
                new=AsyncMock(return_value=REGISTRY),
            ),
        ):
            return await load_won_reference_contacts(force_refresh=True)

    async def test_finalist_bid_and_rfp_documents_are_not_sources(self) -> None:
        contacts = await self._load(
            {
                "07_FIN_CityofX_Proposal_2026.pdf": LIST_SINGLE_LINE,
                "06_WON_CityofY_RFP_2026.pdf": LIST_SINGLE_LINE,
            }
        )
        self.assertEqual(contacts, [])

    async def test_newest_won_list_wins_and_older_list_fills_gaps(self) -> None:
        contacts = await self._load(
            {
                "06_WON_CityofOld_Proposal_2025.pdf": LIST_WITH_TITLES,
                "06_WON_CityofNew_Proposal_2026.pdf": LIST_SINGLE_LINE,
            }
        )
        pat = next(c for c in contacts if c.name == "Pat Lee")
        self.assertEqual(pat.phone, "541-555-0199")
        self.assertEqual(pat.title, "Parks Director")
        self.assertEqual(pat.project, "Municipal brand refresh (2017-2024)")
        self.assertEqual(sum(1 for c in contacts if c.name == "Pat Lee"), 1)
        self.assertEqual(pat.source, "06_WON_CityofNew_Proposal_2026.pdf")

    async def test_client_not_approved_for_public_use_is_left_out(self) -> None:
        text = (
            "# REFERENCES\n\n"
            "- Kim Roe, Thrive Guides | kim@thrive.example | 541-555-0150\n"
            "- Lou Poe, Unlisted Client | lou@unlisted.example | 541-555-0151\n"
            "- Ana Ruiz, City of Medford | ana@cityofmedford.org | 541-555-0152\n"
        )
        contacts = await self._load({"06_WON_CityofNew_Proposal_2026.pdf": text})
        self.assertEqual([c.name for c in contacts], ["Ana Ruiz"])

    async def test_failed_document_fetch_does_not_lose_other_lists(self) -> None:
        async def content(*, custom_id: str = "", document_id: str = "") -> str:
            if "Broken" in custom_id:
                raise RuntimeError("fetch failed")
            return LIST_SINGLE_LINE

        with (
            patch(
                "app.services.supermemory.list_all_container_documents",
                new=AsyncMock(
                    return_value=[
                        _doc("06_WON_Broken_Proposal_2026.pdf"),
                        _doc("06_WON_Good_Proposal_2025.pdf"),
                    ]
                ),
            ),
            patch("app.services.supermemory.get_document_content", new=content),
            patch(
                "app.services.proposal_reference_contacts.load_client_list_registry",
                new=AsyncMock(return_value=REGISTRY),
            ),
        ):
            contacts = await load_won_reference_contacts(force_refresh=True)
        self.assertEqual(len(contacts), 2)


class RetrievalTests(_SupermemoryConfigured):
    def _patches(self, retrieve: AsyncMock):
        return (
            patch(
                "app.services.proposal_intelligence.jit_retrieval.retrieve_for_question",
                new=retrieve,
            ),
            patch(
                "app.services.proposal_intelligence.jit_retrieval.load_client_list_registry",
                new=AsyncMock(return_value=ClientListRegistry()),
            ),
            patch(
                "app.services.supermemory.list_all_container_documents",
                new=AsyncMock(return_value=[_doc("06_WON_CityofNew_Proposal_2026.pdf")]),
            ),
            patch(
                "app.services.supermemory.get_document_content",
                new=AsyncMock(return_value=LIST_SINGLE_LINE),
            ),
            patch(
                "app.services.proposal_reference_contacts.load_client_list_registry",
                new=AsyncMock(return_value=REGISTRY),
            ),
        )

    async def _retrieve(self, title: str, query: str) -> tuple[list, str]:
        retrieve = AsyncMock(return_value=("", [], []))
        entry = RetrievalEntry.model_validate(
            {"sectionId": "rfp-x", "queries": [query], "requiredAssets": ["proof"]}
        )
        p = self._patches(retrieve)
        with p[0], p[1], p[2], p[3], p[4]:
            items = await retrieve_for_section(
                entry, rfp_client="City of Kirkland", section_title=title
            )
        return items, retrieve.await_args.args[0]

    async def test_reference_tab_gets_won_contacts_as_evidence(self) -> None:
        items, _ = await self._retrieve("References", "client references")
        self.assertIn("pat@bendoregon.gov", items[0].excerpt)
        self.assertIn("541-555-0199", items[0].excerpt)
        self.assertEqual(items[0].section_ids, ["rfp-x"])
        self.assertEqual(items[0].source, "06_WON_CityofNew_Proposal_2026.pdf")

    async def test_reference_tab_searches_with_the_plain_query(self) -> None:
        _, question = await self._retrieve("References", "client references contacts")
        self.assertEqual(question, "client references contacts")

    async def test_other_tabs_get_no_contacts_and_keep_the_full_question(self) -> None:
        items, question = await self._retrieve("Implementation plan", "campaign timeline")
        self.assertEqual(items, [])
        self.assertIn('Section: "Implementation plan"', question)

    def test_preference_tabs_are_not_reference_tabs(self) -> None:
        self.assertFalse(section_wants_client_references("New Mexico Preferences"))
        self.assertTrue(section_wants_client_references("Client References and Preferences"))


class SectionEditorContactsTests(_SupermemoryConfigured):
    async def _merge(self, title: str) -> tuple[str, str]:
        with (
            patch(
                "app.services.supermemory.list_all_container_documents",
                new=AsyncMock(return_value=[_doc("06_WON_CityofNew_Proposal_2026.pdf")]),
            ),
            patch(
                "app.services.supermemory.get_document_content",
                new=AsyncMock(return_value=LIST_SINGLE_LINE),
            ),
            patch(
                "app.services.proposal_reference_contacts.load_client_list_registry",
                new=AsyncMock(return_value=REGISTRY),
            ),
        ):
            return await _merge_bio_kb_into_blobs(
                _section("rfp-structure-x", title, "body"),
                kb_block="existing kb",
                fact_blob="existing facts",
            )

    async def test_improve_on_references_sees_the_contacts(self) -> None:
        kb, facts = await self._merge("References")
        self.assertIn("ana@cityofmedford.org", kb)
        self.assertIn("existing kb", kb)
        self.assertIn("ana@cityofmedford.org", facts)

    async def test_improve_on_other_tabs_is_unchanged(self) -> None:
        kb, facts = await self._merge("Implementation plan")
        self.assertEqual((kb, facts), ("existing kb", "existing facts"))


PLACEHOLDER = "[MANUAL FILL: Sonja — verified contact]"
REFERENCE_TABLE = (
    "Lead sentence.\n\n"
    "| Client / Engagement | Scope / Relevance | Reference Contact | Contact Info |\n"
    "|---|---|---|---|\n"
    f"| City of Bend | Brand | {PLACEHOLDER} | {PLACEHOLDER} |\n"
    f"| City of Medford | Marketing | {PLACEHOLDER} | {PLACEHOLDER} |\n"
    f"| [MANUAL FILL: Sonja — client] | Unknown | {PLACEHOLDER} | {PLACEHOLDER} |\n"
    f"| 1 | {PLACEHOLDER} | {PLACEHOLDER} | {PLACEHOLDER} |\n"
)


class ReferenceTableCleanupTests(unittest.TestCase):
    def test_reference_tab_keeps_named_clients_and_drops_blank_rows(self) -> None:
        out, changed = collapse_incomplete_client_tables(
            REFERENCE_TABLE, keep_named_clients=True
        )
        self.assertTrue(changed)
        self.assertIn("| City of Bend |", out)
        self.assertIn("| City of Medford |", out)
        self.assertNotIn("Sonja — client]", out)
        self.assertNotIn("| 1 |", out)

    def test_other_tabs_still_collapse_named_unfinished_rows(self) -> None:
        out, changed = collapse_incomplete_client_tables(REFERENCE_TABLE)
        self.assertTrue(changed)
        self.assertNotIn("City of Bend", out)

    def test_empty_reference_tab_takes_client_names_from_a_drafted_tab(self) -> None:
        examples = _section(
            "rfp-structure-examples",
            "Examples of relevant projects",
            "Lead.\n\n| Client | Work |\n| --- | --- |\n"
            "| City of Santa Clara | Communications |\n| Maricopa County | Brand |\n",
        )
        refs = _section("rfp-structure-references", "References", "Lead sentence only.")
        out, _ = ensure_references_tabs_have_tables(_draft(examples, refs))
        body = next(s for s in out.sections if s.id == refs.id).content
        self.assertIn("| City of Santa Clara |", body)
        self.assertIn("| Maricopa County |", body)
        self.assertNotIn("client / engagement", body)

    def test_empty_reference_tab_with_no_named_clients_stays_a_placeholder(self) -> None:
        refs = _section("rfp-structure-references", "References", "Lead sentence only.")
        out, _ = ensure_references_tabs_have_tables(_draft(refs))
        self.assertIn("MANUAL FILL", out.sections[0].content)

    def test_note_under_an_empty_table_stays_a_note(self) -> None:
        note = "[MANUAL FILL: Sonja — supply verified client references.]"
        text = (
            "Lead.\n\n"
            "| Client / Engagement | Scope / Relevance | Reference Contact | Contact Info |\n"
            "|---|---|---|---|\n\n"
            f"{note}\n"
        )
        out, _ = scrub_broken_reference_pipe_rows(text)
        self.assertNotIn("| 1 |", out)
        self.assertIn(note, out)

    def test_bullets_under_a_table_header_still_become_rows(self) -> None:
        text = (
            "| Client / Engagement | Scope / Relevance | Reference Contact | Contact Info |\n"
            "|---|---|---|---|\n\n"
            "- City of Bend — brand work\n"
        )
        out, _ = scrub_broken_reference_pipe_rows(text)
        self.assertIn("| City of Bend |", out)
        self.assertNotIn("| 1 |", out)


class ReferenceRefillTests(unittest.TestCase):
    def test_placeholder_table_behind_a_long_lead_needs_filling(self) -> None:
        body = ("Our references can speak to our work. " * 12) + "\n\n" + REFERENCE_TABLE
        self.assertTrue(
            section_needs_presubmit_fill(_section("rfp-structure-references", "References", body))
        )

    def test_reference_tab_with_a_real_contact_is_left_alone(self) -> None:
        body = (
            ("Our references can speak to our work. " * 12)
            + "\n\n| Client | Contact | Email |\n|---|---|---|\n"
            + "| City of Bend | Pat Lee | pat@bendoregon.gov |\n"
            + f"| City of Medford | {PLACEHOLDER} | {PLACEHOLDER} |\n"
        )
        self.assertFalse(
            section_needs_presubmit_fill(_section("rfp-structure-references", "References", body))
        )

    def test_long_narrative_with_one_placeholder_table_is_left_alone(self) -> None:
        body = ("We have delivered this work for years. " * 40) + "\n\n" + REFERENCE_TABLE
        self.assertFalse(
            section_needs_presubmit_fill(
                _section("rfp-eval-4", "Business Stability and References", body)
            )
        )


class ReferencePhoneGuardTests(_SupermemoryConfigured):
    async def test_phone_from_a_won_list_survives_a_corpus_that_lacks_it(self) -> None:
        with (
            patch(
                "app.services.supermemory.list_all_container_documents",
                new=AsyncMock(return_value=[_doc("06_WON_CityofNew_Proposal_2026.pdf")]),
            ),
            patch(
                "app.services.supermemory.get_document_content",
                new=AsyncMock(return_value=LIST_SINGLE_LINE),
            ),
            patch(
                "app.services.proposal_reference_contacts.load_client_list_registry",
                new=AsyncMock(return_value=REGISTRY),
            ),
        ):
            await load_won_reference_contacts(force_refresh=True)

        from app.models.proposal import EvidenceItem, ProposalResearchCache

        research = ProposalResearchCache.model_construct(
            evidence_corpus=[
                EvidenceItem(id="E1", source="bio", excerpt="Office 541-555-0000", sectionIds=[])
            ]
        )
        body = "City of Bend | 541-555-0199\nInvented Client | 541-555-0777"
        out, _ = apply_reference_contact_evidence_guard(
            _draft(_section("rfp-x", "References", body)), research
        )
        self.assertIn("541-555-0199", out.sections[0].content)
        self.assertNotIn("541-555-0777", out.sections[0].content)


LONG_SCOPE = " ".join(["We built a unified brand system for every city department."] * 6)


class ReferenceRowSurvivesRewriteTests(_SupermemoryConfigured):
    def test_long_row_with_a_contact_stays_in_the_table(self) -> None:
        row = f"| City of Bend | {LONG_SCOPE} | Pat Lee | pat@bendoregon.gov |"
        text = f"| Client | Scope | Contact | Email |\n|---|---|---|---|\n{row}\n"
        self.assertIn(row, repair_prose_disguised_as_table_rows(text))

    def test_long_row_with_only_a_phone_stays_in_the_table(self) -> None:
        row = f"| City of Bend | {LONG_SCOPE} | Pat Lee | 541-555-0101 |"
        text = f"| Client | Scope | Contact | Phone |\n|---|---|---|---|\n{row}\n"
        self.assertIn(row, repair_prose_disguised_as_table_rows(text))

    def test_long_row_without_a_contact_is_still_pulled_out(self) -> None:
        text = f"| Client | Scope |\n|---|---|\n| 1 | {LONG_SCOPE} |\n"
        out = repair_prose_disguised_as_table_rows(text)
        self.assertNotIn("| 1 |", out)
        self.assertIn(LONG_SCOPE, out)

    async def _load_contacts(self) -> None:
        with (
            patch(
                "app.services.supermemory.list_all_container_documents",
                new=AsyncMock(return_value=[_doc("06_WON_CityofNew_Proposal_2026.pdf")]),
            ),
            patch(
                "app.services.supermemory.get_document_content",
                new=AsyncMock(return_value=LIST_SINGLE_LINE),
            ),
            patch(
                "app.services.proposal_reference_contacts.load_client_list_registry",
                new=AsyncMock(return_value=REGISTRY),
            ),
        ):
            await load_won_reference_contacts(force_refresh=True)

    async def test_redraft_with_contacts_does_not_protect_old_placeholders(self) -> None:
        await self._load_contacts()
        masked, originals = _mask_manual_fill_for_rewrite(
            REFERENCE_TABLE, section_title="References"
        )
        self.assertEqual(originals, [])
        self.assertNotIn("MANUAL FILL", masked)
        self.assertIn("| City of Bend |", masked)

    async def test_redraft_with_no_contacts_keeps_the_handoff_tags(self) -> None:
        _, originals = _mask_manual_fill_for_rewrite(REFERENCE_TABLE, section_title="References")
        self.assertTrue(originals)

    async def test_other_tabs_keep_their_handoff_tags(self) -> None:
        await self._load_contacts()
        _, originals = _mask_manual_fill_for_rewrite(REFERENCE_TABLE, section_title="Insurance")
        self.assertTrue(originals)

    async def test_reference_tab_with_a_real_contact_keeps_its_tags(self) -> None:
        await self._load_contacts()
        text = REFERENCE_TABLE + "| City of Salem | Web | Jo Day | jo@cityofsalem.net |\n"
        _, originals = _mask_manual_fill_for_rewrite(text, section_title="References")
        self.assertTrue(originals)


class ImproveEvidenceTests(_SupermemoryConfigured):
    PRIOR = EvidenceItem(id="E1", source="bio", excerpt="Bio text", sectionIds=["rfp-x"])

    def _source_patches(self):
        return (
            patch(
                "app.services.supermemory.list_all_container_documents",
                new=AsyncMock(return_value=[_doc("06_WON_CityofNew_Proposal_2026.pdf")]),
            ),
            patch(
                "app.services.supermemory.get_document_content",
                new=AsyncMock(return_value=LIST_SINGLE_LINE),
            ),
            patch(
                "app.services.proposal_reference_contacts.load_client_list_registry",
                new=AsyncMock(return_value=REGISTRY),
            ),
        )

    async def _add(self, corpus: list, title: str) -> list:
        a, b, c = self._source_patches()
        with a, b, c:
            return await add_reference_contacts_to_corpus(corpus, "rfp-x", title)

    async def test_contacts_lead_the_evidence_for_a_reference_tab(self) -> None:
        corpus = await self._add([self.PRIOR], "References")
        self.assertIn("pat@bendoregon.gov", corpus[0].excerpt)
        self.assertEqual(corpus[0].section_ids, ["rfp-x"])
        self.assertEqual(corpus[1].excerpt, "Bio text")
        self.assertEqual(len({item.id for item in corpus}), 2)

    async def test_repeated_improve_does_not_stack_contact_items(self) -> None:
        once = await self._add([self.PRIOR], "References")
        twice = await self._add(once, "References")
        self.assertEqual(len(twice), 2)

    async def test_other_tabs_get_nothing_added(self) -> None:
        corpus = await self._add([self.PRIOR], "Implementation plan")
        self.assertEqual(corpus, [self.PRIOR])


CONTACT_TABLE = (
    "| Name | Title | Organization | Telephone number | Email address |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| Pat Lee | Manager | City of Bend | 541-555-0101 | pat@bendoregon.gov |\n"
    "| Ana Ruiz | Director | Deschutes Public Library | "
    "[MANUAL FILL: Sonja, confirm phone for Ana Ruiz] | ana@dpls.lib.or.us |\n"
)
WORK_TABLE = (
    "| Organization | Our work | Why it matters |\n"
    "| --- | --- | --- |\n"
    "| City of Bend | Brand system | One standard |\n"
)


class ReferenceJunkRowTests(unittest.TestCase):
    def _scrub(self, text: str) -> str:
        return scrub_broken_reference_pipe_rows(text)[0]

    def test_list_after_a_row_mentioning_phone_stays_a_list(self) -> None:
        out = self._scrub(CONTACT_TABLE + "\n- What each reference can speak to\n- Our approvals\n")
        self.assertIn("- What each reference can speak to", out)
        self.assertNotIn("| 1 |", out)

    def test_second_table_keeps_its_own_columns(self) -> None:
        out = self._scrub(CONTACT_TABLE + "\n" + WORK_TABLE)
        self.assertIn("| Organization | Our work | Why it matters |", out)
        self.assertIn("| City of Bend | Brand system | One standard |", out)
        self.assertEqual(out.count("MANUAL FILL"), 1)

    def test_second_table_directly_under_the_first_is_not_merged(self) -> None:
        out = self._scrub(CONTACT_TABLE + WORK_TABLE)
        self.assertIn("| City of Bend | Brand system | One standard |", out)
        self.assertEqual(out.count("MANUAL FILL"), 1)

    def test_blank_line_between_tables_survives(self) -> None:
        self.assertIn("|\n\n| Organization |", self._scrub(CONTACT_TABLE + "\n" + WORK_TABLE))

    def test_table_with_no_reference_words_is_left_alone(self) -> None:
        table = "| Client | Work | Relevance |\n| --- | --- | --- |\n| City of Bend | Brand | Fit |\n"
        self.assertEqual(self._scrub(table), table)

    def test_saved_numbered_row_with_only_placeholders_is_removed(self) -> None:
        fill = "[MANUAL FILL: Sonja, verified contact]"
        row = f"| 1 | {fill} | {fill} | {fill} | {fill} |\n"
        out = self._scrub(CONTACT_TABLE + row)
        self.assertNotIn("| 1 |", out)
        self.assertIn("| Pat Lee |", out)

    def test_saved_numbered_row_holding_a_sentence_becomes_a_bullet(self) -> None:
        fill = "[MANUAL FILL: Sonja, verified contact]"
        row = f"| 1 | {fill} | {fill} | We'll tell each reference to expect a call | {fill} |\n"
        out = self._scrub(CONTACT_TABLE + row + "\nClosing line.\n")
        self.assertNotIn("| 1 |", out)
        self.assertIn("- We'll tell each reference to expect a call", out)
        self.assertLess(out.index("| Ana Ruiz |"), out.index("- We'll tell"))
        self.assertLess(out.index("- We'll tell"), out.index("Closing line."))

    def test_deliberately_numbered_reference_table_keeps_its_rows(self) -> None:
        text = (
            "| # | Contact Name | Title | Organization | Phone | Email |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| 1 | | | City of Bend | | |\n"
        )
        out = self._scrub(text)
        self.assertIn("| 1 |", out)
        self.assertIn("City of Bend", out)

    def test_long_row_naming_a_client_stays_in_its_table(self) -> None:
        row = f"| City of Medford | {LONG_SCOPE} | {LONG_SCOPE} |"
        text = f"| Organization | Our work | Why |\n| --- | --- | --- |\n{row}\n"
        self.assertIn(row, repair_prose_disguised_as_table_rows(text))
