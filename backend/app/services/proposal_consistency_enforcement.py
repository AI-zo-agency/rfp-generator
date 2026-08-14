"""Deterministic cross-section consistency enforcement for proposal manuscripts.

Closes recurring submit blockers that prompts alone do not catch:
- conflicting primary contacts (Haley Neff vs locked Ron Comer)
- duplicate placeholder reference emails (sonja@zo.agency × 3)
- Schedule tabs that restate Approach methodology instead of a calendar
- Schedule/Approach week plans that overrun the RFP award→launch window
- Signed cover-letter attachment awareness in submission checklist patterns
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.proposal import (
    ManuscriptLocks,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

_PRIMARY_CLAIM_WINDOW = re.compile(
    r"(?is)"
    r"(?:"
    # Role phrase then Haley
    r"(?:primary\s+(?:account\s+)?(?:contact|representative|rep|liaison)|"
    r"dedicated\s+(?:primary\s+)?(?:customer\s+service\s+)?representative|"
    r"single\s+point\s+of\s+contact|"
    r"dedicated\s+primary\s+contact)"
    r".{0,80}?Haley\s+Neff"
    r"|"
    # Haley then explicit primary role (not "when the primary is unavailable")
    r"Haley\s+Neff\s+(?:is|as|serves\s+as|will\s+be|acts\s+as)\s+"
    r"(?:our\s+|the\s+|zö'?s?\s+)?"
    r"(?:dedicated\s+)?(?:primary\s+(?:account\s+)?(?:contact|representative|rep|liaison)|"
    r"dedicated\s+(?:customer\s+service\s+)?representative|"
    r"single\s+point\s+of\s+contact)"
    r")",
)

_SCHEDULE_TITLE_RE = re.compile(
    r"(?i)\b(?:project\s+)?(?:schedule|timeline|delivery\s+schedule)\b"
)
_APPROACH_TITLE_RE = re.compile(
    r"(?i)\b(?:project\s+)?(?:approach|methodology|work\s+plan|technical\s+approach)\b"
)
_PHASE_HEADING_RE = re.compile(
    r"(?im)^\s*(?:\*\*)?Phase\s+(\d+)\s*[:.\-]?\s*([^\n*]{0,80})"
)

_WEEK_RANGE_RE = re.compile(
    r"(?i)\bweeks?\s+(\d+)\s*[-–—to]+\s*(\d+)\b"
)
_WEEK_SINGLE_RE = re.compile(r"(?i)\bweek\s+(\d+)\b")
_N_WEEK_PLAN_RE = re.compile(r"(?i)\b(\d+)\s*[-–—]?\s*weeks?\b")
_WEEK_PAREN_RE = re.compile(
    r"(?i)\(\s*weeks?\s+\d+(?:\s*[-–—to]+\s*\d+)?\s*\)"
)

_WITHIN_WEEKS_OF_AWARD_RE = re.compile(
    r"(?i)within\s+(\d+)\s+weeks?\s+(?:of|after)\s+(?:the\s+)?"
    r"(?:award|notice\s+to\s+proceed|contract\s+execution|\bntp\b)"
)
_WEEKS_FROM_AWARD_RE = re.compile(
    r"(?i)(\d+)\s*(?:to|[-–—])\s*(\d+)\s+weeks?\s+(?:from|after)\s+(?:the\s+)?"
    r"(?:award|notice\s+to\s+proceed|contract\s+execution|\bntp\b)"
)
_LAUNCH_CONSTRAINT_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"(?:award|notice\s+to\s+proceed|contract\s+execution|\bntp\b).{0,220}"
    r"(?:launch|go[- ]live|mid[- ]?contract|campaign\s+start|commence)"
    r"|"
    r"(?:launch|go[- ]live).{0,120}"
    r"(?:award|notice\s+to\s+proceed|within\s+\d+\s+weeks)"
    r")"
)

_AGENCY_PLACEHOLDER_EMAIL_RE = re.compile(
    r"(?i)\b(?:sonja|info|hello|contact)@zo\.agency\b"
)


def scrub_conflicting_primary_contact(
    content: str,
    *,
    locked_name: str,
) -> tuple[str, list[str]]:
    """Replace wrong primary (e.g. Haley Neff) with the locked primary name."""
    text = content or ""
    logs: list[str] = []
    locked = (locked_name or "").strip()
    if not text.strip() or not locked:
        return text, logs
    if locked.casefold() == "haley neff":
        return text, logs

    updated, n = _PRIMARY_CLAIM_WINDOW.subn(
        lambda m: re.sub(r"Haley\s+Neff", locked, m.group(0), flags=re.I),
        text,
    )
    if n:
        logs.append(
            f"Rewrote {n} conflicting primary-contact claim(s) → {locked}"
        )
    bare = re.compile(
        rf"(?i)(dedicated\s+primary\s+contact|primary\s+contact)\s*(?:is|:)\s*Haley\s+Neff"
    )
    updated2, n2 = bare.subn(rf"\1 is {locked}", updated)
    if n2:
        updated = updated2
        logs.append(f"Normalized dedicated-primary phrasing to {locked}")
    return updated, logs


def scrub_duplicate_reference_emails(content: str) -> tuple[str, list[str]]:
    """Collapse repeated emails in reference packages to honest VERIFY gaps.

    Three identical sonja@zo.agency rows fail RFP 'three references' scoring and
    read as placeholders — keep the first, replace later duplicates.
    """
    text = content or ""
    logs: list[str] = []
    if not text.strip():
        return text, logs

    seen: dict[str, int] = {}
    parts: list[str] = []
    last = 0
    replaced = 0
    for match in _EMAIL_RE.finditer(text):
        email = match.group(0)
        key = email.casefold()
        count = seen.get(key, 0) + 1
        seen[key] = count
        parts.append(text[last : match.start()])
        if count == 1:
            parts.append(email)
        else:
            parts.append(
                "[VERIFY: distinct reference contact — name, title, org, phone, "
                "email from KB (duplicate placeholder email removed)]"
            )
            replaced += 1
        last = match.end()
    parts.append(text[last:])
    if not replaced:
        return text, logs

    # Extra: if the only emails present are agency placeholders and ≥2, flag.
    if sum(1 for k, c in seen.items() if _AGENCY_PLACEHOLDER_EMAIL_RE.search(k)) >= 1 and replaced:
        logs.append(
            f"Replaced {replaced} duplicate reference email(s) with VERIFY "
            "(need three distinct client contacts)"
        )
    else:
        logs.append(f"Replaced {replaced} duplicate reference email(s) with VERIFY")
    return "".join(parts), logs


def _phase_signature(content: str) -> set[str]:
    sig: set[str] = set()
    for match in _PHASE_HEADING_RE.finditer(content or ""):
        num = match.group(1)
        label = re.sub(r"\s+", " ", (match.group(2) or "").strip().casefold())
        label = re.split(r"\(|—|–|-", label, maxsplit=1)[0]
        label = re.sub(r"[^a-z0-9 ]", "", label).strip()
        # First 3 distinctive words so "Discovery & Research (Weeks 1-2)"
        # matches "Discovery & Research".
        words = [w for w in label.split() if len(w) > 2][:3]
        sig.add(f"{num}:{' '.join(words)}")
    return sig


def compress_schedule_restating_approach(
    sections: list[ProposalSection],
) -> tuple[list[ProposalSection], int]:
    """If Schedule restates Approach's five-phase essay, collapse to calendar job."""
    approach_bodies: list[str] = []
    for section in sections:
        title = section.title or ""
        if _APPROACH_TITLE_RE.search(title) and not _SCHEDULE_TITLE_RE.search(title):
            approach_bodies.append(section.content or "")

    if not approach_bodies:
        return sections, 0

    approach_sig: set[str] = set()
    for body in approach_bodies:
        approach_sig |= _phase_signature(body)
    if len(approach_sig) < 3:
        return sections, 0

    out: list[ProposalSection] = []
    compressed = 0
    for section in sections:
        title = section.title or ""
        body = section.content or ""
        if not _SCHEDULE_TITLE_RE.search(title):
            out.append(section)
            continue
        sched_sig = _phase_signature(body)
        if len(sched_sig) < 3:
            out.append(section)
            continue
        overlap = len(sched_sig & approach_sig) / max(len(sched_sig), 1)
        # Same Phase 1–N labels = methodology clone, not a calendar.
        # Even if weeks are present, overlapping phase titles mean Schedule
        # restated Approach instead of owning dates-only.
        if overlap < 0.5:
            out.append(section)
            continue

        approach_title = next(
            (
                s.title
                for s in sections
                if _APPROACH_TITLE_RE.search(s.title or "")
                and not _SCHEDULE_TITLE_RE.search(s.title or "")
            ),
            "Project Approach",
        )
        stub = _schedule_calendar_stub(title, approach_title, window_weeks=None)
        out.append(section.model_copy(update={"content": stub}))
        compressed += 1
        logger.info(
            "Compressed schedule section %s — restated Approach phases (overlap=%.2f)",
            section.id,
            overlap,
        )
    return out, compressed


