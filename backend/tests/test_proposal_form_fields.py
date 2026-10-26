"""RFP built-in forms: KB fill, verbatim labels, and survival through later passes."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import ProposalDraft, ProposalSection, RfpSectionMap
from app.services.proposal_drafting_graph import partition_phase3_sections
from app.services.proposal_form_fields import (
    collect_form_field_labels,
    resolve_form_field_values_from_kb,
    restore_missing_form_fields,
)
from app.services.proposal_ralph import apply_ralph_to_draft
from app.services.proposal_section_dedup import compress_rfp_company_identity_forms
from app.services.proposal_submission_authority import (
    FORM_FIELDS_HEADING,
    resolve_form_field_response,
)

_HEADING_REQ = "Must cover the RFP's required sub-heading: "


def _form_map(**overrides) -> RfpSectionMap:
    data = {
        "id": "form-a",
        "title": "Attachment B — Offeror Information Form",
        "submissionInstrument": "form",
        "requirements": [
            f"{_HEADING_REQ}Offeror Name",
            f"{_HEADING_REQ}Federal Tax ID",
            "Complete and sign Attachment B.",
        ],
        "formFields": ["Offeror Name", "Federal Tax ID", "Authorized Signature"],
    }
    data.update(overrides)
    return RfpSectionMap(**data)


class ResolveFormFieldResponseTests(unittest.TestCase):
    def test_exact_label_beats_containment(self) -> None:
        values = {"Company Name": "zö agency LLC", "Name": "Sonja Doe"}
        self.assertEqual(resolve_form_field_response("Name", values), "Sonja Doe")

    def test_unmatched_label_is_manual_fill(self) -> None:
        self.assertEqual(
            resolve_form_field_response("Authorized Signature", {"Offeror Name": "x"}),
            "[MANUAL FILL: Sonja — Authorized Signature]",
        )


class PartitionFormStubTests(unittest.TestCase):
    def test_ask_line_skips_sub_heading_items(self) -> None:
        _to_draft, already = partition_phase3_sections(
            [_form_map()], {}, form_field_values={}
        )
        body = already[0].content or ""
        self.assertIn("What the RFP asks here: Complete and sign Attachment B.", body)
        self.assertNotIn("Must cover the RFP", body)

    def test_open_labels_listed_after_kb_search(self) -> None:
        _to_draft, already = partition_phase3_sections(
            [_form_map()],
            {},
            form_field_values={"Offeror Name": "zö agency LLC"},
        )
        body = already[0].content or ""
        self.assertIn("| Offeror Name | zö agency LLC |", body)
        self.assertIn(
            "Not found in the knowledge base: Federal Tax ID; Authorized Signature.",
            body,
        )
        self.assertIn("Ask Ralph", body)

    def test_no_missing_list_when_kb_not_searched(self) -> None:
        _to_draft, already = partition_phase3_sections([_form_map()], {})
        self.assertNotIn("Not found in the knowledge base", already[0].content or "")


class ResolveFromKbTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_only_values_grounded_in_evidence(self) -> None:
        evidence = (
            "=== PACKED KB EVIDENCE ===\n"
            "Legal name: zö agency LLC. FEIN 12-3456789.\nSources: 01_companyfacts"
        )
        llm_rows = {
            "fields": [
                {"label": "Offeror Name", "value": "zö agency LLC"},
                {"label": "Federal Tax ID", "value": "98-7654321"},  # not in evidence
                {"label": "Authorized Signature", "value": None},
                {"label": "Invented Label", "value": "zö agency LLC"},
            ]
        }
        with (
            patch(
                "app.services.proposal_section_kb_evidence.fetch_packed_section_kb_evidence",
                new=AsyncMock(return_value=(evidence, ["01_companyfacts"])),
            ),
            patch(
                "app.services.proposal_intelligence.agent_base.safe_chat_json",
                new=AsyncMock(return_value=(llm_rows, "test")),
            ),
        ):
            values = await resolve_form_field_values_from_kb(
                ["Offeror Name", "Federal Tax ID", "Authorized Signature"]
            )
        self.assertEqual(values, {"Offeror Name": "zö agency LLC"})

    async def test_no_evidence_skips_llm(self) -> None:
        chat = AsyncMock()
        with (
            patch(
                "app.services.proposal_section_kb_evidence.fetch_packed_section_kb_evidence",
                new=AsyncMock(return_value=("", [])),
            ),
            patch("app.services.proposal_intelligence.agent_base.safe_chat_json", new=chat),
        ):
            values = await resolve_form_field_values_from_kb(["Offeror Name"])
        self.assertEqual(values, {})
        chat.assert_not_called()

    def test_collect_labels_dedupes_across_form_tabs(self) -> None:
        maps = [
            _form_map(),
            _form_map(id="form-b", formFields=["offeror  name", "DBA"]),
            _form_map(id="narr", submissionInstrument="narrative", formFields=["X"]),
        ]
        self.assertEqual(
            collect_form_field_labels(maps),
            ["Offeror Name", "Federal Tax ID", "Authorized Signature", "DBA"],
        )


class RestoreMissingFormFieldsTests(unittest.TestCase):
    def _draft(self, content: str) -> ProposalDraft:
        return ProposalDraft(
            rfpId="r1",
            updatedAt="2026-10-26T00:00:00Z",
            sections=[ProposalSection(id="form-a", title="Attachment B", content=content)],
        )

    def test_lost_row_comes_back_and_kept_values_stay(self) -> None:
        content = (
            "## Attachment B\n\nIntro.\n\n"
            f"{FORM_FIELDS_HEADING}\n\n| Field | Response |\n| --- | --- |\n"
            "| Offeror Name | zö agency LLC |\n\n"
            "[DESIGNER NOTE: Place the completed form.]"
        )
        draft, logs = restore_missing_form_fields(self._draft(content), [_form_map()])
        body = draft.sections[0].content
        self.assertTrue(logs)
        self.assertIn("| Offeror Name | zö agency LLC |", body)
        self.assertIn("| Federal Tax ID | [MANUAL FILL: Sonja — Federal Tax ID] |", body)
        self.assertIn("| Authorized Signature | [MANUAL FILL:", body)
        self.assertEqual(body.count(FORM_FIELDS_HEADING), 1)
        self.assertTrue(body.rstrip().endswith("[DESIGNER NOTE: Place the completed form.]"))

    def test_table_rebuilt_when_dropped_entirely(self) -> None:
        content = "## Attachment B\n\nIntro.\n\n[DESIGNER NOTE: Place the completed form.]"
        draft, _logs = restore_missing_form_fields(self._draft(content), [_form_map()])
        body = draft.sections[0].content
        self.assertLess(body.index(FORM_FIELDS_HEADING), body.index("[DESIGNER NOTE"))
        self.assertIn("| Offeror Name | [MANUAL FILL:", body)

    def test_complete_table_untouched(self) -> None:
        _to_draft, already = partition_phase3_sections([_form_map()], {})
        draft = self._draft(already[0].content or "")
        out, logs = restore_missing_form_fields(draft, [_form_map()])
        self.assertEqual(logs, [])
        self.assertIs(out, draft)


class LaterPassesKeepFormTableTests(unittest.TestCase):
    def _stub(self) -> str:
        _to_draft, already = partition_phase3_sections(
            [_form_map()], {}, form_field_values={"Offeror Name": "zö agency LLC"}
        )
        return already[0].content or ""

    def test_ralph_hard_fit_never_trims_form_table(self) -> None:
        stub = self._stub()
        long_body = "\n\n".join(["Narrative paragraph " + "word " * 60] * 40)
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-10-26T00:00:00Z",
            sections=[
                ProposalSection(id="form-a", title="Attachment B", content=stub, wordTarget=20),
                ProposalSection(id="n1", title="Approach", content=long_body),
            ],
        )
        out, _logs = apply_ralph_to_draft(draft, page_limit=2)
        self.assertEqual(out.sections[0].content, stub)

    def test_identity_compression_keeps_rfp_labels(self) -> None:
        stub = self._stub()
        business = (
            "| Field | Response |\n| --- | --- |\n| Legal Name | zö agency LLC |\n"
            "| FEIN | 12-3456789 |\n| Address | 1 Main St |"
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="2026-10-26T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-1-business-info",
                    title="1.3 — Business Information",
                    content=business,
                ),
                ProposalSection(id="form-a", title="Attachment B", content=stub),
            ],
        )
        out, _logs = compress_rfp_company_identity_forms(draft)
        self.assertEqual(out.sections[1].content, stub)


if __name__ == "__main__":
    unittest.main()
