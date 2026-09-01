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
from app.services.proposal_draft_structure_stubs import (
    content_looks_like_instructional_checklist,
)
from app.services.proposal_manual_flags import sanitize_bare_bracket_tag_words

logger = logging.getLogger(__name__)

_CONTENT_RISK_FIX_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"content\s+issues?\s+that\s+still\s+matter|"
    r"content\s+(?:gaps?|risks?|issues?)\b|"
    r"unsubstantiat\w+|"
    r"unverif(?:ied|iable)\s+(?:client\s+|technical\s+|capability\s+)?claims?|"
    r"incomplete\s+as\s+content|"
    r"reference\s+list\s+still\s+incomplete|"
    r"fabricated\s+(?:mid[- ]?document\s+)?(?:tagline|positioning|capability|bio)|"
    r"positioning\s+tagline\s+is\s+fabricated|"
    r"past[- ]proven|capability[- ]we[- ](?:can|have)|"
    r"bio\s+(?:inflat\w*|overclaim|years?)|"
    r"inflated?\s+years?|"
    r"ungrounded\s+(?:capability|technical|bio)|"
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
            r"capability|wordpress|past[- ]proven|bio\s+inflat|years?\s+of\s+experience",
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
        "renewals, years, specializations, or client quotes. If KB/evidence "
        "lacks a fact, use [VERIFY: specific field] or remove the overclaim — "
        "do not fabricate a replacement.\n"
        "Bios: REPLACE invented sentences with 04_Bio wording. Never leave a "
        "named person with only a Role line when 04_Bio is provided. Never "
        "insert [E#] markers. Drop empty headers with no body.\n"
        "These rules govern how you write; they are never content. Never write "
        "sentences about submission requirements, pass/fail status, what cannot be "
        "submitted, or what must be verified or confirmed with anyone — apply the "
        "rule silently instead of narrating it. The [VERIFY: ...] tag is the only "
        "trace of a gap; never explain or preface it.\n"
        "Return JSON: {\"content\": \"full markdown\", \"changed\": true/false, "
        "\"notes\": \"one line\"}"
    )
    bio_kb = ""
    from app.services.proposal_capability_bio_grounding import (
        pack_04_bio_kb_for_section,
        section_or_instruction_needs_bio_kb,
    )

    if section_or_instruction_needs_bio_kb(section, instruction):
        try:
            bio_kb = await pack_04_bio_kb_for_section(
                section, user_message=instruction
            )
        except Exception:
            logger.exception("04_Bio pack for content-risk repair failed")
            bio_kb = ""
    # rfp_excerpt/evidence are built once by the caller and identical for every
    # matched job (refs/claims/bio/tagline/exec) this pass repairs — cache them.
    # bio_kb is per-section (fetched above from THIS section/instruction), so it
    # stays in the varying tail along with everything else that changes per call.
    cache_prefix = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\n\n"
        f"RFP excerpt:\n{(rfp_excerpt or '')[:8_000]}\n\n"
        f"KB / evidence (authoritative for claims):\n{(evidence or '')[:14_000]}\n\n"
    )
    user = (
        f"Section: {section.title} (id={section.id})\n\n"
        f"Instruction:\n{instruction}\n\n"
    )
    if bio_kb.strip():
        user += (
            "04_Bio KB (authoritative — every restored bio sentence must come "
            "from here; never invent specialization or years):\n"
            f"{bio_kb[:18_000]}\n\n"
        )
    user += f"Current draft:\n{(section.content or '')[:12_000]}"
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=16000,
            temperature=0.0,
            node_name=node_name,
            rfp_id=rfp.id,
            cache_prefix=cache_prefix,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Content-risk repair failed for %s: %s", section.id, exc)
        return section, False, ""
    if not isinstance(raw, dict):
        return section, False, ""
    content = str(raw.get("content") or "").strip()
    content = sanitize_bare_bracket_tag_words(content)
    if content and content_looks_like_instructional_checklist(content):
        logger.info(
            "%s: rewrite wrote a to-do checklist instead of content — "
            "rejected (%s)",
            node_name,
            section.id,
        )
        return section, False, "rejected: instructional checklist"
    changed = bool(raw.get("changed")) and bool(content)
    if not changed or content == (section.content or "").strip():
        return section, False, str(raw.get("notes") or "")
    if len(content.split()) < 30 and len((section.content or "").split()) > 60:
        return section, False, "refused thin rewrite"
    from app.services.proposal_manuscript import scrub_client_facing_section_artifacts

    content = scrub_client_facing_section_artifacts(content)
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
Scrub UNVERIFIED quantified / past-proven claims that are not supported by KB/evidence:
- Contract length claims (multi-year, five-year, every department) without KB support
- Specific performance metrics / geofencing results without KB support
- 'cataloging over N brand applications' style counts without KB support
- 'comprehensive PR and brand partner for city and stadium authority' overclaims
  unless evidence explicitly supports them
