"""Dynamic Section Planner — nested proposal outline."""

from __future__ import annotations

import logging

from app.services.proposal_rfp_excerpt import submission_documents_excerpt
from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, ProposalOutline

logger = logging.getLogger(__name__)
AGENT = "dynamic_section_planner"

_SYSTEM = """Dynamic Section Planner. Decide which proposal sections must be generated
FOR THIS RFP ONLY — read the RFP TOC / submission instructions in the excerpt.

Rules:
- zö static Sections 1–3 (company / team / experience) are ALWAYS drafted first; every section
  AFTER that must come ONLY from THIS RFP's TOC, submission checklist, evaluation criteria,
  and required forms — read the full excerpt including mid-document forms (references tables,
  vendor questionnaire, pricing/quotation forms).
- Include a section ONLY if the RFP (or its evaluation criteria) clearly asks for it.
- Do NOT invent a default "Methodology" / "Timeline" / "Budget" stack. Titles must
  match the RFP's own language (Approach, Scope of Work, Project Plan, Fee Schedule, etc.).
- Prefer the RFP's numbered outline when present (including nested 4, 4.1, 4.2).
- Closing / compliance package items (References, Non-Collusion, Ownership Disclosure,
  Pricing Proposal Form, Authorized Signature, COI attachments, Exemplar Agreement
  acknowledgment) MUST be included when the RFP names them — even if they are forms.
- For References: capture exact count, institution type, and contact fields from the RFP.
- For Pricing/Quotation forms: include as a section when the RFP supplies a form; do NOT
  replace it with a custom Section A/B/C/D narrative structure in the outline.
- Parse "Documents to be Submitted" / "Forms provided by [buyer] that must be returned with proposal":
  include Acknowledgement of Addenda, signed compliance forms, and attachment list items as outline
  sections (forms may be checklist + [MANUAL FILL]).
- Parse vendor qualification / company history prompts: if the RFP asks for financial stability
  and/or awards & recognitions as narrative (not just a form), include dedicated outline sections.
- Do NOT copy another client's outline. Do NOT write section prose.
- Mark required=true only for mandatory submission items; use conditionalReason for optional ones.

Return JSON only:
{
  "sections": [
    {
      "id": "rfp-sec-1",
      "title": "Title exactly as RFP frames it",
      "order": 1,
      "required": true,
      "conditionalReason": "",
      "parentId": null,
      "children": [],
      "dependencies": []
    }
  ],
  "confidence": 0.0
}
"""


async def run_dynamic_section_planner(
    *,
    plan: ProposalExecutionPlan,
    rfp_context: str,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Understanding:\n{plan.opportunity.understanding.model_dump_json()}\n"
                    f"Compliance item count: {len(plan.opportunity.compliance.items)}\n"
                    f"Evaluation:\n{plan.opportunity.evaluation.model_dump_json()}\n"
                    f"Scope:\n{plan.opportunity.scope.model_dump_json()}\n"
                    f"RFP excerpt (structure/TOC/submission forms):\n{rfp_context[:50000]}\n\n"
                    f"Submission checklist excerpt (documents to return — read even if TOC is elsewhere):\n"
                    f"{submission_documents_excerpt(rfp_context)[:20000]}"
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
            titles = ["Executive Summary", "Approach", "Qualifications", "Pricing"]
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
    plan.writing.proposal_outline = outline
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Outline sections: {len(outline.sections)}",
        reason="Dynamic section plan from THIS RFP structure + evaluation (no fixed Methodology template)",
        confidence=outline.confidence,
    )
    return plan
