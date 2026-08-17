"""Ground past-proven capability claims + bio years/specializations to evidence.

Principles (every RFP):
1. Past-tense "we have implemented / integrated / delivered …" must be evidenced
   by manuscript case studies, bios, or KB corpus — otherwise rewrite to
   can-deliver language or [VERIFY]. Never leave checkable past delivery invented.
2. Bio years and sector specializations must match 04_Bio KB phrasing — never
   inflate 10→12 years or add government/municipal specialization the bio PDF
   does not state.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import llm

logger = logging.getLogger(__name__)

_PAST_PROVEN_CLAIM_RE = re.compile(
    r"(?is)\b(?:"
    r"we\s+have\s+(?:implemented|integrated|delivered|built|deployed|completed|"
    r"managed|launched|migrated)|"
    r"(?:have|has)\s+(?:implemented|integrated|delivered|built|deployed)|"
    r"proven\s+(?:track\s+record|experience)\s+(?:with|in|delivering)|"
    r"experience\s+(?:delivering|implementing|integrating)\b|"
    r"installations?\s+with\b|"
    r"integrated\s+[a-z0-9].{0,40}\s+with\s+(?:third[- ]party|procurement|hr\b)"
    r")"
)

_YEARS_CLAIM_RE = re.compile(
    r"(?i)\b(\d{1,2})\s*\+?\s*years?\s+(?:of\s+)?"
    r"(?:[\w/&-]+\s+){0,6}"
    r"(?:experience|expertise)\b"
)

_YEARS_SIMPLE_RE = re.compile(r"(?i)\b(\d{1,2})\s*\+?\s*years?\b")

# Specialization / sector claims that must appear in the bio KB to stay.
_BIO_SPECIALIZATION_RE = re.compile(
    r"(?is)\b(?:"
    r"government|municipal|public[- ]sector|enterprise\s+system\s+integration|"
    r"accessibility\s+compliance|role[- ]based\s+permissions|"
    r"granular\s+user\s+permissions|procurement\s+systems?|"
    r"agenda\s+management|hr\s+portals?"
    r")\b"
)

_CAPABILITY_SYSTEM = """You fix ONE proposal section that asserts past-proven technical
capabilities without evidence in the proof corpus.

Rules:
- If the section says the agency HAS implemented / integrated / delivered a specific
  technical capability, but the PROOF CORPUS (case studies, bios, companyfacts) does
  not evidence that exact past delivery, rewrite to honest language:
  "We can deliver…" / "Our approach includes…" / capability-we-can-provide —
  OR [VERIFY: substantiate past delivery from 03_CS / 04_Bio — {claim}].
- Do NOT invent case studies, clients, or metrics to "support" the claim.
- Keep RFP requirement rows that are real asks; only fix the zö Experience /
  response side that overclaims past work.
- Preserve section structure (tables OK). Return full markdown.
Return JSON: {"content": "...", "changed": true/false, "notes": "one line"}
"""

_BIO_GROUND_SYSTEM = """You fix ONE team bio tab. Default is a designer-note stub,
not a rewritten resume.

Rules:
- Unless the user/RFP requires inline resumes, keep ### Name, **Role on this
  engagement** (one line), and [DESIGNER NOTE: Insert approved bio PDF — 04_Bio_*.pdf].
  Do NOT paste Work History, Key Accounts, years tables, or 04_Bio PDF body.
- Years / specialization: never inflate or invent. If any prose remains, numbers
  must match 04_Bio exactly.
- Keep **Role on this engagement** if present. Do not invent clients, awards, or
  citation markers like [E3].
Return JSON: {"content": "...", "changed": true/false, "notes": "one line"}
"""

_PERSONNEL_SECTION_SYSTEM = """You fix a TEAM / PERSONNEL section that contains
several named people. Each person's narrative must match THAT person's 04_Bio KB.

Rules:
- Keep Role-on-this-engagement lines.
- If a person has no supporting paragraph, ADD 2–4 sentences from their 04_Bio only.
  If that person's KB is missing, keep Role + [VERIFY: restore bio from 04_Bio].
- Never invent government/municipal/enterprise-integration specialization unless
  that person's KB uses those words. Never invent years, markets, or tool counts.