def max_week_number_claimed(content: str) -> int:
    """Highest week index / N-week span claimed in manuscript prose."""
    text = content or ""
    mx = 0
    for match in _WEEK_RANGE_RE.finditer(text):
        mx = max(mx, int(match.group(1)), int(match.group(2)))
    for match in _WEEK_SINGLE_RE.finditer(text):
        mx = max(mx, int(match.group(1)))
    for match in _N_WEEK_PLAN_RE.finditer(text):
        n = int(match.group(1))
        # Ignore tiny phrases like "1 week turnaround" for ceiling checks —
        # only treat as a plan length when ≥ 4.
        if n >= 4:
            mx = max(mx, n)
    return mx


def infer_rfp_delivery_window_weeks(rfp_text: str) -> int | None:
    """Best-effort weeks available after award before launch. None = unknown."""
    blob = rfp_text or ""
    if not blob.strip():
        return None
    candidates: list[int] = []
    for match in _WITHIN_WEEKS_OF_AWARD_RE.finditer(blob):
        candidates.append(int(match.group(1)))
    for match in _WEEKS_FROM_AWARD_RE.finditer(blob):
        candidates.append(min(int(match.group(1)), int(match.group(2))))
        candidates.append(max(int(match.group(1)), int(match.group(2))))
    if candidates:
        return min(candidates)
    # RFP ties award to launch but never states an explicit week count —
    # use a conservative sequential ceiling so 8–10 week inventions get caught.
    if _LAUNCH_CONSTRAINT_RE.search(blob):
        return 6
    # Broader: award + launch language without a multi-month/year contract term.
    has_award = bool(
        re.search(
            r"(?i)\baward\b|\bnotice\s+to\s+proceed\b|\bcontract\s+execution\b",
            blob,
        )
    )
    has_launch = bool(
        re.search(r"(?i)\blaunch\b|go[- ]live|campaign\s+start|\bcommence\b", blob)
    )
    long_contract = bool(
        re.search(
            r"(?i)\b(?:12|18|24)\s*[-–—]?\s*months?\b|\bone[- ]year\b|\bmulti[- ]?year\b|"
            r"\bannual\s+contract\b",
            blob,
        )
    )
    if has_award and has_launch and not long_contract:
        return 6
    return None


