"""Designer-handoff regressions from the Newport Beach / Gil Aranowitz report.

Every failure mode in that handoff note must stay fixed:
- CA registration claim vs stale MANUAL FILL contradiction
- Option Year held-flat math vs Year 1
- Corrupted / truncated Option Terms prose
- Rev6 mid-word glue (directlytely / choiceust / decisionthe)
- Leaked Action needed / Needs your input / Edit source chrome
- Truncated MANUAL FILL + duplicate References headers
- Title UI chrome (**· needs input**, Edit source)
"""

from __future__ import annotations

import re
import unittest

from app.models.proposal import (
    BudgetLineItem,
    EvidenceItem,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_budget_content import render_budget_markdown
from app.services.proposal_budget_validation import (
    align_held_flat_option_year_line_items,
    rebuild_option_term_notes,
    reconcile_proposal_budget,
)
from app.services.proposal_manuscript import scrub_client_facing_section_artifacts
from app.services.proposal_scan_fact_repairs import (
    apply_leaked_fragment_scrub_to_draft,
    scrub_leaked_system_fragments,
)
from app.services.proposal_state_registration_guard import (
    scrub_unverified_state_registration_claims,
    verified_registration_jurisdictions,
)
from app.services.proposal_voice_enforcement import (
    apply_rev6_voice_scrub_to_draft,
    scrub_rev6_voice_patterns,
)
from app.services.proposal_zero_fabrication import apply_zero_fabrication_guards


# ---------------------------------------------------------------------------
# Fixtures mirroring the reported manuscript
# ---------------------------------------------------------------------------

_CORRUPTED_OPTION_TERMS = (
    "Client media pass-through (at net. Total estimated annual client invoicing "
    "(media pass-through + agency fees): $2,900."
)

_YEAR1_RECURRING = 183_590.0
_BAD_OPTION_YEAR = 189_255.0
_MEDIA_PASSTHROUGH = 2_900.0


def _newport_style_budget(*, corrupt_notes: bool = True) -> ProposalBudget:
    return ProposalBudget(
        rfpId="rfp-newport",
        updatedAt="2026-01-01T00:00:00Z",
        lineItems=[
            BudgetLineItem(
                id="retainer",
                category="Year 1 Retainers",
                description="Monthly retainers",
                extended=120_000,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="events",
                category="Year 1 Events",
                description="Event support",
                extended=30_000,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="measurement",
                category="Year 1 Measurement",
                description="Measurement",
                extended=15_000,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="pm",
                category="Year 1 PM",
                description="Project management",
                extended=10_000,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="hourly",
                category="Year 1 Hourly",
                description="Hourly pool",
                extended=5_590,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="commission",
                category="Year 1 Media commission",
                description="Agency media commission",
                extended=3_000,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="oy2",
                category="Option Year 2",
                description="same scope as Year 1, pricing held flat",
                extended=_BAD_OPTION_YEAR,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="oy3",
                category="Option Year 3",
                description="same scope as Year 1, pricing held flat",
                extended=_BAD_OPTION_YEAR,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="media",
                category="Client media",
                description="Paid media pass-through",
                extended=_MEDIA_PASSTHROUGH,
                lineItemType="client_passthrough",
            ),
        ],
        agencyRevenueEstimate=562_100,
        agencyFeeSubtotal=562_100,
        clientMediaPassthrough=_MEDIA_PASSTHROUGH,
        totalClientInvoicing=565_000,
        optionTermNotes=_CORRUPTED_OPTION_TERMS if corrupt_notes else "",
        budgetFormat="phased",
    )


def _business_license_section(*, with_stale_fill: bool = True) -> ProposalSection:
    fill = (
        "\n[MANUAL FILL: Sonja — do not assert California business registration "
        "until it appears in companyfacts / Section 1.3 State Registrations.]\n"
        if with_stale_fill
        else "\n"
    )
    return ProposalSection(
        id="rfp-sec-16-license",
        title="16. Business License",
        content=(
            "We hold the business registration and standing this contract "
            "requires to operate in California.\n"
            f"{fill}"
            "| Item | Status |\n"
            "| --- | --- |\n"
            "| Business registration | Action needed / Needs your input / "
            "Sonja, do not assert California business registration until it "
            "appears in companyfacts / Section 1.3 State Registrations. |\n"
            "| Local license | , City of Newport Beach business license, "
            "Needs your input — Sonja, confirm license number |\n"
        ),
    )


def _inventory_section() -> ProposalSection:
    return ProposalSection(
        id="section-1-business-info",
        title="1.3 — Business Information",
        content=(
            "### State Registrations\n\n"
            "| State | Status |\n"
            "| Oregon | Active |\n"
            "| Washington | Active |\n"
            "| Texas | Active |\n"
            "| Colorado | Active |\n"
            "| California | Active |\n"
        ),
    )


def _companyfacts_research() -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="rfp-newport",
        updatedAt="2026-01-01T00:00:00Z",
        evidenceCorpus=[
            EvidenceItem(
                id="cf-1",
                source="01_companyfacts_verified.docx",
                chunkKey="companyfacts-1.3",
                excerpt=(
                    "§1.3 State Registrations: Oregon, Washington, Texas, "
                    "Colorado, California — Active."
                ),
            )
        ],
    )


