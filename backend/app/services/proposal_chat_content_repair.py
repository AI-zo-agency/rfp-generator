"""Chat content-risk repair — fix refs, unsubstantiated claims, taglines, exec meta.

When the user pastes a content-issues audit (or asks to fix those risks), mutate
the draft: complete reference contacts from KB when possible, scrub unverifiable
quantified claims, neutralize fabricated campaign taglines, and soften
evaluation-criteria restatement in the executive summary.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services import llm

logger = logging.getLogger(__name__)

_CONTENT_RISK_FIX_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"content\s+issues?\s+that\s+still\s+matter|"
    r"content\s+(?:gaps?|risks?|issues?)\b|"
    r"unsubstantiat\w+|"
    r"unverif(?:ied|iable)\s+(?:client\s+)?claims?|"
    r"incomplete\s+as\s+content|"
    r"reference\s+list\s+still\s+incomplete|"
    r"fabricated\s+(?:mid[- ]?document\s+)?(?:tagline|positioning)|"
    r"positioning\s+tagline\s+is\s+fabricated|"
    r"(?:fix|change|solve|address|repair|scrub|clean|apply)\s+"
    r"(?:these\s+)?(?:content\s+)?(?:issues?|risks?|claims?|gaps?)|"
    r"must\s+be\s+capabl\w*\s+(?:of\s+)?solv|"
    r"make\s+(?:chat\s+)?(?:fix|solve|address)\s+(?:these|this|it)"
    r")"
)

_NUMBERED_ITEM_RE = re.compile(r"(?m)^\s*\d+[\.\)]\s+\S")


def user_asks_content_risk_repair(user_message: str) -> bool:
    """True when chat should APPLY content-risk fixes (not advisory-only)."""
    text = (user_message or "").strip()
    if not text or len(text) < 40:
        return False
    if _CONTENT_RISK_FIX_RE.search(text):
        return True
    numbered = len(_NUMBERED_ITEM_RE.findall(text))
    topics = sum(
        1
        for pat in (
            r"\breference",
            r"unsubstantiat|unverif|fabricat|\bclaims?\b",
            r"tagline|campaign\s+theme|next\s+chapter",
            r"executive\s+summary|evaluation\s+criteria",
            r"case\s+stud",
        )
        if re.search(pat, text, re.I)
    )
    # Long pasted content audit with 3+ numbered issues covering 2+ topics —
    # treat as an apply request when the user is in chat (they want it solved).
    if numbered >= 3 and topics >= 2 and len(text) > 400:
        return True
    return False


@dataclass
class ContentRiskRepairResult:
    draft: ProposalDraft
    logs: list[str] = field(default_factory=list)
    sections_changed: list[str] = field(default_factory=list)
    reply: str = ""


def _section_matches(section: ProposalSection, *needles: str) -> bool:
    blob = f"{section.title or ''}\n{section.id or ''}".casefold()
    return any(n in blob for n in needles)


def _evidence_blob(research: ProposalResearchCache | None, *, max_chars: int = 24_000) -> str:
    if not research:
        return ""
    parts: list[str] = []
    used = 0
    for item in research.evidence_corpus or []:
        src = getattr(item, "source", None) or getattr(item, "title", None) or ""
        excerpt = getattr(item, "excerpt", None) or getattr(item, "content", None) or ""
        block = f"### {src}\n{excerpt}\n"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


async def _llm_repair_section(
    section: ProposalSection,
    *,
    instruction: str,
    rfp: RfpRecord,
    evidence: str,
    rfp_excerpt: str,
    node_name: str,
) -> tuple[ProposalSection, bool, str]:
    if not llm.is_configured():
        return section, False, ""
    system = (
        "You are a proposal content-risk editor for zö agency.\n"
        "Fix ONLY the issues in the instruction. Keep brand voice.\n"
        "NEVER invent phones, emails, titles, metrics, contract lengths, "
        "renewals, or client quotes. If KB/evidence lacks a fact, use "
        "[VERIFY: specific field] or remove the overclaim — do not fabricate.\n"
        "Return JSON: {\"content\": \"full markdown\", \"changed\": true/false, "
        "\"notes\": \"one line\"}"
    )
    user = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\n"
        f"Section: {section.title} (id={section.id})\n\n"
        f"Instruction:\n{instruction}\n\n"
        f"RFP excerpt:\n{(rfp_excerpt or '')[:8_000]}\n\n"
        f"KB / evidence (authoritative for claims):\n{(evidence or '')[:14_000]}\n\n"
        f"Current draft:\n{(section.content or '')[:12_000]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=4096,
            temperature=0.0,
            node_name=node_name,
            rfp_id=rfp.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Content-risk repair failed for %s: %s", section.id, exc)
        return section, False, ""
    if not isinstance(raw, dict):
        return section, False, ""
    content = str(raw.get("content") or "").strip()
    changed = bool(raw.get("changed")) and bool(content)
    if not changed or content == (section.content or "").strip():
        return section, False, str(raw.get("notes") or "")
    if len(content.split()) < 30 and len((section.content or "").split()) > 60:
        return section, False, "refused thin rewrite"
    return (
        section.model_copy(update={"content": content, "status": "generated"}),
        True,
        str(raw.get("notes") or "content-risk repair"),
    )


_REF_INSTRUCTION = """\
Complete the REFERENCES / qualifications contact package as CONTENT (not formatting):
- Every listed client/org must have: contact name, title, organization, phone, email
  when present in KB/evidence.
- If a contact field is missing from evidence, write
  [VERIFY: distinct reference contact — name, title, org, phone, email from KB]
  for THAT org — do not invent names or reuse sonja@zo.agency / placeholder emails.
- Remove 'available upon request' / 'pre-cleared' language.
- Keep the rest of the section; do not delete legitimate portfolio narrative.
"""

_CLAIM_INSTRUCTION = """\
Scrub UNVERIFIED quantified client claims that are not supported by the KB/evidence:
- Contract length claims (multi-year, five-year, every department) without KB support
- Specific performance metrics / geofencing results without KB support
- 'cataloging over N brand applications' style counts without KB support
- 'comprehensive PR and brand partner for city and stadium authority' overclaims
  unless evidence explicitly supports them
Replace unsupported specifics with honest capability language OR
[VERIFY: substantiate from 03_CS / ClientList — {claim}].
Keep verified relationships that evidence supports. Do not invent replacements.
"""

_TAGLINE_INSTRUCTION = """\
Remove or clearly mark fabricated campaign taglines presented as established:
- Lines like 'Oshkosh: Where Your Next Chapter Begins' (or similar) that appear as
  if already validated creative, when the RFP/KB do not establish them.
Rewrite to frame as a WORKING creative direction / hypothesis for discovery, OR
delete the tagline and describe the creative process without pretending a locked
theme exists. Do not invent a replacement tagline as final.
"""

_EXEC_INSTRUCTION = """\
If this executive summary mainly restates RFP evaluation criteria back at the
evaluator ('this proposal will be assessed on… we structured to address each
criterion'), rewrite to lead with substantive Understanding: client goals,
audiences, constraints, and why zö's approach fits — without telling the
evaluator how to grade. Keep it concise. Do not invent metrics.
"""


async def run_content_risk_repair(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_context: str,
    research: ProposalResearchCache | None,
    user_message: str = "",
) -> ContentRiskRepairResult:
    """Apply multi-section content-risk fixes for chat."""
    from app.services.proposal_consistency_enforcement import (
        scrub_duplicate_reference_emails,
    )
    from app.services.proposal_fulfill_fabrication_guard import (
        repair_fabricated_qualifications,
    )

    result = ContentRiskRepairResult(draft=draft)
    evidence = _evidence_blob(research)
    rfp_excerpt = (rfp_context or "")[:12_000]
    sections = list(draft.sections)
    ask = (user_message or "").casefold()

    # Always run deterministic fabrication + duplicate-email scrub first
    working = draft.model_copy(update={"sections": sections})
    working, fab_logs, _human = repair_fabricated_qualifications(working, research)
    if fab_logs:
        result.logs.extend(fab_logs[:8])
    sections = list(working.sections)
    for idx, section in enumerate(sections):
        title_cf = (section.title or "").casefold()
        if "reference" in title_cf or "qualif" in title_cf:
            body, email_logs = scrub_duplicate_reference_emails(section.content or "")
            if email_logs:
                sections[idx] = section.model_copy(update={"content": body})
                result.logs.extend(f"{section.id}: {line}" for line in email_logs)
                result.sections_changed.append(section.id)

    # Target sections by role
    jobs: list[tuple[int, str, str]] = []
    for idx, section in enumerate(sections):
        if _section_matches(section, "reference", "qualif", "relevant experience"):
            if "reference" in ask or "content" in ask or not ask:
                jobs.append((idx, _REF_INSTRUCTION, "chat_content_risk_refs"))
            if any(
                k in ask
                for k in (
                    "claim",
                    "unsubstant",
                    "unverif",
                    "maricopa",
                    "santa clara",
                    "bend",
                    "content",
                )
            ) or not ask:
                if _section_matches(section, "qualif", "experience", "relevant"):
                    jobs.append((idx, _CLAIM_INSTRUCTION, "chat_content_risk_claims"))
        if _section_matches(
            section, "approach", "work plan", "methodology", "schedule", "timeline"
        ):
            if "tagline" in ask or "chapter" in ask or "positioning" in ask or "content" in ask or not ask:
                jobs.append((idx, _TAGLINE_INSTRUCTION, "chat_content_risk_tagline"))
        if _section_matches(section, "executive summary", "exec summary"):
            if "executive" in ask or "criteria" in ask or "content" in ask or not ask:
                jobs.append((idx, _EXEC_INSTRUCTION, "chat_content_risk_exec"))

    # Deduplicate jobs per section+node (keep first instruction merge for same section)
    seen_keys: set[str] = set()
    merged: dict[int, list[tuple[str, str]]] = {}
    for idx, instruction, node in jobs:
        key = f"{idx}:{node}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.setdefault(idx, []).append((instruction, node))

    for idx, items in merged.items():
        section = sections[idx]
        combined = "\n\n".join(ins for ins, _ in items)
        node = items[0][1]
        updated, changed, notes = await _llm_repair_section(
            section,
            instruction=combined,
            rfp=rfp,
            evidence=evidence,
            rfp_excerpt=rfp_excerpt,
            node_name=node,
        )
        if changed:
            sections[idx] = updated
            result.sections_changed.append(section.id)
            result.logs.append(
                f"{section.id}: content-risk repair applied"
                + (f" — {notes}" if notes else "")
            )

    result.draft = draft.model_copy(update={"sections": sections})
    changed_titles = []
    for sid in dict.fromkeys(result.sections_changed):
        sec = next((s for s in sections if s.id == sid), None)
        if sec:
            changed_titles.append(sec.title or sid)

    if result.sections_changed or result.logs:
        result.reply = (
            "Applied **content-risk repairs** across the manuscript:\n"
            + "\n".join(f"- {line}" for line in result.logs[:14])
            + (
                "\n\nUpdated sections: "
                + ", ".join(f"**{t}**" for t in changed_titles[:8])
                if changed_titles
                else ""
            )
            + "\n\nI did **not** invent contact details or metrics — missing facts "
            "are `[VERIFY]` or removed overclaims. Attach real signed PDFs separately; "
            "confirm any remaining VERIFY against KB before submit."
        )
    else:
        result.reply = (
            "I reviewed the content-risk asks but found nothing I could safely change "
            "without inventing facts. Point me at a section or paste KB contacts and "
            "I’ll fill references precisely."
        )
    return result
