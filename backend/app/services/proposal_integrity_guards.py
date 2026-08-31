"""Hard integrity guards — references, pricing tier, case-study fidelity, bio typos.

These run after generation so RFP-prohibited patterns cannot ship even when the LLM
slips (e.g. "available upon request", Average tier at 35% cost weight, genericized CS).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.proposal import (
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_manuscript import strip_internal_flag_tags

logger = logging.getLogger(__name__)

_UPON_REQUEST_RE = re.compile(
    r"(?is)"
    r"(?:"
    # Only the withholding pattern on a named reference row — NOT
    # "Additional references … available on request" (often allowed / intentional).
    r"(?:reference\s+)?contact\s+(?:details?|information|info)\s+"
    r"(?:are\s+|is\s+)?(?:available\s+)?(?:upon|on)\s+request"
    r"|"
    r"contacts?\s+(?:upon|on)\s+request"
    r")",
)

_PRECLEARED_CLAIM_RE = re.compile(
    r"(?is)"
    r"[^.!?\n]*\b(?:"
    r"pre-?cleared|"
    r"have\s+agreed\s+to\s+respond|"
    r"agreed\s+to\s+(?:direct\s+)?(?:reference\s+)?(?:checks?|contact)|"
    r"each\s+has\s+agreed\s+to\s+respond"
    r")\b[^.!?\n]*[.!?]?",
)

_VERIFY_CONTACT = (
    "[VERIFY: reference contact — name, title, organization, phone, email from KB]"
)

_INCOMPLETE_REF_VERIFY_RE = re.compile(
    r"\[VERIFY:\s*(?:"
    r"contact|"
    r"phone|"
    r"email|"
    r"distinct\s+reference\s+contact|"
    r"reference\s+contact|"
    r"client-side\s+reference"
    r")[^\]]*\]",
    re.I,
)

_REFERENCE_BLOCK_SPLIT_RE = re.compile(
    r"(?=(?:^|\n)\s*(?:#{1,3}\s*)?(?:Reference\s+\d+\s*[:.—–-]|"
    r"\*\*Reference\s+\d+|"
    r"Reference\s+\d+\b))",
    re.I,
)

_REAL_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.I,
)
_REAL_PHONE_RE = re.compile(
    r"(?<!\[VERIFY:)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
_AGENCY_PLACEHOLDER_EMAIL_RE = re.compile(
    r"(?i)\b(?:sonja|ella|info|hello|connect)@zo\.agency\b"
)

_AGENCY_AS_CLIENT_LINE_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"sonja\s+anderson|"
    r"agency\s+director|"
    r"\bzö\s+agency\b|\bzo\s+agency\b|"
    r"connect@zo\.agency"
    r")"
)

_REFERENCE_CONTACT_LINE_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"\breference\s+\d+\b|"
    r"@|\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b|"
    r"\bcontact\s*:|"
    r"agency\s+director|"
    r"communications\s+director|"
    r"project\s+manager"
    r")"
)

_MANUAL_FILL_REFERENCES = (
    "[MANUAL FILL: Sonja — supply verified client references from "
    "ClientList / KB only (name, title, org, phone, email). "
    "Do not list agency staff as client references.]"
)

_WE_EXPERTLY_MANAGES_RE = re.compile(
    r"\bWe\s+expertly\s+manages\b",
    re.I,
)

_COST_LABEL_RE = re.compile(
    r"\b(?:"
    r"cost|price|pricing|fee|fees|compensation|"
    r"cost\s*/\s*price|price\s+reasonableness|grand\s+total"
    r")\b",
    re.I,
)

_AVG_TIER_CLAIM_RE = re.compile(
    r"(?i)\b(?:industry\s+)?Average\s+tier\b|"
    r"Pricing\s+is\s+built\s+from\s+the\s+industry\s+Average",
)


def scrub_reference_withholding(content: str) -> tuple[str, list[str]]:
    """Replace 'upon request' contact deferrals + cut unverified pre-clear claims."""
    text = content or ""
    logs: list[str] = []
    if not text.strip():
        return text, logs

    if _UPON_REQUEST_RE.search(text):
        text = _UPON_REQUEST_RE.sub(_VERIFY_CONTACT, text)
        logs.append("Replaced contact 'upon request' with [VERIFY] contact fields")

    if _PRECLEARED_CLAIM_RE.search(text):
        text = _PRECLEARED_CLAIM_RE.sub("", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        logs.append("Removed unverified pre-cleared / agreed-to-respond reference claim")

    return text, logs


def _reference_entry_is_kb_complete(block: str) -> bool:
    """True only when the entry has real contact fields from KB (no VERIFY gaps)."""
    text = block or ""
    if not text.strip():
        return False
    if _INCOMPLETE_REF_VERIFY_RE.search(text):
        return False
    emails = [
        m.group(0)
        for m in _REAL_EMAIL_RE.finditer(text)
        if not _AGENCY_PLACEHOLDER_EMAIL_RE.search(m.group(0))
    ]
    phones = _REAL_PHONE_RE.findall(text)
    has_contact_line = bool(
        re.search(r"(?i)\bcontact\s*:", text)
        and not re.search(r"(?i)contact\s*:\s*\[VERIFY:", text)
    )
    if emails and (phones or has_contact_line):
        return True
    if phones and has_contact_line:
        return True
    return False


def _extract_reference_section_narrative(content: str) -> str:
    """Headers and past-performance prose without Reference N blocks or contact VERIFY tags."""
    text = content or ""
    if not text.strip():
        return ""
    parts = [p for p in _REFERENCE_BLOCK_SPLIT_RE.split(text) if p is not None]
    narrative_chunks: list[str] = []
    for i, part in enumerate(parts):
        chunk = part.strip("\n")
        if not chunk.strip():
            continue
        looks_like_entry = bool(
            re.match(
                r"(?i)^\s*(?:#{1,3}\s*)?(?:\*\*)?Reference\s+\d+",
                chunk,
            )
        )
        if not looks_like_entry:
            narrative_chunks.append(chunk.strip())
    narrative = "\n\n".join(narrative_chunks) if narrative_chunks else text
    narrative = _INCOMPLETE_REF_VERIFY_RE.sub("", narrative)
    narrative = re.sub(r"\[MANUAL\s+FILL:[^\]]*\]", "", narrative, flags=re.I)
    narrative = re.sub(r"\n{3,}", "\n\n", narrative).strip()
    return narrative


def _reference_narrative_word_count(content: str) -> int:
    narrative = _extract_reference_section_narrative(content)
    return len(re.findall(r"\b\w+\b", narrative))


def _reference_contact_gap_note() -> str:
    return (
        "[MANUAL FILL: Sonja — supply verified client references from "
        "ClientList / KB only (name, title, org, phone, email). Do not invent.]"
    )


def _split_trailing_subsections_from_reference_chunk(chunk: str) -> tuple[str, str]:
    """When Reference N is last, tail subsections (## 29. Interviews) must not be dropped."""
    text = chunk or ""
    m = re.search(
        r"\n(?=#{1,3}\s+\d+\.\s+(?!Reference\s+\d)\S)",
        text,
        re.I,
    )
    if not m:
        return text.strip(), ""
    return text[:m.start()].strip(), text[m.start():].strip()


def _append_reference_tail(tail: str, preamble: str, kept: list[str]) -> tuple[str, list[str]]:
    tail = (tail or "").strip()
    if not tail:
        return preamble, kept
    if kept:
        kept.append(tail)
    elif preamble:
        preamble = f"{preamble}\n\n{tail}"
    else:
        preamble = tail
    return preamble, kept


def drop_incomplete_reference_entries(content: str) -> tuple[str, list[str]]:
    """Omit reference rows that lack KB contact fields — never ship VERIFY shells.

    Product rule: if name/phone/email are not in the knowledge base, do not include
    that reference in the proposal at all. Keep only complete, verifiable entries.
    Past-performance narrative and sibling subsections (e.g. Interviews) are preserved.
    """
    text = content or ""
    logs: list[str] = []
    if not text.strip():
        return text, logs

    if not re.search(r"(?i)\breference\s+\d+\b", text) and not _INCOMPLETE_REF_VERIFY_RE.search(
        text
    ):
        return text, logs

    gap = _reference_contact_gap_note()
    narrative_words = _reference_narrative_word_count(text)

    parts = [p for p in _REFERENCE_BLOCK_SPLIT_RE.split(text) if p is not None]
    if len(parts) <= 1:
        if _INCOMPLETE_REF_VERIFY_RE.search(text) or not _reference_entry_is_kb_complete(text):
            if narrative_words >= 35:
                logs.append(
                    "Kept reference/past-performance narrative; flagged contacts for manual fill"
                )
                body = _extract_reference_section_narrative(text)
                if gap.casefold() not in body.casefold():
                    body = f"{body}\n\n{gap}\n"
                return body.strip() + "\n", logs
            logs.append(
                "Dropped incomplete reference package (missing KB contact fields)"
            )
            return f"{gap}\n", logs
        return text, logs

    preamble = ""
    kept: list[str] = []
    dropped = 0
    for i, part in enumerate(parts):
        chunk = part.strip("\n")
        if not chunk.strip():
            continue
        looks_like_entry = bool(
            re.match(
                r"(?i)^\s*(?:#{1,3}\s*)?(?:\*\*)?Reference\s+\d+",
                chunk,
            )
        )
        if i == 0 and not looks_like_entry:
            preamble = chunk.strip()
            continue
        if not looks_like_entry:
            if kept:
                kept.append(chunk.strip())
            elif preamble:
                preamble = f"{preamble}\n\n{chunk.strip()}"
            else:
                preamble = chunk.strip()
            continue
        entry_body, tail = _split_trailing_subsections_from_reference_chunk(chunk)
        preamble, kept = _append_reference_tail(tail, preamble, kept)
        if _reference_entry_is_kb_complete(entry_body):
            kept.append(entry_body.strip())
        else:
            dropped += 1

    if dropped == 0:
        return text, logs

    logs.append(
        f"Dropped {dropped} incomplete reference entr"
        f"{'y' if dropped == 1 else 'ies'} (no KB contact fields — omit, do not VERIFY-shell)"
    )
    gap = (
        "[MANUAL FILL: Sonja — remaining references must come from verified "
        "ClientList / KB contacts only (name, title, org, phone, email). "
        "Do not invent or leave [VERIFY] shells.]"
    )
    if not kept:
        if narrative_words >= 35 and preamble:
            logs.append(
                "Kept reference/past-performance narrative after dropping incomplete rows"
            )
            body = preamble.strip()
            if gap.casefold() not in body.casefold():
                body = f"{body}\n\n{gap}\n"
            return body.strip() + "\n", logs
        body = gap
        if preamble:
            body = f"{preamble}\n\n{gap}"
        return body, logs

    renumbered: list[str] = []
    for idx, entry in enumerate(kept, start=1):
        renumbered.append(
            re.sub(
                r"(?i)(Reference)\s+\d+",
                rf"\1 {idx}",
                entry,
                count=1,
            )
        )
    pieces: list[str] = []
    if preamble:
        pieces.append(preamble)
    pieces.extend(renumbered)
    pieces.append(gap)
    return "\n\n".join(pieces).strip() + "\n", logs


def is_reference_section(section: ProposalSection) -> bool:
    title_cf = (section.title or "").casefold()
    sid_cf = (section.id or "").casefold()
    return "reference" in title_cf or "reference" in sid_cf


def _normalize_reference_contact_line(line: str) -> str:
    norm = re.sub(r"\s+", " ", (line or "").strip().casefold())
    return re.sub(r"[^\w\s@.-]", "", norm)


def _line_looks_like_reference_contact(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped or stripped.startswith("#"):
        return False
    if stripped.startswith("[") and stripped.endswith("]"):
        return False
    return bool(_REFERENCE_CONTACT_LINE_RE.search(stripped))


def _line_is_agency_contact_not_client(
    line: str,
    *,
    primary_contact_name: str = "",
) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    if _AGENCY_AS_CLIENT_LINE_RE.search(stripped):
        return True
    locked = (primary_contact_name or "").strip()
    if locked and locked.casefold() in stripped.casefold():
        if re.search(r"(?i)\b(?:zö\s+agency|zo\s+agency|@zo\.agency)\b", stripped):
            return True
    return False


def scrub_duplicate_reference_contact_lines(content: str) -> tuple[str, list[str]]:
    """Remove duplicate full contact rows (e.g. identical Sonja lines × 2)."""
    text = content or ""
    logs: list[str] = []
    if not text.strip():
        return text, logs

    seen: set[str] = set()
    out_lines: list[str] = []
    removed = 0
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            out_lines.append(raw)
            continue
        if not _line_looks_like_reference_contact(stripped):
            out_lines.append(raw)
            continue
        norm = _normalize_reference_contact_line(stripped)
        if norm in seen:
            removed += 1
            continue
        seen.add(norm)
        out_lines.append(raw)

    if not removed:
        return text, logs
    logs.append(
        f"Removed {removed} duplicate reference contact row"
        f"{'s' if removed != 1 else ''}"
    )
    return "\n".join(out_lines).strip() + ("\n" if text.endswith("\n") else ""), logs


def scrub_agency_contact_as_client_reference(
    content: str,
    *,
    primary_contact_name: str = "",
) -> tuple[str, list[str]]:
    """Drop agency primary-contact rows masquerading as client references."""
    text = content or ""
    logs: list[str] = []
    if not text.strip():
        return text, logs

    kept: list[str] = []
    dropped = 0
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            kept.append(raw)
            continue
        if _line_is_agency_contact_not_client(
            stripped, primary_contact_name=primary_contact_name
        ) and _line_looks_like_reference_contact(stripped):
            dropped += 1
            continue
        kept.append(raw)

    if not dropped:
        return text, logs

    body = "\n".join(kept).strip()
    logs.append(
        f"Removed {dropped} agency-contact row"
        f"{'s' if dropped != 1 else ''} from references (not client refs)"
    )
    # If nothing substantive remains, replace with a single Sonja handoff.
    substantive = [
        ln.strip()
        for ln in body.splitlines()
        if ln.strip()
        and not ln.strip().startswith("[MANUAL FILL:")
        and not ln.strip().startswith("[VERIFY:")
    ]
    has_client_ref = any(
        _line_looks_like_reference_contact(ln)
        and not _line_is_agency_contact_not_client(
            ln, primary_contact_name=primary_contact_name
        )
        for ln in substantive
    )
    if not has_client_ref and _MANUAL_FILL_REFERENCES.casefold() not in body.casefold():
        body = f"{body}\n\n{_MANUAL_FILL_REFERENCES}\n".strip() + "\n"
        logs.append("Inserted MANUAL FILL after agency-contact-only references package")
    return body, logs


def apply_reference_content_scrubs(
    content: str,
    *,
    primary_contact_name: str = "",
) -> tuple[str, list[str]]:
    """Run all deterministic reference integrity scrubs on one section body."""
    from app.services.proposal_manuscript import (
        convert_instruction_blocks,
        convert_note_to_staff_lines,
        scrub_broken_reference_pipe_rows,
    )

    logs: list[str] = []
    text = content or ""
    text, pipe_logs = scrub_broken_reference_pipe_rows(text)
    logs.extend(pipe_logs)
    text = convert_instruction_blocks(text)
    text = convert_note_to_staff_lines(text)
    text, ref_logs = scrub_reference_withholding(text)
    logs.extend(ref_logs)
    text, drop_logs = drop_incomplete_reference_entries(text)
    logs.extend(drop_logs)
    text, dup_logs = scrub_duplicate_reference_contact_lines(text)
    logs.extend(dup_logs)
    text, agency_logs = scrub_agency_contact_as_client_reference(
        text, primary_contact_name=primary_contact_name
    )
    logs.extend(agency_logs)
    from app.services.proposal_consistency_enforcement import scrub_duplicate_reference_emails

    text, email_logs = scrub_duplicate_reference_emails(text)
    logs.extend(email_logs)
    return text, logs


def apply_reference_post_fill_scrubs(
    content: str,
    *,
    primary_contact_name: str = "",
) -> tuple[str, list[str]]:
    """Dedupe + agency-contact scrub only — after contact rows are filled."""
    from app.services.proposal_manuscript import (
        convert_instruction_blocks,
        convert_note_to_staff_lines,
        scrub_broken_reference_pipe_rows,
    )

    logs: list[str] = []
    text = content or ""
    text, pipe_logs = scrub_broken_reference_pipe_rows(text)
    logs.extend(pipe_logs)
    text = convert_instruction_blocks(text)
    text = convert_note_to_staff_lines(text)
    text, dup_logs = scrub_duplicate_reference_contact_lines(text)
    logs.extend(dup_logs)
    text, agency_logs = scrub_agency_contact_as_client_reference(
        text, primary_contact_name=primary_contact_name
    )
    logs.extend(agency_logs)
    from app.services.proposal_consistency_enforcement import scrub_duplicate_reference_emails

    text, email_logs = scrub_duplicate_reference_emails(text)
    logs.extend(email_logs)
    return text, logs


def references_section_has_preservable_content(content: str) -> bool:
    """True when references have KB-complete rows or substantive past-performance prose."""
    text = (content or "").strip()
    if not text:
        return False
    if _reference_narrative_word_count(text) >= 40:
        return True
    if _reference_entry_is_kb_complete(text):
        return True
    parts = [p for p in _REFERENCE_BLOCK_SPLIT_RE.split(text) if p and p.strip()]
    complete = sum(1 for part in parts if _reference_entry_is_kb_complete(part))
    if complete >= 1 and len(text) > 80:
        return True
    # Comma-separated contact lines (Exhibit K form style).
    client_lines = [
        ln.strip()
        for ln in text.splitlines()
        if _line_looks_like_reference_contact(ln)
        and not _line_is_agency_contact_not_client(ln)
        and _REAL_EMAIL_RE.search(ln)
        and not _AGENCY_PLACEHOLDER_EMAIL_RE.search(ln)
    ]
    return len(client_lines) >= 1


def reference_section_has_scrubbable_defects(content: str) -> bool:
    """True when duplicate rows or agency staff appear as client references."""
    text = (content or "").strip()
    if not text:
        return False
    seen: set[str] = set()
    for raw in text.splitlines():
        stripped = raw.strip()
        if not _line_looks_like_reference_contact(stripped):
            continue
        norm = _normalize_reference_contact_line(stripped)
        if norm and norm in seen:
            return True
        if norm:
            seen.add(norm)
        if _line_is_agency_contact_not_client(stripped):
            return True
    return False


def fix_known_bio_typos(content: str) -> tuple[str, list[str]]:
    """Deterministic template typos that recur across proposals."""
    text = content or ""
    logs: list[str] = []
    if _WE_EXPERTLY_MANAGES_RE.search(text):
        text = _WE_EXPERTLY_MANAGES_RE.sub("The agency expertly manages", text)
        logs.append("Fixed bio typo: We expertly manages → The agency expertly manages")
    return text, logs


def apply_manuscript_integrity_guards(
    draft: ProposalDraft,
    *,
    preserve_reference_narrative: bool = False,
) -> tuple[ProposalDraft, list[str]]:
    """Run deterministic integrity scrubs across all sections."""
    logs: list[str] = []
    sections = []
    changed = False
    for section in draft.sections:
        content = section.content or ""
        title_cf = (section.title or "").casefold()
        sid = section.id or ""
        new = content
        section_logs: list[str] = []

        if "reference" in title_cf or "reference" in sid.casefold():
            if preserve_reference_narrative:
                new, ref_logs = apply_reference_post_fill_scrubs(new)
                section_logs.extend(ref_logs)
            else:
                new, ref_logs = apply_reference_content_scrubs(new)
                section_logs.extend(ref_logs)
        else:
            # Still strip upon-request deferrals anywhere (RFP often forbids withholding).
            scrubbed, ref_logs = scrub_reference_withholding(new)
            if ref_logs:
                new, section_logs = scrubbed, list(ref_logs)
            # Non-title reference packages (e.g. buried under Qualifications)
            if re.search(r"(?i)\breference\s+\d+\b", new) and _INCOMPLETE_REF_VERIFY_RE.search(
                new
            ):
                new, drop_logs = drop_incomplete_reference_entries(new)
                section_logs.extend(drop_logs)

        if sid.startswith("section-2-") or "bio" in title_cf:
            new, bio_logs = fix_known_bio_typos(new)
            section_logs.extend(bio_logs)

        flag_scrubbed = strip_internal_flag_tags(new)
        if flag_scrubbed != new:
            new = flag_scrubbed
            section_logs.append("Removed internal [FLAG FOR ...] handoff tags")

        if (
            sid.startswith("section-3-work")
            or "our work" in title_cf
            or "case study" in title_cf
            or re.search(r"\b3\.\d+\b", title_cf)
        ):
            new, cs_logs = scrub_case_study_overbuild(new)
            section_logs.extend(cs_logs)
            is_dump, dump_reason = case_study_looks_like_source_dump(new)
            if is_dump:
                title_hint = (section.title or sid or "case study").strip()
                new = (
                    f"### {title_hint}\n\n"
                    f"**Challenge**\n\n"
                    f"[VERIFY: rewrite Challenge from source case study — "
                    f"rejected source dump ({dump_reason})]\n\n"
                    f"**Solution / Our Approach**\n\n"
                    f"[VERIFY: rewrite Solution from source case study — "
                    f"rejected source dump ({dump_reason})]\n\n"
                    f"Client Voice: [VERIFY: no client quote found in source material]"
                )
                section_logs.append(
                    f"Replaced case-study source dump with VERIFY stub ({dump_reason})"
                )

        if new != content:
            changed = True
            sections.append(section.model_copy(update={"content": new}))
            for line in section_logs:
                logs.append(f"{sid}: {line}")
        else:
            sections.append(section)

    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def _phone_digits(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def _research_evidence_blob(research: ProposalResearchCache | None) -> str:
    if research is None:
        return ""
    corpus = getattr(research, "evidence_corpus", None) or []
    parts: list[str] = []
    for item in corpus:
        excerpt = getattr(item, "excerpt", None) or getattr(item, "text", None) or ""
        if excerpt:
            parts.append(str(excerpt))
    return "\n".join(parts)


def scrub_unverified_reference_phones(
    content: str,
    *,
    evidence_text: str,
) -> tuple[str, list[str]]:
    """Replace reference phones that do not appear anywhere in KB evidence."""
    text = content or ""
    logs: list[str] = []
    if not text.strip() or not evidence_text.strip():
        return text, logs

    evidence_phones = {
        _phone_digits(match.group(0))
        for match in _REAL_PHONE_RE.finditer(evidence_text)
        if len(_phone_digits(match.group(0))) >= 10
    }
    if not evidence_phones:
        return text, logs

    def _replace_phone(match: re.Match[str]) -> str:
        raw = match.group(0)
        digits = _phone_digits(raw)
        if len(digits) < 10 or digits in evidence_phones:
            return raw
        logs.append(f"Replaced unverified phone {raw} with [VERIFY]")
        return "[VERIFY: phone from KB reference doc]"

    updated = _REAL_PHONE_RE.sub(_replace_phone, text)
    return updated, logs


def apply_reference_contact_evidence_guard(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> tuple[ProposalDraft, list[str]]:
    """Drop fabricated reference phones when digits are absent from evidence corpus."""
    evidence = _research_evidence_blob(research)
    if not evidence.strip():
        return draft, []

    logs: list[str] = []
    sections: list[Any] = []
    changed = False
    for section in draft.sections:
        title_cf = (section.title or "").casefold()
        sid_cf = (section.id or "").casefold()
        if "reference" not in title_cf and "reference" not in sid_cf:
            sections.append(section)
            continue
        new, sec_logs = scrub_unverified_reference_phones(
            section.content or "",
            evidence_text=evidence,
        )
        if new != (section.content or ""):
            changed = True
            sections.append(section.model_copy(update={"content": new}))
            for line in sec_logs:
                logs.append(f"{section.id}: {line}")
        else:
            sections.append(section)

    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def infer_cost_weight_pct(
    rfp_text: str,
    research: ProposalResearchCache | None = None,
) -> float | None:
    """Infer cost/price evaluation weight as a percent of total points (0–100)."""
    # Prefer Stage 2 / intelligence costWeight when present.
    if research is not None:
        plan = getattr(research, "proposal_execution_plan", None)
        if plan is not None:
            raw = getattr(plan, "cost_weight", None)
            if raw is None and isinstance(plan, dict):
                raw = plan.get("costWeight") or plan.get("cost_weight")
            try:
                if raw is not None:
                    val = float(raw)
                    if 0 < val <= 1:
                        return val * 100.0
                    if 0 < val <= 100:
                        return val
            except (TypeError, ValueError):
                pass

        # Sum evaluationWeight on price/cost-mapped sections vs total weights.
        sections = getattr(research, "rfp_sections", None) or []
        cost_w = 0
        total_w = 0
        for sec in sections:
            w = getattr(sec, "evaluation_weight", None)
            if w is None:
                continue
            try:
                wi = int(w)
            except (TypeError, ValueError):
                continue
            if wi <= 0:
                continue
            total_w += wi
            title = (getattr(sec, "title", None) or "").casefold()
            if _COST_LABEL_RE.search(title):
                cost_w += wi
        if total_w >= 40 and cost_w > 0:
            return round(100.0 * cost_w / total_w, 1)

    from app.services.evidence_trust.rfp_hard_facts import extract_rfp_hard_facts

    facts = extract_rfp_hard_facts(rfp_text or "")
    lines = facts.get("evaluation_lines") or []
    total = int(facts.get("evaluation_total") or 0)
    if not lines or total <= 0:
        # Fallback: Price X of Y points phrasing.
        m = re.search(
            r"(?is)\b(?:price|cost|pricing)\b.{0,60}?"
            r"(\d{1,3}(?:,\d{3})*|\d{2,4})\s*(?:of|/)\s*"
            r"(\d{1,3}(?:,\d{3})*|\d{2,4})\s*points?",
            rfp_text or "",
        )
        if m:
            num = int(m.group(1).replace(",", ""))
            den = int(m.group(2).replace(",", ""))
            if den > 0 and num <= den:
                return round(100.0 * num / den, 1)
        return None

    cost_pts = 0
    for line in lines:
        label, _, pts_s = line.partition(":")
        if not _COST_LABEL_RE.search(label):
            continue
        try:
            cost_pts += int(pts_s.strip().split()[0])
        except (ValueError, IndexError):
            continue
    if cost_pts <= 0:
        return None
    return round(100.0 * cost_pts / total, 1)


def enforce_pricing_tier_for_cost_weight(
    budget: ProposalBudget,
    *,
    cost_weight_pct: float | None,
) -> tuple[ProposalBudget, list[str]]:
    """Force Low tier when RFP cost weight ≥25% (Pricing Guide Decision Guide)."""
    logs: list[str] = []
    if cost_weight_pct is None or cost_weight_pct < 25:
        return budget, logs

    tier = (budget.pricing_tier or "").strip()
    if tier.casefold() == "low":
        rationale = (budget.rfp_budget_notes or "").strip()
        note = (
            f"Cost weight ~{cost_weight_pct:.0f}% (≥25%) → Low tier per Pricing Guide "
            "Decision Guide."
        )
        if note.casefold() not in rationale.casefold():
            updated_notes = f"{note}\n{rationale}".strip() if rationale else note
            budget = budget.model_copy(update={"rfp_budget_notes": updated_notes[:4000]})
            logs.append(note)
        return budget, logs

    flags = list(budget.pricing_flags or [])
    flag = (
        f"[PRICING FLAG: Cost weight ~{cost_weight_pct:.0f}% (≥25%) — "
        f"Pricing Guide requires Low tier; was '{tier or 'unset'}'. "
        "Auto-set to Low. Confirm with Sonja before submission.]"
    )
    if flag not in flags:
        flags.append(flag)

    notes = (budget.rfp_budget_notes or "").strip()
    override_note = (
        f"AUTO TIER OVERRIDE: cost weight ~{cost_weight_pct:.0f}% ≥25% → Low tier "
        f"(was {tier or 'unset'}) per 00_Guide_Pricing Decision Guide."
    )
    notes = f"{override_note}\n{notes}".strip() if notes else override_note

    # Fix narrative that still claims Average tier.
    fee = budget.fee_structure or ""
    if _AVG_TIER_CLAIM_RE.search(fee):
        fee = _AVG_TIER_CLAIM_RE.sub("Low tier", fee)

    budget = budget.model_copy(
        update={
            "pricing_tier": "Low",
            "pricing_flags": flags,
            "rfp_budget_notes": notes[:4000],
            "fee_structure": fee,
        }
    )
    logs.append(
        f"Forced pricing tier Low (was {tier or 'unset'}) — cost weight "
        f"{cost_weight_pct:.0f}% ≥25%"
    )
    return budget, logs


_FORBIDDEN_CASE_STUDY_HEADINGS = frozenset(
    {
        "strategy",
        "goal",
        "goals",
        "kpi",
        "kpis",
        "creative deliverables",
        "deliverables",
        "why relevant",
        "results",
        "measurable outcomes",
        "key tactics",
        "company overview",
        "client overview",
        "objectives",
        "objective",
    }
)

_ALLOWED_CASE_STUDY_HEADINGS = frozenset(
    {
        "challenge",
        "solution",
        "solution / our approach",
        "our approach",
        "client voice",
    }
)


def _case_study_heading_key(line: str) -> str:
    key = re.sub(r"^#+\s*", "", (line or "").strip()).strip().rstrip(":").casefold()
    # **Challenge** / *Solution* — strip markdown emphasis wrappers.
    key = re.sub(r"\*+", "", key)
    key = re.sub(r"^_+|_+$", "", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key


def _looks_like_case_study_heading(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if s.startswith("#"):
        return True
    if s.endswith(".") or s.endswith("?") or s.endswith("!"):
        return False
    # Normalize markdown bold/italic so **Challenge** still counts as a heading.
    plain = re.sub(r"[*_`]", "", s).strip().rstrip(":")
    if len(plain) > 56:
        return False
    if not re.match(r"^[A-Za-z][\w\s/&'’\-]+$", plain):
        return False
    words = plain.split()
    if not (1 <= len(words) <= 8):
        return False
    key = plain.casefold()
    if key in _ALLOWED_CASE_STUDY_HEADINGS or key in _FORBIDDEN_CASE_STUDY_HEADINGS:
        return True
    if key.startswith("why this matters") or key.startswith("why matters"):
        return True
    if key.startswith("challenge") or key.startswith("solution") or key.startswith(
        "client voice"
    ):
        return True
    # Title-case short labels (Strategy, Goals, KPIs, …)
    return plain[:1].isupper() and len(words) <= 4


def prefer_case_study_kb_text(case_study_text: str) -> tuple[str, list[str]]:
    """
    Prefer 03_CS_* document blocks over 06_WON / full-proposal OCR.

    Retrieved packs often interleave the case-study PDF with a won-proposal PDF;
    feeding only 03_CS sharply reduces Challenge/Solution regurgitation failures.
    """
    text = (case_study_text or "").strip()
    if not text:
        return text, []

    blocks = re.split(r"(?m)(?=^###\s+)", text)
    blocks = [b.strip() for b in blocks if b.strip()]
    if not blocks:
        return text, []

    cs_blocks: list[str] = []
    cs_labels: list[str] = []
    other_blocks: list[str] = []
    for block in blocks:
        first = block.splitlines()[0] if block.splitlines() else ""
        label = re.sub(r"^###\s*", "", first).strip()
        label_cf = label.casefold()
        if "03_cs" in label_cf or re.search(r"(?i)\bcase\s*study\b", label):
            cs_blocks.append(block)
            if label:
                cs_labels.append(label)
        elif re.search(r"(?i)\b06_won_|\b07_fin_", label_cf):
            continue
        else:
            other_blocks.append(block)

    if cs_blocks:
        return "\n\n".join(cs_blocks).strip(), cs_labels
    if other_blocks:
        return "\n\n".join(other_blocks).strip(), []
    return text, []


def case_study_has_required_structure(content: str) -> bool:
    """True when Challenge + Solution/Our Approach headings are present."""
    body = content or ""
    has_challenge = bool(
        re.search(r"(?im)^(?:#{1,6}\s*)?\**\s*challenge\b", body)
    )
    has_solution = bool(
        re.search(
            r"(?im)^(?:#{1,6}\s*)?\**\s*(?:solution(?:\s*/\s*our\s+approach)?|our\s+approach)\b",
            body,
        )
    )
    return has_challenge and has_solution


def case_study_looks_like_source_dump(content: str) -> tuple[bool, str]:
    """
    Detect LLM regurgitation of proposal/OCR blobs instead of a case-study rewrite.

    Used after Case Study Builder so Umatilla-style TOC/cover-letter dumps are rejected.
    Also rejects "RELEVANT CASE STUDIES" catalogs that paste 3–4 projects into one card.
    """
    body = content or ""
    if not body.strip():
        return False, ""

    signals: list[str] = []
    if re.search(r"(?i)\b06_WON_", body):
        signals.append("06_WON filename in body")
    if re.search(r"(?i)\b03_CS_[^\n]{0,120}\.pdf\b", body):
        signals.append("03_CS filename in body")
    if re.search(r"(?i)\[photo\]", body):
        signals.append("photo OCR placeholder")
    if re.search(
        r"(?i)\bSECTION\s+[1-7]\b.{0,80}(?:Firm Overview|Relevant Experience|Key Personnel)",
        body,
    ):
        signals.append("proposal TOC")
    if re.search(r"(?im)^Dear\s+.+(?:Selection Committee|Committee)\b", body):
        signals.append("cover letter salutation")
    if re.search(r"(?i)\bSubmitted by:\s*zo\s*agency\b", body):
        signals.append("proposal submitter line")
    if re.search(r"(?i)\bTable of Contents\b|\bPage\s+\d+\b.*\bPage\s+\d+\b", body):
        signals.append("TOC/page index")
    if re.search(r"(?i)\brelevant\s+case\s+studies\b", body):
        signals.append("relevant case studies catalog")

    # Multiple distinct "CITY OF X" / "COUNTY" project banners in one card =
    # All Case Studies dump pasted instead of a single engagement.
    project_banners = re.findall(
        r"(?im)^(?:#{1,6}\s*)?(?:CITY|COUNTY|TOWN|UNIVERSITY|DEPARTMENT)\s+OF\s+[A-Z][A-Z\s&'-]{2,40}\s*$",
        body,
    )
    # Also catch ALL-CAPS client lines like "CITY OF MEDFORD" / "DESCHUTES COUNTY"
    # without "OF" (common in the All Case Studies master).
    allcaps_clients = re.findall(
        r"(?m)^([A-Z][A-Z0-9 &'-]{6,60})$",
        body,
    )
    allcaps_clients = [
        c
        for c in allcaps_clients
        if re.search(r"(?i)\b(city|county|department|university|town)\b", c)
        or re.search(r"(?i)\b(medford|bend|deschutes|oregon|hampton)\b", c)
    ]
    distinct_projects = list(dict.fromkeys([*project_banners, *allcaps_clients]))
    if len(distinct_projects) >= 3:
        signals.append(f"multi-project catalog ({len(distinct_projects)} clients)")

    strong = {
        "proposal TOC",
        "cover letter salutation",
        "photo OCR placeholder",
        "relevant case studies catalog",
        "multi-project catalog (3 clients)",
        "multi-project catalog (4 clients)",
        "multi-project catalog (5 clients)",
    }
    # Any multi-project catalog with 3+ clients is a hard reject.
    if any(s.startswith("multi-project catalog") for s in signals):
        return True, ", ".join(signals[:5])
    if "relevant case studies catalog" in signals:
        return True, ", ".join(signals[:5])
    if any(s in strong for s in signals) or len(signals) >= 2:
        return True, ", ".join(signals[:5])
    return False, ""


_MAX_CHALLENGE_WORDS = 40
_MAX_SOLUTION_WORDS = 50


def _normalize_inline_case_study_headings(content: str) -> str:
    """Pull Challenge / Solution / Client Voice onto their own lines when inline."""
    text = content or ""
    if not text.strip():
        return text
    # **Challenge** / **Solution / Our Approach** mid-paragraph → own lines
    text = re.sub(
        r"(?i)(?<!\n)\s*(\*\*\s*(?:Challenge|Solution(?:\s*/\s*Our\s+Approach)?|Our\s+Approach|Client\s+Voice)\s*\*\*)",
        r"\n\n\1\n\n",
        text,
    )
    text = re.sub(
        r"(?i)(?<!\n)\s*((?:Challenge|Solution(?:\s*/\s*Our\s+Approach)?|Our\s+Approach|Client\s+Voice)\s*:)",
        r"\n\n\1\n\n",
        text,
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _cap_prose_words(text: str, max_words: int) -> str:
    words = (text or "").split()
    if len(words) <= max_words:
        return (text or "").strip()
    clipped = " ".join(words[:max_words]).rstrip(",;:.—-")
    return f"{clipped}."


def _cap_case_study_section_lengths(content: str) -> tuple[str, list[str]]:
    """Hard-cap Challenge / Solution prose so cards stay scannable.

    Always emit blank lines around headings so the manuscript renderer treats
    Challenge / Solution / Client Voice as separate blocks (not one wall of text).
    """
    text = content or ""
    if not text.strip():
        return text, []

    logs: list[str] = []
    lines = text.splitlines()
    out: list[str] = []
    section: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal buf, section
        if section is None:
            return
        body = "\n".join(buf).strip()
        if section == "challenge":
            capped = _cap_prose_words(body, _MAX_CHALLENGE_WORDS)
            if body and capped != body:
                logs.append(f"Challenge capped to {_MAX_CHALLENGE_WORDS} words")
            if capped:
                out.append(capped)
                out.append("")
        elif section == "solution":
            capped = _cap_prose_words(body, _MAX_SOLUTION_WORDS)
            if body and capped != body:
                logs.append(f"Solution capped to {_MAX_SOLUTION_WORDS} words")
            if capped:
                out.append(capped)
                out.append("")
        else:
            if body:
                out.append(body)
                out.append("")
        buf = []
        section = None

    for line in lines:
        if _looks_like_case_study_heading(line):
            _flush()
            key = _case_study_heading_key(line)
            # Keep a blank line before each structural heading.
            if out and out[-1].strip():
                out.append("")
            out.append(line)
            out.append("")
            if key.startswith("challenge"):
                section = "challenge"
            elif key.startswith("solution") or key == "our approach":
                section = "solution"
            elif key.startswith("client voice"):
                section = "client_voice"
            else:
                section = None
            continue
        if section in {"challenge", "solution", "client_voice"}:
            buf.append(line)
        else:
            out.append(line)
    _flush()

    cleaned = "\n".join(out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, logs


def scrub_case_study_overbuild(content: str) -> tuple[str, list[str]]:
    """
    Enforce Challenge → Solution → Client Voice shape.

    Strips KB-template dump sections (Strategy/Goals/KPIs/Creative Deliverables)
    and invented RFP bridges ("Why this matters for …") that prompts forbid but
    models still emit when copying master case-study docs. Also hard-caps
    Challenge/Solution word counts so Section 3 cards stay short.
    """
    text = content or ""
    if not text.strip():
        return text, []

    logs: list[str] = []
    removed: list[str] = []
    out_lines: list[str] = []
    skipping = False

    for line in text.splitlines():
        if _looks_like_case_study_heading(line):
            key = _case_study_heading_key(line)
            is_forbidden = (
                key in _FORBIDDEN_CASE_STUDY_HEADINGS
                or key.startswith("why this matters")
                or key.startswith("why matters")
            )
            if is_forbidden:
                skipping = True
                if key not in removed:
                    removed.append(key)
                continue
            # Resume on any other heading (allowed template or title).
            skipping = False
            out_lines.append(line)
            continue
        if skipping:
            continue
        # Orphan bridge lines without a clean heading break.
        if re.match(r"(?i)^why\s+(?:this\s+)?matters\b", line.strip()):
            skipping = True
            if "why this matters" not in removed:
                removed.append("why this matters")
            continue
        out_lines.append(line)

    cleaned = "\n".join(out_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if removed:
        logs.append(
            "Stripped case-study overbuild sections: " + ", ".join(removed[:8])
        )
        logger.info("case_study_overbuild_scrub removed=%s", removed[:8])

    cleaned = _normalize_inline_case_study_headings(cleaned)
    cleaned, cap_logs = _cap_case_study_section_lengths(cleaned)
    logs.extend(cap_logs)
    return cleaned, logs


_CASE_STUDY_PERCENT_CLAIM_RE = re.compile(
    r"(?i)"
    r"("
    r"(?:increased|boosted|grew|improved|raised|drove|lifted)\s+"
    r"(?:[\w\s/-]{0,40}?)?"
    r"(?:by|to)\s+"
    r"\d{1,3}(?:\.\d+)?\s*%"
    r"|"
    r"\d{1,3}(?:\.\d+)?\s*%\s+"
    r"(?:increase|growth|lift|improvement|rise)\b"
    r"|"
    r"(?:bookings?|revenue|engagement|traffic|conversions?|donations?|membership)\s+"
    r"(?:increased|grew|up)\s+(?:by\s+)?\d{1,3}(?:\.\d+)?\s*%"
    r")"
)
_CASE_STUDY_VOLUME_METRIC_RE = re.compile(
    r"(?i)"
    r"("
    r"[\d,]{3,}\s+impressions?"
    r"|"
    r"[\d,]{2,}\s+clicks?"
    r"|"
    r"\d+(?:\.\d+)?\s*%\s*CTR"
    r"|"
    r"CTR\s+(?:of\s+)?\d+(?:\.\d+)?\s*%"
    r"|"
    r"click[- ]through\s+rate\s+(?:of\s+)?\d+(?:\.\d+)?\s*%"
    r")"
)


def _metric_numbers_in_source(claim: str, src: str) -> bool:
    nums = re.findall(r"[\d,]+(?:\.\d+)?", claim or "")
    if not nums:
        return False
    src_compact = (src or "").replace(",", "")
    for n in nums:
        compact = n.replace(",", "")
        if not compact:
            continue
        if n in src or compact in src_compact:
            continue
        return False
    return True


def _percent_in_source(claim: str, src: str) -> bool:
    nums = re.findall(r"\d{1,3}(?:\.\d+)?", claim)
    for n in nums:
        if re.search(rf"{re.escape(n)}\s*%", src):
            return True
    return False


def _sentence_has_ungrounded_cs_metric(sent: str, src: str) -> bool:
    for match in _CASE_STUDY_VOLUME_METRIC_RE.finditer(sent):
        if not _metric_numbers_in_source(match.group(0), src):
            return True
    for match in _CASE_STUDY_PERCENT_CLAIM_RE.finditer(sent):
        if not _percent_in_source(match.group(0), src):
            return True
    return False


def scrub_ungrounded_case_study_percent_metrics(
    content: str,
    *,
    source_text: str = "",
) -> tuple[str, list[str]]:
    """Remove invented outcome % / volume claims when they are absent from case-study KB.

    Requires ``source_text`` — without a source we do not guess which figures are real.
    """
    text = content or ""
    src = (source_text or "").strip()
    if not text.strip() or not src:
        return text, []
    logs: list[str] = []

    chunks = re.split(r"(?<=[.!?])(\s+)", text)
    rebuilt: list[str] = []
    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        sep = chunks[i + 1] if i + 1 < len(chunks) else ""
        if _sentence_has_ungrounded_cs_metric(chunk, src):
            logs.append(f"Removed ungrounded case-study metric: {chunk.strip()[:80]}")
            i += 2
            continue
        rebuilt.append(chunk)
        if sep:
            rebuilt.append(sep)
        i += 2

    cleaned = "".join(rebuilt)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    cleaned = re.sub(r"  +", " ", cleaned)
    if logs:
        logger.info("case_study_metric_scrub removed=%d", len(logs))
    return cleaned, logs


def apply_case_study_metric_scrub_to_draft(
    draft: ProposalDraft,
    *,
    source_text: str,
) -> tuple[ProposalDraft, list[str]]:
    """Strip invented impressions/clicks/CTR/% lift when those numbers are not in KB."""
    src = (source_text or "").strip()
    if not draft.sections or not src:
        return draft, []
    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        if not (
            _CASE_STUDY_VOLUME_METRIC_RE.search(body)
            or _CASE_STUDY_PERCENT_CLAIM_RE.search(body)
        ):
            sections.append(section)
            continue
        cleaned, section_logs = scrub_ungrounded_case_study_percent_metrics(
            body, source_text=src
        )
        if section_logs:
            changed = True
            logs.extend(
                f"{section.title or section.id}: {line}" for line in section_logs
            )
            sections.append(section.model_copy(update={"content": cleaned}))
        else:
            sections.append(section)
    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def case_study_fidelity_ok(source_text: str, written: str) -> tuple[bool, str]:
    """Heuristic: write-up must keep real project identity from the source.

    A focused card drawn from a multi-project master dump (All Case Studies)
    is OK when it faithfully covers at least one source project. Fail only when
    the write-up looks genericized — almost none of the distinctive source
    names survive.
    """
    src = source_text or ""
    out = written or ""
    if len(src) < 80 or len(out) < 40:
        return True, ""

    distinctive: list[str] = []
    # Festival / campaign style names with lowercase connectors (Rock the Locks Festival).
    for m in re.finditer(
        r"\b([A-Z][A-Za-z0-9'’&-]+(?:\s+(?:the|of|and|for|a|an)\s+[A-Z][A-Za-z0-9'’&-]+)+"
        r"(?:\s+[A-Z][A-Za-z0-9'’&-]+)*)\b",
        src,
    ):
        distinctive.append(m.group(1).strip())
    # Title-ish tokens: multi-word Capitalized phrases.
    for c in re.findall(
        r"\b([A-Z][A-Za-z0-9'’&-]{2,}(?:\s+[A-Z][A-Za-z0-9'’&-]{2,}){1,4})\b",
        src,
    ):
        distinctive.append(c)

    skip = {
        "city of",
        "state of",
        "united states",
        "case study",
        "digital campaign",
        "our approach",
        "why relevant",
    }
    cleaned: list[str] = []
    for c in distinctive:
        if c.casefold() in skip:
            continue
        if re.search(
            r"(?i)festival|locks|rodeo|fair|campaign|initiative|network|county|city",
            c,
        ) or len(c.split()) >= 3:
            cleaned.append(c)
    distinctive = list(dict.fromkeys(cleaned))[:8]
    if not distinctive:
        return True, ""

    out_cf = out.casefold()

    def _phrase_coverage(phrase: str) -> tuple[float, list[str]]:
        tokens = [
            t
            for t in re.findall(r"[A-Za-z]{3,}", phrase)
            if t.casefold()
            not in {"the", "and", "for", "digital", "campaign", "case", "study", "city", "of"}
        ]
        if not tokens:
            return 1.0, []
        hits = [t for t in tokens if t.casefold() in out_cf]
        return len(hits) / len(tokens), tokens

    covered = 0
    missing: list[str] = []
    for d in distinctive:
        ratio, _tokens = _phrase_coverage(d)
        if ratio >= 0.5:
            covered += 1
        else:
            missing.append(d)

    # At least one real source project survived → not a generic rewrite.
    if covered >= 1:
        return True, ""

    # Also catch when core tokens like "Locks" vanish from a single-project source.
    core_tokens: list[str] = []
    for d in distinctive:
        for tok in re.findall(r"[A-Za-z]{4,}", d):
            if tok.casefold() in {
                "festival",
                "campaign",
                "digital",
                "county",
                "city",
                "study",
            }:
                continue
            core_tokens.append(tok)
    core_missing = [
        t for t in dict.fromkeys(core_tokens) if t.casefold() not in out_cf
    ]

    if covered == 0 and (
        len(missing) >= max(1, (len(distinctive) + 1) // 2)
        or (len(core_missing) >= 2 and len(missing) >= 1)
    ):
        return False, (
            "Case study write-up dropped source project names "
            f"({', '.join((missing or core_missing)[:3])}) — likely genericized away "
            "from the verified file."
        )
    return True, ""