class RegistrationContradictionTests(unittest.TestCase):
    def test_stale_fill_removed_when_section_1_3_lists_california(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-newport",
            updatedAt="t",
            sections=[_inventory_section(), _business_license_section()],
        )
        updated, logs = scrub_unverified_state_registration_claims(draft)
        body = next(s.content or "" for s in updated.sections if s.id == "rfp-sec-16-license")
        self.assertIn("operate in California", body)
        self.assertNotIn("do not assert California", body)
        self.assertNotIn("[MANUAL FILL", body)
        self.assertTrue(any("stale" in line.casefold() for line in logs))

    def test_stale_fill_removed_from_companyfacts_only_inventory(self) -> None:
        """Strict RFP may lack Zo Section 1.3 — companyfacts evidence still counts."""
        draft = ProposalDraft(
            rfpId="rfp-newport",
            updatedAt="t",
            sections=[_business_license_section()],
        )
        research = _companyfacts_research()
        verified = verified_registration_jurisdictions(draft, research)
        self.assertIn("California", verified)
        updated, logs = scrub_unverified_state_registration_claims(draft, research)
        body = updated.sections[0].content or ""
        self.assertNotIn("do not assert California", body)
        self.assertTrue(any("stale" in line.casefold() for line in logs))

    def test_keeps_fill_when_california_not_verified(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-md",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="section-1-business-info",
                    title="1.3 — Business Information",
                    content="### State Registrations\n\n| Oregon | Active |\n",
                ),
                ProposalSection(
                    id="license",
                    title="License",
                    content=(
                        "[MANUAL FILL: Sonja — do not assert California business "
                        "registration until it appears in companyfacts / Section 1.3 "
                        "State Registrations.]"
                    ),
                ),
            ],
        )
        updated, _ = scrub_unverified_state_registration_claims(draft)
        body = updated.sections[1].content or ""
        self.assertIn("do not assert California", body)

    def test_zero_fabrication_stack_clears_contradiction(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-newport",
            updatedAt="t",
            sections=[_inventory_section(), _business_license_section()],
        )
        updated, report = apply_zero_fabrication_guards(draft, label="handoff")
        body = next(s.content or "" for s in updated.sections if s.id == "rfp-sec-16-license")
        self.assertNotIn("do not assert California", body)
        self.assertTrue(
            any("state registration" in line.casefold() for line in report.logs)
            or "do not assert" not in body
        )

    def test_does_not_expand_claim_phrase_list_requirement(self) -> None:
        """Novel claim wording next to a verified CA must not require phrase tables.

        Stale fill removal is enough — we never add 'hold the business registration'
        to a synonym list.
        """
        from app.services import proposal_state_registration_guard as mod

        phrases = " ".join(mod._REGISTRATION_CLAIM_PHRASES)
        self.assertNotIn("hold the business registration", phrases)
        self.assertNotIn("business registration and standing", phrases)


