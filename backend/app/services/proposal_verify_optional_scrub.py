"""RFP-aware scrub of optional [VERIFY] tags — drop if not required; never invent."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.services import llm
from app.services.proposal_manual_flags import VERIFY_TAG_RE
from app.services.proposal_rfp_excerpt import build_priority_rfp_excerpt
from app.services.proposal_section_health import is_dead_section

logger = logging.getLogger(__name__)

_SCRUB_VERIFY_ASK_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"(?:remove|delete|strip|scrub|drop|purge|kill|"
    r"get\s+rid\s+of|take\s+out|cut\s+out)"
    r".{0,60}"
    r"(?:\[?\s*VERIFY|verify\s+tags?|verify\s+placeholders?|verify\s+gaps?)"
    r"|"
    r"(?:clean|clear)\s+(?:out\s+|up\s+)?"
    r"(?:the\s+|all\s+|any\s+)?"
    r"(?:\[?\s*VERIFY|verify\s+tags?|verify\s+placeholders?)"
    r"|"
    r"(?:optional|unnecessary|not\s+(?:needed|necessary|required))"
    r".{0,40}"
    r"(?:\[?\s*VERIFY|verify\s+tags?)"
    r"|"
    r"(?:\[?\s*VERIFY|verify\s+tags?).{0,40}"
    r"(?:not\s+(?:needed|necessary|required)|if\s+optional)"
    r")",
)

_FILL_VERIFY_ASK_RE = re.compile(
    r"(?is)"
    r"(?:fill|resolve|complete|replace).{0,80}"
    r"(?:\[?\s*VERIFY|verify\s+tags?|gaps?|placeholders?)",
)


@dataclass
class VerifyOptionalScrubResult:
    content: str
    tags_before: int
    tags_after: int
    removed: int
    kept_required: int
    changed: bool
    note: str = ""


def count_verify_tags(text: str) -> int:
    return len(VERIFY_TAG_RE.findall(text or "")) + len(
        re.findall(r"\[VERIFY\]", text or "", flags=re.I)
    )


def user_asks_scrub_optional_verify(user_message: str) -> bool:
    """True when the user wants optional/unneeded [VERIFY] tags removed (not KB-filled)."""
    raw = (user_message or "").strip()
    if not raw:
        return False
    if _SCRUB_VERIFY_ASK_RE.search(raw):
        # Explicit fill wins when both appear ("fill then remove leftovers" → fill first).
        if _FILL_VERIFY_ASK_RE.search(raw) and not re.search(
            r"(?i)\b(?:remove|strip|delete|scrub|drop)\b", raw
        ):
            return False
        return True
    return False


def _extract_json_content(raw: dict | list | str | None) -> tuple[str, str, int]:
    if not isinstance(raw, dict):
        return "", "", 0
    content = raw.get("content") or raw.get("updatedContent") or ""
    note = raw.get("note") or raw.get("summary") or ""
    kept = raw.get("keptRequiredCount")
    try:
        kept_n = int(kept) if kept is not None else 0
    except (TypeError, ValueError):
        kept_n = 0
    if not isinstance(content, str):
        content = str(content or "")
    if not isinstance(note, str):
        note = str(note or "")
    return content.strip(), note.strip(), max(0, kept_n)


async def scrub_optional_verify_tags(
    content: str,
    *,
    section_title: str,
    rfp_text: str,
    force: bool = False,
) -> VerifyOptionalScrubResult:
    """Rewrite section: drop [VERIFY] tags the RFP does not require; never invent facts.

    When force=False and there are no VERIFY tags, returns unchanged.
    """
    body = content or ""
    before = count_verify_tags(body)
    if before <= 0 and not force:
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=0,
            tags_after=0,
            removed=0,
            kept_required=0,
            changed=False,
            note="No [VERIFY] tags to scrub.",
        )

    if not llm.is_configured():
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=before,
            tags_after=before,
            removed=0,
            kept_required=before,
            changed=False,
            note="LLM not configured — left [VERIFY] tags unchanged.",
        )

    rfp_excerpt = build_priority_rfp_excerpt(rfp_text or "", max_chars=18_000)

    # Before giving up on a tag, check whether the KB actually has the real answer —
    # a genuine KB-sourced fact beats both a bracket placeholder AND a vague generic
    # rewrite. Bounded to a short, single retrieval per section (asks are batched into
    # one question) to keep this cheap: this only fires on sections that already have
    # [VERIFY] tags, and only once per section, not per tag.
    kb_context = ""
    asks = [a.strip() for a in VERIFY_TAG_RE.findall(body) if a.strip()]
    if asks:
        try:
            from app.services.kb_rag_retrieve import retrieve_for_question

            question = f"{section_title}: " + "; ".join(asks[:6])
            kb_context, _labels, _queries = await retrieve_for_question(
                question, limit=4, max_chars=4_000
            )
        except Exception:
            logger.warning("KB lookup for VERIFY scrub failed on %s", section_title, exc_info=True)
            kb_context = ""

    system = (
        "You scrub proposal manuscript [VERIFY: …] placeholders using the RFP and the KB.\n"
        "BIAS: Prefer REMOVING tags, but a REAL fact beats a removal — never settle for vague "
        "prose when the KB evidence below already answers the tag.\n"
        "RULES:\n"
        "1. FIRST — check the KB EVIDENCE below. If it contains the exact fact a [VERIFY] tag "
        "asks for (a name, number, cert, contact, partner, etc.), REPLACE the tag with that real "
        "fact, verbatim from the evidence. This is the preferred outcome: it lowers the VERIFY "
        "count with a correct answer instead of a placeholder or a vague generic line.\n"
        "2. IF KB evidence does NOT answer it — REMOVE the tag and rewrite the sentence/row/cell "
        "so the section still reads cleanly. Drop optional name/contact/backup columns; say work "
        "is in-house or with vetted partners when names are unknown — WITHOUT inventing.\n"
        "3. KEEP a short [VERIFY: brief field] ONLY when the RFP EXPLICITLY mandates that "
        "exact fact for compliance or scoring (e.g. required named references + phone, "
        "FEIN, insurance dollar limits, required legal attestation) AND neither the KB "
        "evidence nor the RFP already supplies it. If unsure whether it is required → REMOVE.\n"
        "4. NEVER invent facts — no names, phones, emails, rates, certs, clients, or wins. "
        "Only use a fact if it is verbatim in the KB evidence or RFP excerpts below.\n"
        "5. Never leave empty brackets like [] or bare [VERIFY].\n"
        "6. Preserve useful tables/structure; only change what VERIFY tags force.\n"
        "7. Return JSON only."
    )
    user = (
        f"Section title: {section_title}\n\n"
        f"RFP excerpts (source of truth for what is required):\n"
        f"{rfp_excerpt or '(no RFP text provided — treat unknown-named optional details as removable)'}\n\n"
        f"KB evidence (use ONLY if it genuinely answers a [VERIFY] tag below; ignore otherwise):\n"
        f"{kb_context or '(no relevant KB evidence found)'}\n\n"
        f"Current section body:\n{body}\n\n"
        "Return JSON:\n"
        "{\n"
        '  "content": "full updated section markdown",\n'
        '  "keptRequiredCount": <int how many [VERIFY] tags you intentionally kept>,\n'
        '  "note": "one short sentence: what you filled from KB vs removed vs kept"\n'
        "}"
    )
    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=8_000,
            temperature=0.15,
            tier="light",
        )
    except Exception:
        logger.exception("Optional VERIFY scrub LLM failed for %s", section_title)
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=before,
            tags_after=before,
            removed=0,
            kept_required=before,
            changed=False,
            note="Scrub failed — left [VERIFY] tags unchanged.",
        )

    updated, note, kept_n = _extract_json_content(raw)
    if not updated:
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=before,
            tags_after=before,
            removed=0,
            kept_required=before,
            changed=False,
            note="Scrub returned empty content — left original.",
        )

    # Guard: do not accept a near-empty wipe. Applies at every length — the old
    # `len(body) > 400` precondition meant short bodies had no guard at all, so a
    # ~60-character failure stub could be scrubbed away entirely, leaving an empty
    # section and destroying the marker chat needs to rebuild it.
    if len(updated) < max(24, int(len(body) * 0.25)):
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=before,
            tags_after=before,
            removed=0,
            kept_required=before,
            changed=False,
            note="Scrub rejected — rewrite looked truncated.",
        )

    after = count_verify_tags(updated)
    removed = max(0, before - after)
    return VerifyOptionalScrubResult(
        content=updated,
        tags_before=before,
        tags_after=after,
        removed=removed,
        kept_required=kept_n if kept_n else after,
        changed=updated.strip() != body.strip(),
        note=note
        or (
            f"Removed {removed} optional [VERIFY] tag(s); "
            f"kept {after} required."
        ),
    )


async def run_verify_scrub_only_scan(
    rfp_id: str,
) -> tuple[
    "PreSubmitReview",
    "ProposalResearchCache",
    "ProposalDraft",
    dict,
]:
    """Button-only Scan: read sections with [VERIFY], check RFP, remove unless critical.

    Does NOT add closing tabs, structure, budget, or KPI passes — VERIFY scrub only.
    Never invents facts.
    """
    from datetime import datetime, timezone

    from app.models.proposal import ProposalDraft, ProposalResearchCache
    from app.services.go_no_go_service import _assess_rfp_content
    from app.services.proposal_common import ProposalError, aload_rfp_for_proposal
    from app.services.proposal_draft_snapshots import push_proposal_snapshot
    from app.services.proposal_pipeline_checkpoint import record_pipeline_activity
    from app.services.proposal_presubmit_review import (
        run_presubmit_review_with_manual_flags,
    )
    from app.services.proposal_repository import (
        aget_proposal_draft,
        aget_research_cache,
        asave_proposal_draft,
        asave_research_cache,
    )
    from app.services.rfp_content import combine_rfp_text, load_local_rfp_text

    rfp, content, truncated = await aload_rfp_for_proposal(rfp_id)
    _desc, pdf_text, _exists, _missing, _pages, _img = load_local_rfp_text(
        rfp, max_chars=250_000
    )
    rfp_text = combine_rfp_text(
        _desc or (content.description or ""), pdf_text, max_chars=250_000
    )
    if len(rfp_text.strip()) < 200:
        rfp_text = truncated
    if not rfp_text.strip():
        info = _assess_rfp_content(rfp)
        rfp_text = combine_rfp_text(info.description or "", info.pdf_text or "")

    draft = await aget_proposal_draft(rfp_id)
    research = await aget_research_cache(rfp_id)
    if not draft or not any((s.content or "").strip() for s in draft.sections):
        raise ProposalError(
            "No proposal content to scan. Generate the proposal first.",
            status_code=400,
        )

    await record_pipeline_activity(
        rfp_id,
        label="Scan RFP: remove optional [VERIFY]",
        detail="Only pass — sections with [VERIFY] vs full RFP; never invent.",
        step_index=1,
        step_total=1,
        in_progress_phase="fulfill-scan",
    )

    draft = push_proposal_snapshot(draft, label="Before VERIFY scrub")
    await asave_proposal_draft(draft)

    # Task 5's ledger reconciler runs inside THIS path — the real Scan-RFP
    # button always calls mode="verify_scrub_only" (see
    # proposal_fulfill_rfp_gaps.run_fulfill_rfp_gaps), so wiring the
    # reconciler only into the legacy mode="full" body left it unreachable
    # from the UI. It is safe to run unconditionally here: deterministic,
    # zero-LLM, idempotent. ADD (Task 9) is applied, not just surfaced, but
    # only for requirements the matcher found zero matching sections for
    # (len(satisfied_by) == 0) — the same signal that already gated the
    # surfaced-only report — so it cannot create a duplicate of a
    # requirement the matcher already recognized under a different title.
    from app.services.proposal_rfp_compliance import (
        MANUAL_FILL_MARKER,
        draft_added_requirement_sections,
        reconcile_requirement_ledger,
    )

    ledger_result = reconcile_requirement_ledger(
        draft=draft, research=research, rfp=rfp, rfp_text=rfp_text
    )
    if ledger_result.changed:
        draft = ledger_result.draft
        await asave_proposal_draft(draft)

    # Task 10: an ADD stub is a [MANUAL FILL] placeholder, not a clean
    # proposal. Draft real content for exactly the sections THIS pass just
    # added — scoped to applied_additions, so a second Scan-RFP click (whose
    # applied_additions is empty because the section id already exists) never
    # redrafts it. Best-effort: any KB/LLM failure leaves the placeholder
    # exactly as reconcile_requirement_ledger produced it.
    ledger_draft_logs: list[str] = []
    if ledger_result.applied_additions:
        draft, ledger_draft_logs = await draft_added_requirement_sections(
            draft=draft,
            applied_additions=ledger_result.applied_additions,
            research=research,
            rfp=rfp,
            rfp_context=rfp_text,
        )
        if ledger_draft_logs:
            await asave_proposal_draft(draft)

    verify_ids = {
        s.id for s in draft.sections if count_verify_tags(s.content or "") > 0
    }
    report: dict = {
        "mode": "verify_scrub_only",
        "sectionsScanned": len(verify_ids),
        "verifyTagsRemoved": 0,
        "verifyTagsKept": 0,
        "logs": [],
        "closingDetected": [],
        "closingAdded": [],
        "closingAddedSections": [],
        "humanDecisionGaps": [],
        "inPlaceFixCount": 0,
        "ledgerMergesApplied": len(ledger_result.applied_merges),
        "ledgerCutsApplied": len(ledger_result.applied_cuts),
        "ledgerAdditionsApplied": len(ledger_result.applied_additions),
        # Titles, not just counts — the UI banner needs to say WHICH section
        # was added/merged/trimmed, not just how many.
        "ledgerAdditionsSectionTitles": [
            a.section_title for a in ledger_result.applied_additions
        ],
        "ledgerMergesSectionTitles": sorted(
            {m.owner_section_title for m in ledger_result.applied_merges}
        ),
        "ledgerCutsSectionTitles": [c.section_title for c in ledger_result.applied_cuts],
    }
    report["logs"].extend(ledger_result.logs)
    report["logs"].extend(ledger_draft_logs)
    if ledger_result.applied_additions:
        added_by_id = {a.section_id: a for a in ledger_result.applied_additions}
        still_stub = [
            a
            for s in draft.sections
            if (a := added_by_id.get(s.id)) is not None
            and MANUAL_FILL_MARKER in (s.content or "")
        ]
        if still_stub:
            preview = "; ".join(a.requirement_text[:80] for a in still_stub[:5])
            report["humanDecisionGaps"].append(
                f"ledger:add-needs-content — {len(still_stub)} mandatory "
                f"requirement(s) had no matching section and KB drafting could not "
                f"fill them; a [MANUAL FILL] section remains and needs KB search / "
                f"human content: {preview}"
            )

    if not verify_ids:
        report["logs"].append("No [VERIFY] tags found in the manuscript.")
    else:
        scrubbed, scrub_logs = await scrub_draft_optional_verify_tags(
            list(draft.sections),
            rfp_text=rfp_text,
            section_filter_ids=verify_ids,
        )
        report["logs"].extend(scrub_logs)
        before = {s.id: count_verify_tags(s.content or "") for s in draft.sections}
        after = {s.id: count_verify_tags(s.content or "") for s in scrubbed}
        removed = sum(max(0, before.get(i, 0) - after.get(i, 0)) for i in before)
        kept = sum(after.values())
        report["verifyTagsRemoved"] = removed
        report["verifyTagsKept"] = kept
        report["inPlaceFixCount"] = sum(
            1 for line in scrub_logs if "removed" in line.casefold()
        )
        draft = draft.model_copy(
            update={
                "sections": scrubbed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "last_fulfill_report": report,
            }
        )
        await asave_proposal_draft(draft)

    review = run_presubmit_review_with_manual_flags(
        rfp=rfp, draft=draft, research=research, finalized=False
    )

    # Task 11: the presubmit review already runs truncation (T1) and
    # hallucination detection as part of scan_manuscript_consistency /
    # _scan_hallucinations — both were DETECTED and then discarded because
    # nothing counted them into the fulfill report the UI reads. Surfacing
    # only; this does NOT run truncation repair (repair_truncated_manuscript_
    # sections lives only on the mode="full" path, which the Scan-RFP button
    # never calls — see proposal_fulfill_rfp_gaps.run_fulfill_rfp_gaps).
    truncated_section_ids: list[str] = []
    for issue in review.issues:
        if issue.category == "truncation" and issue.section_id:
            if issue.section_id not in truncated_section_ids:
                truncated_section_ids.append(issue.section_id)
    truncated_titles_by_id = {s.id: s.title for s in draft.sections}
    report["truncatedSectionsCount"] = len(truncated_section_ids)
    report["truncatedSectionTitles"] = [
        truncated_titles_by_id.get(sid, sid) for sid in truncated_section_ids
    ]
    report["unverifiedClaimsCount"] = sum(
        1 for issue in review.issues
        if issue.category in ("fabricated_fact", "unverified_claim")
    )

    now = datetime.now(timezone.utc).isoformat()
    updated_research = (
        research or ProposalResearchCache(rfpId=rfp_id, updatedAt=now)
    ).model_copy(update={"presubmit_review": review, "updated_at": now})
    await asave_research_cache(updated_research)
    draft = draft.model_copy(
        update={"last_fulfill_report": report, "updated_at": now}
    )
    await asave_proposal_draft(draft)

    logger.info(
        "scan-rfp:report rfp_id=%s sections_scanned=%s verify_removed=%s "
        "verify_kept=%s ledger_added=%s ledger_added_titles=%s ledger_merged=%s "
        "ledger_cut=%s truncated_sections=%s truncated_titles=%s "
        "unverified_claims=%s",
        rfp_id,
        report.get("sectionsScanned"),
        report.get("verifyTagsRemoved"),
        report.get("verifyTagsKept"),
        report.get("ledgerAdditionsApplied"),
        report.get("ledgerAdditionsSectionTitles"),
        report.get("ledgerMergesApplied"),
        report.get("ledgerCutsApplied"),
        report.get("truncatedSectionsCount"),
        report.get("truncatedSectionTitles"),
        report.get("unverifiedClaimsCount"),
    )
    return review, updated_research, draft, report


async def scrub_draft_optional_verify_tags(
    draft_sections: list,
    *,
    rfp_text: str,
    section_filter_ids: set[str] | None = None,
) -> tuple[list, list[str]]:
    """Scrub optional VERIFYs on draft sections that still have tags. Returns (sections, logs).

    Each section's scrub is an independent, side-effect-free LLM call (no DB writes
    inside the loop), so sections-with-tags are scrubbed concurrently — this is the
    "scan RFP" half of the Senior Editor pass and the main latency cost when a
    manuscript has several flagged sections.
    """
    targets: list[tuple[int, Any, str]] = []
    out: list[Any] = list(draft_sections)
    for idx, section in enumerate(draft_sections):
        sid = getattr(section, "id", "") or ""
        title = getattr(section, "title", "") or sid
        content = getattr(section, "content", "") or ""
        if section_filter_ids is not None and sid not in section_filter_ids:
            continue
        if count_verify_tags(content) <= 0:
            continue
        if is_dead_section(content):
            # The whole body is a failure stub, not prose with optional tags in
            # it. There is nothing to scrub, and removing the stub would destroy
            # the marker chat uses to rebuild the section.
            continue
        targets.append((idx, section, title))

    if not targets:
        return out, []

    results = await asyncio.gather(
        *(
            scrub_optional_verify_tags(
                getattr(section, "content", "") or "",
                section_title=title,
                rfp_text=rfp_text,
            )
            for _idx, section, title in targets
        )
    )

    logs: list[str] = []
    for (idx, section, _title), result in zip(targets, results):
        sid = getattr(section, "id", "") or ""
        if result.changed:
            out[idx] = section.model_copy(update={"content": result.content})
            logs.append(
                f"verify-scrub:{sid}: removed {result.removed}, "
                f"kept {result.tags_after} — {result.note[:120]}"
            )
        elif result.note:
            logs.append(f"verify-scrub:{sid}: {result.note[:120]}")
    return out, logs
