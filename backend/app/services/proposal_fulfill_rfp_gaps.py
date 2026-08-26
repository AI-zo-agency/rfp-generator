"""Fulfill RFP gaps — re-scan THIS RFP, add missing closing sections, patch uncovered reqs.

Generic for every RFP. Never hardcode a client (HCCC/Umatilla/etc.).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

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
from app.services.proposal_generation_cancel import ProposalGenerationCancelled
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


class FulfillStepSkip(Exception):
    """This Complete & clean step already finished before stop — continue later."""

    def __init__(self, step: int) -> None:
        self.step = step
        super().__init__(f"skip step {step}")

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
    from app.services.proposal_closing_ledger import get_or_extract_closing_ledger

    ledger, _ = await get_or_extract_closing_ledger(rfp_text, research=None, persist=False)
    components = {c.id: c for c in detect_closing_components(rfp_text, ledger=ledger)}
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
            if not comp:
                # Prefer any ledger row whose id/title mentions references
                for c in components.values():
                    if "reference" in c.id or "reference" in (c.title or "").casefold():
                        comp = c
                        break
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
            from app.services.proposal_budget_content import (
                official_pricing_form_is_filled,
                section_looks_like_official_pricing_form,
            )

            # Never LLM-redraft a filled buyer RFQ / Quotation form — that wiped
            # DuPage contact fields into [Contact Name] / [Contact Email].
            if section_looks_like_official_pricing_form(section) and official_pricing_form_is_filled(
                content
            ):
                continue
            comp = None
            for c in components.values():
                if any(
                    tok in c.id or tok in (c.title or "").casefold()
                    for tok in ("pric", "quotation", "cost")
                ):
                    comp = c
                    break
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
                logs.append("Re-drafted Pricing form — prior text rewrote buyer form structure.")

    if changed:
        now = datetime.now(timezone.utc).isoformat()
        draft = draft.model_copy(update={"sections": sections, "updated_at": now})
    return draft, logs


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

        raw, _ = await asyncio.wait_for(
            llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You draft ONE closing / submission section for a zö agency public-sector proposal.\n"
                        "GOAL: help this bid WIN — be complete, compliant, and persuasive without inventing facts.\n"
                        "Use ONLY what THIS RFP demands — never invent client-specific facts, "
                        "phones, emails, policy numbers, or signatures.\n"
                        "When a field needs a human/file, use [MANUAL FILL: …].\n"
                        "NEVER certify vendor registration complete, procurement documents "
                        "downloaded, or promise registration attachments — use [MANUAL FILL: Sonja "
                        "confirm registration] unless registration confirmation is on file.\n"
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
                        "Closing/commitment sections: this is the COMPULSORY end of the proposal — "
                        "clear offer to perform, fit to buyer goals, capacity/timeline, validity "
                        "period if stated, invite next steps. Do not repeat full case studies. "
                        "Still no invented proof.\n"
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
                node_name="fulfill_scan_closing_section",
            ),
            timeout=150.0,
        )
        content = str((raw or {}).get("content") or "").strip()
        return content or stub
    except ProposalGenerationCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Closing section draft failed for %s: %s", component.id, exc)
        return stub


_CLOSING_SECTIONS_TIME_BUDGET_SEC = 480.0
_CLOSING_SECTIONS_MAX_CONCURRENT = 3


async def ensure_closing_sections(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None = None,
    on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
) -> tuple[ProposalDraft, list[ClosingComponent], list[str], ProposalResearchCache | None]:
    """Add missing closing sections from the closing-requirement ledger.

    Only components THIS RFP's ledger obliges. A closing statement is not
    forced — under a page limit an unrequested section displaces real asks.
    """
    from app.services.proposal_closing_ledger import get_or_extract_closing_ledger

    ledger, research = await get_or_extract_closing_ledger(
        rfp_text, research=research
    )
    components = detect_closing_components(rfp_text, ledger=ledger)
    if not components:
        return draft, [], ["No closing package items in ledger."], research

    ids = {s.id for s in draft.sections}
    titles = [s.title for s in draft.sections]
    added: list[ClosingComponent] = []
    logs: list[str] = []
    sections = list(draft.sections)

    to_draft = [
        component
        for component in components
        if not draft_already_covers_component(
            draft_section_ids=ids,
            draft_titles=titles,
            component=component,
            draft=draft,
        )
    ]
    for component in components:
        if component not in to_draft:
            logs.append(f"Closing already covered: {component.id}")

    # Each component is an independent closing/submission form — no
    # dependency between them — so draft a few concurrently instead of
    # one-at-a-time, with a hard per-item timeout (see _draft_closing_section)
    # and an overall time budget so this can never hang the pipeline the way
    # it used to (silent, unbounded, sequential LLM calls with zero progress
    # reporting — the same failure shape the "Complete & clean draft" freeze
    # in the Pre-submit refresh step had).
    sem = asyncio.Semaphore(_CLOSING_SECTIONS_MAX_CONCURRENT)

    async def _draft_one(component: ClosingComponent) -> tuple[ClosingComponent, str]:
        async with sem:
            content = await _draft_closing_section(
                component=component,
                rfp=rfp,
                rfp_excerpt=rfp_text,
            )
            content, _ = apply_contractor_kpi_text_fixes(content)
            return component, content

    tasks = {asyncio.ensure_future(_draft_one(c)): c for c in to_draft}
    if tasks:
        done, pending = await asyncio.wait(
            tasks.keys(), timeout=_CLOSING_SECTIONS_TIME_BUDGET_SEC
        )
        if pending:
            for task in pending:
                task.cancel()
            logs.append(
                f"Closing sections: time budget reached — drafted {len(done)}/{len(tasks)}, "
                f"{len(pending)} left for the next pass"
            )
            logger.warning(
                "ensure_closing_sections time budget (%ss) reached — %d/%d done",
                _CLOSING_SECTIONS_TIME_BUDGET_SEC,
                len(done),
                len(tasks),
            )

        # Stable order (RFP ledger order), not completion order.
        done_components = {tasks[t]: t for t in done}
        completed = 0
        for component in to_draft:
            task = done_components.get(component)
            if task is None:
                continue
            _component, content = task.result()
            completed += 1
            if on_progress:
                await on_progress(completed, len(to_draft), component.title)
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

    logs.append(f"__closing_ledger_count__={len(ledger.requirements)}")

    from app.services.proposal_closing_ledger import (
        ensure_missing_closing_stubs,
        repair_fabricated_ready_in_draft,
    )

    # Apply on current sections (including any just added) so Ready fakes die.
    probe = draft if not added else draft.model_copy(
        update={
            "sections": sections,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    probe, fix_logs = repair_fabricated_ready_in_draft(probe, ledger)
    logs.extend(fix_logs)
    probe, stub_logs = ensure_missing_closing_stubs(probe, ledger)
    logs.extend(stub_logs)
    sections = list(probe.sections)

    if not added and not fix_logs and not stub_logs:
        return draft, [], logs, research

    now = datetime.now(timezone.utc).isoformat()
    updated = draft.model_copy(update={"sections": sections, "updated_at": now})
    return updated, added, logs, research


def _merge_closing_into_research_map(
    research: ProposalResearchCache | None,
    added: list[ClosingComponent],
) -> ProposalResearchCache | None:
    if not research or not added:
        return research
    from app.models.proposal import RfpSectionMap
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    existing = list(research.rfp_sections or [])
    existing_ids = {s.id for s in existing}
    existing_titles = [s.title for s in existing]
    for comp in added:
        if comp.section_id in existing_ids:
            continue
        if any(
            outline_titles_near_duplicate(comp.title, prev) for prev in existing_titles
        ):
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
        existing_titles.append(comp.title)
    return research.model_copy(update={"rfp_sections": existing})


async def run_fulfill_rfp_gaps(
    rfp_id: str,
    *,
    use_llm: bool = True,
    mode: str = "full",
) -> tuple[PreSubmitReview, ProposalResearchCache, ProposalDraft, dict[str, Any]]:
    """UI 'Scan RFP & add missing pieces' → full RFP update (default).

    mode='full' runs closing/structure/budget/KPI/VERIFY/pre-submit.
    mode='verify_scrub_only' keeps the lighter scrub + ledger/budget path.
    """
    from app.services.proposal_generation_cancel import (
        ProposalGenerationCancelled,
        bind_active_rfp,
        unbind_active_rfp,
    )
    from app.services.proposal_pipeline_checkpoint import (
        clear_fulfill_scan_activity,
        record_generation_stopped,
    )
    from app.services.proposal_verify_optional_scrub import run_verify_scrub_only_scan
    import uuid

    from app.services.llm_call_context import llm_call_context

    scan_run_id = str(uuid.uuid4())
    token = bind_active_rfp(rfp_id)
    cancelled = False
    try:
        with llm_call_context(
            rfp_id=rfp_id,
            run_id=scan_run_id,
            node_name="fulfill-scan",
        ):
            if (mode or "full").strip().lower() in {
                "verify_scrub_only",
                "verify-scrub",
                "verify_scrub",
                "scrub",
            }:
                return await run_verify_scrub_only_scan(rfp_id)
            return await _run_fulfill_rfp_gaps_body(rfp_id, use_llm=use_llm)
    except ProposalGenerationCancelled:
        cancelled = True
        await record_generation_stopped(rfp_id, "fulfill-scan")
        raise
    finally:
        unbind_active_rfp(token)
        if not cancelled:
            await clear_fulfill_scan_activity(rfp_id)


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

    # HARD BASELINE: capture every section exactly as it enters this scan run,
    # in memory, before ANY step can mutate it. This is the authoritative "good"
    # copy used at the end to refuse saving a section the scan degraded — it does
    # not depend on snapshots (which, on resume, can already be post-damage).
    pre_scan_sections = list(draft.sections)

    from app.services.proposal_pipeline_checkpoint import (
        complete_fulfill_scan,
        compute_fulfill_scan_hash,
        fulfill_resume_step,
        fulfill_scan_is_already_clean,
        record_pipeline_activity,
    )

    resume_at = fulfill_resume_step(research)
    scan_hash = compute_fulfill_scan_hash(draft, rfp_text)
    if (
        fulfill_scan_is_already_clean(
            research=research, resume_at=resume_at, current_hash=scan_hash
        )
        and research is not None
        and research.presubmit_review is not None
        and draft.last_fulfill_report
    ):
        logger.info(
            "Scan RFP %s: draft + RFP text unchanged since last completed scan — "
            "skipping the 18-step pass (nothing new to check).",
            rfp_id,
        )
        return research.presubmit_review, research, draft, dict(draft.last_fulfill_report)

    if resume_at <= 1:
        draft = push_proposal_snapshot(draft, label="Before Scan RFP")
        await asave_proposal_draft(draft)
    else:
        logger.info("Scan RFP resume %s from step %s", rfp_id, resume_at)

    FULFILL_STEPS = (
        "RFP structure (all scored sections)",
        "Closing & submission tabs",
        "Requirement ledger (merge / cut / add)",
        "DQ & gov-policy gate (agentic loop)",
        "Remove duplicate sections",
        "Senior editor review (RFP reviewer)",
        "Budget (regen if missing + thorough)",
        "Consistency repairs",
        "Compliance fabrication guard",
        "Contractor KPIs (Section 2.3)",
        "KB fact-check (Supermemory)",
        "RFP contradiction check (LLM)",
        "Line-by-line KB grounding (async)",
        "Remove optional VERIFY/MANUAL FILL",
        "Compact manuscript (remove duplicates)",
        "Page limit & anti-invention (Ralph)",
        "Pre-submit refresh",
        "Submission readiness (triage + score)",
    )

    # Final two stages always re-run on resume — they produce the ending report
    # and designer-ready verification (hollow fill from won proposals).
    _FINAL_ALWAYS_RUN_FROM = len(FULFILL_STEPS) - 1  # Pre-submit onward


    async def _ensure_not_stopped() -> None:
        from app.services.proposal_generation_cancel import check_generation_cancelled

        await check_generation_cancelled(rfp_id)

    report: dict[str, Any] = {
        "mode": "full",
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
    added: list[Any] = []
    all_closing: list[Any] = []
    if resume_at > 1:
        prior = dict(draft.last_fulfill_report or {})
        if prior:
            logs = list(prior.get("logs") or [])
            report = {**prior, **report, "logs": logs}
        report["logs"].append(
            f"Resume: continuing from step {resume_at} — "
            f"earlier steps are already saved on the draft; "
            f"pre-submit refresh and submission readiness still run in full "
            f"(verify missing answers from past won proposals; designer-ready report)."
        )
        logger.info("Scan RFP resume %s from step %s", rfp_id, resume_at)
        # One checkpoint write, up front, with the TRUE resume target —
        # not one write per skipped step. That per-step version (removed)
        # took several sequential DB round-trips to walk from step 1 up to
        # resume_at before any real work began; if anything interrupted the
        # process mid-walk (a worker restart, a stop landing at the wrong
        # instant), the checkpoint was left holding whatever low number it
        # last wrote — not the real position — so the *next* resume started
        # from that stale low step instead of where the run actually was.
        # A single write of the real target is immune to that: even an
        # interruption a moment later still leaves the correct step behind.
        await record_pipeline_activity(
            rfp_id,
            label=f"Resuming from step {resume_at} (already-done steps saved)",
            detail=None,
            step_index=min(resume_at, len(FULFILL_STEPS)),
            step_total=len(FULFILL_STEPS),
            in_progress_phase="fulfill-scan",
        )

    def _log_resume_skip(step: int) -> None:
        label = (
            FULFILL_STEPS[step - 1]
            if 1 <= step <= len(FULFILL_STEPS)
            else f"step {step}"
        )
        report["logs"].append(f"Resume: skipped '{label}' (already saved).")
        logger.info("Scan RFP %s resume skip step %s (%s)", rfp_id, step, label)

    async def _scan_progress(step: int, label: str, detail: str | None = None) -> None:
        nonlocal draft
        # Never skip the final stages — they produce the ending report and
        # designer-ready verification (hollow fill from past won proposals).
        if step < resume_at and step < _FINAL_ALWAYS_RUN_FROM:
            # No checkpoint write here on purpose — the single upfront
            # "Resuming from step {resume_at}" write (above, at function
            # start) already reflects the true target. Writing per skipped
            # step used to require several sequential DB round-trips just to
            # walk from step 1 up to resume_at, and an interruption mid-walk
            # left the checkpoint on a stale, too-low step instead of the
            # real position. This local log call is enough for visibility —
            # _log_resume_skip only touches in-memory `report["logs"]`.
            raise FulfillStepSkip(step)
        await record_pipeline_activity(
            rfp_id,
            label=label,
            detail=detail,
            step_index=step,
            step_total=len(FULFILL_STEPS),
            in_progress_phase="fulfill-scan",
        )
        draft = draft.model_copy(
            update={
                "last_fulfill_report": report,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        await asave_proposal_draft(draft)

    try:
        from app.services.proposal_fulfill_rfp_structure import run_rfp_structure_alignment_pass

        await _scan_progress(
            1,
            "Scan RFP: structure & scored sections",
            "Establish THIS RFP's TOC first. Order intelligence tabs. "
            "Header-wrap company identity when the RFP names it. Do not rewrite Sections 1–3.",
        )
        await _ensure_not_stopped()
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
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("RFP structure alignment skipped: %s", exc)
        report["logs"].append(f"RFP structure scan skipped: {exc}")

    try:
        await _scan_progress(
            2,
            "Scan RFP: closing & submission",
            f"Fill gaps against the RFP TOC. Reading {len(rfp_text.strip()):,} chars from uploaded PDF.",
        )
        await _ensure_not_stopped()

        async def _closing_section_progress(done: int, total: int, title: str) -> None:
            await record_pipeline_activity(
                rfp_id,
                label="Scan RFP: closing & submission",
                detail=f"Drafting closing sections — {done}/{total}: {title}",
                step_index=2,
                step_total=len(FULFILL_STEPS),
                in_progress_phase="fulfill-scan",
            )

        draft, added, close_logs, research = await ensure_closing_sections(
            draft=draft,
            rfp=rfp,
            rfp_text=rfp_text,
            research=research,
            on_progress=_closing_section_progress,
        )
        report["logs"].extend(close_logs)
        from app.services.proposal_closing_ledger import get_or_extract_closing_ledger

        closing_ledger, research = await get_or_extract_closing_ledger(
            rfp_text, research=research
        )
        all_closing = detect_closing_components(rfp_text, ledger=closing_ledger)
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
                draft=draft,
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
        except ProposalGenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Submission narrative pass skipped: %s", exc)
            report["logs"].append(f"Submission narratives skipped: {exc}")
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Closing & submission skipped: %s", exc)
        report["logs"].append(f"Closing & submission skipped: {exc}")

    if use_llm:
        try:
            from app.services.proposal_draft_structure_stubs import (
                draft_rfp_structure_stubs,
                replace_ineligible_section3_case_studies,
            )

            await _scan_progress(
                2,
                "Scan RFP: draft scored stubs",
                "Write Team Qualifications and other RFP-required stubs left as Action needed.",
            )
            await _ensure_not_stopped()

            async def _stub_draft_progress(done: int, total: int, title: str) -> None:
                await record_pipeline_activity(
                    rfp_id,
                    label="Scan RFP: draft scored stubs",
                    detail=f"Drafting required tabs — {done}/{total}: {title}",
                    step_index=2,
                    step_total=len(FULFILL_STEPS),
                    in_progress_phase="fulfill-scan",
                )

            draft, stub_draft_logs = await draft_rfp_structure_stubs(
                draft, rfp_id=rfp_id, rfp=rfp, on_progress=_stub_draft_progress
            )
            report["logs"].extend(stub_draft_logs)
            if stub_draft_logs:
                await asave_proposal_draft(draft)

            draft, cs_swap_logs = await replace_ineligible_section3_case_studies(
                draft, rfp_id=rfp_id, rfp=rfp
            )
            report["logs"].extend(cs_swap_logs)
            if cs_swap_logs:
                await asave_proposal_draft(draft)
        except ProposalGenerationCancelled:
            raise
        except FulfillStepSkip as skip:
            _log_resume_skip(skip.step)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scored stub / case-study swap skipped: %s", exc)
            report["logs"].append(f"Scored stub draft skipped: {exc}")

    try:
        from app.services.proposal_zero_fabrication import apply_zero_fabrication_guards

        draft, zf_report = apply_zero_fabrication_guards(
            draft,
            research=research,
            budget=research.budget if research else None,
            rfp_text=rfp_text,
            label="scan-preflight",
        )
        report["logs"].extend(zf_report.logs[:16])
        report["humanDecisionGaps"].extend(
            line.split("HUMAN_GAP:", 1)[1].strip()
            for line in zf_report.logs
            if "HUMAN_GAP:" in line
        )
        if zf_report.logs:
            await asave_proposal_draft(draft)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scan preflight integrity skipped: %s", exc)
        report["logs"].append(f"Scan preflight integrity skipped: {exc}")

    try:
        from app.services.proposal_scan_fact_repairs import run_scan_fact_repairs

        await _scan_progress(
            2,
            "Scan RFP: fact repairs",
            "Attach 04_Bio PDFs via designer note; scrub false vendor-registration / insurance "
            "Compliant certifications; fill only tabs with missing answers from past won proposals.",
        )
        await _ensure_not_stopped()
        draft, fact_logs = await run_scan_fact_repairs(
            draft,
            research=research,
            rfp_text=rfp_text,
            rfp_title=rfp.title or "",
            rfp_client=rfp.client or "",
            rfp_sector=getattr(rfp, "sector", None) or "",
            rfp_id=rfp_id,
        )
        report["logs"].extend(fact_logs)
        for line in fact_logs:
            if line.startswith("HUMAN_GAP:"):
                gap = line.split("HUMAN_GAP:", 1)[1].strip()
                if gap and gap not in report["humanDecisionGaps"]:
                    report["humanDecisionGaps"].append(gap)
        if fact_logs:
            await asave_proposal_draft(draft)
        report["factRepairs"] = fact_logs[:24]
        compliance_hits = [
            line
            for line in fact_logs
            if any(
                token in line.casefold()
                for token in (
                    "vendor-registration",
                    "complete-rfp-reviewed",
                    "bio role",
                    "invented bio vertical",
                    "insurance carrier",
                    "registration confirmation",
                    "compliant",
                    "meets or exceeds insurance",
                    "manual fill: sonja",
                    "human_gap",
                )
            )
        ]
        if compliance_hits:
            report["complianceFabricationRepairs"] = compliance_hits[:16]
            report["humanDecisionGaps"].append(
                "Fabricated compliance actions detected (vendor registration / complete RFP "
                "review / bio role mismatch / unverified carrier) — repaired to MANUAL FILL "
                "or VERIFY; Sonja must confirm before submission."
            )
        if fact_logs:
            await asave_proposal_draft(draft)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scan fact repairs skipped: %s", exc)
        report["logs"].append(f"Scan fact repairs skipped: {exc}")

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
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
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
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Insurance repair skipped: %s", exc)
        report["logs"].append(f"Insurance repair skipped: {exc}")

    if resume_at <= 1:
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
        report["closingDetected"] = [c.id for c in all_closing]
        research = _merge_closing_into_research_map(research, added)
        if added:
            await asave_proposal_draft(draft)
            if research:
                await asave_research_cache(research)

    preserved_ids = fulfill_scan_preserve_bio_and_case_study_ids(draft)
    if preserved_ids:
        report["logs"].append(
            f"Preserved {len(preserved_ids)} team bio / case study section(s) from full LLM rewrite "
            "(04_Bio PDFs / case-study bodies). Role-on-this-engagement blurbs still go through "
            "KB fact-check and line grounding."
        )

    try:
        from app.services.proposal_scan_dq_orchestrator import (
            merge_ledger_into_report,
            run_scan_coverage_orchestrator,
        )

        await _scan_progress(
            3,
            "Scan RFP: coverage orchestrator",
            "Ledger ADD/MERGE/CUT — add missing sections if needed; trim for length.",
        )
        await _ensure_not_stopped()
        orch = await run_scan_coverage_orchestrator(
            rfp_id=rfp_id,
            draft=draft,
            research=research,
            rfp=rfp,
            rfp_text=rfp_text,
        )
        draft = orch.draft
        research = orch.research
        report["logs"].extend(orch.logs)
        report["orchestratorLoopPasses"] = orch.loop_passes
        if orch.ledger_result is not None:
            merge_ledger_into_report(report, orch.ledger_result, orch.ledger_draft_logs)

        await _scan_progress(
            4,
            "Scan RFP: DQ & gov-policy gate",
            "Go/No-Go risks, legal attestations, eligibility / form / page-limit disqualifiers.",
        )
        await _ensure_not_stopped()
        if orch.dq is not None:
            draft = orch.dq.draft
            research = orch.dq.research
            report["disqualificationRisks"] = orch.dq.disqualification_risks
            report["disqualificationRiskCount"] = len(orch.dq.disqualification_risks)
            report["humanDecisionGaps"].extend(orch.dq.human_decision_gaps)
            report["logs"].extend(
                line
                for line in orch.dq.logs
                if line not in report["logs"]
            )
            if orch.dq.changed:
                await asave_proposal_draft(draft)
        else:
            report["disqualificationRisks"] = []
            report["disqualificationRiskCount"] = 0

        # Physical-form checklist on the UI Scan path (mode=full) — previously
        # only ran on verify_scrub_only. Stubs do not clear these.
        from app.services.proposal_rfp_submission_requirements import (
            outstanding_submission_checklist_for_scan,
        )

        outstanding = outstanding_submission_checklist_for_scan(rfp_text, draft)
        report["submissionNeedsDraftingCount"] = len(outstanding.needs_drafting)
        report["submissionNeedsDraftingTitles"] = outstanding.needs_drafting
        report["submissionNeedsAttachmentCount"] = len(outstanding.needs_attachment)
        report["submissionNeedsAttachmentTitles"] = outstanding.needs_attachment
        if outstanding.needs_attachment:
            # Physical PDFs cannot be invented — surface as attachment handoff,
            # NOT as manuscript "disqualification risks" (designer notes fix the
            # drafting side; human still attaches the file).
            report["humanDecisionGaps"].append(
                "attachment:needs-human — "
                f"{len(outstanding.needs_attachment)} physical document(s) required "
                "by this RFP — designer notes added; attach signed originals before submit: "
                + "; ".join(outstanding.needs_attachment[:8])
            )
            report["logs"].append(
                "submission-checklist:attachment — "
                f"{len(outstanding.needs_attachment)} physical document(s) still open "
                "(handoff via DESIGNER NOTE — not counted as manuscript DQ)."
            )
        # Strip stale attachment lines from DQ list if any earlier path added them
        report["disqualificationRisks"] = [
            risk
            for risk in (report.get("disqualificationRisks") or [])
            if "missing required attachment" not in risk.casefold()
            and "physical document" not in risk.casefold()
        ]
        report["disqualificationRiskCount"] = len(report["disqualificationRisks"])
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Coverage orchestrator / DQ gate during Scan RFP skipped: %s", exc)
        report["logs"].append(f"Coverage orchestrator / DQ gate skipped: {exc}")

    try:
        from app.services.proposal_section_dedup import dedupe_manuscript_for_scan

        await _scan_progress(
            5,
            "Scan RFP: Removing duplicates",
            "Delete clones, remove mega sections that restate sibling tabs.",
        )
        await _ensure_not_stopped()
        sections, dedupe_logs = dedupe_manuscript_for_scan(
            list(draft.sections),
            drop_clone_tabs=False,
        )
        if dedupe_logs:
            draft = draft.model_copy(
                update={
                    "sections": sections,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            await asave_proposal_draft(draft)
            deleted = [
                log
                for log in dedupe_logs
                if "near-duplicate" in log
                or "content overlap" in log
                or "contained restatement" in log
                or "removed —" in log
                or "heavy overlap" in log
                or "content clone" in log
                or "report twin" in log
            ]
            report["duplicateSectionsRemoved"] = len(deleted)
            report["duplicateSectionsRemovedTitles"] = [
                p.split(" (", 1)[0] for p in deleted[:12]
            ]
            report["logs"].append(
                f"Dedupe: {len(dedupe_logs)} compact action(s): "
                + "; ".join(dedupe_logs[:10])
            )
        # Required form slots: copy Active Client List into missing I.2 from a
        # sibling tab (same as section chat). Run after dedupe so clones are gone.
        try:
            from app.services.proposal_chat_improve_pin import (
                fill_all_active_client_lists_from_siblings,
            )

            draft, form_logs = fill_all_active_client_lists_from_siblings(draft)
            if form_logs:
                draft = draft.model_copy(
                    update={"updated_at": datetime.now(timezone.utc).isoformat()}
                )
                await asave_proposal_draft(draft)
                report["logs"].extend(form_logs[:8])
        except Exception as form_exc:  # noqa: BLE001
            logger.warning("Active Client List form-slot fill after dedupe skipped: %s", form_exc)
            report["logs"].append(f"Form-slot Active Client List fill skipped: {form_exc}")
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scan RFP dedupe prune skipped: %s", exc)
        report["logs"].append(f"Dedupe prune skipped: {exc}")

    try:
        await _scan_progress(
            6,
            "Scan RFP: senior editor review",
            "RFP proposal reviewer — coverage gaps from the outline; overlap already trimmed. "
            "No second senior-editor LLM rewrite.",
        )
        await _ensure_not_stopped()
        from app.services.proposal_scan_senior_reviewer import (
            run_complete_scan_senior_reviewer,
        )

        draft, research, reviewer = await run_complete_scan_senior_reviewer(
            rfp_id=rfp_id,
            rfp=rfp,
            draft=draft,
            research=research,
            rfp_text=rfp_text,
        )
        report["seniorReviewer"] = {
            "deleteTickets": reviewer.delete_tickets,
            "dedupeTickets": reviewer.dedupe_tickets,
            "sectionsImproved": reviewer.sections_improved,
            "coverageGaps": reviewer.coverage_gaps[:12],
            "complianceGaps": reviewer.compliance_gaps[:12],
            "logs": reviewer.logs[:30],
        }
        for gap in reviewer.coverage_gaps[:8]:
            if gap not in report["humanDecisionGaps"]:
                report["humanDecisionGaps"].append(gap)
        for gap in reviewer.compliance_gaps[:8]:
            if gap not in report["humanDecisionGaps"]:
                report["humanDecisionGaps"].append(gap)
        for line in reviewer.logs[:20]:
            report["logs"].append(line)
        if reviewer.sections_improved or reviewer.logs:
            await asave_proposal_draft(draft)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Senior reviewer during Scan RFP skipped: %s", exc)
        report["logs"].append(f"Senior reviewer skipped: {exc}")

    try:
        from app.services.agency_facts import default_business_information_markdown
        from app.services.proposal_section_quality import word_count

        fixed = False
        sections = list(draft.sections)
        for i, sec in enumerate(sections):
            if sec.id != "section-1-business-info":
                continue
            body = (sec.content or "").strip()
            if word_count(body) >= 40:
                break
            sections[i] = sec.model_copy(
                update={
                    "content": default_business_information_markdown(),
                    "status": "generated",
                }
            )
            fixed = True
            break
        if fixed:
            draft = draft.model_copy(
                update={
                    "sections": sections,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            await asave_proposal_draft(draft)
            report["logs"].append(
                "Section 1.3: filled hollow Business Information from canonical agency facts."
            )
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Business Information hollow fill skipped: %s", exc)
        report["logs"].append(f"Business Information fill skipped: {exc}")

    try:
        from app.services.proposal_fulfill_rfp_budget_kpi import (
            run_fulfill_budget_scan,
            run_fulfill_kpi_scan,
        )

        await _scan_progress(
            7,
            "Scan RFP: budget (thorough)",
            "Regenerate if missing; reconcile math; grounding vs manuscript.",
        )
        await _ensure_not_stopped()
        draft, research, budget_logs, budget_meta = await run_fulfill_budget_scan(
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
        report.update(budget_meta)
        if budget_logs or budget_meta.get("budgetChanged"):
            await asave_proposal_draft(draft)
            if research:
                await asave_research_cache(research)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Budget scan skipped: %s", exc)
        report["logs"].append(f"Budget scan skipped: {exc}")
        report["budgetStatus"] = "needs_human"
        report["budgetEscalationNotes"] = [f"budget scan failed: {exc}"]

    try:
        from app.services.proposal_cert_claim_scrub import apply_cert_claim_scrub_to_draft
        from app.services.proposal_consistency_enforcement import (
            apply_consistency_enforcement,
        )
        from app.services.proposal_fulfill_rfp_repairs import run_manuscript_consistency_repairs
        from app.services.proposal_rfp_submission_requirements import (
            outstanding_submission_checklist_for_scan,
        )

        await _scan_progress(
            8,
            "Scan RFP: consistency repairs",
            "Primary contact, references, schedule vs approach, cert claims, "
            "signed-PDF designer notes — existing draft only (no full regen).",
        )
        await _ensure_not_stopped()
        draft, repair_logs, repair_human = await run_manuscript_consistency_repairs(
            draft,
            skip_section_ids=preserved_ids,
        )
        report["logs"].extend(repair_logs)
        report["humanDecisionGaps"].extend(repair_human)

        outstanding_for_notes = outstanding_submission_checklist_for_scan(
            rfp_text, draft
        )
        draft, consistency_logs = apply_consistency_enforcement(
            draft,
            research=research,
            attachment_labels=list(outstanding_for_notes.needs_attachment),
            rfp_text=rfp_text,
        )
        report["logs"].extend(consistency_logs)
        report["consistencyFixesApplied"] = len(consistency_logs)
        if consistency_logs:
            report["consistencyFixSummaries"] = consistency_logs[:12]

        draft, cert_logs = apply_cert_claim_scrub_to_draft(
            draft, skip_section_ids=preserved_ids
        )
        report["logs"].extend(cert_logs)

        if repair_logs or consistency_logs or cert_logs:
            await asave_proposal_draft(draft)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Manuscript consistency repairs skipped: %s", exc)
        report["logs"].append(f"Consistency repairs skipped: {exc}")

    try:
        from app.services.proposal_fulfill_truncation_repair import (
            repair_truncated_manuscript_sections,
        )

        draft, trunc_logs = await repair_truncated_manuscript_sections(
            draft=draft,
            rfp=rfp,
            skip_section_ids=preserved_ids,
            use_llm=use_llm,
        )
        report["logs"].extend(trunc_logs)
        if trunc_logs:
            await asave_proposal_draft(draft)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Truncation repair skipped: %s", exc)
        report["logs"].append(f"Truncation repair skipped: {exc}")

    try:
        from app.services.proposal_manuscript import collapse_empty_subheadings

        gap_logs: list[str] = []
        sections = list(draft.sections)
        changed = False
        for idx, section in enumerate(sections):
            before = section.content or ""
            if not before.strip():
                continue
            after = collapse_empty_subheadings(before)
            if after != before:
                sections[idx] = section.model_copy(update={"content": after})
                changed = True
                gap_logs.append(
                    f"Removed empty subheading(s) in “{section.title or section.id}” "
                    "(deterministic — no LLM fill)."
                )
        if changed:
            draft = draft.model_copy(
                update={
                    "sections": sections,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        report["logs"].extend(gap_logs)
        if gap_logs:
            await asave_proposal_draft(draft)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Empty subheading collapse skipped: %s", exc)
        report["logs"].append(f"Empty subheading collapse skipped: {exc}")

    if use_llm:
        try:
            from app.services.proposal_fulfill_rfp_budget_kpi import run_fulfill_kpi_scan

            await _scan_progress(
                9,
                "Scan RFP: contractor KPIs + detail",
                "Activity Measure tables & BMP linkages — rewrite, not label swap.",
            )
            await _ensure_not_stopped()
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
        except ProposalGenerationCancelled:
            raise
        except FulfillStepSkip as skip:
            _log_resume_skip(skip.step)
        except Exception as exc:  # noqa: BLE001
            logger.warning("KPI scan skipped: %s", exc)
            report["logs"].append(f"KPI scan skipped: {exc}")
    else:
        try:
            from app.services.proposal_fulfill_rfp_budget_kpi import run_fulfill_kpi_scan

            await _scan_progress(10, "Scan RFP: contractor KPIs", "Deterministic KPI alignment (no LLM).")
            await _ensure_not_stopped()
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
        except ProposalGenerationCancelled:
            raise
        except FulfillStepSkip as skip:
            _log_resume_skip(skip.step)
        except Exception as exc:  # noqa: BLE001
            report["logs"].append(f"KPI deterministic scan skipped: {exc}")

    try:
        from app.services.proposal_fulfill_rfp_budget_kpi import summarize_budget_kpi_findings

        report["budgetKpiSummary"] = summarize_budget_kpi_findings(draft, rfp_text, research)
    except Exception:  # noqa: BLE001
        report["budgetKpiSummary"] = []

    report["logs"].append(
        "Scan RFP walks full PDF text, submission checklist, RFP-scored section structure "
        "(Exhibit A / criteria), budget reconcile/sync, contractor KPI alignment, "
        "removes [VERIFY]/[MANUAL FILL] unless RFP-critical, and line-grounds each "
        "section against the KB (async queries) — never invents; "
        "team bios and case studies are not rewritten from scratch."
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

    # Cross-reference open tags against the manuscript itself BEFORE spending
    # an external KB query — a fact three sections away is free, faster, and
    # more authoritative than Supermemory: it is what THIS proposal already
    # told the evaluator. Observed live: "1.4 — Certifications" states WBENC/
    # WOSB are current through a specific date; a different section re-asked
    # the same question as an open [VERIFY: ...] tag anyway. Same step number
    # as KB fact-check below — this is a cheaper first pass over the same
    # gap, not a new pipeline stage, so no checkpoint/resume renumbering risk.
    try:
        await _ensure_not_stopped()
        from app.services.proposal_cross_reference_resolver import (
            resolve_tags_from_manuscript,
        )

        draft, xref_applied = await resolve_tags_from_manuscript(draft)
        if xref_applied:
            report["logs"].append(
                f"Cross-reference: resolved {len(xref_applied)} tag(s) from "
                "facts already stated elsewhere in this manuscript"
            )
            for line in xref_applied[:20]:
                report["logs"].append(f"Cross-reference: {line}")
            await asave_proposal_draft(draft)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cross-reference tag resolution during Scan RFP skipped: %s", exc)
        report["logs"].append(f"Cross-reference resolution skipped: {exc}")

    # Did KB fact-check (step 11) actually change the manuscript? Step 2 already
    # ran the full fact-repair pass; the post-fact-check re-run below only has
    # something new to do when fact-check itself rewrote/repaired a section.
    # Default True so any error path stays conservative (still runs repairs).
    fact_check_changed_manuscript = True
    try:
        await _scan_progress(
            11,
            "Scan RFP: KB fact-check",
            "Requirements → RFP excerpt → Supermemory per section (smart rewrite when needed).",
        )
        await _ensure_not_stopped()
        from app.services.proposal_kb_fact_checker import run_kb_fact_check_pass

        if research is None:
            research = await aget_research_cache(rfp_id)
        logger.info(
            "Scan RFP step 8/10 — KB fact-check starting for %s (%d sections)",
            rfp_id,
            len(draft.sections),
        )
        draft, fc_report = await run_kb_fact_check_pass(
            draft,
            rfp=rfp,
            rfp_context=rfp_text,
            research=research,
        )
        fact_check_changed_manuscript = bool(
            fc_report.requirement_repairs
            or fc_report.verify_tags_filled
            or fc_report.stubs_repaired
            or fc_report.eval_repairs
            or fc_report.duplicates_removed
        )
        if fc_report.logs:
            report["kbFactCheck"] = {
                "sectionsChecked": fc_report.sections_checked,
                "requirementRepairs": fc_report.requirement_repairs,
                "verifyTagsFilled": fc_report.verify_tags_filled,
                "stubsRepaired": fc_report.stubs_repaired,
                "evalRepairs": fc_report.eval_repairs,
                "duplicatesRemoved": fc_report.duplicates_removed,
                "metricFlags": fc_report.metric_flags,
                "logs": fc_report.logs,
            }
            for line in fc_report.logs[:20]:
                report["logs"].append(f"KB fact-check: {line}")
            await asave_proposal_draft(draft)
        elif fc_report.sections_checked:
            await asave_proposal_draft(draft)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("KB fact-check during Scan RFP skipped: %s", exc)
        report["logs"].append(f"KB fact-check skipped: {exc}")

    if not fact_check_changed_manuscript:
        # Step 2 already ran the full fact-repair pass and KB fact-check changed
        # nothing since — re-running the heavy pass (per-bio KB searches, hollow
        # fill, compliance-fabrication) would repeat identical work. Skip it.
        logger.info(
            "Scan RFP %s: KB fact-check made no manuscript changes — "
            "skipping the redundant post-fact-check repair pass.",
            rfp_id,
        )
        report["logs"].append(
            "post-fact-check: skipped (KB fact-check changed nothing to repair)"
        )
    else:
        try:
            from app.services.proposal_scan_fact_repairs import run_scan_fact_repairs

            draft, post_fc_logs = await run_scan_fact_repairs(
                draft,
                research=research,
                rfp_text=rfp_text,
                rfp_title=rfp.title or "",
                rfp_client=rfp.client or "",
                rfp_sector=getattr(rfp, "sector", None) or "",
                rfp_id=rfp_id,
            )
            for line in post_fc_logs:
                if line.startswith("HUMAN_GAP:"):
                    gap = line.split("HUMAN_GAP:", 1)[1].strip()
                    if gap and gap not in report["humanDecisionGaps"]:
                        report["humanDecisionGaps"].append(gap)
            if post_fc_logs:
                report["logs"].extend(f"post-fact-check: {line}" for line in post_fc_logs[:16])
                await asave_proposal_draft(draft)
        except ProposalGenerationCancelled:
            raise
        except FulfillStepSkip as skip:
            _log_resume_skip(skip.step)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Post fact-check repairs skipped: %s", exc)
            report["logs"].append(f"Post fact-check repairs skipped: {exc}")

    # Fact-check can rewrite Approach/Schedule — run the SAME blocker suite
    # Generate-from-scratch uses (titles, consistency, certs, signed PDF note,
    # LLM manuscript-vs-RFP contradictions).
    try:
        await _scan_progress(
            12,
            "Scan RFP: Contradiction check",
            "LLM manuscript vs verified company facts + vs RFP requirements.",
        )
        await _ensure_not_stopped()
        from app.services.proposal_blocker_prevention import (
            apply_feedback_blocker_suite,
        )

        suite = await apply_feedback_blocker_suite(
            draft,
            rfp=rfp,
            research=research,
            rfp_text=rfp_text,
            use_llm_contradiction=use_llm,
            skip_section_ids=preserved_ids,
        )
        draft = suite.draft
        report["logs"].extend(suite.logs)
        report["consistencyFixesApplied"] = len(
            [
                line
                for line in suite.logs
                if not line.startswith("RFP contradiction")
                and "skipped" not in line.casefold()
            ]
        )
        report["consistencyFixSummaries"] = suite.logs[:12]
        report["rfpContradictionCount"] = suite.contradiction_count
        report["rfpContradictionRewrites"] = suite.contradiction_rewrites
        report["rfpContradictionUnresolved"] = suite.contradiction_unresolved
        report["factContradictionCount"] = suite.fact_contradiction_count
        report["factContradictionRewrites"] = suite.fact_contradiction_rewrites
        report["factContradictionUnresolved"] = suite.fact_contradiction_unresolved
        for line in suite.fact_contradiction_unresolved_titles[:8]:
            gap = f"Unresolved fact contradiction — {line}"
            if gap not in report["humanDecisionGaps"]:
                report["humanDecisionGaps"].append(gap)
        report["rfpContradictionTitles"] = suite.contradiction_unresolved_titles or [
            line for line in suite.logs if "FIXED contradiction" in line
        ][:8]
        # Only UNRESOLVED contradictions stay as DQ — fixed ones are done
        if suite.contradiction_unresolved_titles:
            report["disqualificationRisks"] = [
                risk
                for risk in (report.get("disqualificationRisks") or [])
                if "manuscript contradicts rfp" not in risk.casefold()
                and "fact contradiction" not in risk.casefold()
            ] + [
                f"Unresolved contradiction — {line}"
                for line in suite.contradiction_unresolved_titles[:6]
            ]
            seen_r: set[str] = set()
            uniq_r: list[str] = []
            for risk in report["disqualificationRisks"]:
                key = risk.casefold()
                if key in seen_r:
                    continue
                seen_r.add(key)
                uniq_r.append(risk)
            report["disqualificationRisks"] = uniq_r
            report["disqualificationRiskCount"] = len(uniq_r)
        else:
            # Clear prior manuscript-contradiction DQ noise after successful fixes
            report["disqualificationRisks"] = [
                risk
                for risk in (report.get("disqualificationRisks") or [])
                if "manuscript contradicts rfp" not in risk.casefold()
                and "missing required attachment" not in risk.casefold()
            ]
            report["disqualificationRiskCount"] = len(report["disqualificationRisks"])
        if suite.logs:
            await asave_proposal_draft(draft)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("RFP blocker suite / contradiction scan skipped: %s", exc)
        report["logs"].append(f"RFP blocker suite skipped: {exc}")

    # Async per-section KB line grounding — confirm claims in DB; never invent.
    try:
        await _scan_progress(
            13,
            "Scan RFP: line-by-line KB grounding",
            "Agent plans queries per section (async) — remove ungrounded claims; no fabrication.",
        )
        await _ensure_not_stopped()
        from app.services.proposal_scan_line_grounding import (
            run_scan_line_grounding_pass,
        )

        draft, ground_report = await run_scan_line_grounding_pass(
            draft,
            rfp=rfp,
            rfp_text=rfp_text,
            research=research,
        )
        report["lineGrounding"] = {
            "sectionsChecked": ground_report.sections_checked,
            "sectionsChanged": ground_report.sections_changed,
            "queriesRun": ground_report.queries_run,
            "logs": ground_report.logs[:40],
        }
        if ground_report.logs:
            for line in ground_report.logs[:20]:
                report["logs"].append(f"Line ground: {line}")
            await asave_proposal_draft(draft)
        else:
            report["logs"].append(
                f"Line ground: checked {ground_report.sections_checked} section(s); "
                f"{ground_report.queries_run} KB queries."
            )
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Line grounding during Scan RFP skipped: %s", exc)
        report["logs"].append(f"Line grounding skipped: {exc}")

    try:
        from app.services.evidence_trust.load_client_list import load_client_list_registry
        from app.services.proposal_fulfill_fabrication_guard import (
            repair_fabricated_qualifications_async,
        )
        from app.services.proposal_rfp_optional_claim_scrub import (
            apply_optional_claim_scrub_to_draft,
        )

        # Warm ClientList cache, then correct unsupported claims BEFORE stripping flags.
        client_registry = await load_client_list_registry()
        draft, fab_again, _fab_h = await repair_fabricated_qualifications_async(
            draft, research
        )
        if fab_again:
            report["logs"].extend(f"claim-correct: {line}" for line in fab_again[:12])
        draft, claim_logs = apply_optional_claim_scrub_to_draft(
            draft, rfp_text=rfp_text, registry=client_registry
        )
        if claim_logs:
            report["logs"].extend(f"optional-claim scrub: {line}" for line in claim_logs[:20])
            await asave_proposal_draft(draft)
        elif fab_again:
            await asave_proposal_draft(draft)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Optional claim scrub during Scan RFP skipped: %s", exc)
        report["logs"].append(f"Optional claim scrub skipped: {exc}")

    # Dedicated pass: every section with [VERIFY]/[MANUAL FILL] → RFP scan →
    # remove unless critically required. Never invents.
    try:
        await _scan_progress(
            14,
            "Scan RFP: remove optional VERIFY/MANUAL FILL",
            "Drop placeholders unless RFP-critical; fill from KB verbatim only — never invent.",
        )
        await _ensure_not_stopped()
        from app.services.proposal_verify_optional_scrub import (
            count_placeholder_tags,
            count_verify_tags,
            count_manual_fill_tags,
            scrub_draft_optional_verify_tags,
        )

        placeholder_sections = [
            s.id
            for s in draft.sections
            if count_placeholder_tags(s.content or "") > 0
        ]
        if placeholder_sections:
            before_v = {
                s.id: count_verify_tags(s.content or "") for s in draft.sections
            }
            before_m = {
                s.id: count_manual_fill_tags(s.content or "") for s in draft.sections
            }
            scrubbed, scrub_logs = await scrub_draft_optional_verify_tags(
                list(draft.sections),
                rfp_text=rfp_text,
                section_filter_ids=set(placeholder_sections),
            )
            after_v = {
                s.id: count_verify_tags(s.content or "") for s in scrubbed
            }
            after_m = {
                s.id: count_manual_fill_tags(s.content or "") for s in scrubbed
            }
            removed_v = sum(
                max(0, before_v.get(i, 0) - after_v.get(i, 0)) for i in before_v
            )
            removed_m = sum(
                max(0, before_m.get(i, 0) - after_m.get(i, 0)) for i in before_m
            )
            kept = sum(after_v.values()) + sum(after_m.values())
            report["verifyTagsRemoved"] = removed_v
            report["manualFillTagsRemoved"] = removed_m
            report["verifyTagsKept"] = kept
            before_map = {s.id: (s.content or "") for s in draft.sections}
            changed = any(
                before_map.get(s.id, "") != (s.content or "") for s in scrubbed
            )
            if scrub_logs:
                report["verifyScrub"] = {
                    "sectionsScanned": len(placeholder_sections),
                    "verifyTagsRemoved": removed_v,
                    "manualFillTagsRemoved": removed_m,
                    "verifyTagsKept": kept,
                    "logs": scrub_logs,
                }
                for line in scrub_logs[:25]:
                    report["logs"].append(f"Placeholder scrub: {line}")
            if changed:
                draft = draft.model_copy(
                    update={
                        "sections": scrubbed,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                await asave_proposal_draft(draft)
            else:
                report["logs"].append(
                    f"Placeholder scrub: scanned {len(placeholder_sections)} section(s); "
                    "no optional tags removed (kept only if RFP-critical)."
                )
        else:
            report["verifyTagsRemoved"] = 0
            report["manualFillTagsRemoved"] = 0
            report["verifyTagsKept"] = 0
            report["logs"].append("Placeholder scrub: no [VERIFY]/[MANUAL FILL] tags found.")
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Optional placeholder scrub during Scan RFP skipped: %s", exc)
        report["logs"].append(f"Placeholder scrub skipped: {exc}")

    # Orphan VERIFY tails (no opening [VERIFY) are invisible to placeholder scrub
    # — always clear them, and stub hollow shells so gibberish never ships.
    try:
        from app.services.proposal_manual_flags import (
            repair_orphan_verify_leftovers_in_draft,
        )

        draft, orphan_logs = repair_orphan_verify_leftovers_in_draft(draft)
        if orphan_logs:
            draft = draft.model_copy(
                update={"updated_at": datetime.now(timezone.utc).isoformat()}
            )
            await asave_proposal_draft(draft)
            report["logs"].extend(orphan_logs[:12])
    except Exception as orphan_exc:  # noqa: BLE001
        logger.warning("Orphan VERIFY leftover repair skipped: %s", orphan_exc)
        report["logs"].append(f"Orphan VERIFY leftover repair skipped: {orphan_exc}")

    # After optional scrub: plant required board-roster VERIFY on campaign /
    # contribution disclosure tabs (buyer board ≠ zö KB — Ella/Rachel confirm).
    # Must run AFTER scrub so the new flag is not stripped in the same pass.
    try:
        from app.services.proposal_chat_improve_pin import (
            insert_all_board_roster_verify_flags,
        )

        draft, board_logs = insert_all_board_roster_verify_flags(draft)
        if board_logs:
            draft = draft.model_copy(
                update={"updated_at": datetime.now(timezone.utc).isoformat()}
            )
            await asave_proposal_draft(draft)
            report["logs"].extend(board_logs[:8])
    except Exception as board_exc:  # noqa: BLE001
        logger.warning("Board-roster VERIFY insert during Scan skipped: %s", board_exc)
        report["logs"].append(f"Board-roster VERIFY insert skipped: {board_exc}")

    try:
        from app.services.proposal_section_dedup import dedupe_manuscript_for_scan

        await _scan_progress(
            15,
            "Scan RFP: Compact manuscript",
            "Final pass — trim leftover restated prose; collapse same-title twin tabs.",
        )
        await _ensure_not_stopped()
        sections, final_logs = dedupe_manuscript_for_scan(
            list(draft.sections),
            drop_clone_tabs=False,
        )
        if final_logs:
            draft = draft.model_copy(
                update={
                    "sections": sections,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            await asave_proposal_draft(draft)
            deleted = [
                log
                for log in final_logs
                if "near-duplicate" in log
                or "content overlap" in log
                or "contained restatement" in log
                or "removed —" in log
                or "heavy overlap" in log
                or "content clone" in log
                or "report twin" in log
            ]
            prev = int(report.get("duplicateSectionsRemoved") or 0)
            report["duplicateSectionsRemoved"] = prev + len(deleted)
            titles = list(report.get("duplicateSectionsRemovedTitles") or [])
            titles.extend(p.split(" (", 1)[0] for p in deleted[:12])
            report["duplicateSectionsRemovedTitles"] = titles[:20]
            report["logs"].append(
                f"Final compact: {len(final_logs)} action(s): "
                + "; ".join(final_logs[:10])
            )
        # Hard guarantee: Compact must never leave the proposal without Budget.
        from app.services.proposal_budget_content import ensure_budget_section_present

        sections, budget_restored = ensure_budget_section_present(
            list(draft.sections),
            research.budget if research else None,
            rfp_text=rfp_text,
        )
        if budget_restored:
            draft = draft.model_copy(
                update={
                    "sections": sections,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            await asave_proposal_draft(draft)
            report["logs"].append(
                "Budget: restored Budget & Pricing after compact (dedupe must never drop fees)."
            )
            report["budgetChanged"] = True
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scan RFP final compact skipped: %s", exc)
        report["logs"].append(f"Final compact skipped: {exc}")

    try:
        from app.services.proposal_ralph import apply_ralph_to_draft

        await _scan_progress(
            16,
            "Scan RFP: Page limit & anti-invention",
            "If THIS RFP states a page limit, hard-fit without cutting identity/budget/scored floors; scrub invented diagrams.",
        )
        await _ensure_not_stopped()
        draft, ralph_logs = apply_ralph_to_draft(
            draft,
            page_limit=rfp.page_limit,
            rfp_text=rfp_text,
        )
        if ralph_logs:
            await asave_proposal_draft(draft)
            report["ralphActions"] = len(ralph_logs)
            report["logs"].extend(ralph_logs[:20])
            hard_fit = [x for x in ralph_logs if "page-hard-fit" in x or "page-limit:" in x]
            if hard_fit:
                report["pageLimitEnforced"] = True
                report["pageLimitNotes"] = hard_fit[:6]
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scan RFP Ralph skipped: %s", exc)
        report["logs"].append(f"Ralph page-fit skipped: {exc}")

    try:
        from app.services.proposal_scan_fact_repairs import (
            apply_leaked_fragment_scrub_to_draft,
        )

        draft, leak_logs = apply_leaked_fragment_scrub_to_draft(draft)
        if leak_logs:
            await asave_proposal_draft(draft)
            report["logs"].extend(f"Leak scrub: {line}" for line in leak_logs[:16])
            report["leakedArtifactsRemoved"] = len(leak_logs)
    except ProposalGenerationCancelled:
        raise
    except Exception as extra:  # noqa: BLE001
        logger.warning("Leak scrub before final verify skipped: %s", extra)
        report["logs"].append(f"Leak scrub skipped: {extra}")

    # Quality gate (former step 17) removed — expensive 3-act rewrite loop.
    # Designer-ready verify lives in the final stages: fill missing answers from
    # past won proposals, zero-fabrication, pre-submit, submission readiness.
    report["qualityGate"] = {
        "ran": False,
        "stoppedReason": "removed — final submission readiness verifies for designer",
    }

    await _scan_progress(
        17,
        "Scan RFP: pre-submit refresh",
        "Designer-ready verify — inventory missing answers across the manuscript, "
        "plan fills, query past won proposals, fill only those gaps.",
    )
    await _ensure_not_stopped()

    try:
        from app.services.proposal_capability_bio_grounding import (
            repair_misplaced_bio_stub_sections,
        )
        from app.services.proposal_hollow_kb_fill import fill_hollow_sections_for_pipeline

        async def _hollow_fill_progress(done: int, total: int, title: str) -> None:
            # Sub-step ticks so the UI doesn't look frozen during the sequential
            # per-section LLM fill loop — this used to sit silent for the whole step.
            await record_pipeline_activity(
                rfp_id,
                label="Scan RFP: pre-submit refresh",
                detail=f"Filling missing answers — {done}/{total}: {title}",
                step_index=17,
                step_total=len(FULFILL_STEPS),
                in_progress_phase="fulfill-scan",
            )

        draft, misplaced = repair_misplaced_bio_stub_sections(draft)
        draft, hollow = await fill_hollow_sections_for_pipeline(
            draft,
            rfp_title=rfp.title or "",
            rfp_client=rfp.client or "",
            rfp_sector=getattr(rfp, "sector", None) or "",
            rfp_text=rfp_text,
            rfp_id=rfp_id,
            on_progress=_hollow_fill_progress,
            on_cancel_check=_ensure_not_stopped,
        )
        final_fill = misplaced + hollow
        if final_fill:
            await asave_proposal_draft(draft)
            report["logs"].extend(final_fill[:16])
    except ProposalGenerationCancelled:
        raise
    except Exception as extra:  # noqa: BLE001
        logger.warning("Final designer verify / hollow fill skipped: %s", extra)
        report["logs"].append(f"Final designer verify skipped: {extra}")

    # Final zero-fabrication pass — keep claims grounded before ending report.
    try:
        from app.services.proposal_chat_improve_pin import (
            fill_all_active_client_lists_from_siblings,
            insert_all_board_roster_verify_flags,
        )
        from app.services.proposal_cross_reference_resolver import (
            resolve_tags_from_manuscript,
        )
        from app.services.proposal_manual_flags import (
            repair_orphan_verify_leftovers_in_draft,
        )
        from app.services.proposal_manuscript import (
            apply_designer_ready_markup_polish_to_draft,
        )
        from app.services.proposal_pointer_page_integrity import (
            apply_pointer_page_integrity_to_draft,
        )

        # Safety re-run: compact / Ralph / optional scrub must not leave I.2 missing
        # or strip board-roster VERIFY from contribution disclosure forms.
        draft, form_logs = fill_all_active_client_lists_from_siblings(draft)
        if form_logs:
            report["logs"].extend(form_logs[:8])
        # Second cross-ref pass — hollow fill / scrub may leave tags answered elsewhere.
        draft, xref_logs = await resolve_tags_from_manuscript(draft)
        if xref_logs:
            report["logs"].extend(xref_logs[:8])
            report["logs"].append(
                f"Scan-final cross-reference resolve: {len(xref_logs)} tag(s)"
            )
        draft, orphan_logs = repair_orphan_verify_leftovers_in_draft(draft)
        if orphan_logs:
            report["logs"].extend(orphan_logs[:8])
        draft, ptr_logs = apply_pointer_page_integrity_to_draft(draft)
        if ptr_logs:
            report["logs"].extend(ptr_logs[:12])
        draft, board_logs = insert_all_board_roster_verify_flags(draft)
        if board_logs:
            report["logs"].extend(board_logs[:8])
        draft, polish_logs = apply_designer_ready_markup_polish_to_draft(draft)
        if polish_logs:
            report["logs"].extend(polish_logs[:8])
    except Exception as form_exc:  # noqa: BLE001
        logger.warning("Pre-final form-slot fill skipped: %s", form_exc)

    try:
        from app.services.proposal_zero_fabrication import (
            apply_zero_fabrication_guards_before_persist,
        )

        draft, zf_final = await apply_zero_fabrication_guards_before_persist(
            draft,
            research=research,
            rfp_text=rfp_text,
            label="scan-final",
        )
        if zf_final.logs:
            await asave_proposal_draft(draft)
            report["logs"].extend(zf_final.logs[:20])
            report["logs"].append(
                f"Final zero-fabrication pass: {len(zf_final.logs)} guard action(s)"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scan final zero-fabrication pass skipped: %s", exc)
        report["logs"].append(f"Final zero-fabrication pass skipped: {exc}")

    # Post-ZF polish — forms integrity / scrub must not leave orphan VERIFY tails
    # or prose designer-note labels.
    try:
        from app.services.proposal_manual_flags import (
            repair_orphan_verify_leftovers_in_draft,
        )
        from app.services.proposal_manuscript import (
            apply_designer_ready_markup_polish_to_draft,
        )

        draft, orphan_logs = repair_orphan_verify_leftovers_in_draft(draft)
        if orphan_logs:
            report["logs"].extend(orphan_logs[:6])
        draft, polish_logs = apply_designer_ready_markup_polish_to_draft(draft)
        if polish_logs:
            report["logs"].extend(polish_logs[:6])
    except Exception as polish_exc:  # noqa: BLE001
        logger.warning("Post-ZF designer polish skipped: %s", polish_exc)

    review = run_presubmit_review_with_manual_flags(
        rfp=rfp, draft=draft, research=research, finalized=False
    )

    # Final stage — triage flags + readiness for designer handoff.
    await _scan_progress(
        18,
        "Scan RFP: submission readiness",
        "Triage manual flags against the RFP, score what is still open, "
        "and confirm the draft is accurate for designer.",
    )
    gate_report = None
    try:
        await _ensure_not_stopped()
        from app.services.proposal_manual_fill_triage import triage_manual_fill_flags
        from app.services.proposal_readiness import CriterionScore, compute_readiness

        triaged = await triage_manual_fill_flags(
            flags=list(review.manual_fill_flags or []),
            rfp_text=rfp_text,
            rfp_client=rfp.client or "",
            rfp_title=rfp.title or "",
        )
        review = review.model_copy(update={"manual_fill_flags": triaged})

        criterion_scores = [
            CriterionScore(
                section_id=v.section_id,
                criterion=v.criterion,
                score=v.score,
                weight=v.weight,
            )
            for v in (gate_report.scorecard if gate_report else [])
        ]
        unresolved_claims = (
            len(gate_report.unresolved_claims) if gate_report else 0
        )
        readiness = compute_readiness(
            scores=criterion_scores, flags=triaged, unresolved=unresolved_claims
        )
        report["readiness"] = {
            "score": readiness.score,
            "measured": readiness.measured,
            "confidence": readiness.confidence,
            "confidenceNote": readiness.confidence_note,
            "verdict": readiness.verdict,
            "ready": readiness.ready,
            "openDisqualifying": readiness.open_disqualifying,
            "openScored": readiness.open_scored,
        }
        report["logs"].append(
            f"Readiness: {readiness.verdict} "
            f"(disqualifying={readiness.open_disqualifying}, "
            f"scored={readiness.open_scored})"
        )
        report["readinessReport"] = {
            "rfpTitle": rfp.title or "",
            "scorecard": [],
            "changes": [],
            "unverifiedClaims": [],
            "unfixed": [],
        }
        for flag in triaged:
            if flag.criticality != "disqualifying":
                continue
            gap = f"BLOCKER — {flag.tag} ({flag.section_title or flag.section_id})"
            if gap not in report["humanDecisionGaps"]:
                report["humanDecisionGaps"].append(gap)
    except ProposalGenerationCancelled:
        raise
    except FulfillStepSkip as skip:
        _log_resume_skip(skip.step)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scan RFP submission readiness skipped: %s", exc)
        report["logs"].append(f"Submission readiness skipped: {exc}")
        report["readiness"] = {"measured": False, "verdict": "Readiness stage did not run."}

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
                "Fabrication guard",
                "Truncation repair",
                "KB fact-check",
                "Smart fact-check",
                "VERIFY scrub",
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
    from app.services.proposal_draft_snapshots import (
        attach_scan_summary_to_latest_before_scan,
        prior_sections_for_restore,
    )
    from app.services.proposal_draft_structure_stubs import (
        restore_sections_emptied_by_scan,
    )

    # HARD INVARIANT: Complete & Clean must never save a section it degraded —
    # emptied, reduced to an RFP-outline stub, or overwritten with a team-bio
    # stub. Restore from the in-memory pre-scan baseline first (the truest "good"
    # copy for this run); fall back to snapshot content for anything the baseline
    # cannot cover (e.g. a section that only exists post-damage on resume).
    restore_candidates = [*pre_scan_sections, *prior_sections_for_restore(draft)]
    draft, restore_logs = restore_sections_emptied_by_scan(draft, restore_candidates)
    if restore_logs:
        for line in restore_logs:
            logger.warning("Scan RFP %s: %s", rfp_id, line)
        report.setdefault("logs", []).extend(restore_logs)

    # HARD INVARIANT: restore must not reintroduce wrong § pointers, EDITOR NOTES
    # work tickets, or orphan VERIFY leftovers — re-run integrity after restore.
    try:
        from app.services.proposal_manual_flags import (
            repair_orphan_verify_leftovers_in_draft,
        )
        from app.services.proposal_manuscript import (
            apply_designer_ready_markup_polish_to_draft,
        )
        from app.services.proposal_pointer_page_integrity import (
            apply_pointer_page_integrity_to_draft,
        )

        draft, orphan_logs = repair_orphan_verify_leftovers_in_draft(draft)
        if orphan_logs:
            report.setdefault("logs", []).extend(orphan_logs[:6])
        draft, ptr_logs = apply_pointer_page_integrity_to_draft(draft)
        if ptr_logs:
            report.setdefault("logs", []).extend(ptr_logs[:10])
            report["logs"].append(
                f"Post-restore pointer integrity: {len(ptr_logs)} action(s)"
            )
        draft, polish_logs = apply_designer_ready_markup_polish_to_draft(draft)
        if polish_logs:
            report.setdefault("logs", []).extend(polish_logs[:6])
    except Exception as restore_fix_exc:  # noqa: BLE001
        logger.warning(
            "Post-restore integrity polish skipped for %s: %s",
            rfp_id,
            restore_fix_exc,
        )

    draft = attach_scan_summary_to_latest_before_scan(draft, report)
    await asave_proposal_draft(draft)
    await asave_research_cache(updated_research)
    final_scan_hash = compute_fulfill_scan_hash(draft, rfp_text)
    await complete_fulfill_scan(rfp_id, scan_hash=final_scan_hash)

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