class OptionYearBudgetMathTests(unittest.TestCase):
    def test_year1_recurring_fixture_sums_to_183590(self) -> None:
        budget = _newport_style_budget()
        year1 = [
            i
            for i in budget.line_items
            if i.line_item_type != "client_passthrough" and "option year" not in (i.category or "").casefold()
        ]
        total = round(sum(float(i.extended or 0) for i in year1), 2)
        self.assertEqual(total, _YEAR1_RECURRING)

    def test_align_closes_5665_gap_per_option_year(self) -> None:
        budget = _newport_style_budget()
        aligned = align_held_flat_option_year_line_items(list(budget.line_items))
        oy = [i for i in aligned if "option year" in (i.category or "").casefold()]
        self.assertEqual(len(oy), 2)
        for item in oy:
            self.assertEqual(float(item.extended or 0), _YEAR1_RECURRING)
            self.assertNotEqual(float(item.extended or 0), _BAD_OPTION_YEAR)

    def test_reconcile_aligns_and_rebuilds_option_terms(self) -> None:
        budget = _newport_style_budget()
        merged = reconcile_proposal_budget(
            budget,
            rfp_context="County may exercise Option Year 2 and Option Year 3 at held-flat rates.",
        )
        from app.services.proposal_budget_validation import (
            _is_option_year_line,
            _usd,
            split_line_item_totals,
        )

        base_items = [i for i in merged.line_items if not _is_option_year_line(i)]
        _, year1_fee, _ = split_line_item_totals(base_items)
        oy = [
            i
            for i in merged.line_items
            if "option year" in (i.category or "").casefold()
        ]
        self.assertEqual(len(oy), 2)
        for item in oy:
            self.assertAlmostEqual(float(item.extended or 0), year1_fee, places=2)
        notes = merged.option_term_notes or ""
        self.assertNotIn("(at net.", notes)
        self.assertIn("held flat", notes.casefold())
        self.assertIn("2,900", notes)
        self.assertIn("Option Year 2", notes)
        self.assertIn("Option Year 3", notes)
        self.assertNotIn("Option Year 1:", notes)
        self.assertIn(_usd(year1_fee), notes)
        self.assertIn(f"Option Year 2: {_usd(year1_fee)}", notes)
        self.assertNotRegex(notes, r"client invoicing[^:\n]*:\s*\$2,900\.?\s*$")

    def test_render_rewrites_corrupted_option_terms_paragraph(self) -> None:
        budget = reconcile_proposal_budget(
            _newport_style_budget(),
            rfp_context="Option Year 2 and Option Year 3.",
        )
        md = render_budget_markdown(
            budget,
            rfp_text="Cost File. Option Year 2 and Option Year 3 held flat.",
        )
        self.assertIn("## Option Terms", md)
        self.assertNotIn("(at net.", md)
        self.assertNotRegex(md, r"invoicing[^:\n]*:\s*\$2,900")
        # Unclosed paren corruption must be gone.
        opt_block = md.split("## Option Terms", 1)[1]
        open_parens = opt_block.count("(")
        close_parens = opt_block.count(")")
        self.assertEqual(open_parens, close_parens, opt_block[:500])

    def test_rebuild_never_truncates_mid_sentence_at_500_chars(self) -> None:
        long_corrupt = _CORRUPTED_OPTION_TERMS + (" x" * 300)
        budget = _newport_style_budget()
        budget = budget.model_copy(update={"option_term_notes": long_corrupt})
        notes = rebuild_option_term_notes(
            budget,
            rfp_context="Option Year 2 Option Year 3",
        )
        self.assertNotIn("(at net.", notes)
        self.assertIn("pass-through", notes.casefold())
        self.assertTrue(notes.strip().endswith(".") or "Option Year" in notes)

    def test_escalation_path_not_forced_flat(self) -> None:
        items = [
            BudgetLineItem(
                id="y1",
                category="Year 1",
                description="Fees",
                extended=100_000,
                lineItemType="agency_fee",
            ),
            BudgetLineItem(
                id="y2",
                category="Option Year 2",
                description="escalated",
                extended=103_000,
                lineItemType="agency_fee",
            ),
        ]
        aligned = align_held_flat_option_year_line_items(
            items,
            rfp_context="Option years escalate 3% annually.",
            option_term_notes="3% escalation",
        )
        self.assertEqual(float(aligned[1].extended or 0), 103_000.0)


