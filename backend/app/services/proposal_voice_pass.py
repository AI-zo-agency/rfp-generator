"""Draft-level voice gate: one check of each written section, one repair.

The check quotes a sentence only by copying it from the section. Code drops a
quote that is not actually there. A grounded quote is repaired once. A second
check does not repair again. A quote that survives is a VoiceFinding, and the
section is not marked reviewed.
"""

from __future__ import annotations

import logging
from collections.abc import Collection

from app.models.proposal import ProposalDraft, ProposalSection, VoiceFinding
from app.services import llm
from app.services import proposal_voice_llm as voice_llm
from app.services.proposal_brand_voice import voice_standards_for
from app.services.proposal_voice_enforcement import apply_writing_standards_mechanics

logger = logging.getLogger(__name__)

MAX_REVIEWED = 4000

_CHECK_SYSTEM = """You check one proposal section against the zö writing standards in the user message. Return JSON only:
{"misses": [{"quote": "exact sentence from the section", "rule": "short habit name"}]}
Copy quote verbatim from the section, including its punctuation. Quote a sentence only when it breaks a habit in those standards.
If the section holds the standards, return {"misses": []}.
Do not invent a sentence. Do not rewrite the section.
"""

_REPAIR_SYSTEM = """You repair quoted sentences in one proposal section so they follow the zö writing standards. Return JSON only:
{"edits": [{"find": "exact quote", "replace": "the rewritten sentence"}]}
find must be copied from the quotes you are given. Rewrite only those sentences, in your own words for this section. Do not change any other sentence.
Keep numbers, names, [MANUAL FILL], [VERIFY], and [E#] markers that belong to the sentence.
"""


def _eligible(section: ProposalSection, section_ids: Collection[str] | None) -> bool:
    if not (section.content or "").strip():
        return False
    if section_ids is not None:
        return section.id in section_ids
    return section.mode == "write"  # pull / select sections are verbatim KB content


def _ground_quotes(raw_misses: object, content: str) -> list[tuple[str, str]]:
    """Keep a quote only when it is a contiguous substring of the section."""
    if not isinstance(raw_misses, list):
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in raw_misses:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "")
        if not quote or quote not in content or quote in seen:
            continue
        seen.add(quote)
        rule = str(item.get("rule") or "voice").strip() or "voice"
        out.append((quote, rule[:120]))
    return out


def _apply_repairs(content: str, allowed: set[str], edits: object) -> str:
    """Apply a replacement only when find is a grounded quote and occurs once."""
    if not isinstance(edits, list):
        return content
    text = content
    for item in edits:
        if not isinstance(item, dict):
            continue
        find = str(item.get("find") or "")
        replace = str(item.get("replace") or "")
        if find not in allowed or not replace or find == replace:
            continue
        if text.count(find) != 1:
            continue
        text = text.replace(find, replace, 1)
    return text


async def _voice_json(messages: list[dict[str, str]], *, node_name: str, rfp_id: str | None) -> dict | None:
    try:
        raw, _ = await llm.chat_json(
            messages,
            max_tokens=2048,
            temperature=0,
            node_name=node_name,
            rfp_id=rfp_id,
            reasoning_effort="low",
        )
    except Exception as exc:  # noqa: BLE001 - a failed check must not abort the manuscript
        logger.warning("%s failed: %s", node_name, str(exc)[:200])
        return None
    if not isinstance(raw, dict):
        logger.warning("%s returned non-dict JSON", node_name)
        return None
    return raw


async def check_section(content: str, standards: str, *, rfp_id: str | None) -> dict | None:
    """One check of the whole section. None means the call failed."""
    raw = await _voice_json(
        [
            {"role": "system", "content": _CHECK_SYSTEM},
            {"role": "user", "content": f"STANDARDS:\n{standards}\n\nSECTION:\n{content}"},
        ],
        node_name="proposal_voice_gate_check",
        rfp_id=rfp_id,
    )
    if raw is None or not isinstance(raw.get("misses"), list):
        return None
    return raw


async def repair_section(
    content: str,
    standards: str,
    quotes: list[tuple[str, str]],
    *,
    rfp_id: str | None,
) -> dict | None:
    """One repair of the grounded quotes. None means the call failed."""
    listed = "\n".join(f"- {quote}" for quote, _rule in quotes)
    raw = await _voice_json(
        [
            {"role": "system", "content": _REPAIR_SYSTEM},
            {
                "role": "user",
                "content": f"STANDARDS:\n{standards}\n\nQUOTES TO REPAIR:\n{listed}\n\nSECTION:\n{content}",
            },
        ],
        node_name="proposal_voice_gate_repair",
        rfp_id=rfp_id,
    )
    if raw is None or not isinstance(raw.get("edits"), list):
        return None
    return raw


def _findings_for(section_id: str, quotes: list[tuple[str, str]]) -> list[VoiceFinding]:
    return [
        VoiceFinding(sectionId=section_id, find=quote, rule=rule, kind="needs_human")
        for quote, rule in quotes
    ]


