"""Proposal Intelligence Checklister Agent — verification of dynamic RFP outline completeness."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.proposal_intelligence.agent_base import safe_chat_json
from app.services.proposal_intelligence.plan_ops import append_decision
from app.services.proposal_intelligence.schemas import OutlineSection, ProposalExecutionPlan
from app.services.proposal_outline_dedup import humanize_outline_title

logger = logging.getLogger(__name__)
AGENT = "proposal_checklister"

_CHECKLISTER_SYSTEM = """You are zö agency's Proposal Checklister Agent.
Your single job is to compare the RFP document text with the proposed section outline
and verify that ALL dynamic RFP requirements, sub-asks, attachments, forms, insurance tables,
and specific required narrative topics are represented as outline tabs.

Check specifically for:
1. Required Attachments & Forms (e.g., Lobbying Certification, Debarment/Suspension Certification, Non-Collusion, W-9, COI attachment).
2. Required Insurance Tables (when RFP lists general liability, auto, E&O, umbrella, workers comp, etc.).
3. Specific Required Narrative Topics (e.g. §I.D.6 Project Management and Scheduling Expertise, Cost/Budget Control, Quality Assurance).
4. Specific evaluation response forms / required submittal packages.

If any required topic or form is missing from the existing section outline titles, emit a new section entry for it.

Existing Section Titles:
{existing_titles}

Return JSON ONLY:
{{
  "missing_sections": [
    {{
      "title": "SHORT tab heading — a noun phrase, roughly 8 words or fewer, e.g. 'Attachment B — Lobbying Certification'. NEVER a sentence copied from the RFP.",
      "requirementText": "Full RFP requirement, verbatim or closely paraphrased, as long as needed (e.g. 'Attachment B — Certification Regarding Lobbying: Offeror shall certify...')",
      "required": true,
      "conditionalReason": "",
      "evaluationWeight": null,
      "protectFromCap": true,
      "submissionInstrument": "form|references|cost|disclosure|narrative"
    }}
  ],
  "reasoning": "brief summary of checked vs missing items"
}}
"""


async def run_proposal_checklister(
    *,
    plan: ProposalExecutionPlan,
    rfp_context: str,
    rfp_meta: dict[str, str] | None = None,
) -> ProposalExecutionPlan:
    """Audit proposal outline against full RFP text to ensure 100% dynamic section coverage."""
    if not plan.writing.proposal_outline.sections:
        return plan

    existing_sections = plan.writing.proposal_outline.sections
    existing_titles = [s.title for s in existing_sections if s.title]
    existing_titles_blob = "\n".join(f"- {t}" for t in existing_titles)

    prompt = _CHECKLISTER_SYSTEM.format(existing_titles=existing_titles_blob)
    user_msg = f"RFP EXCERPT:\n{rfp_context[:25_000]}"

    # safe_chat_json takes the messages list POSITIONALLY and returns
    # (payload, provider) — same idiom as dynamic_section_planner.
    res, _provider = await safe_chat_json(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
        agent_name=AGENT,
    )

    missing_items = (res or {}).get("missing_sections") or []
    if not missing_items:
        plan = append_decision(
            plan,
            agent=AGENT,
            decision_text="Checklister audit complete: 0 missing dynamic sections detected.",
            reason="Existing outline covers all required RFP forms, attachments, and narratives.",
            confidence=1.0,
        )
        return plan

    added_count = 0
    start_order = len(existing_sections) + 1
    new_sections = list(existing_sections)
    existing_titles_cf = {t.casefold() for t in existing_titles}

    for idx, item in enumerate(missing_items):
        original_title = (item.get("title") or "").strip()
        if not original_title:
            continue
        title = humanize_outline_title(original_title)
        if not title:
            continue
        # Deduplication check
        if title.casefold() in existing_titles_cf or any(title.casefold() in t.casefold() for t in existing_titles_cf):
            continue

        sec_id = f"rfp-checklister-{start_order + idx}"
        instrument = item.get("submissionInstrument")
        if instrument not in ("cost", "form", "disclosure", "references", "narrative"):
            instrument = "form" if "certification" in title.casefold() or "attachment" in title.casefold() else "narrative"

        conditional_reason = item.get("conditionalReason") or ""
        requirement_text = (item.get("requirementText") or "").strip() or original_title
        if title != original_title:
            full_requirement = f"Full RFP requirement: {requirement_text}"
            conditional_reason = (
                f"{full_requirement}\n\n{conditional_reason}".strip()
                if conditional_reason
                else full_requirement
            )

        new_sec = OutlineSection(
            id=sec_id,
            title=title,
            order=start_order + idx,
            required=bool(item.get("required", True)),
            conditionalReason=conditional_reason,
            evaluationWeight=item.get("evaluationWeight"),
            protectFromCap=bool(item.get("protectFromCap", True)),
            submissionInstrument=instrument,
        )
        new_sections.append(new_sec)
        existing_titles_cf.add(title.casefold())
        added_count += 1

    if added_count > 0:
        plan.writing.proposal_outline.sections = new_sections
        logger.info("%s injected %d missing dynamic RFP section(s)", AGENT, added_count)

    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=f"Checklister audit complete: added {added_count} missing section(s).",
        reason=(res or {}).get("reasoning") or f"Injected {added_count} dynamic sections required by RFP.",
        confidence=0.95,
    )
    return plan
