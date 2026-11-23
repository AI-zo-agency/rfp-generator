"""After a cite-linked packet exhibit is uploaded, refresh only the draft tabs that need it.

RFP-scoped: packet files and this apply path always key on ``rfp_id``. Never
rewrites the whole manuscript — selects dependent sections, then pinned improve.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.services.proposal_intelligence.agent_base import safe_chat_json
from app.services.proposal_repository import (
    aget_proposal_draft,
    aget_research_cache,
    asave_research_cache,
)
from app.services.rfp_packet_files import get_packet_file_by_cite, list_packet_files

logger = logging.getLogger(__name__)

_MAX_SECTIONS = 5
_EXHIBIT_EXCERPT_CHARS = 6_000
_SECTION_SNIPPET_CHARS = 400

APPLY_PACKET_EXHIBIT_JOB = "apply-packet-exhibit"


def apply_packet_exhibit_lock_key(rfp_id: str) -> str:
    """Independent of pipeline + Go/No-Go locks; always scoped to one RFP."""
    return f"apply-packet-exhibit:{rfp_id}"


@dataclass
class PacketExhibitApplyResult:
    applied: bool
    reason: str = ""
    cite_target: str = ""
    section_ids: list[str] = field(default_factory=list)
    section_titles: list[str] = field(default_factory=list)
    budget_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "reason": self.reason,
            "citeTarget": self.cite_target,
            "sectionIds": self.section_ids,
            "sectionTitles": self.section_titles,
            "budgetNote": self.budget_note,
        }


def _draft_has_content(draft: ProposalDraft | None) -> bool:
    if not draft or not draft.sections:
        return False
    return any((s.content or "").strip() for s in draft.sections)


def _cite_tokens(cite_target: str) -> list[str]:
    """Loose tokens from the cite label for substring presence (not synonym maps)."""
    raw = (cite_target or "").strip()
    if not raw:
        return []
    parts = [raw.casefold()]
    for chunk in raw.replace("(", " ").replace(")", " ").replace("-", " ").split():
        t = chunk.strip().casefold()
        if len(t) >= 2 and t not in parts:
            parts.append(t)
    return parts


def _text_mentions_cite(text: str, cite_target: str) -> bool:
    body = (text or "").casefold()
    if not body:
        return False
    for token in _cite_tokens(cite_target):
        if token and token in body:
            return True
    return False


def _instrument_for_section(
    research: ProposalResearchCache | None, section_id: str
) -> tuple[str | None, str]:
    """Return (submission_instrument, conditional_reason) from plan or rfpSections."""
    if not research:
        return None, ""
    plan = research.proposal_execution_plan
    if plan is not None:
        try:
            from app.services.proposal_intelligence.schemas import ProposalExecutionPlan

            if not hasattr(plan, "writing"):
                plan = ProposalExecutionPlan.model_validate(plan)
            for sec in plan.writing.proposal_outline.sections:
                if sec.id == section_id:
                    return (
                        getattr(sec, "submission_instrument", None),
                        (getattr(sec, "conditional_reason", None) or ""),
                    )
        except Exception:  # noqa: BLE001
            pass
    for row in research.rfp_sections or []:
        if row.id == section_id:
            return row.submission_instrument, ""
    return None, ""


def heuristic_section_ids_for_cite(
    draft: ProposalDraft,
    cite_target: str,
    *,
    research: ProposalResearchCache | None = None,
) -> list[str]:
    """Sections that already wait on / mention this cite — no LLM required."""
    hits: list[str] = []
    for sec in draft.sections:
        inst, reason = _instrument_for_section(research, sec.id)
        blob = "\n".join(
            [
                sec.title or "",
                sec.content or "",
                reason or "",
            ]
        )
        if inst == "clarify" and _text_mentions_cite(blob, cite_target):
            hits.append(sec.id)
            continue
        if _text_mentions_cite(blob, cite_target):
            hits.append(sec.id)
    return hits


async def select_sections_for_packet_cite(
    draft: ProposalDraft,
    cite_target: str,
    *,
    cite_quote: str = "",
    exhibit_excerpt: str = "",
    research: ProposalResearchCache | None = None,
) -> list[str]:
    """Pick draft tabs that should be rewritten for this exhibit (≤ _MAX_SECTIONS)."""
    seed = heuristic_section_ids_for_cite(
        draft, cite_target, research=research
    )
    catalog: list[dict[str, Any]] = []
    for sec in draft.sections:
        inst, reason = _instrument_for_section(research, sec.id)
        catalog.append(
            {
                "id": sec.id,
                "title": sec.title,
                "instrument": inst or "unset",
                "conditionalReason": (reason or "")[:240],
                "contentSnippet": (sec.content or "")[:_SECTION_SNIPPET_CHARS],
                "heuristicHit": sec.id in seed,
            }
        )

    messages = [
        {
            "role": "system",
            "content": (
                "You select which proposal tabs must be rewritten because a buyer "
                "packet exhibit was just attached for THIS RFP only. "
                "Prefer tabs whose content, clarify stub, or role depends on that "
                "exhibit for scope, design, effort, timeline, or pricing narrative. "
                "Do not select cover letters, unrelated bios, or forms that do not "
                "need the exhibit body. Cap at "
                f"{_MAX_SECTIONS} ids. Return JSON only: "
                '{"sectionIds":["..."]}.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Cite target: {cite_target}\n"
                f"Cite quote: {(cite_quote or '')[:500]}\n"
                f"Exhibit excerpt:\n{(exhibit_excerpt or '')[:_EXHIBIT_EXCERPT_CHARS]}\n\n"
                f"Heuristic hits: {seed}\n\n"
                f"Sections:\n{catalog}"
            ),
        },
    ]
    raw, _provider = await safe_chat_json(
        messages,
        max_tokens=512,
        temperature=0.1,
        agent_name="packet_exhibit_section_select",
    )
    valid_ids = {s.id for s in draft.sections}
    chosen: list[str] = []
    for sid in raw.get("sectionIds") or raw.get("section_ids") or []:
        s = str(sid).strip()
        if s in valid_ids and s not in chosen:
            chosen.append(s)
        if len(chosen) >= _MAX_SECTIONS:
            break

    if not chosen:
        chosen = [s for s in seed if s in valid_ids][:_MAX_SECTIONS]

    # Always include clarify tabs that mention the cite, even if the LLM skipped them.
    for sid in seed:
        inst, _ = _instrument_for_section(research, sid)
        if inst == "clarify" and sid not in chosen and len(chosen) < _MAX_SECTIONS:
            chosen.append(sid)

    return chosen[:_MAX_SECTIONS]


def _improve_message(cite_target: str) -> str:
    return (
        f"A packet exhibit was just attached for this RFP only under cite "
        f"“{cite_target}”. Its body is now in the RFP packet text under "
        f"--- PACKET FILE: {cite_target} ---. "
        f"Rewrite ONLY this section so it answers the RFP using that exhibit "
        f"for scope, design, effort, and time where this tab needs them. "
        f"Replace MANUAL FILL / clarify placeholders that were waiting on this "
        f"exhibit. Do not invent facts beyond the packet and knowledge base. "
        f"Do not change other proposal sections."
    )


async def _clear_clarify_on_sections(
    research: ProposalResearchCache | None,
    section_ids: list[str],
) -> ProposalResearchCache | None:
    """Drop clarify instrument on refreshed tabs now that the exhibit is present."""
    if not research or not section_ids:
        return research
    plan = research.proposal_execution_plan
    if plan is None:
        return research
    try:
        from app.services.proposal_intelligence.schemas import ProposalExecutionPlan

        if not hasattr(plan, "writing"):
            plan = ProposalExecutionPlan.model_validate(plan)
        id_set = set(section_ids)
        changed = False
        next_secs = []
        for sec in plan.writing.proposal_outline.sections:
            if sec.id in id_set and sec.submission_instrument == "clarify":
                next_secs.append(
                    sec.model_copy(
                        update={
                            "submission_instrument": "narrative",
                            "conditional_reason": "",
                        }
                    )
                )
                changed = True
            else:
                next_secs.append(sec)
        if not changed:
            return research
        outline = plan.writing.proposal_outline.model_copy(update={"sections": next_secs})
        writing = plan.writing.model_copy(update={"proposal_outline": outline})
        new_plan = plan.model_copy(update={"writing": writing})
        research = research.model_copy(
            update={"proposal_execution_plan": new_plan.model_dump(by_alias=True)}
        )
        # Mirror onto rfpSections when present.
        if research.rfp_sections:
            rfp_secs = []
            for row in research.rfp_sections:
                if row.id in id_set and row.submission_instrument == "clarify":
                    rfp_secs.append(
                        row.model_copy(update={"submission_instrument": "narrative"})
                    )
                else:
                    rfp_secs.append(row)
            research = research.model_copy(update={"rfp_sections": rfp_secs})
        await asave_research_cache(research)
        return research
    except Exception:  # noqa: BLE001
        logger.warning(
            "packet exhibit clarify clear failed rfp_id=%s",
            getattr(research, "rfp_id", "?"),
            exc_info=True,
        )
        return research


async def _load_exhibit_excerpt(rfp_id: str, cite_target: str) -> tuple[str, str]:
    """Return (cite_quote, exhibit_text_excerpt) for this RFP's packet file only."""
    item = get_packet_file_by_cite(rfp_id, cite_target)
    if not item:
        return "", ""
    quote = item.cite_quote or ""
    try:
        from app.services.rfp_content import _load_packet_supplement_texts

        rows = _load_packet_supplement_texts(rfp_id, max_chars=80_000)
        for cite, _name, text in rows:
            if cite == item.cite_target:
                return quote, (text or "")[:_EXHIBIT_EXCERPT_CHARS]
    except Exception:  # noqa: BLE001
        logger.warning(
            "packet exhibit text load failed rfp_id=%s cite=%s",
            rfp_id,
            cite_target,
            exc_info=True,
        )
    return quote, ""


