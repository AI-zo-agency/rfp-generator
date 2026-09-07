"""Bio stub + designer-note helpers (Option B — no full bio rewrite / no PDF attach)."""

from __future__ import annotations

import re
from typing import Any

_BIO_PDF_DESIGNER_NOTE_RE = re.compile(
    r"\[DESIGNER\s+NOTE:[^\]]*(?:Insert approved bio PDF|04_Bio_)[^\]]*\]",
    re.IGNORECASE,
)

_COMPANY_IDENTITY_TITLE_RE = re.compile(
    r"(?is)\b(?:"
    r"who\s+we\s+are|"
    r"our\s+promise|"
    r"about\s+(?:us|zö|the\s+(?:firm|company|agency))|"
    r"(?:company|firm|agency)\s+overview|"
    r"company\s+history"
    r")\b"
)

# Title-case function / section words — never a person named "Who We Are".
_PERSON_NAME_STOP_WORDS = frozenset(
    {
        "about",
        "agency",
        "and",
        "approach",
        "are",
        "attachments",
        "budget",
        "business",
        "campaign",
        "case",
        "certifications",
        "city",
        "clients",
        "county",
        "digital",
        "employment",
        "example",
        "examples",
        "economic",
        "government",
        "growth",
        "closing",
        "company",
        "cover",
        "evaluation",
        "firm",
        "for",
        "forms",
        "from",
        "history",
        "identification",
        "information",
        "insurance",
        "letter",
        "media",
        "methodology",
        "municipal",
        "municipality",
        "municapility",
        "of",
        "organizational",
        "our",
        "overview",
        "paid",
        "past",
        "performance",
        "portfolio",
        "pricing",
        "promise",
        "qualifications",
        "references",
        "sample",
        "samples",
        "scope",
        "strategy",
        "strength",
        "structure",
        "studies",
        "study",
        "submission",
        "summaries",
        "summary",
        "team",
        "terms",
        "that",
        "the",
        "this",
        "timeline",
        "to",
        "travel",
        "we",
        "who",
        "with",
        "work",
        "your",
    }
)


def is_company_identity_title(title: str) -> bool:
    """True for Who We Are / About Us / company overview — never a bio tab."""
    return bool(_COMPANY_IDENTITY_TITLE_RE.search(title or ""))


def is_plausible_person_name(label: str) -> bool:
    """First Last (or First Middle Last). Rejects Who We Are / Our Work / Municipality Summaries."""
    raw = (label or "").strip()
    if not raw or is_company_identity_title(raw):
        return False
    if "—" in raw:
        raw = raw.split("—", 1)[1].strip()
    elif " - " in raw:
        raw = raw.split(" - ", 1)[1].strip()
    raw = re.sub(r"^[\d.]+\s*", "", raw).strip()
    if is_company_identity_title(raw):
        return False
    parts = [p for p in raw.split() if p]
    if not (2 <= len(parts) <= 3):
        return False
    if any(p.casefold() in _PERSON_NAME_STOP_WORDS for p in parts):
        return False
    if not all(re.match(r"^[A-Z][a-zA-Z'\-]+$", p) for p in parts):
        return False
    return True

_INLINE_BIO_REQUIRED_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"include\s+(?:resumes?|bios?|curriculum\s+vitae|cvs?)\s+"
    r"(?:in\s+)?(?:the\s+)?(?:proposal|response|submission)\s+body"
    r"|"
    r"(?:resumes?|bios?|cvs?)\s+within\s+(?:the\s+)?page\s+limit"
    r"|"
    r"personnel\s+qualifications?\s+narrative"
    r"|"
    r"(?:curriculum\s+vitae|resumes?|bios?)\s+in\s+(?:the\s+)?response\s+body"
    r"|"
    r"bios?\s+(?:must|shall)\s+be\s+(?:included|provided)\s+inline"
    r")",
)

_ATTACHMENT_BIO_OK_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"attach(?:ed|ment)?\s+(?:resumes?|bios?|cvs?)"
    r"|"
    r"(?:resumes?|bios?|cvs?)\s+(?:as\s+)?(?:an?\s+)?(?:appendix|exhibit|attachment)"
    r"|"
    r"(?:appendix|exhibit)\s+[A-Z0-9]+\s*[—:\-].{0,40}(?:resume|bio|cv)"
    r")",
)