- Years must match that person's KB numbers exactly.
- Strip internal citation markers like [E3] or [E3, E4].
- Remove empty headers with no body (e.g. Team Qualifications Summary with nothing under them).
- Do not invent facts that are not in the packed 04_Bio KB.
Return JSON: {"content": "...", "changed": true/false, "notes": "one line"}
"""

_PERSONNEL_TITLE_HINTS = (
    "experience of personnel",
    "key personnel",
    "team qualification",
    "personnel qualification",
    "proposed personnel",
    "staffing plan",
    "project team",
    "team bios",
    "resumes",
)

_PERSON_HEADING_RE = re.compile(
    r"(?m)^(?:#{1,4}\s+|\*\*)"
    r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z'\-]+){1,3})"
    r"(?:\s*[,:(—–-].*)?"
    r"(?:\*\*)?\s*$"
)

# First Last in free text (user ask), case-insensitive.
_PERSON_NAME_IN_TEXT_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z'\-]+){1,3})\b"
)


def is_personnel_bio_section(section: ProposalSection) -> bool:
    sid = (section.id or "").casefold()
    if sid.startswith("section-2-bio-") and not sid.endswith("placeholder"):
        return True
    title = (section.title or "").casefold()
    return any(hint in title for hint in _PERSONNEL_TITLE_HINTS)


def section_or_instruction_needs_bio_kb(
    section: ProposalSection,
    instruction: str = "",
) -> bool:
    if is_personnel_bio_section(section):
        return True
    blob = f"{instruction}\n{section.title or ''}".casefold()
    return any(
        token in blob
        for token in (
            "04_bio",
            "bio ",
            "bios",
            "specializ",
            "years of experience",
            "personnel",
            "inflat",
        )
    )


def named_people_in_section(
    section: ProposalSection,
    *,
    user_message: str = "",
) -> list[str]:
    """Heading names in a personnel / bio tab (First Last).

    Also picks up names the user mentioned (e.g. \"Shawn DiCriscio … resume\")
    and headings like \"### Shawn DiCriscio, Senior Web Developer\".
    """
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section

    names: list[str] = []
    sid = section.id or ""
    if sid.startswith("section-2-bio-") and not sid.endswith("placeholder"):
        member = _member_name_from_bio_section(section.title or "")
        if member:
            names.append(member)
    blob = f"{section.title or ''}\n{section.content or ''}"
    for match in _PERSON_HEADING_RE.finditer(blob):
        name = match.group(1).strip()
        if name.casefold() in {
            "role on this engagement",
            "team qualifications summary",
            "senior web developer",
            "creative director",
            "account director",
        }:
            continue
        if name not in names:
            names.append(name)

    # User named a person — include even when heading parse missed commas/titles.
    msg = (user_message or "").strip()
    content = section.content or ""
    if msg and content:
        content_cf = content.casefold()
        # Prefer people already present in the draft body (any heading style).
        for match in re.finditer(
            r"(?m)^(?:#{1,4}\s+|\*\*)?\s*"
            r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z'\-]+){1,3})"
            r"(?:\s*[,:(—–-].*)?$",
            content,
        ):
            name = match.group(1).strip()
            if name.casefold() in {
                "role on this engagement",
                "team qualifications summary",
            }:
                continue
            # Message mentions this person (case-insensitive).
            if name.casefold() in msg.casefold() and name not in names:
                names.append(name)
            elif (
                name.split()[0].casefold() in msg.casefold()
                and len(name.split()) >= 2
                and name not in names
                and name.casefold() in content_cf
            ):
                # "shawn" alone → Shawn DiCriscio when unique in section
                first = name.split()[0].casefold()
                first_hits = [
                    n
                    for n in re.findall(
                        rf"(?i)\b{re.escape(first)}\s+[A-Z][a-zA-Z'\-]+",
                        content,
                    )
                ]
                if len({h.casefold() for h in first_hits}) == 1:
                    names.append(name)
    return names


def bio_block_is_role_only(content: str, member: str) -> bool:
    """True when this person's block has a Role line and no supporting paragraph."""
    body = content or ""
    cf = member.casefold()
    idx = body.casefold().find(cf)
    if idx < 0:
        narrative = _bio_narrative_without_role(body)
        return len(narrative.split()) < 12
    chunk = body[idx : idx + 1200]
    next_heads = list(
        re.finditer(r"(?m)^(?:#{1,4}\s+|\*\*)[A-Z]", chunk[len(member) + 8 :])
    )
    if next_heads:
        chunk = chunk[: len(member) + 8 + next_heads[0].start()]
    narrative = _bio_narrative_without_role(chunk)
    return len(narrative.split()) < 12


def _bio_narrative_without_role(text: str) -> str:
    kept: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"(?i)\**role on this engagement", stripped):
            continue
        if re.match(r"(?i)\[(?:DESIGNER NOTE|MANUAL FILL|VERIFY)", stripped):
            continue
        kept.append(re.sub(r"\*+", "", stripped))
    return " ".join(kept)


@dataclass
class CapabilityBioGroundingResult:
    draft: ProposalDraft
    logs: list[str] = field(default_factory=list)
    capability_fixes: int = 0
    bio_fixes: int = 0


