"""Dynamic Section Planner — nested proposal outline."""

from __future__ import annotations

import logging

from app.services.proposal_evaluation_coverage import (
    drop_scoring_rubric_duplicate_sections,
    ensure_missing_submittals_coverage,
    ensure_scored_criteria_coverage,
    evaluation_is_published_response_form,
    evaluation_priority_brief,
    evaluation_response_char_limit,
    min_outline_sections_for_evaluation,
    order_outline_sections_by_authority_titles,
)
from app.services.proposal_rfp_excerpt import (
    closing_package_excerpt,
    submission_documents_excerpt,
)
from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import (
    OutlineSection,
    ProposalExecutionPlan,
    ProposalOutline,
)

logger = logging.getLogger(__name__)
AGENT = "dynamic_section_planner"

_SYSTEM = """Dynamic Section Planner. Decide which proposal sections must be generated
FOR THIS RFP ONLY — read the RFP TOC / submission instructions in the excerpt.

RULE 0 — SUBMISSION FORMAT OUTRANKS SCOREBOARD LABELS WHEN THEY CONFLICT.
When THIS RFP publishes a section that defines mandatory proposal content format,
submission layout, or required packet structure, that section's headings and order
outrank evaluation-criteria numbers or labels from elsewhere in the RFP for tab
TITLES and ORDER. Copy the buyer's format-section labels verbatim — do not rename
tabs using evaluation-point numbering when the format section uses different labels.

The user message lists this RFP's SCORED EVALUATION CRITERIA with their points.
Those criteria are not hints; they are the sections an evaluator opens, scores and totals.
- EVERY scored parent criterion gets its OWN tab. No exceptions, no merging two scored
  criteria into one tab, no folding a scored criterion into a forms/exhibit tab.
  A 160-point criterion with no tab of its own is 160 points forfeited.
- Use the buyer's own heading for the tab title, keeping their section code when they
  publish one ("SECTION III — Strategic Planning", "Tab 4 — Technical Approach").
- Numbered sub-asks (III.1, III.2 …) are NOT tabs. They are required sub-headings INSIDE
  the parent tab — list them in that section's `children` as the codes they carry.
- Order scored tabs the way the RFP's criteria form orders them.
- Set evaluationWeight to the criterion's points and protectFromCap=true on every scored tab.
- When the RFP publishes an evaluation-criteria RESPONSE FORM (a form the offeror fills in
  per criterion), the scored criteria ARE the proposal body. Do NOT emit a single
  "Evaluation Criteria Response Form" wrapper tab — emit the criteria themselves.
- Anything NOT scored and NOT a required submittal is padding. Drop it. In particular drop
  restatements of the buyer's own Scope of Work, and acknowledgment essays for documents
  the RFP says to send only on request (sample agreements, insurance certificates).

Rules:
- zö static Sections 1–3 (company / team / experience) are ALWAYS drafted first and keep
  their existing titles (Who We Are, Organizational Structure, Business Information,
  Certifications, Insurance, bios, our work). Do not rename or merge them in the outline.
- Every section AFTER that must come ONLY from THIS RFP's TOC, submission checklist,
  evaluation criteria, and required forms — read the full excerpt including mid-document
  forms (references tables, vendor questionnaire, pricing/quotation forms).
- Emit those RFP-varying (intelligence) tabs IN THIS RFP's stated order only —
  copy the buyer's TOC / "shall submit" sequence. Do not apply a default
  cover-letter / technical / cost stack. Omit any package this RFP does not name.
  Company identity in the TOC is already Sections 1.1–1.5 — do NOT outline a
  second company-background essay tab.
- Include a section ONLY if the RFP (or its evaluation criteria) clearly asks for it.
- Do NOT invent a default "Methodology" / "Timeline" / "Budget" stack.
- Prefer the RFP's numbered outline when present (including nested 4, 4.1, 4.2).
- ONE section per distinct RFP ask — do NOT add near-duplicate tabs that would rehash
  the same proof already covered by Sections 1–3 or another RFP tab.
- NEVER outline tabs that only restate static Sections 1–3 identity blocks already written
  before RFP tabs (Who We Are, Company History + Client Roster, Organizational Structure,
  Team Overview bios as a block, Insurance Information / Certificate of Insurance coverage
  narrative — limits, carriers, GL/E&O/workers/cyber). Coverage facts live in Section 1.5;
  if the RFP only needs a returned COI PDF, use a short attachments checklist / MANUAL FILL
  for the file — do NOT add a second insurance essay tab.
- DO keep scored RFP-specific asks even when related:
  Sample Work Portfolio, Agency Requirements matrices, Qualifications/Experience when the
  RFP TOC names that heading as an evaluation tab, References forms, Pricing forms, Addenda.
- Prefer a LEAN outline evaluators can finish reading — merge overlapping asks into one tab
  when the RFP language allows; never pad with optional narrative the RFP does not score.
- HARD CAP: emit at most the max section count given in the user message (typically 8–18
  RFP tabs). One tab per Direct Question / TOC heading — NEVER one tab per bullet, sub-bullet,
  evaluation criterion synonym, or form field. If the RFP lists many criteria, fold related
  asks under the buyer's parent TOC heading.
- ORDER: emit RFP tabs ONLY in the buyer's Proposal Content / "organize as follows" /
  submission-package sequence. Never invent a default Cover→Technical→Cost stack and never
  reorder lettered packages (A then B then C…).
- EVALUATION vs SUBMISSION (critical):
  * When the RFP publishes a scored RESPONSE FORM (offeror fills each numbered criterion),
    those criteria ARE the proposal body — emit each scored parent as its own tab.
  * When the RFP only publishes a scoring RUBRIC / weights table (how the panel scores),
    those labels are NOT submission sections. Emit only the Proposal Content / TOC packages;
    put evaluationWeight on the matching TOC tab. Never duplicate a rubric label as a
    second tab beside Experience / Personnel / Cost / References packages that already
    answer it.
- SUBMISSION PACKAGE RFPs (Cover Letter + Direct Questions + Budget, e.g. MTC-style):
  Emit ONLY the buyer-named response packages — typically:
  (1) ONE Cover Letter tab (contact + interest + qualifications — never split),
  (2) ONE tab per Direct Question heading (General Experience, Specific Experience,
      Approach & Timetable, Requirements, References, Additional Information),
  (3) ONE Budget tab that covers detailed narrative, rates/fees, AND milestone
      disbursement schedule together.
  Do NOT create separate tabs for every bullet under a Direct Question.
  Do NOT create a wrapper tab named "Address all Direct Questions…" when the individual
  Direct Question headings already exist.
  Do NOT create essay tabs for contractual acknowledgments (no pre-award reimbursement,
  N-day deliverable review period) — fold those into Requirements or Cover Letter.
  Do NOT duplicate Budget / Cover Letter under both a short label and a long "Budget — …"
  / "Cover Letter — …" label.
- Agency Requirements / capability checklist rows (G.1, G.2, … G.16 or Section III A.1–12):
  emit ONE tab only — "Agency Requirements — Capability Matrix (G.1–G.16…)" — covering every
  service line in a single matrix/response. Do NOT create a separate tab per G.# / service.
- Across the WHOLE outline (not only vs Sections 1–3): every tab must have a DISTINCT job.
  If two titles would produce similar prose, KEEP THE FULLER RFP TITLE and drop the shorter one.
  Example: drop bare "Price" when "Proposal Pricing — Hourly Rates by Labor Category" exists.
- Do NOT invent generic filler tabs unless the RFP TOC literally uses that heading.
- TITLES MUST NOT BE SIMPLIFIED OR BORING. Copy the buyer's FULL TOC / submission wording.
  Never rename "Cost Proposal / Fee Schedule — Labor Category Rates" to bare "Price".
  Never rename "Sample Work Submission (Portfolio)" to bare "Portfolio".
  Never rename "Qualifications and Experience of the Firm" to bare "Qualifications".
  Keep section numbers from the RFP when present (e.g. 4.2 …).
- IMPORTANT: when evaluation is a RESPONSE FORM, scored parent tabs MUST be included.
  When evaluation is only a RUBRIC, do NOT mint tabs from criterion labels — stamp weights
  onto the buyer's TOC packages instead.
-   CLOSING / compliance package items MUST be included when the RFP names them (even if forms):
  References, Acknowledgement of Addenda, Non-Collusion / Ownership Disclosure, Pricing
  Proposal Form, Authorized Signature, Exemplar Agreement acknowledgment, Offeror Commitment
  & Closing Statement, Generative AI Disclosure (when the RFP requires it), Vendor
  Questionnaire / OpenGov portal fields (when the RFP is portal-submitted), and attachment
  CHECKLISTS (W-9 / signed forms / "attach COI PDF").
  Do NOT add a narrative "Certificate of Insurance" / insurance-coverage essay — Section 1.5
  already owns coverage; attachment items are file-return checklists only.
  ATTACHMENTS (COI, W-9, signed/notarized forms, exhibits the buyer supplies): outline them
  as checklist tabs only — never as essay topics. The writer will emit short
  [DESIGNER NOTE: Attach …] / [MANUAL FILL: attach …] handoffs for layout, not prose.
  Do NOT invent acknowledgment essay tabs for standing contract clauses that are not a
  named proposal section (pre-award cost rules, deliverable review windows) unless the RFP
  TOC lists them as their own response heading.
- For References: capture exact count, institution type, and contact fields from the RFP.
- For Pricing/Quotation forms: include as a section when the RFP supplies a form; do NOT
  replace it with a custom Section A/B/C/D narrative structure in the outline.
- Parse "Documents to be Submitted" / "Forms provided by [buyer] that must be returned with proposal":
  include signed compliance forms and attachment list items as outline sections
  (forms may be checklist + [MANUAL FILL]).
- Do NOT copy another client's outline. Do NOT write section prose.
- Mark required=true only for mandatory submission items; use conditionalReason for optional ones.
- When an evaluation criterion clearly matches a section, set evaluationWeight to that criterion's points.
- Set protectFromCap=true for mandatory submission instruments the buyer must receive
  (scored Cost / pricing form, official quotation form, required certifications, AI disclosure,
  references package, addenda acknowledgement, portal/vendor questionnaire, attachment checklists).
  Do NOT set protectFromCap for optional narrative padding.
- Set submissionInstrument to exactly one of:
  cost | form | disclosure | references | narrative | null
  Use "cost" for the scored pricing INSTRUMENT (hourly labor-category table, blended rate form,
  official quotation/pricing proposal form) — NOT for optional fee narrative that is not the
  scored Cost deliverable. Use "disclosure" for AI / generative-AI disclosures.
  Use "references" for reference forms. Use "form" for other signed compliance forms.
  Use "narrative" for approach / experience essays. Leave null only when unsure.

Return JSON only:
{
  "sections": [
    {
      "id": "rfp-sec-1",
      "title": "Full RFP heading — never a shortened boring label",
      "order": 1,
      "required": true,
      "conditionalReason": "",
      "parentId": null,
      "children": [],
      "dependencies": [],
      "evaluationWeight": null,
      "protectFromCap": false,
      "submissionInstrument": null
    }
  ],
  "confidence": 0.0
}
"""


