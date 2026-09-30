"""Draft-level voice pass: run the LLM voice editor over a manuscript.

Replaces the regex scrub. Only paragraphs not reviewed before are sent to the model
(hashes live on ProposalDraft.voice_reviewed), so approved copy stays approved and
repeated calls in one pipeline run cost almost nothing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection

from app.models.proposal import ProposalDraft, ProposalSection, VoiceFinding
from app.services import proposal_voice_llm as voice_llm
from app.services.proposal_brand_voice import classify_section_register, voice_standards_for

logger = logging.getLogger(__name__)

MAX_REVIEWED = 4000


def _eligible(section: ProposalSection, section_ids: Collection[str] | None) -> bool:
    if not (section.content or "").strip():
        return False
    if section_ids is not None:
        return section.id in section_ids
    return section.mode == "write"  # pull / select sections are verbatim KB content


def _merge_findings(
    draft: ProposalDraft,
    sections: list[ProposalSection],
    results: dict[str, voice_llm.VoiceResult],
) -> list[VoiceFinding]:
    content = {s.id: s.content or "" for s in sections}
    out: list[VoiceFinding] = []
    seen: set[tuple[str, str]] = set()

    def add(f: VoiceFinding) -> None:
        key = (f.section_id, f.find)
        if key not in seen and f.find in content.get(f.section_id, ""):
            seen.add(key)
            out.append(f)

    for f in draft.voice_findings:
        add(f)
    for sid, res in results.items():
        for edit, why in res.rejected:
            # local guard rejections and a failed verifier call ("no verdict") are noise, not judgment
            if why.startswith("verifier") and why != "verifier: no verdict":
                add(VoiceFinding(sectionId=sid, find=edit.find, rule=edit.rule, kind="needs_human", detail=why))
        for edit in res.suggested:
            add(VoiceFinding(sectionId=sid, find=edit.find, rule=edit.rule, kind="suggestion", detail=edit.replace))
    return out


def _failed(section: ProposalSection, exc: BaseException) -> voice_llm.VoiceResult:
    logger.warning("voice pass failed for section %s: %s", section.id, exc)
    return voice_llm.VoiceResult(text=section.content, skipped=f"error: {exc}")


async def apply_voice_pass(
    draft: ProposalDraft,
    *,
    section_ids: Collection[str] | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """Review eligible sections, write back fixes, record leftovers. Never raises."""
    targets = [s for s in draft.sections if _eligible(s, section_ids)]
    if not targets:
        return draft, []
    standards, rev_id = voice_standards_for(draft.rfp_id)
    reviewed = set(draft.voice_reviewed)
    results = await asyncio.gather(
        *(
            voice_llm.rewrite_for_voice(
                s.content,
                register=classify_section_register(section_id=s.id, title=s.title or "", zo_mode=s.mode or "write"),
                standards=standards,
                rev_id=rev_id,
                reviewed=reviewed,
                rfp_id=draft.rfp_id,
            )
            for s in targets
        ),
        return_exceptions=True,
    )
    results = [
        _failed(s, r) if isinstance(r, BaseException) else r for s, r in zip(targets, results)
    ]
    by_id = {s.id: r for s, r in zip(targets, results)}

    merged = list(draft.voice_reviewed)
    seen = set(merged)
    for res in results:
        for h in res.reviewed:
            if h not in seen:
                seen.add(h)
                merged.append(h)

    sections: list[ProposalSection] = []
    logs: list[str] = []
    for s in draft.sections:
        res = by_id.get(s.id)
        if res is None:
            sections.append(s)
            continue
        if res.text != s.content:
            s = s.model_copy(update={"content": res.text})
        sections.append(s)
        logs += [f"{s.id}: {line}" for line in res.logs]
        if res.skipped:
            # not a fix: callers count the returned logs, and Review shows the gap
            logger.warning("%s: voice pass incomplete (%s)", s.id, res.skipped)

    updated = draft.model_copy(
        update={
            "sections": sections,
            "voice_reviewed": merged[-MAX_REVIEWED:],
            "voice_findings": _merge_findings(draft, sections, by_id),
        }
    )
    return updated, logs


def _hashes(section: ProposalSection, rev_id: str) -> list[str]:
    return [voice_llm.block_hash(b, rev_id) for b in voice_llm._blocks(section.content or "")]


def mark_sections_reviewed(draft: ProposalDraft, section_ids: Collection[str]) -> ProposalDraft:
    """Record every paragraph of the named sections as reviewed (a human approved this text)."""
    _, rev_id = voice_standards_for(draft.rfp_id)
    merged = list(draft.voice_reviewed)
    seen = set(merged)
    for s in draft.sections:
        if s.id not in section_ids:
            continue
        for h in _hashes(s, rev_id):
            if h not in seen:
                seen.add(h)
                merged.append(h)
    return draft.model_copy(update={"voice_reviewed": merged[-MAX_REVIEWED:]})


def voice_coverage_gaps(draft: ProposalDraft) -> list[str]:
    """Ids of write-mode sections with a paragraph the voice pass has not reviewed."""
    _, rev_id = voice_standards_for(draft.rfp_id)
    reviewed = set(draft.voice_reviewed)
    return [
        s.id
        for s in draft.sections
        if _eligible(s, None) and any(h not in reviewed for h in _hashes(s, rev_id))
    ]
