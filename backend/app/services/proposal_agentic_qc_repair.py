"""Agentic manuscript QC repair — LLM rewrites leftover defects (not regex patches).

Complete Scan / Generate can leave:
- Resume tabs showing VERIFY placeholders instead of live §2 bio pointers
- Signed-form claims that contradict unsigned checklists
- Truncated / merged sentence fragments
- Evaluation-criteria numbers cited as if they were RFP document sections

These are fixed by an LLM rewrite with manuscript context (bio TOC, exhibit
status digest, RFP excerpt). Detection only selects which tabs need work —
it never invents replacement prose.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import llm
from app.services.proposal_draft_structure_stubs import (
    content_looks_like_instructional_checklist,
)
from app.services.proposal_edge_case_guards import collect_bio_person_names
from app.services.proposal_manual_flags import sanitize_bare_bracket_tag_words
from app.services.proposal_rfp_excerpt import submission_documents_excerpt

logger = logging.getLogger(__name__)

_MAX_SECTIONS = 10
_MAX_BODY_CHARS = 10_000

_RESUME_TITLE_RE = re.compile(
    r"(?i)\b(?:resumes?|curriculum\s+vitae|\bcv\b|key\s+personnel|"
    r"personnel\s+resumes?|staff\s+resumes?)\b"
)

_QC_FLAG_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "bio_verify_placeholder",
        re.compile(
            r"(?i)incorrectly\s+substituted|manuscript\s+bio\s*§|"
            r"confirm\s+actual\s+RFP\s+section\s+citation"
        ),
    ),
    (
        "fabricated_signed_claim",
        re.compile(
            r"(?i)\b(?:completed,\s*signed,\s*and\s*dated|is\s+included\s+as\s+a\s+"
            r"completed,\s*signed|signed\s+and\s+dated\s+attachment)\b"
        ),
    ),
    (
        "truncated_sentence",
        re.compile(
            r"(?i)Disabled\s*\.(?:\s|$)|dba\s+We\s+have\b|"
            r"See\s+Before\s+the\s+Qualification|"
            r"\bis\s+itself\s+a\s+[A-Za-z-]+\s*\.(?:\s|$)",
            re.M,
        ),
    ),
    (
        "citation_conflation",
        r"(?i)§\s*\d+\s*\([^)]*(?:Price|Exhibit\s+L)[^)]*\)[^.\n]{0,80}"
        r"(?:Accessibility|VPAT|Vendor\s+Accessibility)",
    ),
]


def _compiled_flags() -> list[tuple[str, re.Pattern[str]]]:
    out: list[tuple[str, re.Pattern[str]]] = []
    for name, pat in _QC_FLAG_PATTERNS:
        if isinstance(pat, str):
            out.append((name, re.compile(pat)))
        else:
            out.append((name, pat))
    return out


def section_is_resume_pointer_tab(section: ProposalSection) -> bool:
    title = section.title or ""
    if _RESUME_TITLE_RE.search(title):
        return True
    body = (section.content or "")[:400].casefold()
    return "resumes of key" in body or "key personnel resumes" in body


def exhibit_checklist_has_empty_status(content: str) -> bool:
    """True when an Exhibit|…|Status markdown table has blank Status cells.

    Vendor Supplied Proposal / Required Submittals tables must not ship empty
    Status — every row needs Included / cross-ref / MANUAL FILL / designer-attach.
    """
    body = content or ""
    if "exhibit" not in body.casefold() and "status" not in body.casefold():
        return False
    lines = body.splitlines()
    status_col: int | None = None
    empty_rows = 0
    data_rows = 0
    for raw in lines:
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        # Skip separator rows.
        if all(re.fullmatch(r":?-{3,}:?", c or "") for c in cells):
            continue
        header_cf = " ".join(cells).casefold()
        if status_col is None and "status" in header_cf:
            for i, c in enumerate(cells):
                if "status" in c.casefold():
                    status_col = i
                    break
            continue
        if status_col is None:
            continue
        if status_col >= len(cells):
            continue
        # Only count rows that look like exhibit / form / package items.
        row_cf = " ".join(cells).casefold()
        if not any(
            tok in row_cf
            for tok in ("exhibit", "vendor", "proposal", "form", "declaration", "pricing")
        ):
            continue
        data_rows += 1
        if not (cells[status_col] or "").strip():
            empty_rows += 1
    return data_rows >= 2 and empty_rows >= 1


def detect_qc_defect_reasons(section: ProposalSection) -> list[str]:
    """Which QC defects appear — selection only, not a fix."""
    body = section.content or ""
    if not body.strip():
        return []
    reasons: list[str] = []
    for name, pat in _compiled_flags():
        if pat.search(body):
            reasons.append(name)
    if (
        section_is_resume_pointer_tab(section)
        and "[VERIFY:" in body
        and "manuscript bio" in body.casefold()
        and "bio_verify_placeholder" not in reasons
    ):
        reasons.append("bio_verify_placeholder")
    if exhibit_checklist_has_empty_status(body):
        reasons.append("empty_exhibit_status")
    return reasons


def build_bio_toc_lines(draft: ProposalDraft) -> list[str]:
    """Live manuscript bio marks for the agent — never invent people."""
    names = collect_bio_person_names(draft)
    # Prefer sidebar order: walk sections.
    lines: list[str] = []
    seen: set[str] = set()
    for section in draft.sections:
        sid = section.id or ""
        title = (section.title or "").strip()
        if not (
            sid.startswith("section-2-bio")
            or re.match(r"^\s*2\.\d+", title)
        ):
            continue
        match = re.match(r"^\s*(\d+\.\d+)\s*[.:—–\-)\]]\s*(.+)$", title)
        if not match:
            continue
        mark, rest = match.group(1), match.group(2)
        name = re.split(r"[—–,|(/]", rest, maxsplit=1)[0].strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        lines.append(f"§{mark} — {name}")
    if not lines and names:
        for name_cf, mark in sorted(names.items(), key=lambda x: x[1]):
            display = " ".join(w.capitalize() for w in name_cf.split())
            lines.append(f"§{mark} — {display}")
    return lines


def build_exhibit_status_digest(draft: ProposalDraft) -> str:
    """Short digest so the agent does not claim forms are signed when they are not."""
    rows: list[str] = []
    for section in draft.sections:
        title = (section.title or "").strip()
        title_cf = title.casefold()
        sid = section.id or ""
        if not (
            "exhibit" in title_cf
            or sid.startswith("rfp-closing-")
            or "certification" in title_cf
        ):
            continue
        body = section.content or ""
        body_cf = body.casefold()
        if "[manual fill" in body_cf or "[designer note" in body_cf:
            status = "unsigned / MANUAL FILL / designer-attach — NOT signed"
        elif re.search(r"(?i)☐|\[\s*\]", body) and "signed" not in body_cf:
            status = "checklist blank / unsigned"
        elif re.search(r"(?i)\bsigned\b", body) and "[manual fill" not in body_cf:
            status = "claims signed in this tab"
        else:
            status = "present — treat signature as unverified unless clearly signed"
        rows.append(f"- {title}: {status}")
    return "\n".join(rows[:40]) if rows else "(no exhibit/closing tabs found)"


async def _agent_rewrite_section(
    *,
    section: ProposalSection,
    reasons: list[str],
    bio_toc: list[str],
    exhibit_digest: str,
    rfp_excerpt: str,
) -> str | None:
    if not llm.is_configured():
        return None
    title = section.title or section.id
    body = (section.content or "")[:_MAX_BODY_CHARS]
    bio_block = "\n".join(f"- {line}" for line in bio_toc) or "(no Section 2 bios in draft)"
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a senior proposal QC editor for zö agency.\n"
                        "Rewrite ONE manuscript section to fix the listed QC defects.\n"
                        "Rules:\n"
                        "1. Resume / key-personnel pointer tabs: replace VERIFY / "
                        "'incorrectly substituted' placeholders with real cross-refs "
                        "to the LIVE bio TOC (e.g. See §2.1 — Sonja Anderson). "
                        "Never leave the VERIFY flag as readable content. Never invent "
                        "people not in the bio TOC.\n"
                        "2. Never claim an exhibit/form is completed, signed, or dated "
                        "when the exhibit status digest says MANUAL FILL / unsigned. "
                        "Rewrite to cross-ref + [MANUAL FILL] / [DESIGNER NOTE: Attach "
                        "signed PDF] instead.\n"
                        "3. Repair truncated or merged sentence fragments into grammatical "
                        "prose. Do not invent certifications (e.g. complete "
                        "'Disabled Veteran Business Enterprise' only if that is clearly "
                        "what the checkbox was saying — otherwise MANUAL FILL).\n"
                        "4. Citations: use THIS RFP's document section / exhibit labels "
                        "from the RFP excerpt — do not conflate evaluation-criteria "
                        "numbers with document section numbers (e.g. do not cite Price "
                        "Exhibit L as Accessibility §9).\n"
                        "5. Exhibit / Vendor Supplied Proposal Status tables: NEVER leave "
                        "Status cells blank. For each empty Status, fill from the exhibit "
                        "status digest — e.g. 'Included — see Exhibit D tab; "
                        "[MANUAL FILL: attach signed PDF]' or 'Submitted as separate "
                        "electronic file' when that is true. NEVER invent 'Signed' / "
                        "'Completed' when the digest says MANUAL FILL / unsigned.\n"
                        "6. Keep grounded facts. No invented phones, rates, carriers, "
                        "or signatures.\n"
                        "7. Return JSON: {\"content\": \"full revised markdown\", "
                        "\"notes\": \"short\"}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Section title: {title}\n"
                        f"Defects to fix: {', '.join(reasons)}\n\n"
                        f"CURRENT SECTION BODY:\n{body}"
                    ),
                },
            ],
            max_tokens=16000,
            temperature=0.1,
            tier="heavy",
            node_name=f"agentic_qc_repair:{(section.id or 'section')[:48]}",
            # bio_toc/exhibit_digest/rfp_excerpt are identical for every flagged
            # section this pass rewrites (up to _MAX_SECTIONS=10 per run) — cache
            # them instead of resending unchanged on each section's call.
            cache_prefix=(
                f"LIVE BIO TOC (manuscript Section 2):\n{bio_block}\n\n"
                f"EXHIBIT / CLOSING STATUS DIGEST:\n{exhibit_digest}\n\n"
                f"RFP excerpt (document structure):\n{rfp_excerpt[:14000]}\n\n"
            ),
        )
        content = str((raw or {}).get("content") or "").strip()
        content = sanitize_bare_bracket_tag_words(content)
        if content and content_looks_like_instructional_checklist(content):
            logger.warning(
                "Agentic QC rewrite for %s wrote a to-do checklist instead of "
                "content — rejected",
                section.id or title,
            )
            return None
        return content or None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Agentic QC rewrite failed for %s: %s", section.id or title, exc
        )
        return None


async def run_agentic_manuscript_qc_repair(
    draft: ProposalDraft,
    *,
    rfp_text: str = "",
    max_sections: int = _MAX_SECTIONS,
) -> tuple[ProposalDraft, list[str]]:
    """LLM-fix QC leftovers on flagged tabs only (cost-bounded)."""
    if not llm.is_configured():
        return draft, []

    bio_toc = build_bio_toc_lines(draft)
    exhibit_digest = build_exhibit_status_digest(draft)
    rfp_excerpt = submission_documents_excerpt(rfp_text) or (rfp_text or "")[:20000]

    flagged: list[tuple[ProposalSection, list[str]]] = []
    for section in draft.sections:
        reasons = detect_qc_defect_reasons(section)
        if reasons:
            flagged.append((section, reasons))
        if len(flagged) >= max_sections:
            break

    if not flagged:
        return draft, []

    logs: list[str] = []
    by_id = {s.id: i for i, s in enumerate(draft.sections)}
    sections = list(draft.sections)

    for section, reasons in flagged:
        rewritten = await _agent_rewrite_section(
            section=section,
            reasons=reasons,
            bio_toc=bio_toc,
            exhibit_digest=exhibit_digest,
            rfp_excerpt=rfp_excerpt,
        )
        if not rewritten or rewritten.strip() == (section.content or "").strip():
            continue
        # Refuse destructive wipe of a long section.
        if len(rewritten.split()) < 20 and len((section.content or "").split()) > 80:
            logs.append(
                f"{section.title or section.id}: agentic QC refused thin rewrite"
            )
            continue
        idx = by_id.get(section.id)
        if idx is None:
            continue
        sections[idx] = section.model_copy(update={"content": rewritten})
        logs.append(
            f"{section.title or section.id}: agentic QC repaired "
            f"({', '.join(reasons)})"
        )

    if not logs:
        return draft, []
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs
