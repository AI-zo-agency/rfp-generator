"""Scan RFP — fix sections cut off mid-sentence (output limits / bad reframe)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_drafting_graph import _looks_truncated_prose
from app.services.proposal_fulfill_guard import fulfill_scan_preserves_section

_CLOSING_MIN_COMPLETE: dict[str, str] = {
    "rfp-closing-cvc": """## Contractor Vendor Certification (CVC) / Exhibit H

[MANUAL FILL: Firm Name] acknowledges the requirement to complete and submit the Contractor Vendor Certification (CVC) / Exhibit H on the buyer's official template, signed by an authorized representative, and returned with this proposal.

- Complete every field on the buyer's Exhibit H / CVC form — do not substitute a custom layout.
- [MANUAL FILL: attach signed Exhibit H / CVC PDF with the submission package]
- Do not invent vendor certification numbers, DUNS, or registration IDs.
""",
    "rfp-closing-signature": """## Authorized Signature

By signing below, the undersigned certifies that the information provided in this proposal is accurate and complete, and that the offeror agrees to all terms and conditions of this RFP.

| Field | Response |
| --- | --- |
| Authorized Representative (signature) | [MANUAL FILL: wet/digital signature] |
| Printed Name | [MANUAL FILL: authorized signatory] |
| Title | [MANUAL FILL] |
| Date | [MANUAL FILL] |
| Firm Name | [MANUAL FILL: legal entity name from Section 1] |
""",
}


def looks_truncated_for_fulfill(content: str) -> bool:
    stripped = (content or "").rstrip()
    if not stripped:
        return False
    if _looks_truncated_prose(stripped):
        return True
    if len(stripped) < 60:
        return False
    if stripped.endswith("[") or stripped.endswith("("):
        return True
    if re.search(r"\bas outlined in\s*$", stripped, re.I):
        return True
    if re.search(r"complete and submit\s*$", stripped, re.I):
        return True
    return False


def _closing_template_for_section(section: ProposalSection) -> str | None:
    sid = section.id or ""
    if sid in _CLOSING_MIN_COMPLETE:
        return _CLOSING_MIN_COMPLETE[sid]
    title_cf = (section.title or "").casefold()
    if "vendor certification" in title_cf or "cvc" in title_cf:
        return _CLOSING_MIN_COMPLETE["rfp-closing-cvc"]
    if "authorized signature" in title_cf:
        return _CLOSING_MIN_COMPLETE["rfp-closing-signature"]
    return None


async def _llm_complete_truncated_section(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
) -> str | None:
    if not llm.is_configured():
        return None
    body = section.content or ""
    system = (
        "Complete ONE truncated proposal section. Finish cut-off sentences and lists only.\n"
        "Rules:\n"
        "- Do NOT invent clients, case studies, metrics, or reference contacts.\n"
        "- Do NOT add new Case Study blocks.\n"
        "- Keep existing facts; append the minimum text needed for a complete section.\n"
        'Return JSON: {"content": "full markdown section"}'
    )
    user = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\nSection: {section.title}\n\n"
        f"Truncated draft (complete it):\n{body[-12000:]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=4096,
            temperature=0.15,
        )
        content = str((raw or {}).get("content") or "").strip()
        if content and not looks_truncated_for_fulfill(content):
            return content
    except Exception:  # noqa: BLE001
        return None
    return None


async def repair_truncated_manuscript_sections(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    skip_section_ids: set[str],
    use_llm: bool,
) -> tuple[ProposalDraft, list[str]]:
    logs: list[str] = []
    sections = list(draft.sections)
    changed = False

    for idx, section in enumerate(sections):
        if section.id in skip_section_ids or fulfill_scan_preserves_section(section):
            continue
        body = section.content or ""
        if not looks_truncated_for_fulfill(body):
            continue

        template = _closing_template_for_section(section)
        if template:
            sections[idx] = section.model_copy(update={"content": template, "status": "generated"})
            logs.append(f"Truncation repair: restored complete closing template for “{section.title}”.")
            changed = True
            continue

        if use_llm:
            completed = await _llm_complete_truncated_section(section=section, rfp=rfp)
            if completed and completed != body:
                sections[idx] = section.model_copy(
                    update={"content": completed, "status": "generated"}
                )
                logs.append(f"Truncation repair: completed cut-off section “{section.title}”.")
                changed = True

    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs
