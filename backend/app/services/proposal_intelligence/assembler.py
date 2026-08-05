"""Assemble Proposal Execution Plan + derive legacy Phase 2 fields."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProofPoint, RfpSectionMap
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.services.proposal_intelligence.memory import upsert_memory
from app.services.proposal_intelligence.schemas import (
    ComplianceItem,
    EvaluationCriterion,
    ProposalExecutionPlan,
)


def refresh_proposal_memory(plan: ProposalExecutionPlan) -> ProposalExecutionPlan:
    """Consolidate known facts from opportunity/delivery into proposalMemory."""
    u = plan.opportunity.understanding
    d = plan.delivery
    facts: dict[str, str] = {
        "clientName": u.client,
        "organizationType": u.org_type,
        "industry": u.industry,
        "projectType": u.project_type,
        "complexity": u.complexity,
    }
    if d.delivery_model.type:
        facts["deliveryApproach"] = d.delivery_model.type
    if d.budget.pricing_model:
        facts["pricingModel"] = d.budget.pricing_model
    if d.budget.contract_type:
        facts["contractType"] = d.budget.contract_type
    if plan.opportunity.strategy.winning_theme:
        facts["winningTheme"] = plan.opportunity.strategy.winning_theme
    # Preserve existing memory keys (cms, hosting, accessibility, etc.)
    plan.proposal_memory = upsert_memory(plan.proposal_memory, "assembler", facts)
    return plan


def _zo_mode_for_title(title: str) -> str:
    lower = title.lower()
    if any(k in lower for k in ("team", "personnel", "staff", "bio")):
        return "select"
    if any(k in lower for k in ("experience", "case", "reference", "portfolio")):
        return "select"
    if any(k in lower for k in ("company", "qualification", "about", "firm")):
        return "pull"
    return "write"


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for exact comparison."""
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _match_outline_sections(
    *,
    requirement_text: str,
    target_hint: str,
    outline_sections: list[RfpSectionMap],
) -> list[str]:
    """Deterministic (no LLM) requirement -> section matching.

    Genuinely conservative: every branch is a whole-string comparison on
    normalized text, never a substring test. A short generic title (Summary,
    Cost, Team) is a substring of a large fraction of requirement texts, so the
    old ``title in requirement`` branch produced silent false positives — and a
    false "satisfied" hides the requirement from ``missing()``, which is the one
    thing the ledger exists to catch.

    Three ways a section can cover a requirement, in order of confidence:
      1. the item's own ``target_section`` hint equals the section title;
      2. one of the section's ``requirements`` bullets equals the requirement;
      3. the section title equals the requirement text.
    """
    req_n = _normalize(requirement_text)
    hint_n = _normalize(target_hint)
    matches: list[str] = []
    for section in outline_sections:
        title_n = _normalize(getattr(section, "title", "") or "")
        if hint_n and title_n and hint_n == title_n:
            matches.append(section.id)
            continue
        bullets = getattr(section, "requirements", None) or []
        if req_n and any(_normalize(str(b)) == req_n for b in bullets):
            matches.append(section.id)
            continue
        if req_n and title_n and title_n == req_n:
            matches.append(section.id)
    return matches


# Word-boundary "form"/"forms" only. A bare substring test matched "in-form-ation",
# "per-form-ance", "con-form-ing" and "plat-form" — four of the most common words in
# a compliance matrix — and mislabelled ordinary narrative requirements as forms.
_FORM_RE = re.compile(r"\bforms?\b")
# Phrases that contain the word "form" but are not a document to return.
_FORM_DENY_RE = re.compile(
    r"\b(?:"
    r"form(?:s)?\s+(?:of|a|an|the)\b"      # "in the form of", "form a team"
    r"|in\s+form(?:s)?\b"
    r"|form(?:al|ally|at|atting|ation|ative|ulate|ulated|ulation)\b"
    r")"
)


def _classify_compliance_source(item: ComplianceItem) -> str:
    blob = " ".join(
        str(getattr(item, attr, "") or "")
        for attr in ("requirement", "evidence_needed", "source_ref")
    ).lower()
    if _FORM_RE.search(blob) and not _FORM_DENY_RE.search(blob):
        return "form"
    return "required_content"


def _slug(text: str, *, limit: int = 48) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", (text or "").lower()))[:limit]