_WRITER_LEAK_LINE_RE = re.compile(
    r"(?im)^\s*(?:do not restate\b.*|"
    r"\*\*RFP constraint:\*\*.*|"
    r"delivery calendar for this engagement \(dates and checkpoints only.*)\s*$"
)
_EMPTY_TIMING_CELL_RE = re.compile(
    r"(?im)^\|\s*[^|\n]+\s*\|\s*\|\s*[^|\n]*\|"
)
_VERIFY_TIMING_RE = re.compile(r"(?i)\[VERIFY:[^\]]*(?:week|dates|window|launch)[^\]]*\]")


def _fmt_week_span(start: int, end: int) -> str:
    if start >= end:
        return f"Week {end} after award"
    return f"Weeks {start}–{end} after award"


def _schedule_timing_rows(window_weeks: int | None) -> list[tuple[str, str, str]]:
    """Four overlapping phases a designer can typeset. Relative weeks, not invented dates."""
    if window_weeks and window_weeks >= 4:
        w = int(window_weeks)
        d_end = max(1, round(w * 0.25))
        s_end = max(d_end + 1, round(w * 0.50))
        c_end = max(s_end, round(w * 0.80))
        return [
            ("Discovery", _fmt_week_span(1, d_end), "Kickoff complete"),
            ("Strategy", _fmt_week_span(d_end, s_end), "Framework approval"),
            ("Creative / production", _fmt_week_span(s_end, c_end), "Creative approval"),
            (
                "Launch / handoff",
                f"Week {w} after award (RFP launch window)",
                "Launch-ready",
            ),
        ]
    return [
        ("Discovery", "Starts at award", "Kickoff complete"),
        ("Strategy", "Overlaps Discovery; locks before creative", "Framework approval"),
        ("Creative / production", "After framework lock; before launch", "Creative approval"),
        ("Launch / handoff", "By the RFP launch / go-live date", "Launch-ready"),
    ]


