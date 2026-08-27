"""Packet redistribute — mechanical block/tab moves + optional LLM plan.

Phase B of Align & place content. MOVE ops are verbatim (no prose rewrite).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProposalDraft, ProposalSection

logger = logging.getLogger(__name__)

PACKET_REDISTRIBUTE_PHASE = "packet-redistribute"


def _parse_heading_line(line: str) -> tuple[int, str] | None:
    """Return (level, title) if line is a markdown heading; else None."""
    raw = (line or "").rstrip("\n\r")
    if not raw.startswith("#"):
        return None
    level = 0
    for ch in raw:
        if ch == "#":
            level += 1
        else:
            break
    if level < 1 or level > 6:
        return None
    if level >= len(raw) or raw[level] not in " \t":
        return None
    title = raw[level + 1 :].strip()
    if not title:
        return None
    return level, title


def extract_heading_block(
    content: str, heading_text: str
) -> tuple[str, str, str] | None:
    """Split markdown into (before, block, after) for a heading match.

    Block runs from the matching heading line through the line before the next
    heading of the same or higher level (fewer ``#``). Match is case-insensitive
    on heading text with leading ``#`` stripped.
    """
    needle = (heading_text or "").strip().lstrip("#").strip().casefold()
    if not needle:
        return None
    lines = (content or "").splitlines(keepends=True)
    if not lines:
        return None

    start: int | None = None
    start_level = 2
    for i, line in enumerate(lines):
        parsed = _parse_heading_line(line)
        if not parsed:
            continue
        level, title = parsed
        title_cf = title.casefold()
        if title_cf == needle or needle in title_cf or title_cf in needle:
            start = i
            start_level = level
            break
    if start is None:
        return None

    end = len(lines)
    for j in range(start + 1, len(lines)):
        parsed = _parse_heading_line(lines[j])
        if not parsed:
            continue
        if parsed[0] <= start_level:
            end = j
            break

    before = "".join(lines[:start])
    block = "".join(lines[start:end])
    after = "".join(lines[end:])
    return before, block, after


def _section_index(draft: ProposalDraft, section_id: str) -> int:
    for i, s in enumerate(draft.sections):
        if s.id == section_id:
            return i
    return -1


def _is_static_company_section(section: ProposalSection) -> bool:
    sid = section.id or ""
    return (
        sid.startswith("section-1-")
        or sid.startswith("section-2-bio-")
        or sid.startswith("section-3-")
        or "case-study" in sid
        or sid in {"section-2", "section-3", "our-work"}
    )


def apply_move_block(
    draft: ProposalDraft,
    *,
    from_id: str,
    to_id: str,
    heading_text: str,
) -> tuple[ProposalDraft, str]:
    """Cut a heading block from one section and append it to another (verbatim)."""
    if from_id == to_id:
        return draft, "move_block skipped: same section"
    from_i = _section_index(draft, from_id)
    to_i = _section_index(draft, to_id)
    if from_i < 0 or to_i < 0:
        return draft, f"move_block skipped: missing section ({from_id!r} → {to_id!r})"

    src = draft.sections[from_i]
    dst = draft.sections[to_i]
    hit = extract_heading_block(src.content or "", heading_text)
    if hit is None:
        return draft, f"move_block skipped: heading {heading_text!r} not in {from_id}"

    before, block, after = hit
    new_src = f"{before}{after}".strip()
    dst_body = (dst.content or "").rstrip()
    new_dst = f"{dst_body}\n\n{block.strip()}".strip() if dst_body else block.strip()

    sections = list(draft.sections)
    sections[from_i] = src.model_copy(update={"content": new_src})
    sections[to_i] = dst.model_copy(update={"content": new_dst})
    now = datetime.now(timezone.utc).isoformat()
    updated = draft.model_copy(update={"sections": sections, "updated_at": now})
    return updated, f"moved heading {heading_text!r}: {from_id} → {to_id}"


def apply_move_tab(
    draft: ProposalDraft,
    *,
    section_id: str,
    after_section_id: str | None,
) -> tuple[ProposalDraft, str]:
    """Reorder a whole tab to sit after ``after_section_id`` (or first if None)."""
    from_i = _section_index(draft, section_id)
    if from_i < 0:
        return draft, f"move_tab skipped: missing {section_id!r}"

    sections = list(draft.sections)
    item = sections.pop(from_i)
    if after_section_id:
        after_i = next((i for i, s in enumerate(sections) if s.id == after_section_id), -1)
        insert_at = after_i + 1 if after_i >= 0 else len(sections)
    else:
        insert_at = 0
    sections.insert(insert_at, item)
    now = datetime.now(timezone.utc).isoformat()
    updated = draft.model_copy(update={"sections": sections, "updated_at": now})
    return updated, f"moved tab {section_id} after {after_section_id!r}"


def execute_redistribute_plan(
    draft: ProposalDraft,
    plan: dict[str, Any],
    *,
    allow_static_reorder: bool = False,
) -> tuple[ProposalDraft, list[str]]:
    """Apply planner ops mechanically. Unknown ops are logged and skipped."""
    logs: list[str] = []
    working = draft
    ops = plan.get("ops") if isinstance(plan, dict) else None
    if not isinstance(ops, list):
        return draft, ["redistribute: no ops list"]

    for raw in ops:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or "").strip()
        if op == "leave":
            continue
        if op == "move_block":
            match = raw.get("match") if isinstance(raw.get("match"), dict) else {}
            heading = str(match.get("text") or raw.get("headingText") or "").strip()
            working, msg = apply_move_block(
                working,
                from_id=str(raw.get("fromSectionId") or ""),
                to_id=str(raw.get("toSectionId") or ""),
                heading_text=heading,
            )
            logs.append(msg)
            continue
        if op == "move_tab":
            sid = str(raw.get("sectionId") or "")
            idx = _section_index(working, sid)
            if idx >= 0 and _is_static_company_section(working.sections[idx]):
                if not allow_static_reorder:
                    logs.append(f"move_tab skipped (static locked): {sid}")
                    continue
            working, msg = apply_move_tab(
                working,
                section_id=sid,
                after_section_id=(
                    str(raw["afterSectionId"])
                    if raw.get("afterSectionId") is not None
                    else None
                ),
            )
            logs.append(msg)
            continue
        logs.append(f"redistribute: skipped unknown op {op!r}")

    return working, logs


def _section_digest(draft: ProposalDraft) -> list[dict[str, Any]]:
    """Compact section map for the light planner (token-cheap)."""
    from app.services.proposal_section_quality import word_count

    out: list[dict[str, Any]] = []
    for s in draft.sections[:48]:
        body = s.content or ""
        headings: list[str] = []
        for line in body.splitlines():
            parsed = _parse_heading_line(line)
            if parsed:
                headings.append(parsed[1][:80])
            if len(headings) >= 8:
                break
        out.append(
            {
                "id": s.id,
                "title": (s.title or "")[:80],
                "wordCount": word_count(body),
                "isStub": "[MANUAL FILL" in body[:500].upper() or word_count(body) < 80,
                "isStaticCompany": _is_static_company_section(s),
                "headings": headings,
            }
        )
    return out


def _outline_targets_from_draft(draft: ProposalDraft) -> list[dict[str, str]]:
    """Use current tabs as the target outline — no second RFP-structure LLM."""
    return [
        {"id": s.id, "title": ((s.title or "").strip() or s.id)[:80]}
        for s in draft.sections[:48]
    ]


def _actionable_ops(plan: dict[str, Any]) -> list[dict[str, Any]]:
    ops = plan.get("ops") if isinstance(plan.get("ops"), list) else []
    return [
        op
        for op in ops
        if isinstance(op, dict)
        and str(op.get("op") or "").strip() in {"move_block", "move_tab"}
    ]


def build_place_preview(draft: ProposalDraft, plan: dict[str, Any]) -> dict[str, Any]:
    """Human-readable scan result for the Place preview modal (no LLM)."""
    title_by_id = {
        s.id: ((s.title or "").strip() or s.id) for s in draft.sections
    }
    moves: list[dict[str, str]] = []
    issues: list[str] = []
    for raw in _actionable_ops(plan):
        op = str(raw.get("op") or "").strip()
        if op == "move_block":
            match = raw.get("match") if isinstance(raw.get("match"), dict) else {}
            heading = str(match.get("text") or raw.get("headingText") or "").strip()
            from_id = str(raw.get("fromSectionId") or "")
            to_id = str(raw.get("toSectionId") or "")
            from_title = title_by_id.get(from_id, from_id)
            to_title = title_by_id.get(to_id, to_id)
            label = (
                f"“{heading or 'block'}” is under “{from_title}” but belongs in "
                f"“{to_title}”"
            )
            issues.append(label)
            moves.append(
                {
                    "kind": "block",
                    "heading": heading or "block",
                    "fromTitle": from_title,
                    "toTitle": to_title,
                    "summary": f"Move “{heading or 'block'}”: {from_title} → {to_title}",
                }
            )
        elif op == "move_tab":
            sid = str(raw.get("sectionId") or "")
            tab_title = title_by_id.get(sid, sid)
            issues.append(f"Tab “{tab_title}” is in the wrong place in the left list")
            moves.append(
                {
                    "kind": "tab",
                    "heading": tab_title,
                    "fromTitle": tab_title,
                    "toTitle": "(new position)",
                    "summary": f"Reorder whole tab “{tab_title}”",
                }
            )

    gaps = [str(x).strip() for x in (plan.get("humanGaps") or []) if str(x).strip()]
    stub_ids = [str(x) for x in (plan.get("stubFillIds") or [])]
    stub_titles = [title_by_id.get(sid, sid) for sid in stub_ids]
    for gap in gaps:
        if gap not in issues:
            issues.append(gap)
    for title in stub_titles:
        note = f"Empty / stub tab still needs writing: “{title}”"
        if note not in issues:
            issues.append(note)

    if not moves and not gaps and not stub_titles:
        # Empty state is explained in the UI — don't dump jargon into issues.
        pass

    return {
        "issues": issues[:40],
        "moves": moves[:40],
        "humanGaps": gaps[:20],
        "stubTitles": stub_titles[:20],
        "plannedMoves": len(moves),
        "nothingToMove": len(moves) == 0,
        "summary": (
            f"{len(moves)} move(s) proposed"
            if moves
            else "No writing needs moving between sections"
        ),
    }


async def plan_packet_redistribution(
    *,
    draft: ProposalDraft,
    rfp_title: str,
    rfp_specs: list[dict[str, Any]] | None = None,
    rfp_id: str | None = None,
) -> dict[str, Any]:
    """One light JSON planner call — ops only, no prose rewrite.

    Uses the current draft tab list as the target outline (cheap) instead of
    re-running RFP structure extraction.
    """
    from app.services.llm import chat_json

    digest = _section_digest(draft)
    targets = rfp_specs if rfp_specs is not None else _outline_targets_from_draft(draft)
    system = (
        "Plan MOVE ops only. JSON only. Never rewrite prose.\n"
        'Ops: move_block{fromSectionId,toSectionId,match:{type:"heading",text}}, '
        "move_tab{sectionId,afterSectionId}, leave{sectionId}.\n"
        "move_block when a heading sits under the wrong tab. "
        "Skip isStaticCompany unless clearly required. "
        "match.text must be an existing heading. "
        "stubFillIds = hollow stubs after moves. "
        "humanGaps = short human notes.\n"
        'Schema: {"ops":[],"stubFillIds":[],"humanGaps":[]}'
    )
    user = (
        f"RFP: {(rfp_title or '')[:120]}\n"
        f"Sections:{digest}\n"
        f"Target tabs:{targets[:40]}"
    )
    raw, _provider = await chat_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=1200,
        temperature=0.1,
        tier="light",
        node_name="packet-redistribute-plan",
        rfp_id=rfp_id,
        include_corrections=False,
    )
    if not isinstance(raw, dict):
        return {"ops": [], "stubFillIds": [], "humanGaps": ["Planner returned no JSON."]}
    ops = raw.get("ops") if isinstance(raw.get("ops"), list) else []
    stubs = raw.get("stubFillIds") if isinstance(raw.get("stubFillIds"), list) else []
    gaps = raw.get("humanGaps") if isinstance(raw.get("humanGaps"), list) else []
    return {
        "ops": ops,
        "stubFillIds": [str(x) for x in stubs],
        "humanGaps": [str(x) for x in gaps],
    }


async def preview_packet_redistribute(rfp_id: str) -> dict[str, Any]:
    """Scan-only: one light plan call, store pending plan, change nothing."""
    from app.services.proposal_common import load_rfp_for_proposal
    from app.services.proposal_repository import (
        aget_proposal_draft,
        asave_proposal_draft,
    )

    rfp, _desc, _rfp_text = load_rfp_for_proposal(rfp_id)
    draft = await aget_proposal_draft(rfp_id)
    if draft is None or not draft.sections:
        raise ValueError("No proposal draft to scan — generate a draft first.")

    plan = await plan_packet_redistribution(
        draft=draft,
        rfp_title=rfp.title or "",
        rfp_id=rfp_id,
    )
    preview = build_place_preview(draft, plan)
    pending = {
        "plan": plan,
        "preview": preview,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    updated = draft.model_copy(
        update={
            "pending_packet_redistribute": pending,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    await asave_proposal_draft(updated)
    logger.info(
        "packet-redistribute preview rfp_id=%s moves=%d gaps=%d",
        rfp_id,
        preview.get("plannedMoves") or 0,
        len(preview.get("humanGaps") or []),
    )
    return {
        "ok": True,
        "preview": preview,
        "plannedMoves": preview.get("plannedMoves") or 0,
    }


async def run_packet_redistribute(
    rfp_id: str,
    *,
    allow_static_reorder: bool = False,
) -> dict[str, Any]:
    """Apply Place moves. Uses pending preview plan when present (0 LLM)."""
    from app.services.proposal_common import load_rfp_for_proposal
    from app.services.proposal_draft_snapshots import push_proposal_snapshot
    from app.services.proposal_pipeline_checkpoint import (
        record_phase_completed,
        record_pipeline_activity,
    )
    from app.services.proposal_repository import (
        aget_proposal_draft,
        asave_proposal_draft,
    )

    async def _progress(step: int, detail: str) -> None:
        await record_pipeline_activity(
            rfp_id,
            label="Place content in RFP tabs",
            detail=detail,
            step_index=step,
            step_total=4,
            in_progress_phase=PACKET_REDISTRIBUTE_PHASE,
        )

    rfp, _desc, _rfp_text = load_rfp_for_proposal(rfp_id)
    draft = await aget_proposal_draft(rfp_id)
    if draft is None or not draft.sections:
        raise ValueError("No proposal draft to redistribute — generate a draft first.")

    pending = draft.pending_packet_redistribute
    plan: dict[str, Any] | None = None
    used_pending = False
    if isinstance(pending, dict) and isinstance(pending.get("plan"), dict):
        plan = pending["plan"]
        used_pending = True

    await _progress(1, "Saving undo checkpoint (Before packet redistribute)…")
    draft = push_proposal_snapshot(draft, label="Before packet redistribute")
    await asave_proposal_draft(draft)

    if plan is None:
        await _progress(2, "No saved preview — scanning with one light plan call…")
        plan = await plan_packet_redistribution(
            draft=draft,
            rfp_title=rfp.title or "",
            rfp_id=rfp_id,
        )
        await _progress(3, "Plan ready — applying moves…")
    else:
        await _progress(2, "Using your preview plan (no extra AI scan)…")
        await _progress(3, "Applying approved moves verbatim…")

    actionable = _actionable_ops(plan)
    await _progress(
        4,
        f"Applying {len(actionable)} planned move(s)…",
    )

    title_by_id = {
        s.id: ((s.title or "").strip() or s.id) for s in draft.sections
    }
    working = draft
    move_logs: list[str] = []
    move_summaries: list[str] = []
    skipped: list[str] = []
    for i, raw in enumerate(actionable, start=1):
        op = str(raw.get("op") or "").strip()
        if op == "move_block":
            match = raw.get("match") if isinstance(raw.get("match"), dict) else {}
            heading = str(match.get("text") or raw.get("headingText") or "").strip()
            from_id = str(raw.get("fromSectionId") or "")
            to_id = str(raw.get("toSectionId") or "")
            from_title = title_by_id.get(from_id, from_id)
            to_title = title_by_id.get(to_id, to_id)
            await _progress(
                4,
                f"Move {i}/{len(actionable)}: “{heading or 'block'}” — {from_title} → {to_title}",
            )
            working, msg = apply_move_block(
                working,
                from_id=from_id,
                to_id=to_id,
                heading_text=heading,
            )
            move_logs.append(msg)
            if msg.startswith("moved "):
                move_summaries.append(
                    f"Moved “{heading}” from “{from_title}” → “{to_title}”"
                )
                title_by_id = {
                    s.id: ((s.title or "").strip() or s.id) for s in working.sections
                }
            else:
                skipped.append(msg)
            continue
        if op == "move_tab":
            sid = str(raw.get("sectionId") or "")
            tab_title = title_by_id.get(sid, sid)
            await _progress(
                4,
                f"Move {i}/{len(actionable)}: whole tab “{tab_title}”…",
            )
            working, one_logs = execute_redistribute_plan(
                working,
                {"ops": [raw]},
                allow_static_reorder=allow_static_reorder,
            )
            move_logs.extend(one_logs)
            for msg in one_logs:
                if msg.startswith("moved tab"):
                    move_summaries.append(f"Reordered tab “{tab_title}”")
                    title_by_id = {
                        s.id: ((s.title or "").strip() or s.id)
                        for s in working.sections
                    }
                elif "skipped" in msg:
                    skipped.append(msg)

    moved = len(move_summaries)
    human_gaps = [str(x) for x in (plan.get("humanGaps") or [])]
    stub_ids = [str(x) for x in (plan.get("stubFillIds") or [])]
    stub_titles = [title_by_id.get(sid, sid) for sid in stub_ids]
    report: dict[str, Any] = {
        "mode": PACKET_REDISTRIBUTE_PHASE,
        "plan": plan,
        "logs": list(move_logs),
        "movedCount": moved,
        "moveSummaries": move_summaries,
        "skipped": skipped[:40],
        "humanGaps": human_gaps,
        "stubFillIds": stub_ids,
        "stubTitles": stub_titles[:20],
        "plannedMoves": len(actionable),
        "usedPendingPreview": used_pending,
        "summary": (
            f"**Place content** — {moved} of {len(actionable)} planned move(s) applied "
            "(verbatim). Drafted wording was not rewritten."
        ),
        "changed": moved > 0,
        "completedAt": datetime.now(timezone.utc).isoformat(),
    }
    updated = working.model_copy(
        update={
            "last_fulfill_report": report,
            "pending_packet_redistribute": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    await asave_proposal_draft(updated)
    try:
        await record_phase_completed(rfp_id, PACKET_REDISTRIBUTE_PHASE)
    except Exception:  # noqa: BLE001
        logger.warning(
            "packet-redistribute could not clear checkpoint for %s", rfp_id, exc_info=True
        )
    logger.info(
        "packet-redistribute rfp_id=%s moved=%d planned=%d gaps=%d pending=%s",
        rfp_id,
        moved,
        len(actionable),
        len(human_gaps),
        used_pending,
    )
    return report