def build_requirement_ledger(
    compliance_items: list[ComplianceItem],
    evaluation_criteria: list[EvaluationCriterion],
    outline_sections: list[RfpSectionMap],
) -> RequirementLedger:
    """Build the persisted requirement ledger from Phase 2's parsed compliance
    matrix and evaluation criteria — the data that was previously reduced to
    ``f"Compliance item count: {len(...)}"`` for the outline planner and then
    discarded. Matches each requirement against the (already lean-filtered)
    outline sections so ``missing()``/``duplicated()`` can be read later.

    Pure data plumbing — makes zero LLM calls. Never raises: malformed or
    missing inputs degrade to an empty/partial ledger rather than blocking
    Phase 2.
    """
    requirements: list[LedgerRequirement] = []
    used_ids: set[str] = set()

    def _unique(candidate: str, fallback: str) -> str:
        """Ids are keys for later stages; the LLM can emit the same id twice."""
        base = (candidate or "").strip() or fallback
        if base not in used_ids:
            used_ids.add(base)
            return base
        suffix = 2
        while f"{base}-{suffix}" in used_ids:
            suffix += 1
        unique = f"{base}-{suffix}"
        used_ids.add(unique)
        return unique

    for index, item in enumerate(compliance_items or [], start=1):
        try:
            text = (getattr(item, "requirement", "") or "").strip()
            if not text:
                continue
            satisfied_by = _match_outline_sections(
                requirement_text=text,
                target_hint=getattr(item, "target_section", "") or "",
                outline_sections=outline_sections,
            )
            evidence_needed = (getattr(item, "evidence_needed", "") or "").strip()
            requirements.append(
                LedgerRequirement(
                    id=_unique(getattr(item, "id", "") or "", f"comp-{index}"),
                    text=text,
                    source=_classify_compliance_source(item),  # type: ignore[arg-type]
                    mandatory=bool(getattr(item, "mandatory", True)),
                    points=None,
                    satisfiedBy=satisfied_by,
                    kbQueries=[evidence_needed] if evidence_needed else [],
                )
            )
        except Exception:  # noqa: BLE001 — one bad item must never block Phase 2
            continue

    for index, crit in enumerate(evaluation_criteria or [], start=1):
        try:
            name = (getattr(crit, "name", "") or "").strip()
            if not name:
                continue
            satisfied_by = _match_outline_sections(
                requirement_text=name,
                target_hint="",
                outline_sections=outline_sections,
            )
            weight = getattr(crit, "weight", None)
            # Slug, not position: criterion identity must survive a Phase 2 re-run
            # that returns the same criteria in a different order.
            slug = _slug(name)
            requirements.append(
                LedgerRequirement(
                    id=_unique(
                        f"scored-{slug}" if slug else "", f"scored-{index}"
                    ),
                    text=name,
                    source="scored_criterion",
                    mandatory=True,
                    points=float(weight) if weight is not None else None,
                    satisfiedBy=satisfied_by,
                    kbQueries=[name],
                )
            )
        except Exception:  # noqa: BLE001
            continue

    return RequirementLedger(requirements=requirements)


def derive_legacy_fields(plan: ProposalExecutionPlan) -> dict[str, Any]:
    """Derive rfpSections / sectionQueries / proofPoints. Never returns evidenceCorpus."""
    from app.services.proposal_outline_dedup import filter_lean_outline_sections

    plans_by_id = {p.section_id: p for p in plan.writing.section_plans.plans}
    retrieval_by_id = {e.section_id: e for e in plan.writing.retrieval_plan.entries}

    # Near-dup + static only — outline already lean-filtered with RFP context upstream.
    lean_sections, _dropped = filter_lean_outline_sections(
        list(plan.writing.proposal_outline.sections),
        rfp_context="",
        drop_generic_filler=False,
    )

    rfp_sections: list[RfpSectionMap] = []
    section_queries: dict[str, list[str]] = {}

    for section in lean_sections:
        brief = plans_by_id.get(section.id)
        entry = retrieval_by_id.get(section.id)
        requirements: list[str] = []
        if brief:
            requirements.extend(brief.key_messages)
            requirements.extend(brief.evidence_needed)
        if not requirements:
            requirements = [f"Address {section.title} per RFP"]

        weight = None
        if brief and brief.evaluation_criteria:
            for crit in plan.opportunity.evaluation.criteria:
                if crit.name in brief.evaluation_criteria and crit.weight is not None:
                    weight = int(crit.weight)
                    break

        focus: list[str] = []
        if entry:
            focus = list(entry.expected_sources)[:6]
            section_queries[section.id] = list(entry.queries)[:5]

        rfp_sections.append(
            RfpSectionMap(
                id=section.id,
                title=section.title,
                requirements=requirements[:12],
                retrievalFocus=focus or ["company facts"],
                zoMode=_zo_mode_for_title(section.title),  # type: ignore[arg-type]
                evaluationWeight=weight,
            )
        )

    proof_points: list[ProofPoint] = []
    for brief in plan.writing.section_plans.plans:
        for need in brief.evidence_needed[:3]:
            proof_points.append(
                ProofPoint(
                    requirement=need,
                    caseStudy=need,
                    narrativeHook=brief.purpose,
                    relevance="planned",
                    sectionIds=[brief.section_id],
                    evaluationWeight=None,
                )
            )

    requirement_ledger = build_requirement_ledger(
        list(plan.opportunity.compliance.items),
        list(plan.opportunity.evaluation.criteria),
        rfp_sections,
    )

    return {
        "rfpSections": rfp_sections,
        "sectionQueries": section_queries,
        "proofPoints": proof_points,
        "requirementLedger": requirement_ledger,
    }


def stamp_metadata(plan: ProposalExecutionPlan, *, rfp_id: str, provider: str | None) -> ProposalExecutionPlan:
    plan.metadata.rfp_id = rfp_id
    plan.metadata.generated_at = datetime.now(timezone.utc).isoformat()
    if provider:
        plan.metadata.provider = provider
    plan.metadata.validation_status = plan.validation.readiness_status
    confidences = [
        plan.opportunity.understanding.confidence,
        plan.opportunity.strategy.confidence,
        plan.delivery.methodology.confidence,
        plan.delivery.budget.confidence,
        plan.writing.proposal_outline.confidence,
        plan.writing.retrieval_plan.confidence,
    ]
    nonzero = [c for c in confidences if c > 0]
    plan.metadata.plan_confidence = sum(nonzero) / len(nonzero) if nonzero else 0.0
    return plan