class Rev6WordGlueTests(unittest.TestCase):
    CASES = [
        (
            "sec-2",
            "One point we want to name directlytely, since each service area will "
            "likely define 'urgent' differently.",
            "directlytely",
        ),
        (
            "sec-10",
            "That is a deliberate design choiceust timing before either gets buried.",
            "choiceust",
        ),
        (
            "sec-15",
            "That's a deliberate bid decisionthe same feed slot before it publishes.",
            "decisionthe",
        ),
    ]

    def test_each_reported_glue_artifact_is_repaired(self) -> None:
        for _sid, raw, bad in self.CASES:
            with self.subTest(artifact=bad):
                cleaned, _ = scrub_rev6_voice_patterns(raw)
                self.assertNotIn(bad, cleaned.casefold())
                # Must leave readable word boundaries (space after stem).
                if bad == "choiceust":
                    self.assertIn("choice just", cleaned.casefold())
                elif bad == "decisionthe":
                    self.assertIn("decision the", cleaned.casefold())

    def test_scrub_does_not_create_new_glue_from_not_x_its_y(self) -> None:
        raw = (
            "That's a deliberate bid decision not a filler it's the same feed "
            "slot before it publishes."
        )
        cleaned, _ = scrub_rev6_voice_patterns(raw)
        self.assertNotIn("decisionthe", cleaned.casefold())
        self.assertNotRegex(cleaned, r"[a-z]{4,}the\b")

    def test_draft_wide_scrub_clears_all_three_sections(self) -> None:
        draft = ProposalDraft(
            rfpId="r",
            updatedAt="t",
            sections=[
                ProposalSection(id=sid, title=sid, content=raw)
                for sid, raw, _ in self.CASES
            ],
        )
        updated, logs = apply_rev6_voice_scrub_to_draft(draft)
        blob = "\n".join(s.content or "" for s in updated.sections)
        for _, _, bad in self.CASES:
            self.assertNotIn(bad, blob.casefold())
        self.assertTrue(logs or "choice just" in blob.casefold())