def _schedule_calendar_stub(
    title: str,
    approach_title: str,
    window_weeks: int | None,
) -> str:
    rows = _schedule_timing_rows(window_weeks)
    table = "| Phase | Timing | Milestone |\n| --- | --- | --- |\n" + "".join(
        f"| {phase} | {timing} | {milestone} |\n" for phase, timing, milestone in rows
    )
    if window_weeks and window_weeks >= 4:
        lead = (
            f"Workstreams overlap so launch lands in the RFP window "
            f"({window_weeks} weeks from award). Phase method lives in {approach_title}."
        )
    else:
        lead = (
            f"Timing is weeks from award through the RFP launch date. "
            f"Phase method lives in {approach_title}."
        )
    note = (
        "[DESIGNER NOTE: Typeset as a 4-row calendar. Columns: Phase | Timing | "
        "Milestone. Timing is weeks from award — never leave Timing blank.]"
    )
    return f"## {title}\n\n{lead}\n\n{table}\n{note}\n"


def _schedule_needs_designer_polish(body: str) -> bool:
    text = body or ""
    if _WRITER_LEAK_LINE_RE.search(text):
        return True
    if _VERIFY_TIMING_RE.search(text):
        return True
    if _EMPTY_TIMING_CELL_RE.search(text) and re.search(
        r"(?i)\|\s*timing\s*\|", text
    ):
        return True
    return False


def polish_schedule_tabs_for_designer(
    sections: list[ProposalSection],
    *,
    rfp_text: str = "",
) -> tuple[list[ProposalSection], list[str]]:
    """Replace writer-facing calendar stubs with a filled table a designer can layout."""
    logs: list[str] = []
    window = infer_rfp_delivery_window_weeks(rfp_text)
    approach_title = next(
        (
            s.title
            for s in sections
            if _APPROACH_TITLE_RE.search(s.title or "")
            and not _SCHEDULE_TITLE_RE.search(s.title or "")
        ),
        "Project Approach",
    )
    out: list[ProposalSection] = []
    for section in sections:
        title = section.title or ""
        body = section.content or ""
        if not _SCHEDULE_TITLE_RE.search(title) or not _schedule_needs_designer_polish(body):
            out.append(section)
            continue
        stub = _schedule_calendar_stub(title, approach_title, window)
        out.append(section.model_copy(update={"content": stub}))
        logs.append(
            f"{section.id}: Schedule/Timeline rewritten as a filled calendar "
            "(no blank Timing cells, no writer instructions)"
        )
    return out, logs