async def _gate_one(
    section: ProposalSection,
    *,
    standards: str,
    rfp_id: str | None,
) -> tuple[ProposalSection, list[str], list[VoiceFinding], bool]:
    """Returns section, logs, findings, and whether the check failed closed.

    ``checked`` False means invalid JSON: do not mark reviewed, do not invent a finding.
    """
    content = apply_writing_standards_mechanics(section.content or "")
    first = await check_section(content, standards, rfp_id=rfp_id)
    if first is None:
        logger.warning("%s: voice gate check invalid", section.id)
        return section.model_copy(update={"content": content}), [], [], False
    grounded = _ground_quotes(first.get("misses"), content)
    if not grounded:
        updated = section.model_copy(update={"content": content})
        return updated, [], [], True
    repair = await repair_section(content, standards, grounded, rfp_id=rfp_id)
    if repair is None:
        logger.warning("%s: voice gate repair invalid", section.id)
        return section.model_copy(update={"content": content}), [], [], False
    allowed = {quote for quote, _rule in grounded}
    repaired = _apply_repairs(content, allowed, repair.get("edits"))
    second = await check_section(repaired, standards, rfp_id=rfp_id)
    if second is None:
        logger.warning("%s: voice gate second check invalid", section.id)
        updated = section.model_copy(update={"content": repaired})
        logs = [f"{section.id}: voice gate repaired"] if repaired != content else []
        return updated, logs, [], False
    still = _ground_quotes(second.get("misses"), repaired)
    updated = section.model_copy(update={"content": repaired})
    logs = [f"{section.id}: voice gate repaired"] if repaired != content else []
    if still:
        logs.append(f"{section.id}: voice gate flagged {len(still)}")
        return updated, logs, _findings_for(section.id, still), True
    return updated, logs, [], True


async def apply_voice_pass(
    draft: ProposalDraft,
    *,
    section_ids: Collection[str] | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """Check eligible sections once, repair quoted sentences once. Never raises."""
    targets = [s for s in draft.sections if _eligible(s, section_ids)]
    if not targets:
        return draft, []
    standards, rev_id = voice_standards_for(draft.rfp_id)
    results: list[tuple[ProposalSection, list[str], list[VoiceFinding], bool] | BaseException] = []
    for section in targets:
        try:
            results.append(await _gate_one(section, standards=standards, rfp_id=draft.rfp_id))
        except Exception as exc:  # noqa: BLE001 - one section must not abort the rest
            logger.warning("%s: voice gate failed (%s)", section.id, exc)
            results.append(exc)

    by_id: dict[str, tuple[ProposalSection, list[str], list[VoiceFinding], bool]] = {}
    for section, result in zip(targets, results):
        if isinstance(result, BaseException):
            logger.warning("%s: voice gate failed (%s)", section.id, result)
            continue
        by_id[section.id] = result

    sections: list[ProposalSection] = []
    logs: list[str] = []
    final_by_id: dict[str, ProposalSection] = {}
    clean_ids: set[str] = set()
    flagged_ids: set[str] = set()
    new_findings: list[VoiceFinding] = []
    for section in draft.sections:
        result = by_id.get(section.id)
        if result is None:
            sections.append(section)
            final_by_id[section.id] = section
            continue
        updated, section_logs, findings, checked = result
        sections.append(updated)
        final_by_id[section.id] = updated
        logs.extend(section_logs)
        if not checked:
            continue
        if findings:
            flagged_ids.add(section.id)
            new_findings.extend(findings)
        else:
            clean_ids.add(section.id)

    merged = list(draft.voice_reviewed)
    drop: set[str] = set()
    for sid in flagged_ids:
        drop.update(_hashes(final_by_id[sid], rev_id))
    if drop:
        merged = [h for h in merged if h not in drop]
    seen = set(merged)
    for sid in clean_ids:
        for h in _hashes(final_by_id[sid], rev_id):
            if h not in seen:
                seen.add(h)
                merged.append(h)

    kept: list[VoiceFinding] = []
    seen_findings: set[tuple[str, str]] = set()
    replaced = clean_ids | flagged_ids
    for finding in draft.voice_findings:
        if finding.section_id in replaced:
            continue
        body = final_by_id.get(finding.section_id)
        text = (body.content if body else "") or ""
        key = (finding.section_id, finding.find)
        if finding.find in text and key not in seen_findings:
            seen_findings.add(key)
            kept.append(finding)
    for finding in new_findings:
        key = (finding.section_id, finding.find)
        if key not in seen_findings:
            seen_findings.add(key)
            kept.append(finding)

    updated = draft.model_copy(
        update={
            "sections": sections,
            "voice_reviewed": merged[-MAX_REVIEWED:],
            "voice_findings": kept,
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


async def run_saved_voice_gate(rfp_id: str) -> list[str]:
    """Check each written section, then save. One section at a time.

    The voice agent writes any repair. This function does not compose prose.
    """
    from app.services.proposal_pipeline_checkpoint import record_pipeline_activity
    from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft
    from app.services.proposal_zero_fabrication import apply_zero_fabrication_guards_before_persist

    draft = await aget_proposal_draft(rfp_id)
    if draft is None or not draft.sections:
        raise ValueError("No proposal draft to voice-check.")
    targets = [s for s in draft.sections if _eligible(s, None)]
    logs: list[str] = []
    total = len(targets)
    for index, section in enumerate(targets, start=1):
        title = section.title or section.id
        await record_pipeline_activity(
            rfp_id,
            label="Brand voice",
            detail=f"{title} ({index}/{total})",
            step_index=index,
            step_total=total,
            in_progress_phase="packet-redistribute",
        )
        current = await aget_proposal_draft(rfp_id)
        if current is None:
            raise ValueError("Proposal draft disappeared during the voice gate.")
        current, section_logs = await apply_voice_pass(current, section_ids={section.id})
        current, _report = await apply_zero_fabrication_guards_before_persist(
            current,
            label="voice-gate",
            skip_llm_repairs=True,
        )
        await asave_proposal_draft(current)
        logs.extend(section_logs)
        logger.info(
            "voice gate saved %s %s (%d/%d) %s",
            rfp_id,
            section.id,
            index,
            total,
            "; ".join(section_logs) or "clean",
        )
    return logs
