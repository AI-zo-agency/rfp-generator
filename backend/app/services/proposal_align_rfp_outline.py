"""Align to RFP outline — reorder / stub missing tabs to match RFP packet order.

Separate from Improve (one-section wording) and Complete Scan (content quality).
Does not rewrite substantial drafted prose — layout + missing stubs only.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.services.proposal_common import load_rfp_for_proposal
from app.services.proposal_draft_snapshots import push_proposal_snapshot
from app.services.proposal_fulfill_guard import (
    fulfill_scan_preserve_bio_and_case_study_ids,
)
from app.services.proposal_fulfill_rfp_structure import (
    run_rfp_structure_alignment_pass,
)
from app.services.proposal_repository import (
    aget_proposal_draft,
    aget_research_cache,
    asave_proposal_draft,
)

logger = logging.getLogger(__name__)

ALIGN_PHASE = "align-rfp-outline"

# Whole-packet structure asks — not one-tab wording polish.
# Keep this tight; expand via LLM intent later if needed, not synonym tables.
_ALIGN_OUTLINE_ASK_RE = re.compile(
    r"(?is)"
    r"("
    r"\brearrang\w*.{0,100}\b"
    r"(?:proposal|sections?|tabs?|outline|packet|format|order|rfp|solicitation)\b"
    r"|"
    r"\b(?:reorder|re-?order|restructure)\b.{0,80}\b"
    r"(?:proposal|sections?|tabs?|outline|packet|format|order|rfp)\b"
    r"|"
    r"\b(?:put|match|align|follow)\b.{0,60}\b"
    r"(?:(?:the\s+)?(?:rfp|solicitation|submission)\s+)?"
    r"(?:order|outline|format|structure|sequence|packet)\b"
    r"|"
    r"\b(?:rfp|solicitation|submission)\s+(?:order|outline|format|structure|sequence)\b"
    r"|"
    r"\bformat(?:ting)?\s+(?:required|per|to|as|needs)\b.{0,80}\brfp\b"
    r"|"
    r"\balign\s+to\s+(?:the\s+)?rfp\b"
    r"|"
    r"\bmatch\s+(?:the\s+)?rfp\s+(?:outline|structure|order|packet)\b"
    r")"
)


def message_asks_align_rfp_outline(text: str) -> bool:
    """True when the user wants whole-packet RFP order/format — not Improve polish."""
    raw = (text or "").strip()
    if not raw:
        return False
    if len(raw) < 12:
        return False
    return bool(_ALIGN_OUTLINE_ASK_RE.search(raw))


def _outline_titles(draft: ProposalDraft) -> list[str]:
    return [((s.title or "").strip() or s.id) for s in draft.sections]


def build_align_preview(
    *,
    current_titles: list[str],
    proposed_titles: list[str],
    rfp_needed_titles: list[str],
    logs: list[str] | None = None,
) -> dict[str, Any]:
    """Plain-language Align preview for the modal (no LLM)."""
    cur = [t for t in current_titles if t]
    prop = [t for t in proposed_titles if t]
    needed = [t for t in rfp_needed_titles if t]
    cur_set = {t.casefold() for t in cur}
    prop_set = {t.casefold() for t in prop}
    added = [t for t in prop if t.casefold() not in cur_set]
    removed = [t for t in cur if t.casefold() not in prop_set]
    order_changed = cur != prop and not (not added and not removed and cur == prop)
    if cur != prop and not added and not removed:
        order_changed = True

    changes: list[str] = []
    if order_changed and cur != prop:
        changes.append("Reorder the names on the left to match the RFP sequence")
    for title in added[:20]:
        changes.append(f"Add empty slot: “{title}”")
    for title in removed[:12]:
        changes.append(f"Drop from the left list: “{title}” (content kept in Restore backup)")

    if not changes and cur == prop:
        changes.append("Already matches — nothing to change")

    return {
        "currentTitles": cur[:40],
        "proposedTitles": prop[:40],
        "rfpNeededTitles": needed[:40],
        "changes": changes[:40],
        "addedTitles": added[:20],
        "nothingToChange": cur == prop,
        "summary": (
            "Already matches the RFP list"
            if cur == prop
            else f"{len(changes)} change(s) proposed"
        ),
        "logs": list(logs or [])[:20],
    }


def _format_outline_diff(before: list[str], after: list[str]) -> str:
    lines = [
        "**Align to RFP outline** — tabs reordered / missing RFP tabs stubbed. "
        "Drafted prose was not rewritten.",
        "",
        f"**Before ({len(before)} tabs):**",
    ]
    for i, title in enumerate(before[:40], start=1):
        lines.append(f"{i}. {title}")
    if len(before) > 40:
        lines.append(f"… +{len(before) - 40} more")
    lines.append("")
    lines.append(f"**After ({len(after)} tabs):**")
    for i, title in enumerate(after[:40], start=1):
        lines.append(f"{i}. {title}")
    if len(after) > 40:
        lines.append(f"… +{len(after) - 40} more")
    return "\n".join(lines)


async def _dry_run_align_draft(
    *,
    draft: ProposalDraft,
    rfp: Any,
    rfp_text: str,
    research: ProposalResearchCache | None,
) -> tuple[ProposalDraft, list[str], list[str], list[str]]:
    """Run layout pass in memory — does not snapshot or persist sections."""
    skip_ids = fulfill_scan_preserve_bio_and_case_study_ids(draft)
    updated, logs, human = await run_rfp_structure_alignment_pass(
        draft=draft,
        rfp=rfp,
        rfp_text=rfp_text or "",
        research=research,
        skip_section_ids=skip_ids,
        use_llm=False,
        include_missing_submittals=True,
    )
    # Spec titles for “what RFP needs” (best-effort from logs + after titles).
    rfp_needed = _outline_titles(updated)
    return updated, logs, human, rfp_needed


async def preview_align_to_rfp_outline(rfp_id: str) -> dict[str, Any]:
    """Scan-only Align preview — stores proposed draft for Apply (no second pass)."""
    rfp, _desc, rfp_text = load_rfp_for_proposal(rfp_id)
    draft = await aget_proposal_draft(rfp_id)
    if draft is None or not draft.sections:
        raise ValueError("No proposal draft to align — generate a draft first.")

    research = await aget_research_cache(rfp_id)
    before_titles = _outline_titles(draft)
    proposed, logs, human, rfp_needed = await _dry_run_align_draft(
        draft=draft,
        rfp=rfp,
        rfp_text=rfp_text or "",
        research=research,
    )
    after_titles = _outline_titles(proposed)
    preview = build_align_preview(
        current_titles=before_titles,
        proposed_titles=after_titles,
        rfp_needed_titles=rfp_needed,
        logs=logs,
    )
    if human:
        preview["humanDecisionGaps"] = [str(x) for x in human][:20]

    saved_at = datetime.now(timezone.utc).isoformat()
    pending = {
        "preview": preview,
        "proposedDraft": proposed.model_dump(by_alias=True, mode="json"),
        "createdAt": saved_at,
        # Snapshot marker so Apply can detect the draft changed underneath this
        # preview (e.g. a gap-fill pass added sections) and re-run instead of
        # blindly overwriting the current sections with this stale preview.
        "basedOnUpdatedAt": saved_at,
    }
    # Keep current sections; only stash pending preview + proposed layout.
    saved = draft.model_copy(
        update={
            "pending_align_rfp_outline": pending,
            "updated_at": saved_at,
        }
    )
    await asave_proposal_draft(saved)
    logger.info(
        "align-rfp-outline preview rfp_id=%s current=%d proposed=%d nothing=%s",
        rfp_id,
        len(before_titles),
        len(after_titles),
        preview.get("nothingToChange"),
    )
    return {
        "ok": True,
        "preview": preview,
        "nothingToChange": bool(preview.get("nothingToChange")),
    }


async def run_align_to_rfp_outline(rfp_id: str) -> dict[str, Any]:
    """Reorder sidebar to RFP submission order; stub missing required tabs.

    Uses pending preview draft when present (0 extra structure pass). Layout-only.
    """
    from app.services.proposal_pipeline_checkpoint import record_pipeline_activity

    async def _progress(step: int, label: str, detail: str) -> None:
        await record_pipeline_activity(
            rfp_id,
            label=label,
            detail=detail,
            step_index=step,
            step_total=4,
            in_progress_phase=ALIGN_PHASE,
        )

    rfp, _desc, rfp_text = load_rfp_for_proposal(rfp_id)
    draft = await aget_proposal_draft(rfp_id)
    if draft is None or not draft.sections:
        raise ValueError("No proposal draft to align — generate a draft first.")

    research = await aget_research_cache(rfp_id)
    before_titles = _outline_titles(draft)
    pending = draft.pending_align_rfp_outline
    used_pending = False
    proposed_from_pending: ProposalDraft | None = None
    if isinstance(pending, dict) and isinstance(pending.get("proposedDraft"), dict):
        if pending.get("basedOnUpdatedAt") != draft.updated_at:
            # Draft changed since this preview was computed (e.g. new sections
            # were added by a gap-fill pass, or the user edited content) —
            # applying the frozen preview would silently discard that work, so
            # fall through to a fresh pass against the current draft instead.
            logger.info(
                "align-rfp-outline pending preview stale for %s "
                "(based_on=%s current=%s) — re-running instead of applying it",
                rfp_id,
                pending.get("basedOnUpdatedAt"),
                draft.updated_at,
            )
        else:
            try:
                proposed_from_pending = ProposalDraft.model_validate(pending["proposedDraft"])
                used_pending = True
            except Exception:  # noqa: BLE001
                logger.warning(
                    "align-rfp-outline pending draft invalid for %s — re-running",
                    rfp_id,
                    exc_info=True,
                )
                proposed_from_pending = None
                used_pending = False

    await _progress(
        1,
        "Align to RFP outline",
        "Saving undo checkpoint (Before Align) — drafted wording stays untouched…",
    )
    draft = push_proposal_snapshot(draft, label="Before Align to RFP outline")
    await asave_proposal_draft(draft)

    if proposed_from_pending is not None:
        await _progress(
            2,
            "Align to RFP outline",
            "Using your preview (no extra AI scan)…",
        )
        await _progress(
            3,
            "Align to RFP outline",
            "Applying the left-list order you approved…",
        )
        updated = draft.model_copy(
            update={
                "sections": proposed_from_pending.sections,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        logs = list((pending or {}).get("preview", {}).get("logs") or [])
        human = list((pending or {}).get("preview", {}).get("humanDecisionGaps") or [])
    else:
        await _progress(
            2,
            "Align to RFP outline",
            "Reading RFP submission format / TOC to learn required tab order…",
        )
        skip_ids = fulfill_scan_preserve_bio_and_case_study_ids(draft)
        await _progress(
            3,
            "Align to RFP outline",
            "Reordering sidebar tabs + stubbing missing RFP tabs (no prose rewrite)…",
        )
        updated, logs, human = await run_rfp_structure_alignment_pass(
            draft=draft,
            rfp=rfp,
            rfp_text=rfp_text or "",
            research=research,
            skip_section_ids=skip_ids,
            use_llm=False,
            include_missing_submittals=True,
        )

    await _progress(
        4,
        "Align to RFP outline",
        "Saving aligned outline…",
    )
    after_titles = _outline_titles(updated)
    summary = _format_outline_diff(before_titles, after_titles)
    report: dict[str, Any] = {
        "mode": ALIGN_PHASE,
        "beforeTitles": before_titles,
        "afterTitles": after_titles,
        "logs": list(logs),
        "humanDecisionGaps": list(human),
        "summary": summary,
        "changed": before_titles != after_titles or bool(logs),
        "usedPendingPreview": used_pending,
        "completedAt": datetime.now(timezone.utc).isoformat(),
    }
    updated = updated.model_copy(
        update={
            "last_fulfill_report": report,
            "pending_align_rfp_outline": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    await asave_proposal_draft(updated)
    try:
        from app.services.proposal_pipeline_checkpoint import record_phase_completed

        await record_phase_completed(rfp_id, ALIGN_PHASE)
    except Exception:  # noqa: BLE001
        logger.warning(
            "align-rfp-outline could not clear checkpoint for %s", rfp_id, exc_info=True
        )
    logger.info(
        "align-rfp-outline rfp_id=%s before=%d after=%d logs=%d pending=%s",
        rfp_id,
        len(before_titles),
        len(after_titles),
        len(logs),
        used_pending,
    )
    return report


async def align_draft_from_chat(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    section: ProposalSection,
) -> tuple[ProposalSection, ProposalDraft, ProposalResearchCache | None, str, bool]:
    """Run Align during Improve chat when the user asks for RFP packet order."""
    del draft  # reload from store after align
    report = await run_align_to_rfp_outline(rfp_id)
    updated = await aget_proposal_draft(rfp_id)
    if updated is None:
        raise ValueError("Align finished but draft was missing.")
    research = await aget_research_cache(rfp_id) or research
    focus = next((s for s in updated.sections if s.id == section.id), None)
    if focus is None:
        focus = updated.sections[0] if updated.sections else section
    reply = str(report.get("summary") or "Aligned tabs to the RFP outline.")
    if report.get("humanDecisionGaps"):
        gaps = report["humanDecisionGaps"][:5]
        reply += "\n\n**Still needs attention:**\n" + "\n".join(f"- {g}" for g in gaps)
    reply += (
        "\n\nUse **Align to RFP outline** on the Content tab anytime for this "
        "reorder — Improve stays for wording inside one section."
    )
    return focus, updated, research, reply, bool(report.get("changed"))