def scrub_schedule_calendar_overrun(
    sections: list[ProposalSection],
    *,
    rfp_text: str = "",
) -> tuple[list[ProposalSection], list[str]]:
    """Collapse Schedule (and strip Approach week labels) that overrun the RFP window.

    Complements compress_schedule_restating_approach — catches 8–10 week inventions
    even when phase titles do not overlap Approach.
    """
    logs: list[str] = []
    window = infer_rfp_delivery_window_weeks(rfp_text)
    if window is None:
        return sections, logs

    approach_title = next(
        (
            s.title
            for s in sections
            if _APPROACH_TITLE_RE.search(s.title or "")
            and not _SCHEDULE_TITLE_RE.search(s.title or "")
        ),
        "Project Approach",
    )

    out: list[ProposalSection] = []
    for section in sections:
        title = section.title or ""
        body = section.content or ""
        max_week = max_week_number_claimed(body)
        if max_week <= window:
            out.append(section)
            continue

        if _SCHEDULE_TITLE_RE.search(title):
            stub = _schedule_calendar_stub(title, approach_title, window)
            out.append(section.model_copy(update={"content": stub}))
            logs.append(
                f"{section.id}: Schedule claimed Week {max_week} but RFP "
                f"award→launch window is ~{window} weeks — replaced with "
                "a filled week-from-award calendar"
            )
            continue

        if _APPROACH_TITLE_RE.search(title):
            # Keep methodology; strip invented week spans that overrun the window.
            cleaned, n_paren = _WEEK_PAREN_RE.subn(
                f"(timing: within the {window}-week RFP launch window)",
                body,
            )
            # Bare "Weeks 8-9" / "Week 10" outside parens
            cleaned2, n_range = _WEEK_RANGE_RE.subn(
                f"within the {window}-week RFP launch window",
                cleaned,
            )
            cleaned3, n_single = _WEEK_SINGLE_RE.subn(
                f"within the {window}-week RFP launch window",
                cleaned2,
            )
            if n_paren + n_range + n_single:
                banner = (
                    f"Timing fits a {window}-week award-to-launch window "
                    f"(a sequential {max_week}-week plan does not).\n\n"
                )
                if "Timing fits a" not in cleaned3:
                    cleaned3 = banner + cleaned3
                out.append(section.model_copy(update={"content": cleaned3}))
                logs.append(
                    f"{section.id}: Approach claimed Week {max_week} vs ~{window}-week "
                    "RFP window — replaced week labels (kept methodology)"
                )
                continue

        out.append(section)

    return out, logs


def format_rfp_calendar_constraint(
    *,
    rfp_due_date: str | None = None,
    rfp_context_excerpt: str = "",
    section_title: str = "",
) -> str:
    """Prompt block for Schedule/Timeline tabs — never invent a 10-week plan past launch."""
    if section_title and not _SCHEDULE_TITLE_RE.search(section_title):
        # Still useful for approach when both exist, but keep short for non-schedule.
        if not _APPROACH_TITLE_RE.search(section_title):
            return ""

    lines = [
        "## RFP CALENDAR CONSTRAINT (mandatory)",
        "- Schedule/Timeline tabs: dates, milestones, owners ONLY — do NOT rewrite "
        "Approach/methodology phase essays.",
        "- Fit the plan inside the RFP award → launch / contract window. If that window "
        "is short (weeks, not months), do NOT invent a sequential multi-month plan that "
        "overruns launch unless you explicitly state concurrent workstreams or "
        "post-launch phases. Never invent dates or durations absent from the RFP.",
    ]
    if rfp_due_date:
        lines.append(f"- Proposal submission due date: {rfp_due_date}")
    # Light extract of award/launch hints from RFP text
    blob = (rfp_context_excerpt or "")[:12000]
    for pat, label in (
        (r"(?i)(?:award|contract\s+execution|notice\s+to\s+proceed).{0,40}"
         r"(?:mid[- ]?september|september|october|by\s+[A-Z][a-z]+\s+\d{1,2})",
         "award/execution cue"),
        (r"(?i)(?:launch|go[- ]live|campaign\s+launch).{0,40}"
         r"(?:mid[- ]?september|september|by\s+[A-Z][a-z]+\s+\d{1,2})",
         "launch cue"),
    ):
        m = re.search(pat, blob)
        if m:
            cue = re.sub(r"\s+", " ", m.group(0)).strip()[:120]
            lines.append(f"- RFP {label}: {cue}")
    return "\n".join(lines)


