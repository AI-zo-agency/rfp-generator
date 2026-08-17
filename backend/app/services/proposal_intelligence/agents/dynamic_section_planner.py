"""Dynamic Section Planner — nested proposal outline."""

from __future__ import annotations

import logging

from app.services.proposal_rfp_excerpt import (
    closing_package_excerpt,
    submission_documents_excerpt,
)
from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, ProposalOutline

logger = logging.getLogger(__name__)
AGENT = "dynamic_section_planner"

_SYSTEM = """Dynamic Section Planner. Decide which proposal sections must be generated
FOR THIS RFP ONLY — read the RFP TOC / submission instructions in the excerpt.

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
- IMPORTANT scored tabs from evaluation criteria + TOC MUST be included when the RFP names them.
-   CLOSING / compliance package items MUST be included when the RFP names them (even if forms):
  References, Acknowledgement of Addenda, Non-Collusion / Ownership Disclosure, Pricing
  Proposal Form, Authorized Signature, Exemplar Agreement acknowledgment, Offeror Commitment
  & Closing Statement, and attachment CHECKLISTS (W-9 / signed forms / "attach COI PDF").
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
      "evaluationWeight": null
    }
  ],
  "confidence": 0.0
}
"""


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
) -> ProposalExecutionPlan:
    from app.services.proposal_outline_dedup import max_rfp_outline_sections

    page_limit = _parse_page_limit(rfp_meta)
    section_cap = max_rfp_outline_sections(page_limit)
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"HARD MAXIMUM RFP outline tabs (excluding static Sections 1–3): {section_cap}. "
                    f"Emit at most {section_cap} sections in the JSON array — merge aggressively.\n"
                    f"Page limit from RFP: {page_limit if page_limit else 'not stated'}.\n\n"
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
        # Minimal fallback from evaluation emphasis + scope — NEVER force Methodology.
        from app.services.proposal_intelligence.schemas import OutlineSection

        titles: list[str] = []
        for crit in plan.opportunity.evaluation.criteria[:6]:
            name = (crit.name or "").strip()
            if name and name.casefold() not in {t.casefold() for t in titles}:
                titles.append(name)
        if not titles:
            # Prefer concrete RFP-shaped asks over a generic marketing stack.
            titles = ["Technical Approach", "Scope & Deliverables", "Pricing"]
        outline = ProposalOutline(
            sections=[
                OutlineSection(
                    id=f"rfp-sec-{i}",
                    title=title,
                    order=i,
                    required=True,
                    conditionalReason="Fallback from evaluation criteria — confirm against RFP TOC",
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

    kept, dropped = filter_lean_outline_sections(
        list(outline.sections),
        rfp_context=rfp_context,
    )
    if not kept and outline.sections:
        # Avoid emptying the outline when generic-filler rules are too aggressive
        # without matching RFP phrasing — still drop static + near-dups.
        kept, dropped_safe = filter_lean_outline_sections(
            list(outline.sections),
            rfp_context=rfp_context,
            drop_generic_filler=False,
        )
        dropped = list(dropped) + list(dropped_safe)
    kept, closing_added = merge_closing_components_into_outline(
        kept,
        rfp_context=rfp_context,
    )
    # Closing merge can reintroduce near-dups — free Jaccard/head-label pass only
    # (no second LLM call; cost stays in the single planner request).
    if closing_added:
        kept, post_dropped = filter_lean_outline_sections(
            kept,
            rfp_context=rfp_context,
            drop_generic_filler=False,
        )
        dropped = list(dropped) + list(post_dropped)
    kept, cap_dropped = enforce_outline_section_cap(kept, section_cap)
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
    if cap_dropped:
        logger.info(
            "%s hard-capped outline to %d tab(s); dropped %d: %s",
            AGENT,
            section_cap,
            len(cap_dropped),
            cap_dropped[:12],
        )
    outline.sections = kept

    plan.writing.proposal_outline = outline
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=(
            f"Outline sections: {len(outline.sections)} (cap {section_cap})"
            + (f"; closing added: {len(closing_added)}" if closing_added else "")
            + (f"; hard-cap dropped: {len(cap_dropped)}" if cap_dropped else "")
        ),
        reason=(
            "Dynamic section plan from THIS RFP structure + evaluation + closing package "
            "(lean prompt + near-dup hygiene + hard section cap)"
        ),
        confidence=outline.confidence,
    )
    return plan