def bio_file_slug(member: str) -> str:
    """04_Bio_RachelRice.pdf style slug from display name."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", (member or "").strip())
    return cleaned or "Unknown"


def expected_bio_pdf_filename(member: str) -> str:
    return f"04_Bio_{bio_file_slug(member)}.pdf"


def bio_filename_matches_member(file_name: str, member: str) -> bool:
    """True when a KB file is this person's 04_Bio, ignoring spaces/underscores.

    Exact lookup uses 04_Bio_LetitiaHopper.pdf. Drive uploads often keep
    04_Bio_Letitia_Hopper.pdf or 04_Bio_Letitia Hopper.pdf — those must still count.
    """
    base = str(file_name or "").strip().split("/")[-1].casefold()
    compact = re.sub(r"[^a-z0-9]+", "", base)
    if not compact.startswith("04bio"):
        return False
    tokens = [
        re.sub(r"[^a-z0-9]+", "", part.casefold())
        for part in (member or "").split()
        if part
    ]
    tokens = [t for t in tokens if len(t) >= 3]
    if len(tokens) < 2:
        return False
    return all(token in compact for token in tokens)


def is_bio_pdf_designer_note(text: str) -> bool:
    """True when a designer-note (or section body) is a bio-PDF insert handoff."""
    return bool(_BIO_PDF_DESIGNER_NOTE_RE.search(text or ""))


def is_bio_stub_section(section_id: str, content: str | None = None) -> bool:
    """True for section-2-bio-* tabs using the stub + PDF designer-note model."""
    if not (section_id or "").startswith("section-2-bio-"):
        return False
    if section_id == "section-2-bio-placeholder":
        return False
    if content is None:
        return True
    return is_bio_pdf_designer_note(content) or not (content or "").strip()


def looks_like_bio_stub_body(content: str) -> bool:
    """True when body is the short Role-on-engagement (+ optional 04_Bio) stub shape."""
    body = (content or "").strip()
    if not body:
        return False
    if "Role on this engagement" not in body:
        return False
    if is_bio_pdf_designer_note(body):
        # Designer-note stubs are short; long narrative + note is a resume dump.
        return len(body.split()) < 80
    # Bare role line with almost no substance (Scan wrongly stubbed a TOC tab).
    return len(body.split()) < 40


def is_section2_bio_id(section_id: str) -> bool:
    sid = section_id or ""
    return sid.startswith("section-2-bio-") and not sid.endswith("placeholder")


def prior_content_for_rewrite(section_id: str, content: str) -> str:
    """Body to show the rewriter. Misplaced bio stubs on non-bio tabs are empty."""
    body = content or ""
    sid = section_id or ""
    if looks_like_bio_stub_body(body) and not is_section2_bio_id(sid):
        return ""
    if is_bio_pdf_designer_note(body) and not is_section2_bio_id(sid):
        return ""
    return body


MISPLACED_BIO_STUB_REWRITE_NOTE = (
    "Current body is a misplaced 04_Bio / Role-on-engagement stub. Discard it. "
    "This tab is not a team bio. Write the RFP ask for THIS section from verified "
    "evidence. Do not keep a bio PDF handoff or Role-on-this-engagement line."
)


def extract_engagement_role(content: str) -> str:
    """One-line Role-on-this-engagement from a bio tab. Drops dumped resume prose."""
    match = re.search(
        r"(?im)^\*\*Role on this engagement:\*\*\s*(.+)$",
        content or "",
    )
    if not match:
        return ""
    role = match.group(1).strip()
    parts = re.split(r"(?<=\w)[.;]\s+", role, maxsplit=1)
    if len(parts) == 2 and 0 < len(parts[0]) <= 80:
        role = parts[0].strip()
    return role[:160]


def skip_inline_bio_expansion(rfp_text: str) -> bool:
    """True when Generate must not fetch/rewrite 04_Bio into the manuscript."""
    return not rfp_requires_inline_bios(rfp_text or "")


def rfp_requires_inline_bios(rfp_text: str) -> bool:
    """Conservative: default False (PDF/attachment OK). True only on clear inline asks."""
    text = rfp_text or ""
    if _ATTACHMENT_BIO_OK_RE.search(text) and not _INLINE_BIO_REQUIRED_RE.search(text):
        return False
    return bool(_INLINE_BIO_REQUIRED_RE.search(text))


def resolve_bio_pdf_filename(member: str, kb_sources: list[str] | None = None) -> str:
    """Prefer actual KB source filename when it looks like 04_Bio_*.pdf."""
    expected = expected_bio_pdf_filename(member)
    for src in kb_sources or []:
        name = str(src).strip().split("/")[-1]
        if re.search(r"04_Bio_.*\.pdf", name, re.I):
            return name
    return expected


def _verbatim_bullets_from_kb(kb_text: str, *, limit: int = 3) -> list[str]:
    """Pull short bullets only from lines that already exist in 04_Bio text."""
    if not (kb_text or "").strip():
        return []
    bullets: list[str] = []
    for raw in kb_text.splitlines():
        line = raw.strip().lstrip("-*•").strip()
        if len(line) < 12 or len(line) > 160:
            continue
        # Skip headings / key-accounts blocks to avoid fabricating narrative.
        if line.startswith("#") or line.casefold().startswith("key account"):
            continue
        if re.match(r"(?i)^(years? of experience|work history|education|licenses?|certifications?)\b", line):
            continue
        # Prefer expertise-looking or employment-looking lines.
        if (
            re.search(r"\d+\s*\+?\s*years?", line, re.I)
            or re.search(r"\b(20\d{2}|19\d{2})\b", line)
            or "|" in raw
        ):
            # Table row → take first cell-ish chunk
            cell = line.split("|")[0].strip() if "|" in line else line
            cell = cell.strip("| ").strip()
            if cell and cell not in bullets and len(cell) >= 8:
                bullets.append(cell)
        if len(bullets) >= limit:
            break
    return bullets[:limit]


def _role_is_just_the_person(role: str, member: str) -> bool:
    """True when the 'role' carries no role information — it is the person again.

    Team selection returns a role per member, and it has returned the KB
    DOCUMENT identity instead of a job title: every one of twelve bios rendered
    "**Role on this engagement:** Bio AlejandroPerez", derived from
    04_Bio_AlejandroPerez.pdf.

    Structural, not a word list: strip everything except letters and digits from
    both strings and ask whether what is left is the person's own name. A real
    title ("Creative Director") shares no such containment; a filename echo
    ("Bio AlejandroPerez", "04_Bio_AlejandroPerez") reduces to exactly the name.
    Nothing here needs to know what job titles look like, so it cannot go stale.
    """
    if not role or not member:
        return False
    def _letters(text: str) -> str:
        return "".join(ch for ch in text.casefold() if ch.isalnum())

    role_key = _letters(role)
    member_key = _letters(member)
    if not role_key or not member_key:
        return False
    if role_key == member_key:
        return True
    # "bioalejandroperez" -> strip the document-word noise the name is wrapped in
    # by checking whether removing the name empties the role of substance.
    remainder = role_key.replace(member_key, "")
    return member_key in role_key and len(remainder) <= 6


def format_bio_stub_content(
    *,
    member: str,
    role: str = "",
    pdf_filename: str | None = None,
    kb_text: str = "",
    kb_available: bool = True,
    inline_required: bool = False,
    title: str = "",
) -> str:
    """Build manuscript bio stub — never invents Key Accounts."""
    pdf = pdf_filename or expected_bio_pdf_filename(member)
    role_line = (role or "").strip()
    if _role_is_just_the_person(role_line, member):
        role_line = ""
    role_line = role_line or "Team member on this engagement"
    parts = [
        f"### {member}",
        f"**Role on this engagement:** {role_line}",
        "",
    ]

    if inline_required and kb_available and (kb_text or title):
        if title.strip():
            parts.append(f"**Title:** {title.strip()}")
            parts.append("")
        bullets = _verbatim_bullets_from_kb(kb_text, limit=3)
        if bullets:
            parts.append("**Verified highlights (from approved bio PDF):**")
            for b in bullets:
                parts.append(f"- {b}")
            parts.append("")

    parts.append(
        f"[DESIGNER NOTE: Insert approved bio PDF — {pdf}. "
        "Do not rewrite Key Accounts or work history in-manuscript.]"
    )

    if not kb_available:
        parts.append("")
        parts.append(
            f"[MANUAL FILL: Ella — approved bio PDF not found for {member}; "
            "attach when available.]"
        )

    return "\n".join(parts).strip() + "\n"


def stub_from_extraction(
    *,
    member: str,
    role: str,
    pdf_filename: str,
    kb_text: str,
    kb_available: bool,
    inline_required: bool,
    extracted: dict[str, Any] | None = None,
) -> str:
    """Convenience for Sections graph — ignores extracted key_accounts entirely."""
    title = ""
    if extracted:
        raw = str(extracted.get("title") or "").strip()
        if raw and not raw.upper().startswith("[VERIFY"):
            title = raw
    return format_bio_stub_content(
        member=member,
        role=role,
        pdf_filename=pdf_filename,
        kb_text=kb_text if inline_required else "",
        kb_available=kb_available,
        inline_required=inline_required,
        title=title if inline_required else "",
    )
