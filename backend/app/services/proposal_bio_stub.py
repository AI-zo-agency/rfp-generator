"""Bio stub + designer-note helpers (Option B — no full bio rewrite / no PDF attach)."""

from __future__ import annotations

import re
from typing import Any

_BIO_PDF_DESIGNER_NOTE_RE = re.compile(
    r"\[DESIGNER\s+NOTE:[^\]]*(?:Insert approved bio PDF|04_Bio_)[^\]]*\]",
    re.IGNORECASE,
)

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
    role_line = (role or "").strip() or "Team member on this engagement"
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
