"""Post-edit DeliveryConstraints fact gate for SOW / Timeline sections.

v1 policy: **flag-only** — keep the edited section body; surface PreSubmitIssue(s).
v1.1 may revert offending edits. No platform synonym frozensets — LLM judge +
verbatim excerpt evidence only (see no-regex-evidence-matching).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

from app.models.delivery_constraints import DeliveryConstraints
from app.models.proposal import (
    PreSubmitIssue,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_intelligence.agent_base import safe_chat_json
from app.services.pricing_delivery_context import format_pricing_delivery_constraints_block

logger = logging.getLogger(__name__)

AGENT = "delivery_constraints_gate"

# Shared with self-edit SOW/Timeline hints — keep in sync (gate must not import self_edit).
_SOW_TIMELINE_TITLE_HINTS = (
    "statement of work",
    "scope of work",
    "scope of services",
    "work plan",
    "methodology",
    "approach",
    "timeline",
    "schedule",
    "project plan",
    "implementation plan",
)

_SYSTEM = """You are a delivery-constraints fact gate for proposal SOW/Timeline sections.

Compare SECTION CONTENT against LOCKED DeliveryConstraints (canonical).
Flag only MATERIAL fact drift. Prose polish / rewording without changing facts is OK.

Material drift (flag these):
- Invented work listed in out-of-scope (or clearly outside RFP tracks)
- Dropped or omitted mandatory deliverables the section should cover
  (especially when a scope-confirmation table lists fewer lines than
  mandatoryDeliverables — missing trailing Scope of Services items)
- Merged / commingled track NTEs or scopes when nonCommingleTracks is true
- Horizon overrun (promised term beyond maxTerm / base+renewals)
- Extra tracks or scopes not in tracks / mandatoryDeliverables / bidderProposes
- Ownership conflict: section claims shared "usage rights" / agency retention of
  deliverable ownership when buyerOwnsDeliverables is true
- Unverified SLA: "same-day" / "often same-day" / hard turnaround promises without
  a VERIFY/MANUAL FILL when no locked SLA exists in constraints