class LeakAndChromeTests(unittest.TestCase):
    def test_action_needed_table_cell_scrubbed(self) -> None:
        body = _business_license_section().content or ""
        cleaned, logs = scrub_leaked_system_fragments(body)
        self.assertTrue(logs)
        self.assertNotIn("Action needed", cleaned)
        self.assertNotIn("Needs your input", cleaned)
        # Bracket MANUAL FILL is authoring chrome — registration guard clears it
        # when CA is verified (see test_license_block_full_cleanup_*).
        self.assertIn("MANUAL FILL", cleaned)

    def test_title_chrome_stripped_on_draft(self) -> None:
        draft = ProposalDraft(
            rfpId="r",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s5",
                    title="Vendor Conflict of Interest Disclosure Form**· needs input**",
                    content="Form body.",
                ),
                ProposalSection(
                    id="s8",
                    title="COST FILE INSTRUCTIONS**· needs input**",
                    content="Instructions.",
                ),
                ProposalSection(
                    id="s18",
                    title="Draft Agreement AcknowledgmentEdit source",
                    content="Agreement.",
                ),
            ],
        )
        updated, logs = apply_leaked_fragment_scrub_to_draft(draft)
        titles = {s.id: s.title or "" for s in updated.sections}
        self.assertNotIn("needs input", titles["s5"].casefold())
        self.assertNotIn("**", titles["s5"])
        self.assertNotIn("needs input", titles["s8"].casefold())
        self.assertNotIn("Edit source", titles["s18"])
        self.assertIn("Draft Agreement Acknowledgment", titles["s18"])
        self.assertTrue(any("chrome" in line.casefold() for line in logs))

    def test_manuscript_scrub_strips_needs_input_and_edit_source_in_body(self) -> None:
        raw = (
            "### 5. Vendor Conflict of Interest Disclosure Form**· needs input**\n\n"
            "Body text.\n\n"
            "18. Draft Agreement AcknowledgmentEdit source\n"
        )
        out = scrub_client_facing_section_artifacts(raw)
        self.assertNotIn("needs input", out.casefold())
        self.assertNotIn("Edit source", out)

    def test_references_truncated_manual_fill_survives_as_closed_or_scrubbed(self) -> None:
        """Truncated tags must not ship mid-sentence cutoffs."""
        raw = (
            "## References, Similar Services Performed\n\n"
            "## References, Similar Services Performed\n\n"
            "| Client | Contact |\n"
            "| --- | --- |\n"
            "| [MANUAL FILL] — Sonja, supply verified client references from "
            "ClientList / KB only (name… |\n"
        )
        out = scrub_client_facing_section_artifacts(raw)
        # Duplicate heading collapsed to one.
        self.assertEqual(
            out.casefold().count("## references, similar services performed"),
            1,
            out,
        )
        open_tags = len(re.findall(r"\[MANUAL\s+FILL", out, flags=re.I))
        close_brackets = out.count("]")
        self.assertGreaterEqual(close_brackets, open_tags)
        self.assertNotIn("(name…", out)

    def test_license_block_full_cleanup_via_leak_then_registration(self) -> None:
        draft = ProposalDraft(
            rfpId="r",
            updatedAt="t",
            sections=[_inventory_section(), _business_license_section()],
        )
        draft, _ = apply_leaked_fragment_scrub_to_draft(draft)
        draft, _ = scrub_unverified_state_registration_claims(draft)
        body = next(s.content or "" for s in draft.sections if s.id == "rfp-sec-16-license")
        self.assertNotIn("Action needed", body)
        self.assertNotIn("Needs your input", body)
        self.assertNotIn("do not assert California", body)
        self.assertIn("California", body)


