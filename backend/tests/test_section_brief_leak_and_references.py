"""Section briefs must not reach the client, and References must get evidence.

Two reported defects, same underlying shape — the writer had planning text but
no facts, so it wrote about the section instead of writing the section:

1. Agents paraphrased their own private brief into the proposal body ("This
   section is important because…"), which then shipped to the designer as-is.
2. The References tab came back empty or as process narration ("select three
   references, obtain contact info"), because no retrieval lane ever asked the
   KB for past client reference contacts.
"""

from __future__ import annotations

from app.services.proposal_intelligence.agents.retrieval_planner import (
    client_reference_queries,
    section_wants_client_references,
)
from app.services.proposal_drafting_prompts import format_proof_points_block
from app.services.proposal_fulfill_rfp_structure import (
    RfpSectionSpec,
    outline_sections_from_rfp_specs,
)
from app.services.proposal_hollow_kb_fill import section_answers_missing
from app.services.proposal_manuscript import (
    strip_brief_echo_sentences,
    strip_schema_description_tables,
)
from app.services.proposal_rfp_compliance import requirement_likely_covered

PURPOSE = (
    "Demonstrate to the evaluator that zo agency has delivered comparable "
    "public outreach work for county government clients."
)
SUCCESS = "Evaluator can score our public outreach experience from this tab alone."
INSTRUCTIONS = "Name specific comparable campaigns; do not rehash the approach tab."
DIRECTIVES = [PURPOSE, INSTRUCTIONS, SUCCESS]


class TestBriefEchoStripping:
    def test_removes_a_paraphrase_of_the_brief_purpose(self):
        body = (
            "This section is important because it demonstrates to the evaluator "
            "that zo agency has delivered comparable public outreach work for "
            "county government clients. We ran the 2023 Clark County recycling "
            "campaign across 14 zip codes."
        )
        out = strip_brief_echo_sentences(body, DIRECTIVES)
        assert "important because" not in out
        assert "Clark County recycling" in out

    def test_removes_a_success_definition_echo(self):
        body = (
            "The evaluator should be able to score our public outreach experience "
            "from this tab alone. Our team completed nine county campaigns since 2019."
        )
        out = strip_brief_echo_sentences(body, DIRECTIVES)
        assert "score our public outreach experience" not in out
        assert "nine county campaigns" in out

    def test_keeps_real_content_that_reuses_brief_vocabulary(self):
        """A brief exists to seed the vocabulary of the section. Overlap alone
        must never delete substance — that is why a meta marker is also required."""
        body = (
            "zo agency has delivered public outreach work for county government "
            "clients in Oregon, Washington and Arizona since 2016."
        )
        assert strip_brief_echo_sentences(body, DIRECTIVES) == body

    def test_keeps_a_transition_that_does_not_echo_the_brief(self):
        body = "This section lists our three references in order of contract size."
        assert strip_brief_echo_sentences(body, DIRECTIVES) == body

    def test_never_touches_headings_tables_or_handoff_tags(self):
        body = (
            "## References\n"
            "| Client | Contact |\n"
            "| --- | --- |\n"
            "| Clark County | [VERIFY: contact name] |\n"
            "[MANUAL FILL: Sonja — this section is important because the evaluator "
            "should be able to score our public outreach experience]\n"
        )
        assert strip_brief_echo_sentences(body, DIRECTIVES) == body

    def test_drops_a_bullet_that_is_pure_echo(self):
        body = (
            "- This section is important because it demonstrates to the evaluator "
            "that zo agency has delivered comparable public outreach work.\n"
            "- Clark County, 2023, 14 zip codes.\n"
        )
        out = strip_brief_echo_sentences(body, DIRECTIVES)
        assert "important because" not in out
        assert "Clark County, 2023" in out

    def test_no_directives_is_a_no_op(self):
        body = "This section is important because it matters a great deal."
        assert strip_brief_echo_sentences(body, []) == body
        assert strip_brief_echo_sentences(body, ["", "   "]) == body

    def test_empty_content_is_safe(self):
        assert strip_brief_echo_sentences("", DIRECTIVES) == ""


