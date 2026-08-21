"""Section Strategy Planner — writer briefs per section."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, SectionPlans

logger = logging.getLogger(__name__)
AGENT = "section_strategy_planner"

_SYSTEM = """Section Strategy Planner. For each outline section, define the writer brief.

HARD RULES:
- Each section has ONE distinct job — purpose must NOT overlap another section's purpose.
- Be concise and designer-ready for EVERY tab: prefer wordBudget 250–500; short lead +
  tables/bullets + [DESIGNER NOTE] for layout — never essay walls or "designer will cut later."
  When a proposal page limit is provided, allocate so the SUM of wordBudgets fits that limit
  (~350 words/page for narrative), leaving room for static Sections 1–3.
  wordBudget is a HARD CEILING. Never plan volume "for the designer to cut later."
- ATTACHMENT / form-return tabs (COI PDF, W-9, signed buyer forms, exhibits): wordBudget 80–120.
  writerInstructions MUST tell the writer to emit ONLY a short checklist plus
  [DESIGNER NOTE: Attach <exact file>] and/or [MANUAL FILL: attach …] — NO insurance essays,
  NO invented certificate text, NO paraphrasing coverage limits (Section 1.5 owns that).
- Agency Requirements / capability matrix tabs: instruct the writer to cover ALL G.# / Section
  III A service lines in ONE matrix (not a separate narrative per service).
- writerInstructions MUST say: do not rehash other tabs; add only NEW RFP-specific detail;
  no generic agency marketing filler; hit the scored ask then stop.
- keyMessages: 2–4 concrete bullets tied to THIS RFP's language — not vague brand claims.
- successDefinition: what the evaluator should be able to score after reading THIS tab only.

Return JSON only:
{
  "plans": [
    {
      "sectionId": "rfp-sec-1",
      "title": "string",
      "purpose": "string",
      "keyMessages": ["string"],
      "evaluationCriteria": ["string"],
      "evidenceNeeded": ["string"],
      "retrievalGoal": "string",
      "writerInstructions": "string",
      "successDefinition": "string",
      "wordBudget": 500,
      "tone": "executive",
      "register": "narrative",
      "audience": "string"
    }
  ],
  "confidence": 0.0
}
No proposal prose — strategy only.
"""


def _parse_page_limit(rfp_meta: dict[str, str] | None) -> int | None:
    if not rfp_meta:
        return None
    raw = (rfp_meta.get("pageLimit") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _is_checklist_tab(section_id: str) -> bool:
    """Consolidated buyer forms/attachments tab — checklist only, not prose."""
    sid = (section_id or "").casefold()
    return "forms-attachment" in sid or sid.endswith("-attachments")


async def run_section_strategy_planner(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    page_limit = _parse_page_limit(rfp_meta)
    page_limit_line = (
        f"Proposal page limit from RFP: {page_limit} pages "
        f"(~{page_limit * 350} narrative words total including static Sections 1–3). "
        "Keep every wordBudget tight so the full package fits — compact for the designer, "
        "complete for the evaluator."
        if page_limit
        else "No reliable proposal page limit detected — still keep wordBudgets lean (250–500)."
    )
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"{page_limit_line}\n\n"
                    f"Outline:\n{plan.writing.proposal_outline.model_dump_json()}\n"
                    f"Strategy:\n{plan.opportunity.strategy.model_dump_json()}\n"
                    f"Evaluation:\n{plan.opportunity.evaluation.model_dump_json()}\n"
                    f"Success:\n{plan.opportunity.success_criteria.model_dump_json()}\n"
                    f"Delivery methodology:\n{plan.delivery.methodology.model_dump_json()}"
                ),
            },
        ],
        max_tokens=4096,
        agent_name=AGENT,
    )
    return apply_section_strategy_from_raw(
        plan, raw, provider=provider, rfp_meta=rfp_meta
    )


def apply_section_strategy_from_raw(
    plan: ProposalExecutionPlan,
    raw: dict | None,
    *,
    provider: str,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    """Validate writer briefs and apply word-budget / attachment guards."""
    page_limit = _parse_page_limit(rfp_meta)
    try:
        plans = SectionPlans.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        plans = SectionPlans(confidence=0.2)
    if not plans.plans:
        plans = SectionPlans(
            plans=[
                {
                    "sectionId": s.id,
                    "title": s.title,
                    "purpose": f"Address {s.title}",
                    "successDefinition": f"Evaluator understands {s.title}",
                    "retrievalGoal": "Relevant KB evidence for this section",
                    "writerInstructions": "Follow Proposal Execution Plan; do not invent facts.",
                    "wordBudget": 800,
                    "tone": plan.opportunity.strategy.tone or "executive",
                    "register": "narrative",
                }
                for s in plan.writing.proposal_outline.sections
            ],
            confidence=0.35,
        )
    existing_patterns = {
        p.section_id: p.winning_pattern for p in plan.writing.section_plans.plans
    }
    section_count = max(1, len(plans.plans))
    # Soft per-tab ceiling when a page limit is known (reserve ~35% for static 1–3).
    soft_cap = 550
    if page_limit and page_limit > 0:
        narrative_words = max(400, int(page_limit * 350 * 0.65))
        soft_cap = max(200, min(550, narrative_words // section_count))

    attachment_guard = (
        "Emit ONLY a short attachment checklist with [DESIGNER NOTE: Attach …] and "
        "[MANUAL FILL: attach …] — do not write insurance/COI/W-9 essay prose; "
        "certificates and signed forms are files for the designer to insert."
    )
    lean_guard = (
        "Do not rehash other tabs or Sections 1–3; only NEW RFP-specific detail; "
        "no generic agency filler; hit the scored ask then stop."
    )

    for section_plan in plans.plans:
        existing = existing_patterns.get(section_plan.section_id)
        if existing and not section_plan.winning_pattern.confidence:
            section_plan.winning_pattern = existing

        sid = section_plan.section_id or ""
        if _is_checklist_tab(sid):
            section_plan.word_budget = min(section_plan.word_budget or 100, 120)
            if section_plan.word_budget < 80:
                section_plan.word_budget = 80
            instr = (section_plan.writer_instructions or "").strip()
            if "DESIGNER NOTE" not in instr.upper() and "MANUAL FILL" not in instr.upper():
                section_plan.writer_instructions = (
                    f"{instr} {attachment_guard}".strip() if instr else attachment_guard
                )
            continue

        # Keep briefs lean — inflate only when already above the soft cap.
        if section_plan.word_budget and section_plan.word_budget > 900:
            section_plan.word_budget = min(900, max(soft_cap, 550))
        elif not section_plan.word_budget or section_plan.word_budget > soft_cap:
            if not section_plan.word_budget or section_plan.word_budget >= 800:
                section_plan.word_budget = soft_cap
            else:
                section_plan.word_budget = min(section_plan.word_budget, soft_cap)

        instr = (section_plan.writer_instructions or "").strip()
        if lean_guard.casefold() not in instr.casefold():
            section_plan.writer_instructions = (
                f"{instr} {lean_guard}".strip() if instr else lean_guard
            )
    plans.confidence = clamp_confidence(plans.confidence)
    plan.writing.section_plans = plans
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Section strategies: {len(plans.plans)}",
        reason="Writer briefs with purpose/success/retrievalGoal",
        confidence=plans.confidence,
    )
    return plan