_ZO_FIRST_OUTLINE_RULES = """- zö static Sections 1–3 (company / team / experience) are ALWAYS drafted first and keep
  their existing titles (Who We Are, Organizational Structure, Business Information,
  Certifications, Insurance, bios, our work). Do not rename or merge them in the outline.
- Every section AFTER that must come ONLY from THIS RFP's TOC, submission checklist,
  evaluation criteria, and required forms — read the full excerpt including mid-document
  forms (references tables, vendor questionnaire, pricing/quotation forms).
- Emit those RFP-varying (intelligence) tabs IN THIS RFP's stated order only —
  copy the buyer's TOC / "shall submit" sequence. Do not apply a default
  cover-letter / technical / cost stack. Omit any package this RFP does not name.
  Company identity in the TOC is already Sections 1.1–1.5 — do NOT outline a
  second company-background essay tab.
- Include a section ONLY if the RFP (or its evaluation criteria) clearly asks for it.
- Do NOT invent a default "Methodology" / "Timeline" / "Budget" stack.
- Prefer the RFP's numbered outline when present (including nested 4, 4.1, 4.2).
- ONE section per distinct RFP ask — do NOT add near-duplicate tabs that would rehash
  the same proof already covered by Sections 1–3 or another RFP tab.
- NEVER outline tabs that only restate static Sections 1–3 identity blocks already written
  before RFP tabs (Who We Are, Company History + Client Roster, Organizational Structure,
  Team Overview bios as a block, Insurance Information / Certificate of Insurance coverage
  narrative — limits, carriers, GL/E&O/workers/cyber). Coverage facts live in Section 1.5;
  if the RFP only needs a returned COI PDF, use a short attachments checklist / MANUAL FILL
  for the file — do NOT add a second insurance essay tab."""

