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

_EDU_GENERIC_TOKENS = frozenset(
    {
        "arts",
        "associate",
        "bachelor",
        "college",
        "degree",
        "diploma",
        "earned",
        "education",
        "federal",
        "from",
        "graduated",
        "holds",
        "master",
        "phd",
        "received",
        "science",
        "university",
    }
)

_PERSONAL_EDUCATION_RE = re.compile(
    r"(?is)"
    r"("
    r"[^.!?\n]{0,80}?\b(?:holds?|earned|received|completed|has)\s+"
    r"(?:a\s+|an\s+)?(?:bachelor|master|associate|ph\.?d|mba|degree)\b[^.!?\n]{0,160}[.!]?"
    r"|"
    r"[^.!?\n]{0,50}?\b(?:bachelor|master|associate)\s+(?:of|in|'s)\s+[^.!?\n]{0,140}[.!]?"
    r"|"
    r"[^.!?\n]{0,40}?\b(?:university|college)\s+of\s+[A-Z][^.!?\n]{0,100}[.!]?"
    r")"
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


def person_name_from_tab_title(title: str) -> str:
    """'2.2 — Sonja Anderson' → 'Sonja Anderson'; Who We Are / org tabs → ''."""
    from app.services.proposal_bio_stub import is_plausible_person_name
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section

    candidate = _member_name_from_bio_section(title or "")
    if not candidate or not is_plausible_person_name(candidate):
        return ""
    return candidate


def is_who_we_are_section(section: ProposalSection) -> bool:
    sid = (section.id or "").casefold()
    if sid.startswith("section-1-who-we-are"):
        return True
    from app.services.proposal_bio_stub import is_company_identity_title

    return is_company_identity_title(section.title or "")


def is_our_work_section(section: ProposalSection) -> bool:
    sid = (section.id or "").casefold()
    return sid.startswith("section-3-")


def is_named_person_bio_tab(section: ProposalSection) -> bool:
    """True for Section 2 bio cards, or numbered person cards («2.2 — First Last»).

    Never treat a bare TOC / closing label as a person — that wrongly replaced
    program / application / policy tabs with Role-on-engagement bio stubs.
    """
    sid = section.id or ""
    if sid.startswith("section-1-") or is_who_we_are_section(section):
        return False
    if is_our_work_section(section):
        return False
    if sid.startswith("section-2-bio-") and not sid.endswith("placeholder"):
        return True
    title = section.title or ""
    # Require dotted section number + separator (2.1 — Name), not "Drug-Free …".
    if not re.search(r"(?i)^\s*(?:section\s+)?\d+\.\d+\s*[—\-–:]", title):
        return False
    return bool(person_name_from_tab_title(title))


def repair_misplaced_bio_stub_sections(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Replace Role-on-engagement bio stubs that landed on non–Section-2 tabs."""
    from app.services.proposal_bio_stub import looks_like_bio_stub_body

    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        if is_named_person_bio_tab(section):
            sections.append(section)
            continue
        body = section.content or ""
        if not looks_like_bio_stub_body(body):
            sections.append(section)
            continue
        title = (section.title or "this section").strip()
        replacement = (
            f"## {title}\n\n"
            f"[MANUAL FILL: Draft full «{title}» for this RFP. Cover every scored ask "
            "in the RFP for this tab. A prior pass wrongly replaced this body with a "
            "team-bio stub — rewrite the actual requirement here.]"
        )
        sections.append(
            section.model_copy(update={"content": replacement, "status": "generated"})
        )
        logs.append(
            f"Restored misplaced bio stub on «{title}» — tab is not a Section 2 bio."
        )
        changed = True
    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def is_personnel_bio_section(section: ProposalSection) -> bool:
    sid = (section.id or "").casefold()
    if sid.startswith("section-1-") or is_who_we_are_section(section):
        return False
    if is_our_work_section(section):
        return False
    if is_named_person_bio_tab(section):
        return True
    title = section.title or ""
    # Numbered person cards ("2.1 — Sonja Anderson") only — not bare TOC labels.
    if person_name_from_tab_title(title) and re.search(
        r"(?i)^\s*(?:section\s+)?\d+\.\d+\s*[—\-–:]",
        title,
    ):
        return True
    title_cf = title.casefold()
    return any(hint in title_cf for hint in _PERSONNEL_TITLE_HINTS)


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


_NAME_COMMA_ROLE_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z'\-]+){1,2})\s*,\s+[A-Z]"
)


def named_people_in_section(
    section: ProposalSection,
    *,
    user_message: str = "",
) -> list[str]:
    """Heading names in a personnel / bio tab (First Last).

    Also picks up names the user mentioned (e.g. \"Shawn DiCriscio … resume\"),
    headings like \"### Shawn DiCriscio, Senior Web Developer\", and staffing
    prose like \"Letitia Hopper, Digital Media Strategist, …\".
    """
    from app.services.proposal_bio_stub import is_plausible_person_name
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section

    names: list[str] = []
    skip = {
        "role on this engagement",
        "team qualifications summary",
        "senior web developer",
        "creative director",
        "account director",
    }

    def _add(name: str) -> None:
        cleaned = (name or "").strip()
        if not cleaned or cleaned.casefold() in skip:
            return
        if not is_plausible_person_name(cleaned):
            return
        if cleaned not in names:
            names.append(cleaned)

    sid = section.id or ""
    from_title = person_name_from_tab_title(section.title or "")
    if from_title:
        _add(from_title)
    elif sid.startswith("section-2-bio-") and not sid.endswith("placeholder"):
        member = _member_name_from_bio_section(section.title or "")
        if member:
            _add(member)
    blob = f"{section.title or ''}\n{section.content or ''}"
    for match in _PERSON_HEADING_RE.finditer(blob):
        _add(match.group(1).strip())

    content = section.content or ""
    # Staffing / team paragraphs: "Letitia Hopper, Digital Media Strategist, …"
    # — not a markdown heading, so heading parse misses them.
    for match in _NAME_COMMA_ROLE_RE.finditer(f"{content}\n{user_message or ''}"):
        cand = match.group(1).strip()
        if is_plausible_person_name(cand):
            _add(cand)

    # User named a person — include even when heading parse missed commas/titles.
    msg = (user_message or "").strip()
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
            if name.casefold() in skip:
                continue
            # Message mentions this person (case-insensitive).
            if name.casefold() in msg.casefold():
                _add(name)
            elif (
                name.split()[0].casefold() in msg.casefold()
                and len(name.split()) >= 2
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
                    _add(name)
        for match in _PERSON_NAME_IN_TEXT_RE.finditer(msg):
            cand = match.group(1).strip()
            if is_plausible_person_name(cand) and cand.casefold() in content_cf:
                _add(cand)
            elif is_plausible_person_name(cand):
                _add(cand)
    return names


def named_people_in_text(text: str) -> list[str]:
    """First Last names in a pin / chat ask (not the whole staffing roster)."""
    from app.services.proposal_bio_stub import is_plausible_person_name

    names: list[str] = []
    blob = text or ""
    for match in _NAME_COMMA_ROLE_RE.finditer(blob):
        cand = match.group(1).strip()
        if is_plausible_person_name(cand) and cand not in names:
            names.append(cand)
    for match in _PERSON_NAME_IN_TEXT_RE.finditer(blob):
        cand = match.group(1).strip()
        if is_plausible_person_name(cand) and cand not in names:
            names.append(cand)
    return names


def people_for_bio_pack(
    section: ProposalSection,
    *,
    user_message: str = "",
    excerpt: str = "",
) -> list[str]:
    """People whose 04_Bio to fetch for this turn.

    A pinned Letitia paragraph must not pull Ron/Curt/every teammate on the tab.
    Fall back to the full tab roster only when the ask does not name anyone.
    """
    focus_blob = f"{excerpt or ''}\n{user_message or ''}"
    focused = named_people_in_text(focus_blob)
    if not focused:
        return named_people_in_section(section, user_message=user_message)
    roster = {
        n.casefold()
        for n in named_people_in_section(section, user_message=focus_blob)
    }
    content_cf = f"{section.content or ''}\n{excerpt or ''}".casefold()
    kept: list[str] = []
    for name in focused:
        if name.casefold() in roster or name.casefold() in content_cf:
            if name not in kept:
                kept.append(name)
    return kept or named_people_in_section(section, user_message=user_message)


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


def _kb_education_excerpt(kb_text: str) -> str:
    """First education line already present in 04_Bio — never invent a school."""
    kb = (kb_text or "").strip()
    if not kb:
        return ""
    headed = re.search(
        r"(?is)(?:^|\n)\s*education\s*[:\n]+\s*(.+?)(?:\n\s*\n|\n\s*(?:work history|experience|certif|license)|\Z)",
        kb,
    )
    if headed:
        line = headed.group(1).strip().splitlines()[0].strip(" -•*\t")
        if 8 <= len(line) <= 200:
            return line if line.endswith(".") else f"{line}."
    for sent in re.split(r"(?<=[.!?])\s+", kb):
        if re.search(
            r"(?i)\b(?:associate|bachelor|master|ph\.?d|mba|college|university)\b",
            sent,
        ):
            cleaned = sent.strip()
            if 8 <= len(cleaned) <= 240:
                return cleaned
    return ""


def align_bio_education_deterministically(
    content: str,
    kb_text: str,
    *,
    member: str = "",
) -> tuple[str, list[str]]:
    """Replace invented degree / school sentences with 04_Bio education wording."""
    kb = (kb_text or "").strip()
    if not kb or kb.startswith("(Supermemory"):
        return content, []
    kb_cf = kb.casefold()
    name_tokens = {p.casefold() for p in (member or "").split() if len(p) >= 2}
    logs: list[str] = []
    text = content or ""
    excerpt = _kb_education_excerpt(kb)

    def _repl(match: re.Match[str]) -> str:
        sent = match.group(0).strip()
        distinctive = [
            tok
            for tok in re.findall(r"[A-Za-z]{4,}", sent)
            if tok.casefold() not in _EDU_GENERIC_TOKENS
            and tok.casefold() not in name_tokens
        ]
        if not distinctive:
            return match.group(0)
        if all(tok.casefold() in kb_cf for tok in distinctive):
            return match.group(0)
        logs.append(
            "Bio education ungrounded vs 04_Bio — replaced invented degree/school"
        )
        return excerpt

    new_text = _PERSONAL_EDUCATION_RE.sub(_repl, text)
    new_text = re.sub(r"[ \t]+\n", "\n", new_text)
    new_text = re.sub(r"\n{3,}", "\n\n", new_text)
    new_text = re.sub(r"  +", " ", new_text)
    return new_text, logs


async def align_named_bios_to_kb(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Years + education on named-person tabs must match packed 04_Bio."""
    from app.services.proposal_bio_stub import is_bio_pdf_designer_note
    from app.services.proposal_sections_graph import _fetch_member_bio_kb

    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False

    for section in draft.sections:
        if not is_named_person_bio_tab(section):
            sections.append(section)
            continue
        if is_bio_pdf_designer_note(section.content or ""):
            sections.append(section)
            continue
        member = person_name_from_tab_title(section.title or "")
        if not member:
            from app.services.proposal_kb_fact_checker import _member_name_from_bio_section

            member = _member_name_from_bio_section(section.title or "")
        if not member:
            sections.append(section)
            continue
        try:
            kb_text, _ = await _fetch_member_bio_kb(member)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Bio KB align skipped for %s: %s", member, exc)
            sections.append(section)
            continue
        body = section.content or ""
        body, year_logs = align_bio_years_deterministically(body, kb_text)
        body, edu_logs = align_bio_education_deterministically(
            body, kb_text, member=member
        )
        if year_logs or edu_logs:
            changed = True
            logs.extend(f"{section.title or section.id}: {line}" for line in year_logs + edu_logs)
            sections.append(section.model_copy(update={"content": body}))
        else:
            sections.append(section)

    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


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


async def _lookup_approved_bio_pdf(member: str) -> tuple[str, bool]:
    """Resolve 04_Bio_*.pdf from KB when possible; never invent a different person."""
    from app.services.proposal_bio_stub import (
        expected_bio_pdf_filename,
        resolve_bio_pdf_filename,
    )

    expected = expected_bio_pdf_filename(member)
    try:
        from app.services import supermemory

        if not supermemory.is_configured():
            return expected, True
        from app.services.proposal_sections_graph import _find_member_bio_document

        doc = await _find_member_bio_document(member)
        if not doc:
            return expected, False
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        name = str(meta.get("fileName") or expected)
        return resolve_bio_pdf_filename(member, [name]), True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Bio PDF lookup skipped for %s: %s", member, exc)
        return expected, True


async def ground_bios_to_kb(
    draft: ProposalDraft,
    *,
    rfp_text: str = "",
    rfp_id: str = "",
    use_llm: bool = True,
) -> tuple[ProposalDraft, list[str]]:
    """Every named bio tab → designer-note stub (insert approved 04_Bio PDF).

    Never LLM-rewrite resumes into the manuscript. Capabilities stay in their
    own tabs and are grounded separately. Who We Are / Section 1 identity cards
    are written by the Section 1 brand-voice path — never stubbed as 04_Bio PDFs.
    Our Work / Section 3 case-study cards are never bios, even when the title is
    two Title-Case words (Municipality Summaries).
    """
    del rfp_id, use_llm, rfp_text
    from app.services.proposal_bio_stub import (
        extract_engagement_role,
        format_bio_stub_content,
        is_bio_pdf_designer_note,
        is_plausible_person_name,
    )
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section
    from app.services.proposal_scan_fact_repairs import parse_org_chart_roles

    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    org_roles = parse_org_chart_roles(draft)

    for section in draft.sections:
        if not is_named_person_bio_tab(section):
            sections.append(section)
            continue

        sid = section.id or ""
        member = person_name_from_tab_title(section.title or "")
        if not member and sid.startswith("section-2-bio-") and not sid.endswith("placeholder"):
            member = _member_name_from_bio_section(section.title or "")
        if member and not is_plausible_person_name(member):
            member = ""
        if not member:
            sections.append(section)
            continue

        body = section.content or ""
        if is_bio_pdf_designer_note(body) and "### " in body and "Role on this engagement" in body:
            # Already a clean stub — skip unless leftover resume prose remains.
            if len(_bio_narrative_without_role(body).split()) < 12:
                sections.append(section)
                continue

        role = extract_engagement_role(body)
        if not role:
            role = org_roles.get(member.casefold(), "")
            if not role:
                for name, mapped in org_roles.items():
                    parts = member.casefold().split()
                    if len(parts) >= 2 and parts[0] in name and parts[-1] in name:
                        role = mapped
                        break

        pdf, kb_available = await _lookup_approved_bio_pdf(member)
        stub = format_bio_stub_content(
            member=member,
            role=role,
            pdf_filename=pdf,
            kb_available=kb_available,
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

    if changed:
        draft = draft.model_copy(update={"sections": sections})
    draft, misplaced_logs = repair_misplaced_bio_stub_sections(draft)
    logs.extend(misplaced_logs)
    return draft, logs


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