- PAST-PROVEN technical capability language ("we have implemented / integrated /
  delivered …", enterprise permissions, third-party municipal integrations) when
  case studies / bios do not evidence that exact past delivery — rewrite to
  capability-we-can-deliver language OR [VERIFY: substantiate from 03_CS / 04_Bio]
Replace unsupported specifics with honest capability language OR
[VERIFY: substantiate from 03_CS / ClientList — {claim}].
Keep verified relationships that evidence supports. Do not invent replacements.
"""

_BIO_INSTRUCTION = """\
Ground TEAM BIOS to 04_Bio KB only:
- Years of experience must match KB numbers exactly (never inflate 10→12).
- If KB lists skill categories (Management N years, Creative M years), keep that
  breakdown — do not invent a larger total "marketing industry experience".
- Remove government / municipal / enterprise-integration specialization unless
  the KB uses that language.
- NEVER leave a named person with only a Role line. Restore 2–4 sentences from
  that person's packed 04_Bio only (years, tools, markets the KB actually names).
  If 04_Bio is missing for that person, keep Role + [VERIFY: restore bio from 04_Bio].
- Strip [E#] citation markers. Drop empty headers with no body
  (e.g. Team Qualifications Summary).
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
    from app.services.proposal_integrity_guards import apply_reference_content_scrubs
    from app.services.proposal_fulfill_fabrication_guard import (
        repair_fabricated_qualifications,
    )

    result = ContentRiskRepairResult(draft=draft)
    evidence = _evidence_blob(research)
    rfp_excerpt = (rfp_context or "")[:12_000]
    sections = list(draft.sections)
    ask = (user_message or "").casefold()
    locks = research.manuscript_locks if research else None
    primary = (locks.primary_contact_name if locks else "") or ""

    # Always run deterministic fabrication + reference integrity scrub first
    working = draft.model_copy(update={"sections": sections})
    working, fab_logs, _human = repair_fabricated_qualifications(working, research)
    if fab_logs:
        result.logs.extend(fab_logs[:8])
    sections = list(working.sections)
    for idx, section in enumerate(sections):
        title_cf = (section.title or "").casefold()
        if "reference" in title_cf or "qualif" in title_cf:
            body, ref_logs = apply_reference_content_scrubs(
                section.content or "",
                primary_contact_name=primary,
            )
            if ref_logs:
                sections[idx] = section.model_copy(update={"content": body})
                result.logs.extend(f"{section.id}: {line}" for line in ref_logs)
                result.sections_changed.append(section.id)

    # Target sections by role — only when the user message names the risk class.
    # Never blast every bio tab on an empty message (that duplicated Build + Review).
    jobs: list[tuple[int, str, str]] = []
    for idx, section in enumerate(sections):
        if _section_matches(section, "reference", "qualif", "relevant experience"):
            if "reference" in ask or ("content" in ask and "reference" in (section.title or "").casefold()):
                jobs.append((idx, _REF_INSTRUCTION, "chat_content_risk_refs"))
            if any(
                k in ask
                for k in (
                    "claim",
                    "unsubstant",
                    "unverif",
                    "capability",
                    "past-proven",
                    "past proven",
                    "maricopa",
                    "santa clara",
                    "bend",
                    "qualif",
                    "experience",
                )
            ):
                if _section_matches(section, "qualif", "experience", "relevant"):
                    jobs.append((idx, _CLAIM_INSTRUCTION, "chat_content_risk_claims"))
        if _section_matches(
            section,
            "approach",
            "work plan",
            "methodology",
            "schedule",
            "timeline",
            "requirement",
            "experience",
            "technical",
            "capability",
        ):
            if any(
                k in ask
                for k in (
                    "claim",
                    "capability",
                    "wordpress",
                    "past-proven",
                    "past proven",
                    "unsubstant",
                    "unverif",
                    "fabricat",
                )
            ):
                jobs.append((idx, _CLAIM_INSTRUCTION, "chat_content_risk_capability"))
        if (section.id or "").startswith("section-2-bio-") or _section_matches(
            section, "bio", "personnel", "key staff", "team"
        ):
            if any(
                k in ask
                for k in (
                    "bio",
                    "years",
                    "inflat",
                    "fabricat",
                    "unverif",
                )
            ):
                jobs.append((idx, _BIO_INSTRUCTION, "chat_content_risk_bio"))
        if _section_matches(
            section, "approach", "work plan", "methodology", "schedule", "timeline"
        ):
            if any(k in ask for k in ("tagline", "chapter", "positioning", "campaign theme")):
                jobs.append((idx, _TAGLINE_INSTRUCTION, "chat_content_risk_tagline"))
        if _section_matches(section, "executive summary", "exec summary"):
            if any(k in ask for k in ("executive", "criteria", "evaluation")):
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

    working = draft.model_copy(update={"sections": sections})
    from app.services.proposal_capability_bio_grounding import (
        run_capability_bio_grounding,
    )

    grounded = await run_capability_bio_grounding(
        working,
        extra_evidence=evidence,
        rfp_text=rfp_context,
        rfp_id=rfp.id,
        use_llm=True,
    )
    sections = list(grounded.draft.sections)
    for line in grounded.logs:
        result.logs.append(line)
        # Mark changed sections from grounding log titles when possible
        for section in sections:
            title = section.title or ""
            if title and title in line:
                result.sections_changed.append(section.id)

    result.draft = grounded.draft
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
            + "\n\nI scrubbed ungrounded **past-proven capability** claims and "
            "**bio year/specialization** overclaims against case studies / 04_Bio. "
            "Missing facts stay `[VERIFY]` — I did not invent replacements."
        )
    else:
        result.reply = (
            "I reviewed the content-risk asks but found nothing I could safely change "
            "without inventing facts. Point me at a section or paste KB contacts and "
            "I’ll fill references precisely."
        )
    return result