class TestReferenceRetrievalLane:
    def test_reference_tabs_are_recognised(self):
        assert section_wants_client_references("References")
        assert section_wants_client_references("Section 5 — Client References")
        assert section_wants_client_references("6. REFERENCES AND PAST CLIENTS")

    def test_rfp_document_reference_tabs_are_not(self):
        """These are about the RFP's own paperwork, not past clients."""
        assert not section_wants_client_references("Cross-Reference Matrix")
        assert not section_wants_client_references("Referenced Documents")
        assert not section_wants_client_references("Reference Number and Addenda")

    def test_unrelated_tabs_are_not(self):
        assert not section_wants_client_references("Approach and Methodology")
        assert not section_wants_client_references("Cost Proposal")

    def test_queries_ask_for_contact_records_not_case_study_narrative(self):
        queries = client_reference_queries("Kitsap County", "public sector")
        joined = " ".join(queries).casefold()
        assert "contact" in joined
        assert "phone" in joined or "email" in joined
        assert any("kitsap county" in q.casefold() for q in queries)
        assert any("public sector" in q.casefold() for q in queries)

    def test_queries_survive_missing_client_and_sector(self):
        queries = client_reference_queries("", "")
        assert len(queries) >= 2
        assert all(q.strip() for q in queries)


class TestRequirementCoverageProximity:
    """Coverage means answered in one passage, not keywords dusted over a packet.

    The old check asked only whether half a requirement's keywords appeared
    ANYWHERE in the manuscript blob, so prose in one tab could mark another
    tab's requirement satisfied.
    """

    REQ = (
        "Describe your quality control process for translating outreach "
        "materials into Spanish"
    )

    def test_a_real_answer_counts_as_covered(self):
        body = (
            "Quality control for Spanish-language work follows a three-step "
            "process: our bilingual strategist drafts, a certified translator "
            "reviews, and the county liaison approves before any outreach "
            "materials are printed."
        )
        assert requirement_likely_covered(self.REQ, body)

    def test_keywords_scattered_across_unrelated_tabs_do_not_count(self):
        scattered = (
            "Quality is our watchword. "
            + "x" * 4000
            + " We control costs tightly. "
            + "y" * 4000
            + " Translating research into action. "
            + "z" * 4000
            + " Spanish."
        )
        assert not requirement_likely_covered(self.REQ, scattered)

    def test_empty_manuscript_is_not_covered(self):
        assert not requirement_likely_covered(self.REQ, "")

    def test_requirement_with_no_scorable_tokens_is_not_flagged(self):
        """Short stopword-only requirements must not manufacture false gaps."""
        assert requirement_likely_covered("Use the form", "anything at all")

    def test_answer_spanning_a_few_paragraphs_still_counts(self):
        body = (
            "Our quality control approach is documented end to end.\n\n"
            "Every piece is translated by a certified linguist.\n\n"
            "Outreach materials in Spanish are proofed by the county liaison "
            "before release, completing the process."
        )
        assert requirement_likely_covered(self.REQ, body)


class TestAllEchoSectionsRouteToRefill:
    """A section that is ONLY agent instruction must not ship, and must not stay
    blank either — it is emptied so the wired hollow-fill pass rewrites it.

    Both halves matter: preserving the text would ship the exact defect this
    function removes, and leaving a lone heading would slip past the refill
    trigger and ship as an empty heading.
    """

    def test_an_all_echo_section_is_emptied_for_refill(self):
        body = (
            "## Executive Summary\n\n"
            "This section is important because it demonstrates to the evaluator "
            "that zo agency has delivered comparable public outreach work."
        )
        out = strip_brief_echo_sentences(body, DIRECTIVES)
        assert out == ""
        # Empty body is exactly what the hollow-fill pass triggers on.
        assert section_answers_missing(out)

    def test_no_agent_instruction_survives_into_the_draft(self):
        body = (
            "The purpose of this section is to demonstrate to the evaluator that "
            "zo agency has delivered comparable public outreach work."
        )
        out = strip_brief_echo_sentences(body, DIRECTIVES)
        assert "purpose of this section" not in out
        assert "evaluator" not in out

    def test_structure_only_sections_are_untouched(self):
        body = "## References\n\n| Client | Contact |\n| --- | --- |\n"
        assert strip_brief_echo_sentences(body, DIRECTIVES) == body

    def test_mixed_content_still_loses_only_the_echo(self):
        body = (
            "This section is important because it demonstrates to the evaluator "
            "that zo agency has delivered comparable public outreach work. "
            "We ran the 2023 Clark County recycling campaign across 14 zip codes."
        )
        out = strip_brief_echo_sentences(body, DIRECTIVES)
        assert "important because" not in out
        assert "Clark County recycling" in out
        assert out.strip()