_COVER_LETTER_TITLE_RE = re.compile(
    r"(?i)\b(?:cover\s+letter|letter\s+of\s+transmittal|transmittal\s+letter)\b"
)
_SIGNATURE_PAGE_TITLE_RE = re.compile(
    r"(?i)\b(?:signature|signatory|authorization|authorized\s+sign)\b"
)
_COST_TITLE_RE = re.compile(
    r"(?i)\b(?:cost|fee|pricing|budget|quotation)\b"
)
_CLOSING_PACKAGE_TITLE_RE = re.compile(
    r"(?i)\b(?:closing|submission|attachments?\s+checklist|required\s+forms|"
    r"forms?\s+(?:and|&)\s+attachments?)\b"
)
_SIGNED_COVER_LABEL_RE = re.compile(
    r"(?i)signed\s+cover|letter\s+of\s+transmittal|physically\s+signed"
)
_SIGNATURE_PAGE_LABEL_RE = re.compile(
    r"(?i)authorized\s+signature|signature\s+page|signature\s+block|wet[\s-]?ink"
)
_ALREADY_HAS_ATTACH_PDF_NOTE_RE = re.compile(
    r"(?i)\[DESIGNER\s+NOTE:[^\]]*(?:attach|signed\s+pdf|physically\s+signed|"
    r"wet[- ]?ink|buyer[- ]?required\s+signature|signature\s+page)[^\]]*\]"
)

_SIGNED_COVER_DESIGNER_NOTE = (
    "[DESIGNER NOTE: Attach the physically signed cover letter / letter of "
    "transmittal PDF as a separate submission document. Do not invent signature "
    "dates, notary numbers, stamp IDs, certificate numbers, or dollar figures — "
    "use only the real signed file and figures stated in the RFP or knowledge base.]"
)
_SIGNATURE_PAGE_DESIGNER_NOTE = (
    "[DESIGNER NOTE: Attach the authorized signature page / wet-ink signature "
    "PDF required by the RFP. Do not invent signature dates, titles, or notary "
    "numbers — use only the real signed buyer form.]"
)


def _signed_cover_required(
    attachment_labels: list[str] | None,
    rfp_text: str,
) -> bool:
    for label in attachment_labels or []:
        if _SIGNED_COVER_LABEL_RE.search(label or ""):
            return True
    blob = (rfp_text or "")[:80_000]
    if not blob.strip():
        return False
    return bool(
        re.search(
            r"(?i)(?:physically\s+)?signed\s+cover\s+letter|"
            r"cover\s+letter\s+(?:must\s+be\s+)?signed|"
            r"signed\s+letter\s+of\s+transmittal|"
            r"original\s+signature\s+on\s+(?:the\s+)?cover\s+letter",
            blob,
        )
    )


def _signature_page_required(
    attachment_labels: list[str] | None,
    rfp_text: str,
) -> bool:
    for label in attachment_labels or []:
        if _SIGNATURE_PAGE_LABEL_RE.search(label or ""):
            return True
    blob = (rfp_text or "")[:80_000]
    if not blob.strip():
        return False
    return bool(
        re.search(
            r"(?i)authorized\s+(?:representative|signatory|signature)|"
            r"signature\s+(?:block|page)|wet[\s-]?ink\s+signature",
            blob,
        )
    )