_STRICT_RFP_OUTLINE_RULES = """- STRICT RFP outline mode: there is NO Zo Agency Sections 1–3 template in this manuscript.
- Emit ONLY what THIS RFP demands: TOC / submission-package headings (verbatim titles + order)
  UNION every scored parent criterion and required form/submittal not already covered by a
  TOC tab. Use the buyer's own wording for titles whenever possible.
- Firm qualifications, company overview, key personnel / bios, past performance / case
  studies, insurance, and certifications are REAL outline tabs when the RFP names them —
  companyfacts / bios / case studies are woven into those tabs at draft time.
- Do NOT invent Zo-numbered Who We Are / Team Overview / Our Work shells.
- Include a section ONLY if the RFP (or its evaluation criteria) clearly asks for it.
- Do NOT invent a default "Methodology" / "Timeline" / "Budget" stack.
- Prefer the RFP's numbered outline when present (including nested 4, 4.1, 4.2).
- ONE section per distinct RFP ask — do NOT add near-duplicate tabs that rehash the same ask.
- Insurance / COI: if the RFP only needs a returned PDF, use a checklist / MANUAL FILL tab;
  if the RFP asks for an insurance narrative or coverage response, keep that as its own tab."""


def _planner_system_prompt(outline_mode: str) -> str:
    mode = (outline_mode or "zo_template").strip().lower()
    if mode == "strict_rfp":
        return _SYSTEM.replace(_ZO_FIRST_OUTLINE_RULES, _STRICT_RFP_OUTLINE_RULES)
    return _SYSTEM