- Protocol heading conflict: title like "outside this scope" while claiming full
  scope coverage (should be buyer protocols, not can't-perform)

Not material: synonyms for the same deliverable, tone, structure, or ordering.

Return JSON only:
{
  "drifts": [
    {
      "kind": "out_of_scope" | "missing_mandatory" | "commingle" | "horizon" | "extra_track" | "ownership" | "unverified_sla" | "protocol_heading" | "other",
      "severity": "critical" | "warning",
      "message": "one sentence; for out-of-scope include the words 'out of scope'",
      "excerpt": "verbatim quote from SECTION CONTENT evidencing the drift",
      "constraintEvidence": "which locked constraint was violated"
    }
  ]
}
If no material drift: {"drifts": []}
Never invent drifts without an excerpt grounded in the section text.
"""


def section_title_is_sow_or_timeline(title: str) -> bool:
    t = (title or "").casefold()
    return any(h in t for h in _SOW_TIMELINE_TITLE_HINTS)


def constraints_are_material(constraints: DeliveryConstraints | None) -> bool:
    if constraints is None:
        return False
    horizon = constraints.horizon
    horizon_bits = bool(
        horizon
        and any((horizon.base_term, horizon.renewals, horizon.max_term))
    )
    return bool(
        constraints.tracks
        or constraints.mandatory_deliverables
        or constraints.out_of_scope
        or constraints.bidder_proposes
        or horizon_bits
        or constraints.non_commingle_tracks
        or constraints.buyer_owns_deliverables
        or constraints.letter_proposal_gate
    )


def _constraints_json_brief(constraints: DeliveryConstraints) -> str:
    """Compact locked facts for the judge (typed model, not freeform RFP)."""
    return format_pricing_delivery_constraints_block(
        delivery=constraints,
        focus="all",
        max_chars=6000,
    )


def _normalize_drifts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    drifts = raw.get("drifts") if isinstance(raw, dict) else None
    if not isinstance(drifts, list):
        return []
    out: list[dict[str, Any]] = []
    for item in drifts:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        if not message:
            continue
        severity = str(item.get("severity") or "critical").strip().casefold()
        if severity not in ("critical", "warning", "info"):
            severity = "critical"
        out.append(
            {
                "kind": str(item.get("kind") or "other").strip() or "other",
                "severity": severity,
                "message": message[:500],
                "excerpt": str(item.get("excerpt") or "").strip()[:240] or None,
                "constraintEvidence": str(item.get("constraintEvidence") or "").strip()[:300],
            }
        )
    return out


def _drifts_to_issues(
    drifts: list[dict[str, Any]],
    *,
    section_title: str,
    section_id: str | None = None,
) -> list[PreSubmitIssue]:
    issues: list[PreSubmitIssue] = []
    for d in drifts:
        kind = d.get("kind") or "other"
        evidence = d.get("constraintEvidence") or ""
        msg = d["message"]
        if evidence and evidence.casefold() not in msg.casefold():
            msg = f"{msg} (constraint: {evidence})"
        # Ensure out-of-scope wording is searchable for tests / scanners.
        if kind == "out_of_scope" and "out of scope" not in msg.casefold():
            msg = f"Out of scope drift: {msg}"
        issues.append(
            PreSubmitIssue(
                severity=d["severity"],  # type: ignore[arg-type]
                category="delivery_constraints",
                message=msg,
                sectionId=section_id,
                sectionTitle=section_title,
                excerpt=d.get("excerpt"),
            )
        )
    return issues


async def gate_delivery_section(
    section_title: str,
    content: str,
    constraints: DeliveryConstraints,
    *,
    section_id: str | None = None,
) -> list[PreSubmitIssue]:
    """LLM-judge section body against DeliveryConstraints. Flag-only (no rewrite)."""
    if not constraints_are_material(constraints):
        return []
    body = (content or "").strip()
    if not body:
        return []

    logger.info(
        "delivery_constraints_gate start title=%s chars=%s tracks=%s out_of_scope=%s",
        section_title,
        len(body),
        len(constraints.tracks),
        len(constraints.out_of_scope),
    )
    user = (
        f"SECTION TITLE: {section_title}\n\n"
        f"LOCKED CONSTRAINTS:\n{_constraints_json_brief(constraints)}\n\n"
        f"SECTION CONTENT:\n{body[:12000]}\n"
    )
    raw, provider = await safe_chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        max_tokens=2048,
        temperature=0.0,
        agent_name=AGENT,
        cache_prefix="delivery_constraints_gate",
    )
    # Fail-open when constraints are material: LLM failure must surface review-required,
    # not an empty issue list that looks like a clean pass.
    if not isinstance(raw, dict) or not raw:
        logger.warning(
            "delivery_constraints_gate LLM failure title=%s provider=%s — review required",
            section_title,
            provider,
        )
        return [
            PreSubmitIssue(
                severity="critical",
                category="delivery_constraints",
                message=(
                    "Delivery-constraints verification failed — review required "
                    "(SOW/Timeline could not be checked against locked constraints)."
                ),
                sectionId=section_id,
                sectionTitle=section_title,
                excerpt=None,
            )
        ]
    drifts = _normalize_drifts(raw)
    issues = _drifts_to_issues(
        drifts, section_title=section_title, section_id=section_id
    )
    logger.info(
        "delivery_constraints_gate done title=%s provider=%s drifts=%s",
        section_title,
        provider,
        len(issues),
    )
    return issues


async def collect_delivery_constraint_issues(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    section_ids: set[str] | None = None,
) -> list[PreSubmitIssue]:
    """Gate SOW/Timeline sections when research has material DeliveryConstraints."""
    constraints = research.delivery_constraints if research else None
    if not constraints_are_material(constraints):
        return []
    assert constraints is not None

    issues: list[PreSubmitIssue] = []
    for section in draft.sections:
        if section_ids is not None and section.id not in section_ids:
            continue
        if not section_title_is_sow_or_timeline(section.title or ""):
            continue
        if not (section.content or "").strip():
            continue
        section_issues = await gate_delivery_section(
            section.title or "",
            section.content or "",
            constraints,
            section_id=section.id,
        )
        issues.extend(section_issues)
    return issues


def _run_coro_sync(coro: Any) -> Any:
    """Run async gate from sync callers (presubmit / consistency)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside an event loop — run in a worker thread with its own loop.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=120)


def _verification_failed_issue() -> PreSubmitIssue:
    return PreSubmitIssue(
        severity="critical",
        category="delivery_constraints",
        message=(
            "Delivery-constraints verification failed — review required "
            "(gate could not run against locked constraints)."
        ),
        sectionId=None,
        sectionTitle=None,
        excerpt=None,
    )


def scan_delivery_constraints_on_draft_sync(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> list[PreSubmitIssue]:
    """Sync wrapper for scan_manuscript_consistency / run_presubmit_review."""
    constraints = research.delivery_constraints if research else None
    if not constraints_are_material(constraints):
        return []
    try:
        return _run_coro_sync(
            collect_delivery_constraint_issues(draft=draft, research=research)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "delivery_constraints_gate sync scan failed — review required: %s",
            str(exc)[:200],
        )
        return [_verification_failed_issue()]


async def flag_delivery_drift_after_edit(
    *,
    section: ProposalSection,
    research: ProposalResearchCache | None,
) -> list[PreSubmitIssue]:
    """Post-edit hook: keep content; return issues when SOW/Timeline drifted."""
    if not section_title_is_sow_or_timeline(section.title or ""):
        return []
    constraints = research.delivery_constraints if research else None
    if not constraints_are_material(constraints):
        return []
    assert constraints is not None
    try:
        return await gate_delivery_section(
            section.title or "",
            section.content or "",
            constraints,
            section_id=section.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "delivery_constraints_gate after-edit failed — review required: %s",
            str(exc)[:200],
        )
        return [
            PreSubmitIssue(
                severity="critical",
                category="delivery_constraints",
                message=(
                    "Delivery-constraints verification failed — review required "
                    "(SOW/Timeline could not be checked against locked constraints)."
                ),
                sectionId=section.id,
                sectionTitle=section.title,
                excerpt=None,
            )
        ]