def _inject_designer_note_on_section(
    sections: list[ProposalSection],
    *,
    target_idx: int,
    note: str,
    designer_field: str,
    allow_alongside_existing_attach_note: bool = False,
) -> tuple[list[ProposalSection], bool]:
    section = sections[target_idx]
    body = section.content or ""
    if not allow_alongside_existing_attach_note:
        if _ALREADY_HAS_ATTACH_PDF_NOTE_RE.search(body):
            return sections, False
        if _ALREADY_HAS_ATTACH_PDF_NOTE_RE.search(section.designer_note or ""):
            return sections, False
    updates: dict[str, Any] = {"content": f"{note}\n\n{body}".strip()}
    if not (section.designer_note or "").strip():
        updates["designer_note"] = designer_field
    sections[target_idx] = section.model_copy(update=updates)
    return sections, True


def ensure_signed_cover_designer_note(
    draft: ProposalDraft,
    *,
    attachment_labels: list[str] | None = None,
    rfp_text: str = "",
) -> tuple[ProposalDraft, list[str]]:
    """Add DESIGNER NOTEs for signed cover + authorized signature PDFs.

    Manuscript-side fix for attachment DQ items: handoff is complete when the
    note exists. Physical files still must be attached by a human.
    """
    logs: list[str] = []
    sections = list(draft.sections)
    if not sections:
        return draft, logs

    if _signed_cover_required(attachment_labels, rfp_text):
        target_idx: int | None = None
        for idx, section in enumerate(sections):
            if _COVER_LETTER_TITLE_RE.search(section.title or ""):
                target_idx = idx
                break
        if target_idx is None:
            for idx, section in enumerate(sections):
                if _COVER_LETTER_TITLE_RE.search(
                    f"{section.title or ''}\n{section.content or ''}"
                ):
                    target_idx = idx
                    break
        if target_idx is None:
            for idx, section in enumerate(sections):
                if _CLOSING_PACKAGE_TITLE_RE.search(section.title or ""):
                    target_idx = idx
                    break
        if target_idx is None:
            target_idx = 0
        sections, changed = _inject_designer_note_on_section(
            sections,
            target_idx=target_idx,
            note=_SIGNED_COVER_DESIGNER_NOTE,
            designer_field=(
                "Attach the physically signed cover letter / letter of transmittal PDF. "
                "Do not invent signature dates, notary numbers, or figures."
            ),
        )
        if changed:
            logs.append(
                f"{sections[target_idx].id}: added DESIGNER NOTE to attach signed cover letter PDF"
            )

    if _signature_page_required(attachment_labels, rfp_text):
        target_idx = None
        for idx, section in enumerate(sections):
            if _SIGNATURE_PAGE_TITLE_RE.search(section.title or "") and not _COVER_LETTER_TITLE_RE.search(
                section.title or ""
            ):
                target_idx = idx
                break
        if target_idx is None:
            for idx, section in enumerate(sections):
                if _CLOSING_PACKAGE_TITLE_RE.search(section.title or ""):
                    target_idx = idx
                    break
        if target_idx is None:
            for idx, section in enumerate(sections):
                if _COVER_LETTER_TITLE_RE.search(section.title or ""):
                    target_idx = idx
                    break
        if target_idx is None:
            # Never stamp wet-ink / signature DESIGNER NOTEs onto Budget & Pricing
            # — that painted the fee tab "needs input" and looked like Complete &
            # Clean broke a finished budget (Providence). Prefer any non-cost tab.
            for idx in range(len(sections) - 1, -1, -1):
                if not _COST_TITLE_RE.search(sections[idx].title or ""):
                    target_idx = idx
                    break
        if target_idx is None:
            target_idx = 0
        # Avoid double-stacking identical attach notes on same section
        body = sections[target_idx].content or ""
        if "authorized signature page" not in body.casefold():
            # Cover may already have the signed-cover attach note; still allow the
            # distinct wet-ink signature-page note (never put it on Budget/Cost).
            allow_stack = bool(_COVER_LETTER_TITLE_RE.search(sections[target_idx].title or ""))
            sections, changed = _inject_designer_note_on_section(
                sections,
                target_idx=target_idx,
                note=_SIGNATURE_PAGE_DESIGNER_NOTE,
                designer_field=(
                    "Attach the authorized signature page / wet-ink signature PDF. "
                    "Do not invent signature dates or notary numbers."
                ),
                allow_alongside_existing_attach_note=allow_stack,
            )
            if changed:
                logs.append(
                    f"{sections[target_idx].id}: added DESIGNER NOTE to attach "
                    "authorized signature page PDF"
                )

    if not logs:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def apply_first_pass_manuscript_polish(
    draft: ProposalDraft,
    *,
    research: ProposalResearchCache | None = None,
    rfp_text: str = "",
    skip_section_ids: set[str] | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """Deterministic polish so first Generate lands closer to Scan quality.

    Covers consistency (primary contact, refs, schedule compress), signed-cover
    DESIGNER NOTE, and cert overclaim scrub. Never invents numbers.
    """
    from app.services.proposal_cert_claim_scrub import apply_cert_claim_scrub_to_draft
    from app.services.proposal_rfp_submission_requirements import (
        outstanding_submission_checklist_for_scan,
    )

    logs: list[str] = []
    skip = skip_section_ids or set()
    outstanding = outstanding_submission_checklist_for_scan(rfp_text, draft)
    draft, consistency_logs = apply_consistency_enforcement(
        draft,
        research=research,
        attachment_labels=list(outstanding.needs_attachment),
        rfp_text=rfp_text,
    )
    logs.extend(consistency_logs)
    draft, cert_logs = apply_cert_claim_scrub_to_draft(
        draft, skip_section_ids=skip
    )
    logs.extend(cert_logs)
    return draft, logs


def apply_consistency_enforcement(
    draft: ProposalDraft,
    *,
    research: ProposalResearchCache | None = None,
    attachment_labels: list[str] | None = None,
    rfp_text: str = "",
) -> tuple[ProposalDraft, list[str]]:
    """Run deterministic consistency scrubs across the manuscript."""
    logs: list[str] = []
    locks: ManuscriptLocks | None = research.manuscript_locks if research else None
    locked_name = (locks.primary_contact_name if locks else "") or ""

    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        title_cf = (section.title or "").casefold()
        new = body
        section_logs: list[str] = []

        if locked_name:
            new, lock_logs = scrub_conflicting_primary_contact(
                new, locked_name=locked_name
            )
            section_logs.extend(lock_logs)

        if "reference" in title_cf or "qualif" in title_cf or "experience" in title_cf:
            new, email_logs = scrub_duplicate_reference_emails(new)
            section_logs.extend(email_logs)

        if new != body:
            changed = True
            sections.append(section.model_copy(update={"content": new}))
            for line in section_logs:
                logs.append(f"{section.id}: {line}")
        else:
            sections.append(section)

    sections2, n_sched = compress_schedule_restating_approach(sections)
    if n_sched:
        changed = True
        logs.append(
            f"Compressed {n_sched} Schedule/Timeline section(s) that restated Approach"
        )
        sections = sections2

    sections3, overrun_logs = scrub_schedule_calendar_overrun(
        sections, rfp_text=rfp_text
    )
    if overrun_logs:
        changed = True
        logs.extend(overrun_logs)
        sections = sections3

    sections4, polish_logs = polish_schedule_tabs_for_designer(
        sections, rfp_text=rfp_text
    )
    if polish_logs:
        changed = True
        logs.extend(polish_logs)
        sections = sections4

    working = draft.model_copy(update={"sections": sections}) if changed else draft

    from app.services.proposal_section_dedup import compress_rfp_company_identity_forms

    working, company_logs = compress_rfp_company_identity_forms(working)
    if company_logs:
        changed = True
        logs.extend(company_logs)

    working, note_logs = ensure_signed_cover_designer_note(
        working,
        attachment_labels=attachment_labels,
        rfp_text=rfp_text,
    )
    if note_logs:
        changed = True
        logs.extend(note_logs)

    if not changed:
        return draft, logs
    return working, logs
