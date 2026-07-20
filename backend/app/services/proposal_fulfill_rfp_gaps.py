"""Fulfill RFP gaps — re-scan THIS RFP, add missing closing sections, patch uncovered reqs.

Generic for every RFP. Never hardcode a client (HCCC/Umatilla/etc.).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    PreSubmitReview,
)
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_closing_package import (
    ClosingComponent,
    detect_closing_components,
    draft_already_covers_component,
)
from app.services.proposal_common import ProposalError, aload_rfp_for_proposal
from app.services.proposal_ending_report import (
    build_proposal_ending_report,
    ending_report_as_dict,
)
from app.services.proposal_presubmit_review import (
    run_presubmit_review_with_manual_flags,
)
from app.services.proposal_rfp_excerpt import (
    closing_package_excerpt,
    evaluation_and_kpi_excerpt,
    extract_reference_requirement_summary,
    insurance_requirements_excerpt,
    rfp_forbids_quotation_form_changes,
)
from app.services.rfp_content import combine_rfp_text, load_local_rfp_text
from app.services.proposal_draft_snapshots import push_proposal_snapshot
from app.services.proposal_fulfill_guard import fulfill_scan_preserve_bio_and_case_study_ids
from app.services.proposal_fulfill_kpi_fix import apply_contractor_kpi_text_fixes
from app.services.proposal_repository import (
    aget_proposal_draft,
    aget_research_cache,
    asave_proposal_draft,
    asave_research_cache,
)

logger = logging.getLogger(__name__)

_REF_DENIAL_RE = re.compile(
    r"(?:rfp|excerpt|solicitation).{0,80}(?:does not|did not|do not)\s+specify.{0,160}"
    r"(?:reference|number of references|institution type)",
    re.I | re.S,
)
_FORM_REWRITE_RE = re.compile(r"\bsection\s+[a-d]\b", re.I)


async def _repair_misstated_closing_sections(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
) -> tuple[ProposalDraft, list[str]]:
    """Re-draft References / Pricing closing tabs when they contradict the RFP."""
    components = {c.id: c for c in detect_closing_components(rfp_text)}
    logs: list[str] = []
    sections = list(draft.sections)
    changed = False

    ref_spec = extract_reference_requirement_summary(rfp_text)
    for idx, section in enumerate(sections):
        title_cf = (section.title or "").casefold()
        content = section.content or ""

        if "reference" in title_cf and ref_spec and (
            _REF_DENIAL_RE.search(content)
            or "does not specify" in content.casefold()
        ):
            comp = components.get("references")
            if comp:
                new_content = await _draft_closing_section(
                    component=comp,
                    rfp=rfp,
                    rfp_excerpt=rfp_text,
                )
                sections[idx] = section.model_copy(
                    update={"content": new_content, "status": "generated"}
                )
                changed = True
                logs.append("Re-drafted References — prior text denied RFP requirements.")

        if (
            rfp_forbids_quotation_form_changes(rfp_text)
            and any(k in title_cf for k in ("pricing", "quotation", "cost proposal", "fee"))
            and _FORM_REWRITE_RE.search(content)
        ):
            comp = components.get("pricing_form")
            if comp:
                new_content = await _draft_closing_section(
                    component=comp,
                    rfp=rfp,
                    rfp_excerpt=rfp_text,
                )
                sections[idx] = section.model_copy(
                    update={"content": new_content, "status": "generated"}
                )
                changed = True
                logs.append(
                    "Re-drafted Pricing/Quotation section — removed substitute Section A–D structure."
                )

    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


async def _draft_closing_section(
    *,
    component: ClosingComponent,
    rfp: RfpRecord,
    rfp_excerpt: str,
) -> str:
    stub = (
        f"## {component.title}\n\n"
        f"This RFP requires a closing package item matched as “{component.match_hint}”.\n\n"
        f"[MANUAL FILL: complete {component.title} per RFP instructions — "
        f"attach signed forms / fill agency form fields before export.]\n"
    )
    if not llm.is_configured():
        return stub
    try:
        excerpt_parts = [
            f"RFP excerpt (closing / forms / attachments):\n{closing_package_excerpt(rfp_excerpt, max_chars=28000)}",
        ]
        ins_ex = insurance_requirements_excerpt(rfp_excerpt, max_chars=12000)
        if ins_ex.strip():
            excerpt_parts.append(
                f"RFP insurance / Section 5.9 minimum limits:\n{ins_ex}"
            )
        kpi_ex = evaluation_and_kpi_excerpt(rfp_excerpt, max_chars=16000)
        if kpi_ex.strip():
            excerpt_parts.append(
                f"RFP contractor KPI / evaluation excerpt:\n{kpi_ex}"
            )
        user_excerpt = "\n\n".join(excerpt_parts)

        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You draft ONE closing / submission section for a zö agency public-sector proposal.\n"
                        "GOAL: help this bid WIN — be complete, compliant, and persuasive without inventing facts.\n"
                        "Use ONLY what THIS RFP demands — never invent client-specific facts, "
                        "phones, emails, policy numbers, or signatures.\n"
                        "When a field needs a human/file, use [MANUAL FILL: …].\n"
                        "When insurance/pricing numbers are stated in the RFP excerpt, copy them exactly.\n"
                        "NEVER cite HTA strategic-plan / agency four KPIs (Resident Sentiment, Visitor "
                        "Satisfaction, Average Daily Visitor Spending as an agency set). If KPIs appear, "
                        "use ONLY contractor Section 2.3 KPIs from the excerpt: Total Visitor Arrivals, "
                        "Total Visitor Expenditures, Average Islands Visited Per Person (+ growth targets).\n"
                        "Signature/certification blocks: do not certify wrong KPIs — omit KPI lists or "
                        "use contractor KPIs only.\n"
                        "References: quote the RFP's required count and institution type when stated; "
                        "never claim the RFP is silent on references if the excerpt specifies them.\n"
                        "Never claim the RFP is silent on insurance minimums if the excerpt states limits.\n"
                        "Pricing form: if the RFP forbids altering the quotation form, list only "
                        "official field labels — no Section A/B/C/D rewrites or extra clauses on the form.\n"
                        "Acknowledgement of Addenda / required forms: treat as pass/fail — follow RFP "
                        "wording tightly so evaluators can check the box.\n"
                        "Closing/commitment sections: end with a clear offer to perform, fit to buyer "
                        "goals, and readiness to start — still no invented proof.\n"
                        "Return JSON: {\"content\": \"markdown with ## headings\"}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Client: {rfp.client}\nRFP: {rfp.title}\n"
                        f"Closing component: {component.title} ({component.id})\n"
                        f"Instructions:\n{component.draft_instructions}\n\n"
                        f"{user_excerpt}"
                    ),
                },
            ],
            max_tokens=2048,
            temperature=0.2,
        )
        content = str((raw or {}).get("content") or "").strip()
        return content or stub
    except Exception as exc:  # noqa: BLE001
        logger.warning("Closing section draft failed for %s: %s", component.id, exc)
        return stub


async def ensure_closing_sections(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
) -> tuple[ProposalDraft, list[ClosingComponent], list[str]]:
    """Add missing closing sections demanded by THIS RFP text."""
    components = detect_closing_components(rfp_text)
    if not components:
        return draft, [], ["No closing-package patterns matched in RFP text."]

    ids = {s.id for s in draft.sections}
    titles = [s.title for s in draft.sections]
    added: list[ClosingComponent] = []
    logs: list[str] = []
    sections = list(draft.sections)

    for component in components:
        if draft_already_covers_component(
            draft_section_ids=ids,
            draft_titles=titles,
            component=component,
        ):
            logs.append(f"Closing already covered: {component.id}")
            continue
        content = await _draft_closing_section(
            component=component,
            rfp=rfp,
            rfp_excerpt=rfp_text,
        )
        content, _ = apply_contractor_kpi_text_fixes(content)
        sections.append(
            ProposalSection(
                id=component.section_id,
                title=component.title,
                content=content,
                status="generated",
                source="rfp",
                mode="write",
                required=True,
            )
        )
        ids.add(component.section_id)
        titles.append(component.title)
        added.append(component)
        logs.append(f"Added closing section: {component.title}")

    if not added:
        return draft, [], logs

    now = datetime.now(timezone.utc).isoformat()
    updated = draft.model_copy(update={"sections": sections, "updated_at": now})
    return updated, added, logs


def _merge_closing_into_research_map(
    research: ProposalResearchCache | None,
    added: list[ClosingComponent],
) -> ProposalResearchCache | None:
    if not research or not added:
        return research
    from app.models.proposal import RfpSectionMap

    existing = list(research.rfp_sections or [])
    existing_ids = {s.id for s in existing}
    for comp in added:
        if comp.section_id in existing_ids:
            continue
        existing.append(
            RfpSectionMap(
                id=comp.section_id,
                title=comp.title,
                requirements=[comp.draft_instructions],
                retrievalFocus=["company facts", "references", "pricing"],
                zoMode="write",
            )
        )
        existing_ids.add(comp.section_id)
    return research.model_copy(update={"rfp_sections": existing})


async def run_fulfill_rfp_gaps(
    rfp_id: str,
    *,
    use_llm: bool = True,
) -> tuple[PreSubmitReview, ProposalResearchCache, ProposalDraft, dict[str, Any]]:
    """Re-walk THIS RFP → add missing closing tabs → patch gaps → refresh ending report."""
    from app.services.proposal_pipeline_checkpoint import clear_fulfill_scan_activity

    try:
        return await _run_fulfill_rfp_gaps_body(rfp_id, use_llm=use_llm)
    finally:
        clear_fulfill_scan_activity(rfp_id)


async def _run_fulfill_rfp_gaps_body(
    rfp_id: str,
    *,
    use_llm: bool = True,
) -> tuple[PreSubmitReview, ProposalResearchCache, ProposalDraft, dict[str, Any]]:
    rfp, content, _rfp_text_truncated = await aload_rfp_for_proposal(rfp_id)
    # Full PDF extract for Scan (proposal drafting uses a 50k priority excerpt in context).
    _desc, pdf_text, pdf_exists, _missing, pdf_pages, _img = load_local_rfp_text(
        rfp, max_chars=250_000
    )
    rfp_text = combine_rfp_text(_desc or (content.description or ""), pdf_text, max_chars=250_000)
    if len(rfp_text.strip()) < 200:
        rfp_text = _rfp_text_truncated

    draft = await aget_proposal_draft(rfp_id)
    research = await aget_research_cache(rfp_id)
    has_body = draft and any((s.content or "").strip() for s in draft.sections)
    has_research = research and (
        (research.rfp_sections and len(research.rfp_sections) > 0)
        or (research.evidence_corpus and len(research.evidence_corpus) > 0)
    )
    if not has_body and not has_research:
        raise ProposalError(
            "No proposal content to fulfill. Generate the proposal first.",
            status_code=400,
        )
    if not draft:
        from app.services.proposal_generator import _default_sections

        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=_default_sections(None),
            updatedAt=datetime.now(timezone.utc).isoformat(),
        )
    draft = push_proposal_snapshot(draft, label="Before Scan RFP")
    await asave_proposal_draft(draft)

    from app.services.proposal_pipeline_checkpoint import (
        clear_fulfill_scan_activity,
        record_pipeline_activity,
    )

    FULFILL_STEPS = (
        "Closing & submission tabs",
        "RFP structure (all scored sections)",
        "Budget reconcile",
        "Consistency repairs",
        "Contractor KPIs (Section 2.3)",
        "Pre-submit refresh",
    )

    def _scan_progress(step: int, label: str, detail: str | None = None) -> None:
        record_pipeline_activity(
            rfp_id,
            label=label,
            detail=detail,
            step_index=step,
            step_total=len(FULFILL_STEPS),
            in_progress_phase="fulfill-scan",
        )

    _scan_progress(
        1,
        "Scan RFP: closing & submission",
        f"Reading {len(rfp_text.strip()):,} chars from uploaded PDF.",
    )

    report: dict[str, Any] = {
        "snapshotSavedAt": draft.snapshots[-1].saved_at if draft.snapshots else None,
        "rfpPdfPages": pdf_pages if pdf_exists else None,
        "rfpTextCharsUsedForScan": len(rfp_text.strip()),
        "closingDetected": [],
        "closingAdded": [],
        "logs": [],
        "humanDecisionGaps": [],
        "submissionNarrativesAdded": [],
        "submissionChecklistExpected": [],
    }

    draft, added, close_logs = await ensure_closing_sections(
        draft=draft,
        rfp=rfp,
        rfp_text=rfp_text,
    )
    report["logs"].extend(close_logs)
    all_closing = detect_closing_components(rfp_text)
    ids_after = {s.id for s in draft.sections}
    titles_after = [s.title for s in draft.sections]
    report["closingDetectedSections"] = [
        {"id": c.id, "title": c.title} for c in all_closing
    ]
    report["closingAlreadyPresent"] = [
        {"id": c.id, "title": c.title}
        for c in all_closing
        if draft_already_covers_component(
            draft_section_ids=ids_after,
            draft_titles=titles_after,
            component=c,
        )
    ]
    report["logs"].append(
        f"Closing package: {len(all_closing)} item(s) in RFP text; "
        f"{len(added)} new section(s) added; "
        f"{len(report['closingAlreadyPresent'])} already in proposal (Scan updates those in place, "
        f"does not duplicate)."
    )
    report["logs"].append(
        f"Scan uses {len(rfp_text.strip()):,} chars from uploaded RFP PDF "
        f"({pdf_pages or '?'} pages) — not the truncated drafting excerpt."
    )

    try:
        from app.services.proposal_rfp_submission_requirements import (
            ensure_all_rfp_submission_requirements,
            merge_deliverables_into_research,
        )

        draft, deliverables_added, sub_logs, checklist = await ensure_all_rfp_submission_requirements(
            draft=draft,
            rfp=rfp,
            rfp_text=rfp_text,
            research=research,
        )
        report["logs"].extend(sub_logs)
        report["submissionNarrativesAdded"] = [d.id for d in deliverables_added]
        report["submissionDeliverablesAdded"] = [
            {"id": d.id, "title": d.title, "kind": d.kind} for d in deliverables_added
        ]
        report["submissionChecklistExpected"] = checklist
        research = merge_deliverables_into_research(research, deliverables_added)
        if deliverables_added:
            await asave_proposal_draft(draft)
            if research:
                await asave_research_cache(research)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Submission narrative pass skipped: %s", exc)
        report["logs"].append(f"Submission narratives skipped: {exc}")

    try:
        from app.services.proposal_fulfill_rfp_structure import run_rfp_structure_alignment_pass

        _scan_progress(
            2,
            "Scan RFP: structure & scored sections",
            "Exhibit A / TOC / criteria — reframe BMP, qualifications, approach per RFP.",
        )
        preserved_pre = fulfill_scan_preserve_bio_and_case_study_ids(draft)
        draft, struct_logs, struct_human = await run_rfp_structure_alignment_pass(
            draft=draft,
            rfp=rfp,
            rfp_text=rfp_text,
            research=research,
            skip_section_ids=preserved_pre,
            use_llm=use_llm,
        )
        report["logs"].extend(struct_logs)
        report["structureScan"] = struct_logs
        report["humanDecisionGaps"].extend(struct_human)
        if struct_logs:
            await asave_proposal_draft(draft)
    except Exception as exc:  # noqa: BLE001
        logger.warning("RFP structure alignment skipped: %s", exc)
        report["logs"].append(f"RFP structure scan skipped: {exc}")

    draft, repair_logs = await _repair_misstated_closing_sections(
        draft=draft,
        rfp=rfp,
        rfp_text=rfp_text,
    )
    report["logs"].extend(repair_logs)
    if repair_logs:
        await asave_proposal_draft(draft)

    try:
        from app.services.proposal_section3_repair import repair_corrupted_section_3

        draft, s3_logs = await repair_corrupted_section_3(draft, rfp=rfp)
        report["logs"].extend(s3_logs)
        if s3_logs and any("Rebuilt Section 3" in line for line in s3_logs):
            await asave_proposal_draft(draft)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Section 3 repair skipped: %s", exc)
        report["logs"].append(f"Section 3 repair skipped: {exc}")

    try:
        from app.services.proposal_insurance_rfp_table import (
            repair_insurance_eo_table,
            repair_insurance_minimum_limits,
        )

        draft, ins_logs = repair_insurance_minimum_limits(draft, rfp_text=rfp_text)
        report["logs"].extend(ins_logs)
        draft, ins_logs = repair_insurance_eo_table(draft, rfp_text=rfp_text)
        report["logs"].extend(ins_logs)
        if ins_logs and any("Added E&O" in line for line in ins_logs):
            await asave_proposal_draft(draft)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Insurance repair skipped: %s", exc)
        report["logs"].append(f"Insurance repair skipped: {exc}")

    report["closingAdded"] = [c.id for c in added]
    report["closingAddedSections"] = [
        {"id": c.section_id, "title": c.title} for c in added
    ]
    # Also surface narrative qualification sections added from the full RFP scan
    for d in report.get("submissionDeliverablesAdded") or []:
        if isinstance(d, dict) and d.get("title"):
            report["closingAddedSections"].append(
                {
                    "id": d.get("id") or d.get("title"),
                    "title": d["title"],
                }
            )
    report["closingDetected"] = [
        c.id for c in detect_closing_components(rfp_text)
    ]
    research = _merge_closing_into_research_map(research, added)
    if added:
        await asave_proposal_draft(draft)
        if research:
            await asave_research_cache(research)

    preserved_ids = fulfill_scan_preserve_bio_and_case_study_ids(draft)
    if preserved_ids:
        report["logs"].append(
            f"Preserved {len(preserved_ids)} team bio / case study section(s) from LLM rewrite."
        )

    try:
        from app.services.proposal_fulfill_rfp_budget_kpi import (
            run_fulfill_budget_scan,
            run_fulfill_kpi_scan,
            summarize_budget_kpi_findings,
        )

        _scan_progress(3, "Scan RFP: budget reconcile", "Fee tables vs RFP spend — not full-manuscript context.")
        draft, research, budget_logs = await run_fulfill_budget_scan(
            rfp_id=rfp_id,
            rfp=rfp,
            draft=draft,
            research=research,
            rfp_text=rfp_text,
            use_llm=use_llm,
            skip_section_ids=preserved_ids,
        )
        report["logs"].extend(budget_logs)
        report["budgetScan"] = budget_logs
        if budget_logs:
            await asave_proposal_draft(draft)
            if research:
                await asave_research_cache(research)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Budget scan skipped: %s", exc)
        report["logs"].append(f"Budget scan skipped: {exc}")

    try:
        from app.services.proposal_fulfill_rfp_repairs import run_manuscript_consistency_repairs

        _scan_progress(4, "Scan RFP: consistency repairs", "Roster, KPI wording, qualification stubs.")
        draft, repair_logs, repair_human = await run_manuscript_consistency_repairs(
            draft,
            skip_section_ids=preserved_ids,
        )
        report["logs"].extend(repair_logs)
        report["humanDecisionGaps"].extend(repair_human)
        if repair_logs:
            await asave_proposal_draft(draft)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Manuscript consistency repairs skipped: %s", exc)
        report["logs"].append(f"Consistency repairs skipped: {exc}")

    if use_llm:
        try:
            from app.services.proposal_fulfill_rfp_budget_kpi import run_fulfill_kpi_scan

            _scan_progress(
                5,
                "Scan RFP: contractor KPIs + detail",
                "Activity Measure tables & BMP linkages — rewrite, not label swap.",
            )
            draft, kpi_logs, kpi_human = await run_fulfill_kpi_scan(
                draft=draft,
                rfp=rfp,
                rfp_text=rfp_text,
                research=research,
                skip_section_ids=preserved_ids,
                use_llm=True,
            )
            report["logs"].extend(kpi_logs)
            report["kpiScan"] = kpi_logs
            report["humanDecisionGaps"].extend(kpi_human)
            await asave_proposal_draft(draft)
        except Exception as exc:  # noqa: BLE001
            logger.warning("KPI scan skipped: %s", exc)
            report["logs"].append(f"KPI scan skipped: {exc}")
    else:
        try:
            from app.services.proposal_fulfill_rfp_budget_kpi import run_fulfill_kpi_scan

            _scan_progress(5, "Scan RFP: contractor KPIs", "Deterministic KPI alignment (no LLM).")
            draft, kpi_logs, kpi_human = await run_fulfill_kpi_scan(
                draft=draft,
                rfp=rfp,
                rfp_text=rfp_text,
                research=research,
                skip_section_ids=preserved_ids,
                use_llm=False,
            )
            report["logs"].extend(kpi_logs)
            report["kpiScan"] = kpi_logs
            report["humanDecisionGaps"].extend(kpi_human)
            await asave_proposal_draft(draft)
        except Exception as exc:  # noqa: BLE001
            report["logs"].append(f"KPI deterministic scan skipped: {exc}")

    try:
        from app.services.proposal_fulfill_rfp_budget_kpi import summarize_budget_kpi_findings

        report["budgetKpiSummary"] = summarize_budget_kpi_findings(draft, rfp_text, research)
    except Exception:  # noqa: BLE001
        report["budgetKpiSummary"] = []

    report["logs"].append(
        "Scan RFP walks full PDF text, submission checklist, RFP-scored section structure "
        "(Exhibit A / criteria), budget reconcile/sync, and contractor KPI alignment — "
        "team bios and case studies are not rewritten."
    )

    # Flag qualification gaps that writing cannot invent (references type, geo experience).
    lowered = rfp_text.casefold()
    if re_search_two_year(lowered):
        report["humanDecisionGaps"].append(
            "RFP asks for like-institution / two-year public references — confirm KB has "
            "qualifying clients or frame the gap honestly for leadership."
        )
    if re_search_geo_experience(lowered, rfp):
        report["humanDecisionGaps"].append(
            "RFP emphasizes prior work in the buyer's state/region — if the portfolio has none, "
            "acknowledge openly rather than implying local history."
        )

    _scan_progress(6, "Scan RFP: pre-submit refresh", "Updating checklist and ending report.")
    review = run_presubmit_review_with_manual_flags(
        rfp=rfp, draft=draft, research=research, finalized=False
    )
    now = datetime.now(timezone.utc).isoformat()
    research_for_ending = (research or ProposalResearchCache(rfpId=rfp_id, updatedAt=now)).model_copy(
        update={"presubmit_review": review, "updated_at": now}
    )
    ending = build_proposal_ending_report(
        rfp=rfp, draft=draft, research=research_for_ending
    )
    ending_dict = ending_report_as_dict(ending)
    ending_dict["closingPackage"] = {
        "detected": report["closingDetected"],
        "added": report["closingAdded"],
        "humanDecisionGaps": report["humanDecisionGaps"],
        "submissionChecklistExpected": report.get("submissionChecklistExpected") or [],
        "submissionNarrativesAdded": report.get("submissionNarrativesAdded") or [],
    }
    if report["humanDecisionGaps"]:
        next_actions = list(ending_dict.get("nextActions") or [])
        for gap in report["humanDecisionGaps"]:
            if gap not in next_actions:
                next_actions.append(gap)
        ending_dict["nextActions"] = next_actions

    report["inPlaceFixCount"] = sum(
        1
        for line in report.get("logs") or []
        if any(
            k in line
            for k in (
                "KPI fix",
                "KPI deterministic",
                "KPI scan",
                "Global contractor KPI",
                "Budget:",
                "Insurance limits",
                "Roster fix",
                "Accuracy repair",
                "Re-drafted",
            )
        )
    )

    updated_research = research_for_ending.model_copy(
        update={"ending_report": ending_dict, "updated_at": now}
    )
    draft = draft.model_copy(
        update={
            "last_fulfill_report": report,
            "updated_at": now,
        }
    )
    from app.services.proposal_draft_snapshots import attach_scan_summary_to_latest_before_scan

    draft = attach_scan_summary_to_latest_before_scan(draft, report)
    await asave_proposal_draft(draft)
    await asave_research_cache(updated_research)

    logger.info(
        "Fulfill RFP gaps for %s: closing+%s, issues=%d, ready=%s",
        rfp_id,
        report["closingAdded"],
        len(review.issues),
        review.ready_to_submit,
    )
    return review, updated_research, draft, report


def re_search_two_year(lowered: str) -> bool:
    import re

    return bool(
        re.search(
            r"two[- ]year|community\s+college|like\s+institution",
            lowered,
        )
    )


def re_search_geo_experience(lowered: str, rfp: RfpRecord) -> bool:
    import re

    loc = (rfp.location or "").casefold()
    state_hints = (
        "new jersey",
        " california",
        " oregon",
        " washington",
        " arizona",
        " colorado",
        " mississippi",
        " texas",
    )
    asks = bool(
        re.search(
            r"work\s+previously\s+done|prior\s+(?:work|experience)\s+in|"
            r"public\s+entities?\s+and\s+colleges\s+in",
            lowered,
        )
    )
    if not asks:
        return False
    # Only flag when RFP asks for in-state work; still a human decision.
    return bool(loc) or any(s.strip() in lowered for s in state_hints)
