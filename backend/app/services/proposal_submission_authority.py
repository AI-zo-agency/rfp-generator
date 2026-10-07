"""Post-outline submission authority: instruments, constraints, ambiguities.

Principle-based LLM pass over focused RFP excerpts — no client-specific regex tables.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from app.services.proposal_intelligence.plan_ops import append_decision
from app.services.proposal_intelligence.schemas import (
    CostRequirementStatus,
    OutlineSection,
    PlanAmbiguity,
    ProposalExecutionPlan,
    SubmissionConstraint,
)
from app.services.proposal_rfp_excerpt import (
    closing_package_excerpt,
    submission_documents_excerpt,
)

logger = logging.getLogger(__name__)
AGENT = "submission_authority"

CHECKLIST_INSTRUMENTS = frozenset({"form", "references", "disclosure"})
VALID_INSTRUMENTS = CHECKLIST_INSTRUMENTS | frozenset(
    {"narrative", "cost", "clarify", "letter"}
)

_AUTHORITY_SYSTEM = """You are zö agency's submission-authority agent for proposal outlines.

You receive:
1) The current outline tab titles (with ids and submissionInstrument if set)
2) Focused RFP excerpts (submission instructions, attachment lists, closing/forms)

Your ONLY job: classify outline rows and global rules so downstream writers do not
treat forms, constraints, and contradictions as ordinary narrative sections.