class TestProofPointsNeverCarryPlanningText:
    """The root cause of instructions-as-content, fixed at the source.

    A section's `purpose` was being written into ProofPoint.narrativeHook and
    rendered under a header telling the writer to LEAD WITH it as verified
    evidence — so the pipeline was handing the model its own brief labelled as
    proof material. The model was not misbehaving; it was doing as told.
    """

    def test_planned_placeholders_are_not_shown_as_verified_proof(self):
        block = format_proof_points_block(
            [
                {
                    "requirement": "Two examples of scaling existing infrastructure",
                    "caseStudy": "Two examples of scaling existing infrastructure",
                    "narrativeHook": "",
                    "relevance": "planned",
                }
            ],
            section_id="rfp-sec-2",
        )
        assert block == "", "an evidence NEED is not a delivered case study"

    def test_real_proof_points_still_render(self):
        block = format_proof_points_block(
            [
                {
                    "requirement": "Scaled an existing brand's digital performance",
                    "caseStudy": "City of Umatilla — Digital Campaign",
                    "kbSource": "03_CS_City of Umatilla.pdf",
                    "narrativeHook": "We grew session volume 3x on their existing site.",
                    "relevance": "high",
                    "sectionIds": ["rfp-sec-2"],
                }
            ],
            section_id="rfp-sec-2",
        )
        assert "City of Umatilla" in block
        assert "We grew session volume" in block

    def test_a_mixed_set_drops_only_the_placeholders(self):
        block = format_proof_points_block(
            [
                {
                    "requirement": "need",
                    "caseStudy": "need",
                    "relevance": "planned",
                    "sectionIds": ["rfp-sec-2"],
                },
                {
                    "requirement": "Scaled existing infrastructure",
                    "caseStudy": "Christopher Ranch — Brand Refresh",
                    "relevance": "high",
                    "sectionIds": ["rfp-sec-2"],
                },
            ],
            section_id="rfp-sec-2",
        )
        assert "Christopher Ranch" in block
        assert block.count("- Requirement:") == 1

    def test_assembler_no_longer_puts_purpose_in_the_hook(self):
        """The purpose text must never reach a field the writer leads with."""
        import inspect

        from app.services.proposal_intelligence import assembler

        src = inspect.getsource(assembler)
        assert "narrativeHook=brief.purpose" not in src


class TestSubmissionWrapperExpansion:
    """A buyer's "what to send us" list is not a section — its headings are.

    Live-run defect on the Gilroy Garlic Festival RFP: the Align extract path
    returned ONE spec ("4. Proposal Submission Requirements") whose
    required_headings were the five real deliverables, so the whole proposal
    collapsed into a single tab. The planner LLM forbids exactly this ("Do NOT
    create a wrapper tab ... when the individual headings already exist") but
    that path SHORT-CIRCUITS the planner, so the rule never ran.
    """

    @staticmethod
    def _spec(title, headings, **kw):
        return RfpSectionSpec(
            rfp_title=title,
            required_headings=list(headings),
            instructions=kw.get("instructions", "Submit the following items."),
            evaluation_weight=kw.get("evaluation_weight", ""),
            same_ask_as=[],
            satisfied_by_static_company_block=False,
            mandated_submission_format=kw.get("mandated", True),
        )

    GILROY_HEADINGS = [
        "Executive Summary",
        "Case Studies",
        "Strategic Growth Approach",
        "Budget & Cost Breakdown",
        "References",
    ]

    def test_the_wrapper_becomes_its_headings(self):
        secs = outline_sections_from_rfp_specs(
            [self._spec("4. Proposal Submission Requirements", self.GILROY_HEADINGS)],
            section_factory=None,
        )
        assert [s["title"] for s in secs] == self.GILROY_HEADINGS

    def test_downstream_title_keyed_machinery_can_now_see_the_tabs(self):
        """The collapse was costly precisely because later passes key off titles."""
        secs = outline_sections_from_rfp_specs(
            [self._spec("4. Proposal Submission Requirements", self.GILROY_HEADINGS)],
            section_factory=None,
        )
        titles = [s["title"] for s in secs]
        assert any(section_wants_client_references(t) for t in titles)
        assert any("Budget" in t for t in titles)

    def test_a_real_parent_section_is_not_expanded(self):
        """Numbered sub-asks belong INSIDE their parent tab, not beside it."""
        secs = outline_sections_from_rfp_specs(
            [
                self._spec(
                    "SECTION III — Technical Approach",
                    ["III.1 Staffing", "III.2 Schedule", "III.3 QA Plan"],
                )
            ],
            section_factory=None,
        )
        assert len(secs) == 1
        assert secs[0]["title"] == "SECTION III — Technical Approach"
        assert len(secs[0]["children"]) == 3

    def test_too_few_headings_is_not_treated_as_a_packet_list(self):
        secs = outline_sections_from_rfp_specs(
            [self._spec("Proposal Submission Requirements", ["Cover Letter", "Price"])],
            section_factory=None,
        )
        assert len(secs) == 1, "2 headings reads as a real section, not a packet list"