def _parse_page_limit(rfp_meta: dict[str, str] | None) -> int | None:
    if not rfp_meta:
        return None
    raw = (rfp_meta.get("pageLimit") or "").strip()
    if not raw:
        return None
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


async def run_dynamic_section_planner(
    *,
    plan: ProposalExecutionPlan,
    rfp_context: str,
    rfp_meta: dict[str, str] | None = None,
    outline_mode: str = "zo_template",
) -> ProposalExecutionPlan:
    from app.services.proposal_outline_dedup import max_rfp_outline_sections

    mode = (outline_mode or "zo_template").strip().lower()
    if mode not in {"zo_template", "strict_rfp"}:
        mode = "zo_template"
    skip_static = mode == "strict_rfp"

    page_limit = _parse_page_limit(rfp_meta)
    evaluation = plan.opportunity.evaluation
    # The RFP's own criteria count sets the floor — our page arithmetic cannot
    # shrink a buyer-published section list.
    section_cap = max_rfp_outline_sections(
        page_limit,
        min_sections=min_outline_sections_for_evaluation(evaluation),
    )
    scoreboard = evaluation_priority_brief(evaluation)
    response_form = evaluation_is_published_response_form(evaluation)
    if response_form:
        eval_shape_rule = (
            "EVALUATION SHAPE: published scored RESPONSE FORM — emit each scored "
            "parent criterion as its own tab in the buyer's order; NEVER drop a "
            "scored parent to fit the cap (cut unscored narrative instead)."
        )
    else:
        eval_shape_rule = (
            "EVALUATION SHAPE: scoring RUBRIC / weights only — do NOT emit tabs "
            "titled as criterion labels. Emit only Proposal Content / TOC / "
            "required forms packages in the buyer's stated order; stamp "
            "evaluationWeight onto the matching TOC tab."
        )
    package_char_limit = evaluation_response_char_limit(evaluation)
    char_limit_line = (
        f"Per-response character limit stated by this RFP: {package_char_limit} characters "
        "per response field — plan tabs the writer can answer within that budget."
        if package_char_limit
        else "No per-response character limit stated by this RFP."
    )
    from app.services.proposal_fulfill_rfp_structure import (
        align_outline_sections_to_rfp_specs,
        extract_rfp_submission_format_specs,
        outline_sections_from_rfp_specs,
        static_company_block_titles,
    )

    rfp_title = str((rfp_meta or {}).get("title") or "").strip()
    # One Align read (submission-format / packet layout) — NOT the full
    # format+scored+completeness stack, and NOT stacked on top of the planner.
    # When this returns tabs, it *replaces* the planner LLM. Scored criteria
    # coverage below is free (already on the plan). Completeness still runs
    # once against the outline actually produced.
    structure_specs = await extract_rfp_submission_format_specs(
        rfp_context,
        rfp_title=rfp_title,
        existing_section_titles=(
            [] if skip_static else static_company_block_titles()
        ),
    )
    used_align_extract = False
    provider = ""
    if structure_specs:
        from_specs = outline_sections_from_rfp_specs(
            structure_specs,
            section_factory=lambda raw: OutlineSection.model_validate(raw),
            skip_static_dedupe=skip_static,
        )
        if from_specs:
            used_align_extract = True
            logger.info(
                "%s using Align submission-format extract as outline (%d tabs) "
                "— skipping planner LLM",
                AGENT,
                len(from_specs),
            )
            outline = ProposalOutline(sections=from_specs, confidence=0.85)
    if not used_align_extract:
        raw, provider = await safe_chat_json(
            [
                {"role": "system", "content": _planner_system_prompt(mode)},
                {
                    "role": "user",
                    "content": (
                        f"{scoreboard}\n\n"
                        f"{char_limit_line}\n"
                        f"{eval_shape_rule}\n"
                        f"OUTLINE MODE: {mode}.\n"
                        + (
                            f"HARD MAXIMUM outline tabs (full manuscript — no Zo 1–3 shell): {section_cap}. "
                            if skip_static
                            else f"HARD MAXIMUM RFP outline tabs (excluding static Sections 1–3): {section_cap}. "
                        )
                        + f"Emit at most {section_cap} sections in the JSON array — merge aggressively.\n"
                        f"Page limit from RFP: {page_limit if page_limit else 'not stated'}. "
                        f"Under a tight page budget, emit ONLY the buyer's required Proposal "
                        f"Content / submission-package tabs in their stated order — no rubric "
                        f"duplicates, no optional padding.\n\n"
                        f"Understanding:\n{plan.opportunity.understanding.model_dump_json()}\n"
                        f"Compliance item count: {len(plan.opportunity.compliance.items)}\n"
                        f"Evaluation:\n{plan.opportunity.evaluation.model_dump_json()}\n"
                        f"Scope:\n{plan.opportunity.scope.model_dump_json()}\n"
                        f"RFP excerpt (structure/TOC/submission forms):\n{rfp_context[:50000]}\n\n"
                        f"Submission checklist excerpt (documents to return — read even if TOC is elsewhere):\n"
                        f"{submission_documents_excerpt(rfp_context)[:20000]}\n\n"
                        f"Closing / forms / attachments excerpt (must select these when present):\n"
                        f"{closing_package_excerpt(rfp_context)[:20000]}"
                    ),
                },
            ],
            max_tokens=3072,
            agent_name=AGENT,
        )
        try:
            outline = ProposalOutline.model_validate(raw or {})
        except Exception as exc:
            logger.warning("%s validation failed: %s", AGENT, exc)
            outline = ProposalOutline(confidence=0.2)
        if not outline.sections:
            # Prefer submission-format titles over inventing a scoreboard outline.
            titles: list[str] = []
            if response_form:
                for crit in plan.opportunity.evaluation.criteria[:6]:
                    name = (crit.name or "").strip()
                    if name and name.casefold() not in {t.casefold() for t in titles}:
                        titles.append(name)
            if not titles:
                titles = ["Technical Approach", "Scope & Deliverables", "Pricing"]
            outline = ProposalOutline(
                sections=[
                    OutlineSection(
                        id=f"rfp-sec-{i}",
                        title=title,
                        order=i,
                        required=True,
                        conditionalReason=(
                            "Fallback from evaluation response form — confirm against RFP TOC"
                            if response_form
                            else "Fallback outline — confirm against RFP TOC"
                        ),
                    )
                    for i, title in enumerate(titles, start=1)
                ],
                confidence=0.35,
            )
    outline.confidence = clamp_confidence(outline.confidence)
    from app.services.proposal_outline_dedup import (
        enforce_outline_section_cap,
        filter_lean_outline_sections,
        merge_closing_components_into_outline,
        stamp_outline_evaluation_weights,
    )

    # Stamp eval weights BEFORE lean filter so scored carve-outs actually fire.
    stamp_outline_evaluation_weights(
        list(outline.sections),
        list(plan.opportunity.evaluation.criteria),
    )
    # Drop scoreboard-label clones before lean hygiene (rubric RFPs only).
    outline.sections, rubric_dropped = drop_scoring_rubric_duplicate_sections(
        list(outline.sections),
        evaluation,
    )
    if rubric_dropped:
        logger.info(
            "%s dropped %d scoring-rubric duplicate tab(s): %s",
            AGENT,
            len(rubric_dropped),
            rubric_dropped[:6],
        )

    kept, dropped = filter_lean_outline_sections(
        list(outline.sections),
        rfp_context=rfp_context,
        skip_static_dedupe=skip_static,
    )
    if not kept and outline.sections:
        # Avoid emptying the outline when generic-filler rules are too aggressive
        # without matching RFP phrasing — still drop static + near-dups.
        kept, dropped_safe = filter_lean_outline_sections(
            list(outline.sections),
            rfp_context=rfp_context,
            drop_generic_filler=False,
            skip_static_dedupe=skip_static,
        )
        dropped = list(dropped) + list(dropped_safe)
    from app.services.proposal_closing_ledger import get_or_extract_closing_ledger
    from app.services.proposal_repository import aget_research_cache

    research = None
    rfp_id = getattr(getattr(plan, "metadata", None), "rfp_id", None) or ""
    if rfp_id:
        research = await aget_research_cache(str(rfp_id))
    closing_ledger, research = await get_or_extract_closing_ledger(
        rfp_context, research=research
    )
    kept, closing_added = merge_closing_components_into_outline(
        kept,
        rfp_context=rfp_context,
        ledger=closing_ledger,
    )
    # Closing merge can reintroduce near-dups — free Jaccard/head-label pass only
    # (no second LLM call; cost stays in the single planner request).
    if closing_added:
        kept, post_dropped = filter_lean_outline_sections(
            kept,
            rfp_context=rfp_context,
            drop_generic_filler=False,
            skip_static_dedupe=skip_static,
        )
        dropped = list(dropped) + list(post_dropped)
    # Deterministic backstop: the planner is an LLM under 20+ anti-bloat rules,
    # and the passes above are all subtractive. Whatever they did, every scored
    # criterion gets a tab of its own here.
    #
    # This MUST run BEFORE the cap. Run after, and the cap budgets its free
    # slots against an outline the scored tabs are still missing from — it then
    # admits unscored filler (a Scope-of-Work restatement, an acknowledgment
    # essay) into slots the buyer's own scored sections were about to claim,
    # and the outline finishes over cap with padding the RFP never asked for.
    # Running first makes every scored tab count as protected, so the cap
    # spends what is left on filler: nothing.
    kept, scored_added, scored_dropped = ensure_scored_criteria_coverage(
        kept,
        evaluation,
        section_factory=lambda raw: OutlineSection.model_validate(raw),
    )
    dropped = list(dropped) + list(scored_dropped)
    # Rubric RFPs: ensure_scored is a no-op, but planner/closing may still have
    # reintroduced scoreboard-label clones — drop again before Align reorder.
    kept, rubric_dropped_2 = drop_scoring_rubric_duplicate_sections(kept, evaluation)
    dropped = list(dropped) + list(rubric_dropped_2)
    # Same stub + reorder + mandated titles as the Align to RFP outline button,
    # applied to the plan before Phase 3 drafts — so generate ships the RFP's
    # required tabs instead of leaving empty stubs for a later button click.
    kept, align_logs = align_outline_sections_to_rfp_specs(
        kept,
        structure_specs,
        section_factory=lambda raw: OutlineSection.model_validate(raw),
        skip_static_dedupe=skip_static,
    )
    align_added = [
        line
        for line in align_logs
        if "added missing scored section stub" in line
    ]
    if align_logs:
        logger.info(
            "%s Align-to-RFP-outline on plan: %s",
            AGENT,
            align_logs[:8],
        )
    # Align / stubs can reintroduce instruction-shaped titles — lean again.
    kept, post_align_dropped = filter_lean_outline_sections(
        kept,
        rfp_context=rfp_context,
        drop_generic_filler=False,
        skip_static_dedupe=skip_static,
    )
    dropped = list(dropped) + list(post_align_dropped)
    # Focused completeness check on the outline actually produced (including
    # any tabs Align-extract just stubbed). Same single-question pass as before.
    kept, exhibit_added = await ensure_missing_submittals_coverage(
        kept,
        rfp_context,
        section_factory=lambda raw: OutlineSection.model_validate(raw),
    )
    kept, rubric_dropped_3 = drop_scoring_rubric_duplicate_sections(kept, evaluation)
    dropped = list(dropped) + list(rubric_dropped_3)
    # Buyer submission-format order is authoritative when Align extract exists.
    authority_titles = [
        str(getattr(spec, "rfp_title", None) or getattr(spec, "title", None) or "").strip()
        for spec in (structure_specs or [])
    ]
    authority_titles = [t for t in authority_titles if t]
    if authority_titles:
        kept = order_outline_sections_by_authority_titles(kept, authority_titles)
    # Recompute the cap floor AFTER the outline exists: min_sections above was
    # derived from the upstream evaluation extraction, which can under-count
    # (e.g. it returns a single criterion with points=None when parsing
    # fails). That must never let the cap shrink below the number of RFP-
    # derived tabs the planner + coverage passes actually produced on this
    # page. The pre-outline section_cap is kept as-is for the prompt text
    # above (the model still needs a target number to aim for before the
    # outline exists); only the value passed to enforce_outline_section_cap
    # is raised here.
    from app.services.proposal_outline_dedup import section_is_rfp_derived

    rfp_derived_count = sum(1 for sec in kept if section_is_rfp_derived(sec))
    effective_section_cap = max(section_cap, rfp_derived_count)

    kept, cap_dropped = enforce_outline_section_cap(kept, effective_section_cap)
    dropped = list(dropped) + list(cap_dropped)
    if dropped:
        logger.info(
            "%s dropped %d lean-outline tab(s): %s",
            AGENT,
            len(dropped),
            dropped[:12],
        )
    if closing_added:
        logger.info(
            "%s added %d closing package tab(s): %s",
            AGENT,
            len(closing_added),
            closing_added[:12],
        )
    if exhibit_added:
        logger.warning(
            "%s completeness check injected %d missing submittal tab(s): %s",
            AGENT,
            len(exhibit_added),
            exhibit_added[:12],
        )
    if scored_added:
        logger.warning(
            "%s injected %d uncovered scored criterion tab(s): %s",
            AGENT,
            len(scored_added),
            scored_added[:12],
        )
    if cap_dropped:
        # These are planner-invented tabs trimmed for page budget — never RFP-
        # derived sections (enforce_outline_section_cap keeps every
        # RFP-derived tab unconditionally). Escalated from info to warning so
        # a page-budget trim of invented padding is visible in normal logs,
        # not just on request.
        logger.warning(
            "%s hard-capped outline to %d tab(s); trimmed %d planner-invented "
            "tab(s) for page budget (RFP-derived tabs are never dropped): %s",
            AGENT,
            effective_section_cap,
            len(cap_dropped),
            cap_dropped[:12],
        )
    if len(kept) > effective_section_cap:
        # RFP-derived tabs alone exceeded the cap — enforce_outline_section_cap
        # kept them all anyway. Surface the overflow instead of leaving it silent.
        logger.warning(
            "%s outline finished with %d tab(s), over the %d-tab page-budget "
            "cap — the RFP itself demands more sections than the page budget "
            "allows; no section was dropped to force the count down.",
            AGENT,
            len(kept),
            effective_section_cap,
        )
    outline.sections = kept

    plan.writing.proposal_outline = outline
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=(
            f"Outline sections: {len(outline.sections)} (cap {section_cap})"
            + (f"; scored criteria added: {len(scored_added)}" if scored_added else "")
            + (f"; closing added: {len(closing_added)}" if closing_added else "")
            + (f"; Align-outline stubs: {len(align_added)}" if align_added else "")
            + (f"; hard-cap dropped: {len(cap_dropped)}" if cap_dropped else "")
        ),
        reason=(
            "Dynamic section plan from THIS RFP "
            + (
                "Align submission-format extract"
                if used_align_extract
                else "structure"
            )
            + " + evaluation + closing package "
            "(lean prompt + near-dup hygiene + hard section cap)"
        ),
        confidence=outline.confidence,
    )
    return plan
