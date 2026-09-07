"""Retrieval Planner — plans assets/queries only; never retrieves."""

from __future__ import annotations

import logging

from app.services.proposal_intelligence.agent_base import clamp_confidence, safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision, set_provider
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan, RetrievalPlan

logger = logging.getLogger(__name__)
AGENT = "retrieval_planner"

_SYSTEM = """Retrieval Planner. Plan what each section must retrieve in Phase 3.
Do NOT fetch documents. Do NOT include evidence excerpts or content fields.

Each entry's ``queries`` must be ONE natural-language question per section — the same
style a human would type into the KB QA loop. Good examples:
- "Find zö agency tourism destination social media case studies with KPIs and results"
- "San Francisco Travel Summer of Love campaign strategy and measurable results"
- "03_CS case studies for destination marketing and visitor conversion metrics"

Bad (too fragmentary): "social media KPIs", "tourism accounts", "zö agency experience"

Return JSON only:
{
  "entries": [
    {
      "sectionId": "rfp-sec-1",
      "requiredAssets": ["tourism destination social media accounts managed"],
      "queries": ["Find zö agency case studies for tourism destination social media accounts with before/after engagement KPIs"],
      "priority": "required|high|medium",
      "constraints": ["no marketing fluff"],
      "expectedSources": ["case_studies", "methodology"],
      "whyNeeded": "RFP requires examples with measurable visitation/conversion metrics"
    }
  ],
  "confidence": 0.0
}
expectedSources values: won_proposals|case_studies|testimonials|references|methodology|
pricing|bios|company_facts|portfolio|images|diagrams|playbooks|standards
"""


async def run_retrieval_planner(
    *,
    plan: ProposalExecutionPlan,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Section plans:\n{plan.writing.section_plans.model_dump_json()}\n"
                    f"Outline:\n{plan.writing.proposal_outline.model_dump_json()}\n"
                    f"Proof strategy:\n{plan.opportunity.strategy.proof_strategy}"
                ),
            },
        ],
        max_tokens=3072,
        agent_name=AGENT,
    )
    return apply_retrieval_plan_from_raw(plan, raw, provider=provider)


# A References tab asks for something no other section wants: the contact
# records for past clients (name, title, phone, email), which live inside the
# reference tables of past proposals rather than in case-study narrative. The
# planner, left to itself, writes case-study-shaped queries for it, so the tab
# reached the writer with no reference evidence at all and the model fell back
# to narrating the process ("select three references, obtain contact info") —
# which then shipped to the client as the section body.
_REFERENCE_TITLE_HINT = "reference"


def section_wants_client_references(title: str) -> bool:
    """True for a tab that must name actual past clients and their contacts.

    Deliberately narrow: "reference" in the title. Broadening this to past
    performance / qualifications would attach contact-record queries to
    narrative experience tabs that do not want them.
    """
    lowered = (title or "").casefold()
    if _REFERENCE_TITLE_HINT not in lowered:
        return False
    # "Reference" also appears in cross-reference / referenced-document tabs,
    # which are about the RFP's own documents, not past clients.
    for phrase in ("cross-reference", "cross reference", "referenced document", "reference number"):
        if phrase in lowered:
            return False
    return True


def client_reference_queries(rfp_client: str, rfp_sector: str) -> list[str]:
    """KB questions that actually surface reference rows from past proposals."""
    queries = [
        "zö agency client references list with contact name, title, phone and email "
        "from past submitted proposals",
        "zö agency past client reference contacts and the project scope and dates "
        "delivered for each",
    ]
    sector = (rfp_sector or "").strip()
    if sector:
        queries.append(
            f"zö agency {sector} client references with contact information and "
            f"comparable scope of work"
        )
    client = (rfp_client or "").strip()
    if client:
        queries.append(
            f"zö agency references for public sector clients comparable to {client}"
        )
    return queries


def _attach_reference_queries(
    retrieval: RetrievalPlan, plan: ProposalExecutionPlan
) -> RetrievalPlan:
    """Give every References tab explicit contact-record queries."""
    titles = {p.section_id: p.title for p in plan.writing.section_plans.plans}
    client = plan.opportunity.understanding.client or ""
    sector = plan.opportunity.understanding.industry or ""
    for entry in retrieval.entries:
        if not section_wants_client_references(titles.get(entry.section_id, "")):
            continue
        extra = client_reference_queries(client, sector)
        existing = list(entry.queries or [])
        entry.queries = extra + [q for q in existing if q not in extra]
        for source in ("references", "won_proposals"):
            if source not in entry.expected_sources:
                entry.expected_sources.append(source)
        if "client reference contacts" not in entry.required_assets:
            entry.required_assets.append("client reference contacts")
    return retrieval


def apply_retrieval_plan_from_raw(
    plan: ProposalExecutionPlan,
    raw: dict | None,
    *,
    provider: str,
) -> ProposalExecutionPlan:
    """Validate retrieval entries and fill fallbacks from section briefs."""
    # Strip any accidental content/excerpt keys
    if isinstance(raw, dict):
        for entry in raw.get("entries") or []:
            if isinstance(entry, dict):
                entry.pop("excerpt", None)
                entry.pop("content", None)
                entry.pop("evidence", None)
    try:
        retrieval = RetrievalPlan.model_validate(raw or {})
    except Exception as exc:
        logger.warning("%s validation failed: %s", AGENT, exc)
        retrieval = RetrievalPlan(confidence=0.2)
    if not retrieval.entries and plan.writing.section_plans.plans:
        from app.services.kb_rag_retrieve import build_retrieval_question_from_entry

        retrieval = RetrievalPlan(
            entries=[
                {
                    "sectionId": p.section_id,
                    "requiredAssets": list(p.evidence_needed) or [p.retrieval_goal or p.title],
                    "queries": [
                        build_retrieval_question_from_entry(
                            section_id=p.section_id,
                            section_title=p.title,
                            required_assets=list(p.evidence_needed) or [p.retrieval_goal or p.title],
                            planner_queries=[],
                            why_needed=p.retrieval_goal or p.purpose or "",
                            rfp_client=plan.opportunity.understanding.client or "",
                        )
                    ],
                    "priority": "required",
                    "expectedSources": ["company_facts", "case_studies"],
                    "whyNeeded": p.retrieval_goal or p.purpose,
                }
                for p in plan.writing.section_plans.plans
            ],
            confidence=0.35,
        )
    retrieval = _attach_reference_queries(retrieval, plan)
    retrieval.confidence = clamp_confidence(retrieval.confidence)
    plan.writing.retrieval_plan = retrieval
    plan.writing.reviewer_personas = None
    plan.metadata.layer_status.writing = "complete"
    plan = set_provider(plan, provider)
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Retrieval plan entries: {len(retrieval.entries)}",
        reason="Planned Phase 3 JIT retrieval — no evidence fetched",
        confidence=retrieval.confidence,
    )
    return plan