def manuscript_proof_digest(draft: ProposalDraft, *, max_chars: int = 28_000) -> str:
    """Case studies + bios + company identity — what past delivery can cite."""
    parts: list[str] = []
    used = 0
    for section in draft.sections:
        sid = section.id or ""
        title = section.title or sid
        body = (section.content or "").strip()
        if not body:
            continue
        keep = (
            sid.startswith("section-3-work-")
            or sid.startswith("section-2-bio-")
            or sid.startswith("section-1-")
            or "case stud" in title.casefold()
            or "our work" in title.casefold()
        )
        if not keep:
            continue
        block = f"### {title} ({sid})\n{body[:3500]}\n"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def section_asserts_past_proven_capability(content: str) -> bool:
    return bool(_PAST_PROVEN_CLAIM_RE.search(content or ""))


def kb_year_values(kb_text: str) -> set[int]:
    return {int(m.group(1)) for m in _YEARS_SIMPLE_RE.finditer(kb_text or "")}


def draft_year_claims(content: str) -> list[int]:
    return [int(m.group(1)) for m in _YEARS_CLAIM_RE.finditer(content or "")]


def bio_years_inflated_vs_kb(content: str, kb_text: str) -> bool:
    """True when draft claims years not present in the bio KB."""
    kb_years = kb_year_values(kb_text)
    if not kb_years:
        return False
    for years in draft_year_claims(content):
        if years not in kb_years:
            return True
    return False


def bio_adds_ungrounded_specialization(content: str, kb_text: str) -> bool:
    """True when draft adds sector/tech specialization absent from KB."""
    kb_cf = (kb_text or "").casefold()
    if not kb_cf.strip() or len(kb_cf) < 80:
        return bool(_BIO_SPECIALIZATION_RE.search(content or ""))
    for match in _BIO_SPECIALIZATION_RE.finditer(content or ""):
        token = match.group(0).casefold()
        if token not in kb_cf:
            return True
    return False


def align_bio_years_deterministically(
    content: str,
    kb_text: str,
) -> tuple[str, list[str]]:
    """Replace inflated year claims with the nearest KB year ≤ the draft claim."""
    kb_years = sorted(kb_year_values(kb_text))
    if not kb_years:
        return content, []
    logs: list[str] = []
    text = content or ""

    def _repl(match: re.Match[str]) -> str:
        claimed = int(match.group(1))
        if claimed in kb_years:
            return match.group(0)
        candidates = [y for y in kb_years if y <= claimed]
        if not candidates:
            return match.group(0)
        fixed = max(candidates)
        if fixed == claimed:
            return match.group(0)
        logs.append(f"Bio years {claimed} → {fixed} (04_Bio KB)")
        return re.sub(rf"\b{claimed}\b", str(fixed), match.group(0), count=1)

    new_text = _YEARS_CLAIM_RE.sub(_repl, text)
    return new_text, logs


async def _llm_rewrite(
    section: ProposalSection,
    *,
    system: str,
    user: str,
    node_name: str,
    rfp_id: str = "",
) -> tuple[ProposalSection, bool, str]:
    if not llm.is_configured():
        return section, False, ""
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=4096,
            temperature=0.0,
            node_name=node_name,
            rfp_id=rfp_id or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s failed for %s: %s", node_name, section.id, exc)
        return section, False, ""
    if not isinstance(raw, dict):
        return section, False, ""
    content = str(raw.get("content") or "").strip()
    changed = bool(raw.get("changed")) and bool(content)
    if not changed or content == (section.content or "").strip():
        return section, False, str(raw.get("notes") or "")
    if len(content.split()) < 25 and len((section.content or "").split()) > 60:
        return section, False, "refused thin rewrite"
    return (
        section.model_copy(update={"content": content, "status": "generated"}),
        True,
        str(raw.get("notes") or "grounded rewrite"),
    )


async def scrub_ungrounded_past_capability_claims(
    draft: ProposalDraft,
    *,
    extra_evidence: str = "",
    rfp_id: str = "",
    use_llm: bool = True,
) -> tuple[ProposalDraft, list[str]]:
    """Rewrite past-proven capability claims not supported by manuscript proof."""
    if not use_llm or not llm.is_configured():
        return draft, []

    proof = manuscript_proof_digest(draft)
    if extra_evidence.strip():
        proof = f"{proof}\n\n### Extra evidence\n{extra_evidence[:12_000]}"
    if len(proof.strip()) < 80:
        proof = "(No case-study/bio proof corpus — treat past-delivery claims as unverified.)"

    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False

    for section in draft.sections:
        body = section.content or ""
        sid = section.id or ""
        if sid.startswith("section-2-bio-"):
            sections.append(section)
            continue
        if not section_asserts_past_proven_capability(body):
            sections.append(section)
            continue
        user = (
            f"PROOF CORPUS (only cite past delivery if supported here):\n{proof[:20_000]}\n\n"
            f"SECTION TO FIX: {section.title} (id={sid})\n\n{body[:12_000]}"
        )
        updated, did, notes = await _llm_rewrite(
            section,
            system=_CAPABILITY_SYSTEM,
            user=user,
            node_name=f"capability_past_proven_scrub:{sid}",
            rfp_id=rfp_id,
        )
        if did:
            changed = True
            sections.append(updated)
            logs.append(
                f"{section.title or sid}: scrubbed ungrounded past-proven capability "
                f"claims → can-deliver / VERIFY"
                + (f" ({notes})" if notes else "")
            )
        else:
            sections.append(section)

    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