class TestSchemaDescriptionTables:
    """A table that describes its own schema is instructions, not content.

    Shipped on the Gilroy References tab: headers FIELD | WHAT WE PROVIDE with
    cells like "Client name and sector" — the writer explaining what a reference
    entry WOULD contain instead of naming three references. Invisible to
    strip_brief_echo_sentences, which never touches table rows.
    """

    GILROY = (
        "We stand behind our work with references who can speak directly to "
        "the kind of engagement Gilroy is evaluating.\n\n"
        "### Reference Format\n\n"
        "| FIELD | WHAT WE PROVIDE |\n"
        "| --- | --- |\n"
        "| Organization | Client name and sector |\n"
        "| Contact | Name and title of the person who directed our engagement |\n"
        "| Phone & Email | Direct contact information, not routed through us |\n\n"
        "[MANUAL FILL: Sonja — insert complete reference contacts.]\n"
    )

    def test_the_schema_table_is_removed(self):
        out = strip_schema_description_tables(self.GILROY)
        assert "WHAT WE PROVIDE" not in out
        assert "Client name and sector" not in out

    def test_its_orphaned_heading_goes_with_it(self):
        assert "Reference Format" not in strip_schema_description_tables(self.GILROY)

    def test_real_prose_and_the_handoff_tag_survive(self):
        out = strip_schema_description_tables(self.GILROY)
        assert "We stand behind our work" in out
        assert "[MANUAL FILL: Sonja" in out

    def test_a_genuine_reference_table_is_untouched(self):
        real = (
            "| Organization | Contact | Phone | Email |\n"
            "| --- | --- | --- | --- |\n"
            "| Clark County | Jane Roe, Director | 555-0100 | j@clark.gov |\n"
        )
        assert strip_schema_description_tables(real) == real

    def test_a_budget_table_is_untouched(self):
        budget = (
            "| Item | Amount |\n"
            "| --- | --- |\n"
            "| Website maintenance | $24,000 |\n"
        )
        assert strip_schema_description_tables(budget) == budget

    def test_text_with_no_tables_is_returned_unchanged(self):
        body = "Just prose, no pipes here at all."
        assert strip_schema_description_tables(body) == body


class TestSubmissionWrapperNeverMintsItsOwnTab:
    def test_align_does_not_re_add_the_wrapper(self):
        """outline_sections_from_rfp_specs expands the wrapper into tabs, then
        ensure_missing_scored_section_stubs put the wrapper straight back —
        six tabs on a five-deliverable RFP."""
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.services.proposal_fulfill_rfp_structure import (
            ensure_missing_scored_section_stubs,
        )

        headings = [
            "Executive Summary",
            "Case Studies",
            "Strategic Growth Approach",
            "Budget & Cost Breakdown",
            "References",
        ]
        spec = RfpSectionSpec(
            rfp_title="4. Proposal Submission Requirements",
            required_headings=headings,
            instructions="Submit the following items.",
            evaluation_weight="",
            same_ask_as=[],
            satisfied_by_static_company_block=False,
            mandated_submission_format=True,
        )
        draft = ProposalDraft(
            rfpId="r",
            updatedAt="2026-09-02T00:00:00Z",
            sections=[
                ProposalSection(
                    id=f"rfp-structure-{h.lower().replace(' ', '-').replace('&', '')}",
                    title=h,
                    source="generated",
                    mode="write",
                    status="outline",
                )
                for h in headings
            ],
        )
        out, _logs = ensure_missing_scored_section_stubs(draft, [spec])
        titles = [s.title for s in out.sections]
        assert titles == headings
        assert not any("Submission Requirement" in t for t in titles)