Rules (judge by meaning, not keyword lists):
- Parent/container headings that only introduce child submittals (e.g. "Offers Content
  Requirements" when 3.4.1–3.4.4 already exist as tabs) are NOT tabs — mark their ids
  for removal.
- Signature / authorized-representative rules are global constraints, NOT narrative tabs.
- Page limits, deadlines, and copy-count rules are submissionConstraints, NOT tabs.
- When early TOC lists an attachment (e.g. Cost Sheet) but operative submission text
  marks the same attachment RESERVED or omits pricing instructions, set costRequirementStatus
  to "ambiguous", stamp pricing-related tabs submissionInstrument "clarify", and add an
  ambiguity with blocksBudget=true. Do NOT mark pricing as confirmed required.
- When operative text clearly requires a pricing/cost form or fee schedule, set
  costRequirementStatus "confirmed" and stamp the matching tab submissionInstrument "cost".
- When no operative pricing submittal exists, costRequirementStatus "absent".
- Scored evaluation narrative tabs: submissionInstrument "narrative" (with evaluationWeight).
- Signed offer / interest / cover / transmittal letter the buyer wants with the packet:
  submissionInstrument "letter" — judge by MEANING (whatever the buyer named it), not
  title synonyms. One letter tab per package.
- Required forms, references, certifications, attachments: form | references | disclosure.
- Do NOT invent tabs. Only update/remove existing outline ids.

Return JSON only:
{
  "removeSectionIds": ["id", "..."],
  "sectionUpdates": [
    {
      "id": "rfp-sec-1",
      "submissionInstrument": "narrative|form|references|disclosure|cost|clarify|letter",
      "required": true,
      "conditionalReason": ""
    }
  ],
  "submissionConstraints": [
    {
      "kind": "page_limit|signature|deadline|copy_count|other",
      "text": "plain English rule",
      "required": true,
      "sourceText": "short quote from RFP",
      "sourceSection": "section ref if known"
    }
  ],
  "ambiguities": [
    {
      "topic": "Cost / pricing deliverable",
      "status": "unresolved",
      "evidenceFor": "",
      "evidenceAgainst": "",
      "recommendedAction": "",
      "blocksDrafting": false,
      "blocksBudget": true
    }
  ],
  "costRequirementStatus": "confirmed|absent|ambiguous",
  "confidence": 0.0
}
"""


def instrument_is_checklist(instrument: str | None) -> bool:
    return (instrument or "").strip().casefold() in CHECKLIST_INSTRUMENTS


def instrument_skips_full_narrative_draft(instrument: str | None) -> bool:
    inst = (instrument or "").strip().casefold()
    if inst in CHECKLIST_INSTRUMENTS or inst == "clarify":
        return True
    if inst == "cost":
        return True
    return False


def instrument_supports_budget_build(pricing_instrument: Any | None) -> bool:
    """True when the extract already asks for rates/hours under a positive NTE.

    Principle-based: hourly (or hours) loading plus a track ceiling means Phase
    3.5 should build a SOW-shaped ledger even if the cost *form* is still labeled
    ambiguous (TOC vs RESERVED, post-award invoicing language, etc.).
    """
    if pricing_instrument is None:
        return False
    tracks = getattr(pricing_instrument, "tracks", None)
    if tracks is None and isinstance(pricing_instrument, dict):
        tracks = pricing_instrument.get("tracks") or pricing_instrument.get("Tracks")
    if not isinstance(tracks, list) or not tracks:
        return False
    for track in tracks:
        if isinstance(track, dict):
            asks_hourly = bool(track.get("asksHourly") or track.get("asks_hourly"))
            asks_hours = bool(track.get("asksHours") or track.get("asks_hours"))
            nte = track.get("nteAnnual")
            if nte is None:
                nte = track.get("nte_annual")
        else:
            asks_hourly = bool(getattr(track, "asks_hourly", False))
            asks_hours = bool(getattr(track, "asks_hours", False))
            nte = getattr(track, "nte_annual", None)
        try:
            nte_f = float(nte) if nte is not None else 0.0
        except (TypeError, ValueError):
            nte_f = 0.0
        if (asks_hourly or asks_hours) and nte_f > 0:
            return True
    return False


def phase35_budget_gate(
    plan: ProposalExecutionPlan | None,
    pricing_instrument: Any | None = None,
) -> tuple[Literal["proceed", "skip", "block"], str | None]:
    """Whether Phase 3.5 may generate budget content.

    Ambiguous cost/pricing (e.g. TOC lists a cost sheet but operative text is
    RESERVED) normally skips so we do not invent a fee form. Exception: when the
    pricing instrument already extracted hourly/hours asks plus a positive NTE,
    proceed — the SOW still needs Task IDs × rates and an NTE total.
    """
    if plan is None:
        return "proceed", None
    status = plan.writing.cost_requirement_status or "absent"
    if status == "confirmed":
        return "proceed", None
    if status == "ambiguous":
        if instrument_supports_budget_build(pricing_instrument):
            return (
                "proceed",
                "ambiguous cost form, but instrument asks hourly/hours under NTE — building ledger",
            )
        detail = "Cost/pricing requirement is ambiguous — skipping budget generation until confirmed."
        for amb in plan.writing.ambiguities:
            if amb.blocks_budget and amb.status != "resolved":
                detail = (
                    amb.recommended_action
                    or f"Unresolved ambiguity: {amb.topic}"
                )
                break
        return "skip", detail
    if status == "absent":
        return "skip", "No confirmed cost/pricing submittal in RFP submission authority."
    return "skip", None


def attachment_checklist_stub(title: str, instrument: str) -> str:
    label = title or "Required attachment"
    kind = "form"
    if instrument == "references":
        kind = "reference forms"
    elif instrument == "disclosure":
        kind = "disclosure / exemption affidavit"
    return (
        f"## {label}\n\n"
        f"Required {kind} per the RFP submission package.\n\n"
        "[DESIGNER NOTE: Attach the buyer's required PDF/form here.]\n\n"
        "[MANUAL FILL: Sonja — attach signed/completed file before submit.]"
    )


def clarify_blocker_stub(title: str, reason: str) -> str:
    label = title or "Requires clarification"
    detail = (reason or "Resolve against OregonBuys attachments or amendments.").strip()
    return (
        f"## {label}\n\n"
        f"[MANUAL FILL: Sonja — RFP ambiguity — {detail}]"
    )


def _normalize_instrument(raw: Any) -> str | None:
    if raw is None:
        return None
    inst = str(raw).strip().casefold()
    if inst in VALID_INSTRUMENTS:
        return inst
    return None


def _normalize_cost_status(raw: Any) -> CostRequirementStatus:
    val = str(raw or "absent").strip().casefold()
    if val in {"confirmed", "absent", "ambiguous"}:
        return val  # type: ignore[return-value]
    return "absent"


def apply_authority_from_raw(
    plan: ProposalExecutionPlan,
    raw: dict[str, Any],
) -> ProposalExecutionPlan:
    """Apply LLM authority JSON onto plan (testable without network)."""
    remove_ids = {
        str(x).strip()
        for x in (raw.get("removeSectionIds") or raw.get("remove_section_ids") or [])
        if str(x).strip()
    }
    sections = [
        s for s in plan.writing.proposal_outline.sections if s.id not in remove_ids
    ]
    by_id = {s.id: s for s in sections}

    for upd in raw.get("sectionUpdates") or raw.get("section_updates") or []:
        if not isinstance(upd, dict):
            continue
        sid = str(upd.get("id") or "").strip()
        if not sid or sid not in by_id:
            continue
        sec = by_id[sid]
        inst = _normalize_instrument(upd.get("submissionInstrument"))
        kwargs: dict[str, Any] = {}
        if inst is not None:
            kwargs["submission_instrument"] = inst
        if "required" in upd:
            kwargs["required"] = bool(upd.get("required"))
        cr = upd.get("conditionalReason") or upd.get("conditional_reason")
        if cr is not None:
            kwargs["conditional_reason"] = str(cr)
        by_id[sid] = sec.model_copy(update=kwargs)

    plan.writing.proposal_outline.sections = sorted(
        by_id.values(), key=lambda s: int(s.order or 0)
    )

    constraints: list[SubmissionConstraint] = []
    for row in raw.get("submissionConstraints") or raw.get("submission_constraints") or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        constraints.append(
            SubmissionConstraint.model_validate(
                {
                    "kind": str(row.get("kind") or "other"),
                    "text": text,
                    "required": bool(row.get("required", True)),
                    "sourceText": row.get("sourceText") or row.get("source_text") or "",
                    "sourceSection": row.get("sourceSection")
                    or row.get("source_section")
                    or "",
                }
            )
        )
    plan.writing.submission_constraints = constraints

    ambiguities: list[PlanAmbiguity] = []
    for row in raw.get("ambiguities") or []:
        if not isinstance(row, dict):
            continue
        topic = str(row.get("topic") or "").strip()
        if not topic:
            continue
        ambiguities.append(PlanAmbiguity.model_validate(row))
    plan.writing.ambiguities = ambiguities
    plan.writing.cost_requirement_status = _normalize_cost_status(
        raw.get("costRequirementStatus") or raw.get("cost_requirement_status")
    )

    _sync_validation_from_routing(plan)
    return plan


def _sync_validation_from_routing(plan: ProposalExecutionPlan) -> None:
    blockers = list(plan.validation.blockers or [])
    warnings = list(plan.validation.warnings or [])
    for amb in plan.writing.ambiguities:
        if amb.status == "resolved":
            continue
        msg = f"RFP ambiguity ({amb.topic}): {amb.recommended_action or 'needs human review'}"
        if amb.blocks_drafting:
            if msg not in blockers:
                blockers.append(msg)
        elif amb.blocks_budget:
            if msg not in warnings:
                warnings.append(msg)
    plan.validation.blockers = blockers
    plan.validation.warnings = warnings
    if blockers:
        plan.validation.readiness_status = "blocked"
        plan.metadata.validation_status = "blocked"
    elif warnings:
        plan.validation.readiness_status = "partial"
        plan.metadata.validation_status = "partial"


def outline_routing_quality_tickets(
    plan: ProposalExecutionPlan | None,
) -> list[dict[str, str]]:
    """Manual-fill style findings for Complete Scan / quality gate."""
    if plan is None:
        return []
    out: list[dict[str, str]] = []
    for amb in plan.writing.ambiguities:
        if amb.status == "resolved":
            continue
        out.append(
            {
                "code": "rfp_ambiguity",
                "topic": amb.topic,
                "detail": amb.recommended_action or amb.evidence_against or amb.evidence_for,
            }
        )
    for c in plan.writing.submission_constraints:
        if c.kind == "page_limit" and c.required:
            out.append(
                {
                    "code": "page_limit_constraint",
                    "topic": "Proposal page limit",
                    "detail": c.text,
                }
            )
    return out


def page_limit_from_constraints(plan: ProposalExecutionPlan | None) -> int | None:
    if not plan:
        return None
    for c in plan.writing.submission_constraints:
        if c.kind != "page_limit":
            continue
        m = re.search(r"\b(\d{1,3})\s*pages?\b", c.text, flags=re.I)
        if m:
            try:
                n = int(m.group(1))
                return n if n > 0 else None
            except ValueError:
                continue
        m = re.search(r"\bno more than (\d{1,3})\b", c.text, flags=re.I)
        if m:
            try:
                n = int(m.group(1))
                return n if n > 0 else None
            except ValueError:
                continue
    return None


async def apply_submission_authority_pass(
    plan: ProposalExecutionPlan,
    rfp_context: str,
) -> ProposalExecutionPlan:
    """Run authority LLM and normalize outline routing metadata."""
    sections = plan.writing.proposal_outline.sections
    if not sections:
        return plan

    from app.services.proposal_intelligence.agent_base import safe_chat_json

    titles_block = "\n".join(
        f"- id={s.id} title={s.title!r} instrument={s.submission_instrument or 'null'} "
        f"weight={s.evaluation_weight} required={s.required}"
        for s in sections
    )
    stable = (
        f"Current outline ({len(sections)} tabs):\n{titles_block}\n\n"
        f"Submission-documents excerpt:\n"
        f"{submission_documents_excerpt(rfp_context)[:24000]}\n\n"
        f"Closing/forms excerpt:\n{closing_package_excerpt(rfp_context)[:16000]}"
    )
    raw, _provider = await safe_chat_json(
        [
            {"role": "system", "content": _AUTHORITY_SYSTEM},
            {"role": "user", "content": ""},
        ],
        max_tokens=4096,
        temperature=0.15,
        agent_name=AGENT,
        cache_prefix=stable,
    )
    if not isinstance(raw, dict):
        logger.warning("%s returned non-dict payload", AGENT)
        return plan

    plan = apply_authority_from_raw(plan, raw)
    counts: dict[str, int] = {}
    for sec in plan.writing.proposal_outline.sections:
        key = sec.submission_instrument or "unset"
        counts[key] = counts.get(key, 0) + 1
    logger.info(
        "%s applied cost_status=%s instruments=%s constraints=%d ambiguities=%d",
        AGENT,
        plan.writing.cost_requirement_status,
        counts,
        len(plan.writing.submission_constraints),
        len(plan.writing.ambiguities),
    )
    plan = append_decision(
        plan,
        agent=AGENT,
        decision_text=(
            f"Submission authority: cost={plan.writing.cost_requirement_status}, "
            f"tabs={len(plan.writing.proposal_outline.sections)}, "
            f"ambiguities={len(plan.writing.ambiguities)}"
        ),
        reason="Classified narrative vs submittals vs constraints after outline completeness.",
        confidence=float(raw.get("confidence") or 0.85),
    )
    return plan