async def apply_packet_exhibit_to_draft(
    rfp_id: str,
    cite_target: str,
) -> PacketExhibitApplyResult:
    """Rewrite only cite-dependent draft sections for an exhibit on this RFP."""
    from app.services.proposal_section_editor import improve_proposal_section

    target = (cite_target or "").strip()
    if not target:
        return PacketExhibitApplyResult(applied=False, reason="missing_cite_target")

    # Hard scope: exhibit must belong to this rfp_id.
    owned = get_packet_file_by_cite(rfp_id, target)
    if not owned:
        # Allow caller to pass any known cite on this RFP's packet list.
        known = {f.cite_target for f in list_packet_files(rfp_id)}
        if target not in known:
            return PacketExhibitApplyResult(
                applied=False,
                reason="exhibit_not_on_this_rfp",
                cite_target=target,
            )

    draft = await aget_proposal_draft(rfp_id)
    if not _draft_has_content(draft):
        return PacketExhibitApplyResult(
            applied=False,
            reason="no_draft",
            cite_target=target,
        )
    assert draft is not None

    research = await aget_research_cache(rfp_id)
    cite_quote, excerpt = await _load_exhibit_excerpt(rfp_id, target)
    section_ids = await select_sections_for_packet_cite(
        draft,
        target,
        cite_quote=cite_quote or (owned.cite_quote if owned else ""),
        exhibit_excerpt=excerpt,
        research=research,
    )
    if not section_ids:
        return PacketExhibitApplyResult(
            applied=False,
            reason="no_affected_sections",
            cite_target=target,
        )

    title_by_id = {s.id: s.title for s in draft.sections}
    updated: list[str] = []
    titles: list[str] = []
    msg = _improve_message(target)
    for sid in section_ids:
        try:
            await improve_proposal_section(
                rfp_id,
                sid,
                msg,
                persist=True,
                improve_section_pinned=True,
                proposal_wide=False,
                apply_fix=True,
            )
            updated.append(sid)
            titles.append(title_by_id.get(sid) or sid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "packet exhibit section improve failed rfp_id=%s section=%s: %s",
                rfp_id,
                sid,
                str(exc)[:200],
            )

    if not updated:
        return PacketExhibitApplyResult(
            applied=False,
            reason="improve_failed",
            cite_target=target,
            section_ids=section_ids,
        )

    await _clear_clarify_on_sections(research, updated)

    budget_note = None
    for sid in updated:
        inst, _ = _instrument_for_section(research, sid)
        if inst == "cost":
            budget_note = (
                "A cost tab was refreshed from the exhibit. Re-run Generate budget "
                "if the fee ledger should change."
            )
            break

    logger.info(
        "packet exhibit applied rfp_id=%s cite=%s sections=%s",
        rfp_id,
        target,
        updated,
    )
    return PacketExhibitApplyResult(
        applied=True,
        reason="ok",
        cite_target=target,
        section_ids=updated,
        section_titles=titles,
        budget_note=budget_note,
    )