async def pack_04_bio_kb_for_section(
    section: ProposalSection,
    *,
    user_message: str = "",
) -> str:
    """Fetch 04_Bio text for every named person in this tab."""
    from app.services.proposal_sections_graph import _fetch_member_bio_kb

    parts: list[str] = []
    people = named_people_in_section(section, user_message=user_message)
    if not people:
        logger.info(
            "pack_04_bio: no named people parsed for section=%s title=%r",
            section.id,
            (section.title or "")[:60],
        )
    for member in people:
        try:
            kb_text, sources = await _fetch_member_bio_kb(member)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Bio KB fetch failed for %s: %s", member, exc)
            continue
        if not (kb_text or "").strip() or kb_text.startswith("(Supermemory"):
            continue
        src = ", ".join(sources[:3]) if sources else "04_Bio"
        parts.append(f"### {member} ({src})\n{kb_text[:8000]}\n")
    return "\n".join(parts)


async def ground_bios_to_kb(
    draft: ProposalDraft,
    *,
    rfp_text: str = "",
    rfp_id: str = "",
    use_llm: bool = True,
) -> tuple[ProposalDraft, list[str]]:
    """Section 2 bios → designer-note stub. Never LLM-rewrite 04_Bio into the manuscript."""
    del rfp_id, use_llm  # bios are stub-only; capability LLM is a separate pass
    from app.services.proposal_bio_stub import (
        expected_bio_pdf_filename,
        extract_engagement_role,
        format_bio_stub_content,
        skip_inline_bio_expansion,
    )
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section

    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    if not skip_inline_bio_expansion(rfp_text):
        return draft, logs

    for section in draft.sections:
        sid = section.id or ""
        if not sid.startswith("section-2-bio-") or sid.endswith("placeholder"):
            sections.append(section)
            continue

        member = _member_name_from_bio_section(section.title or "")
        if not member:
            sections.append(section)
            continue

        body = section.content or ""
        pdf = expected_bio_pdf_filename(member)
        stub = format_bio_stub_content(
            member=member,
            role=extract_engagement_role(body),
            pdf_filename=pdf,
            kb_available=True,
            inline_required=False,
        )
        if stub.strip() != body.strip():
            changed = True
            logs.append(
                f"{section.title or sid}: designer-note stub ({pdf}) — no LLM bio rewrite"
            )
            sections.append(section.model_copy(update={"content": stub}))
        else:
            sections.append(section)

    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


async def persist_collapsed_bio_stubs(
    rfp_id: str,
    *,
    draft: ProposalDraft | None = None,
    rfp_text: str = "",
) -> tuple[ProposalDraft | None, list[str]]:
    """Cheap no-LLM collapse of Section 2 resume dumps → designer-note stubs.

    Safe on Continue Proposal / phase start / pipeline end. Does not fetch 04_Bio.
    """
    from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft

    current = draft
    if current is None:
        current = await aget_proposal_draft(rfp_id)
    if current is None:
        return None, []
    text = rfp_text or ""
    if not text.strip():
        try:
            from app.services.proposal_common import load_rfp_for_proposal

            text = load_rfp_for_proposal(rfp_id)[2] or ""
        except Exception:  # noqa: BLE001
            text = ""
    updated, logs = await ground_bios_to_kb(current, rfp_text=text, use_llm=False)
    if logs:
        await asave_proposal_draft(updated)
    return updated, logs


async def run_capability_bio_grounding(
    draft: ProposalDraft,
    *,
    extra_evidence: str = "",
    rfp_text: str = "",
    rfp_id: str = "",
    use_llm: bool = True,
) -> CapabilityBioGroundingResult:
    """Full pass: capability past-proven scrub + bio KB grounding."""
    result = CapabilityBioGroundingResult(draft=draft)
    draft, cap_logs = await scrub_ungrounded_past_capability_claims(
        draft,
        extra_evidence=extra_evidence,
        rfp_id=rfp_id,
        use_llm=use_llm,
    )
    result.logs.extend(cap_logs)
    result.capability_fixes = len(cap_logs)

    draft, bio_logs = await ground_bios_to_kb(
        draft,
        rfp_text=rfp_text,
        rfp_id=rfp_id,
        use_llm=use_llm,
    )
    result.logs.extend(bio_logs)
    result.bio_fixes = len(bio_logs)
    result.draft = draft
    return result
