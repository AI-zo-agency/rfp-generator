"""Forms & Attachments compliance integrity — LLM + verbatim quotes, no regex matching."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

if "langchain_openai" not in sys.modules:
    langchain_openai = types.ModuleType("langchain_openai")

    class ChatOpenAI:  # pragma: no cover
        pass

    langchain_openai.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = langchain_openai

from app.models.proposal import (
    BudgetLineItem,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_forms_attachments_integrity import (
    FormsIntegrityFinding,
    FormsIntegrityResult,
    apply_verbatim_replacements,
    audit_and_repair_forms_attachments,
    format_forms_integrity_reply,
    section_is_forms_attachments,
)


class FormsAttachmentsIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def _forms_body(self) -> str:
        return (
            "| **Cost Proposal / Pricing Table** | Included | "
            "hourly labor category rates provided per RFP |\n"
            "| **References** | Included | "
            "Three client references with full contact information provided |\n"
            "\n"
            "## Insurance Compliance\n\n"
            "We maintain coverage through Next Insurance Company (NAIC #16285).\n"
        )

    def test_apply_verbatim_replacements_all_three(self) -> None:
        findings = [
            FormsIntegrityFinding(
                code="insurance_carrier_unverified",
                summary="Named carrier/NAIC not in 1.5",
                verbatim_quote="through Next Insurance Company (NAIC #16285)",
                replacement="with A-rated carriers [MANUAL FILL: Sonja — confirm carrier/NAIC on COI]",
            ),
            FormsIntegrityFinding(
                code="cost_row_hourly_mismatch",
                summary="Hourly rates not in manuscript",
                verbatim_quote="hourly labor category rates provided per RFP",
                replacement="Fixed project fees by phase; hourly table pending MANUAL FILL",
            ),
            FormsIntegrityFinding(
                code="references_row_contact_mismatch",
                summary="Contacts blank",
                verbatim_quote="Three client references with full contact information provided",
                replacement="Three client references included; contact details pending MANUAL FILL",
            ),
        ]
        result = apply_verbatim_replacements(self._forms_body(), findings)
        self.assertTrue(result.changed)
        self.assertTrue(all(f.fixed for f in findings))
        self.assertNotIn("Next Insurance", result.content)
        self.assertNotIn("NAIC #16285", result.content)
        self.assertNotIn("hourly labor category rates provided", result.content)
        self.assertNotIn("full contact information", result.content)
        self.assertIn("MANUAL FILL", result.content)

    def test_skips_quote_not_in_draft(self) -> None:
        findings = [
            FormsIntegrityFinding(
                code="other",
                summary="missing quote",
                verbatim_quote="this string is not in the section",
                replacement="x",
            )
        ]
        result = apply_verbatim_replacements("plain section", findings)
        self.assertFalse(result.changed)
        self.assertFalse(findings[0].fixed)

    async def test_llm_audit_uses_chat_json_not_regex(self) -> None:
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="rfp-req-forms-attachments",
                    title="Required Forms & Attachments",
                    content=self._forms_body(),
                    status="generated",
                ),
                ProposalSection(
                    id="section-1-insurance",
                    title="1.5 — Insurance Information",
                    content="General Liability $1M / $2M. No named carrier.",
                    status="generated",
                ),
            ],
        )
        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="t",
            budget=ProposalBudget(
                rfpId="r1",
                updatedAt="t",
                budgetFormat="phased",
                lineItems=[
                    BudgetLineItem(
                        id="li-1",
                        category="labor",
                        description="Discovery",
                        unit="flat",
                        extended=7000,
                    )
                ],
            ),
        )
        payload = {
            "issues": [
                {
                    "code": "insurance_carrier_unverified",
                    "summary": "Carrier/NAIC not in 1.5",
                    "verbatimQuote": "Next Insurance Company (NAIC #16285)",
                    "replacement": "A-rated carriers [MANUAL FILL: Sonja confirm COI]",
                    "fixAction": "replace",
                }
            ]
        }
        with patch(
            "app.services.proposal_forms_attachments_integrity.llm.chat_json",
            new_callable=AsyncMock,
            return_value=(payload, "test"),
        ) as mock_json:
            result = await audit_and_repair_forms_attachments(
                self._forms_body(),
                draft=draft,
                research=research,
            )
        self.assertTrue(mock_json.await_count)
        self.assertNotIn("Next Insurance Company (NAIC #16285)", result.content)
        self.assertTrue(result.changed)

    def test_section_identity(self) -> None:
        self.assertTrue(
            section_is_forms_attachments(
                ProposalSection(
                    id="rfp-req-forms-attachments",
                    title="Required Forms & Attachments",
                    content="x",
                )
            )
        )
        self.assertFalse(
            section_is_forms_attachments(
                ProposalSection(id="section-cost", title="7. Cost Proposal", content="x")
            )
        )

    def test_reply_lists_each_issue(self) -> None:
        result = FormsIntegrityResult(
            content="fixed",
            findings=[
                FormsIntegrityFinding(
                    code="a", summary="Insurance carrier unverified", fixed=True
                ),
                FormsIntegrityFinding(
                    code="b", summary="Cost row hourly mismatch", fixed=True
                ),
            ],
            fix_logs=["Rewrote insurance", "Fixed cost row"],
        )
        reply = format_forms_integrity_reply(
            result, section_title="Required Forms & Attachments"
        )
        self.assertIn("Issues found", reply)
        self.assertIn("Insurance carrier", reply)
        self.assertIn("Fixes applied", reply)


if __name__ == "__main__":
    unittest.main()