class EndToEndHandoffDraftTests(unittest.TestCase):
    """Full dirty draft → scrub stack; assert the report's blockers are gone."""

    def _dirty_draft(self) -> ProposalDraft:
        glue_sections = [
            ProposalSection(id=sid, title=title, content=raw)
            for (sid, raw, _), title in zip(
                Rev6WordGlueTests.CASES,
                ["2. Approach", "10. Timing", "15. Bid decision"],
            )
        ]
        return ProposalDraft(
            rfpId="rfp-newport",
            updatedAt="t",
            sections=[
                _inventory_section(),
                _business_license_section(),
                ProposalSection(
                    id="s14",
                    title="14. References**· needs input**",
                    content=(
                        "## References, Similar Services Performed\n\n"
                        "## References, Similar Services Performed\n\n"
                        "[MANUAL FILL] — Sonja, supply verified client references "
                        "from ClientList / KB only (name…\n"
                    ),
                ),
                ProposalSection(
                    id="s5",
                    title="Vendor Conflict of Interest Disclosure Form**· needs input**",
                    content="Disclosure body.",
                ),
                ProposalSection(
                    id="s18",
                    title="Draft Agreement AcknowledgmentEdit source",
                    content="Ack body.",
                ),
                *glue_sections,
            ],
        )

    def test_handoff_scrub_stack_clears_report_blockers(self) -> None:
        draft = self._dirty_draft()
        draft, _ = apply_rev6_voice_scrub_to_draft(draft)
        draft, _ = apply_leaked_fragment_scrub_to_draft(draft)
        draft, _ = scrub_unverified_state_registration_claims(draft)
        draft, _ = apply_zero_fabrication_guards(draft, label="handoff-e2e")

        blob_titles = " | ".join(s.title or "" for s in draft.sections)
        blob_bodies = "\n".join(s.content or "" for s in draft.sections)

        self.assertNotIn("needs input", blob_titles.casefold())
        self.assertNotIn("Edit source", blob_titles)
        self.assertNotIn("do not assert California", blob_bodies)
        self.assertNotIn("Action needed", blob_bodies)
        for bad in ("directlytely", "choiceust", "decisionthe"):
            self.assertNotIn(bad, blob_bodies.casefold())

        budget = reconcile_proposal_budget(
            _newport_style_budget(),
            rfp_context="Option Year 2 and Option Year 3.",
        )
        from app.services.proposal_budget_validation import (
            _is_option_year_line,
            split_line_item_totals,
        )

        base_items = [i for i in budget.line_items if not _is_option_year_line(i)]
        _, year1_fee, _ = split_line_item_totals(base_items)
        md = render_budget_markdown(
            budget,
            rfp_text="Cost File with Option Year 2 and Option Year 3.",
        )
        self.assertNotIn("(at net.", md)
        for item in budget.line_items:
            if "option year" in (item.category or "").casefold():
                self.assertAlmostEqual(float(item.extended or 0), year1_fee, places=2)


class ParamMatrixRegistrationTests(unittest.TestCase):
    """Many jurisdiction × inventory combinations."""

    JURISDICTIONS = (
        "California",
        "Maryland",
        "Oregon",
        "Arizona",
        "New York",
    )

    def test_stale_fill_only_cleared_for_verified_jurisdiction(self) -> None:
        for claimed in self.JURISDICTIONS:
            for verified_name in self.JURISDICTIONS:
                with self.subTest(claimed=claimed, verified=verified_name):
                    draft = ProposalDraft(
                        rfpId="r",
                        updatedAt="t",
                        sections=[
                            ProposalSection(
                                id="section-1-business-info",
                                title="1.3",
                                content=(
                                    f"### State Registrations\n\n| {verified_name} | Active |\n"
                                ),
                            ),
                            ProposalSection(
                                id="lic",
                                title="License",
                                content=(
                                    f"[MANUAL FILL: Sonja — do not assert {claimed} "
                                    "business registration until it appears in "
                                    "companyfacts / Section 1.3 State Registrations.]"
                                ),
                            ),
                        ],
                    )
                    updated, _ = scrub_unverified_state_registration_claims(draft)
                    body = updated.sections[1].content or ""
                    if claimed == verified_name:
                        self.assertNotIn("do not assert", body.casefold())
                    else:
                        self.assertIn("do not assert", body.casefold())


class ParamMatrixWordGlueTests(unittest.TestCase):
    STEMS = (
        ("decision", "the", "decisionthe"),
        ("choice", "ust", "choiceust"),
        ("choice", "just", "choicejust"),
        ("approach", "the", "approachthe"),
        ("design", "just", "designjust"),
    )

    def test_splice_matrix(self) -> None:
        for stem, tail, glued in self.STEMS:
            with self.subTest(glued=glued):
                raw = f"That is a deliberate {glued} timing before launch."
                cleaned, _ = scrub_rev6_voice_patterns(raw)
                self.assertNotIn(glued, cleaned.casefold())
                if tail == "ust":
                    self.assertIn(f"{stem} just", cleaned.casefold())
                else:
                    self.assertIn(f"{stem} {tail}", cleaned.casefold())


if __name__ == "__main__":
    unittest.main()
