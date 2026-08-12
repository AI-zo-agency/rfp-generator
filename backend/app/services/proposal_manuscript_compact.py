"""Compact overlong proposal sections into designer-ready layout (Senior Editor pass)."""

from __future__ import annotations

import re
from typing import Any

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_section_quality import word_count

# Trigger compact when draft exceeds target or reads like an essay (layout problem).
_OVERSHOOT_RATIO = 1.02
_DEFAULT_WORD_CEILING = 420
_ESSAY_WALL_MIN_WORDS = 200

# Structural signals — essay walls, not title keywords.
_HEADING_RE = re.compile(r"^#{1,4}\s+\S", re.MULTILINE)
_LABELLED_BLOCK_RE = re.compile(r"(?im)^\*\w[\w\s/&-]{0,40}:\*")
_PHASE_BLOCK_RE = re.compile(r"(?im)^(?:#{1,4}\s*)?phase\s+\d+\b")
_MANUAL_FILL_RE = re.compile(r"\[MANUAL FILL", re.IGNORECASE)


def _section_mode(section: ProposalSection) -> str:
    return str(section.mode or "").casefold()


def section_skip_compact(section: ProposalSection) -> bool:
    """Tabs that should stay as-is (checklists, templates, canon blocks)."""
    sid = section.id or ""
    from app.services.proposal_section_dedup import (
        _is_protected_budget_section,
        _is_static_cq_section_id,
    )

    if _is_static_cq_section_id(sid):
        return True
    if _is_protected_budget_section(section):
        return True
    if sid.startswith("section-2-bio-"):
        return True
    if _section_mode(section) in {"pull", "select", "template"}:
        return True

    body = (section.content or "").strip()
    if not body:
        return True
    # Attachment / form checklists — designer inserts files, not prose essays.
    if body.count("[MANUAL FILL") >= 2 or len(_MANUAL_FILL_RE.findall(body)) >= 2:
        manual_ratio = len(_MANUAL_FILL_RE.findall(body)) / max(1, body.count("\n") + 1)
        if manual_ratio > 0.08 or word_count(body) < 220:
            return True
    return False


def section_compact_ceiling(section: ProposalSection) -> int:
    wt = section.word_target
    if isinstance(wt, int) and wt > 0:
        return wt
    return _DEFAULT_WORD_CEILING


def _content_is_essay_wall(content: str) -> bool:
    wc = word_count(content)
    if wc < _ESSAY_WALL_MIN_WORDS:
        return False
    headings = _HEADING_RE.findall(content)
    if len(headings) >= 3:
        return True
    if len(_LABELLED_BLOCK_RE.findall(content)) >= 2:
        return True
    if len(_PHASE_BLOCK_RE.findall(content)) >= 2 and wc >= 200:
        return True
    paragraphs = [p for p in re.split(r"\n\s*\n", content) if p.strip()]
    long_paras = sum(1 for p in paragraphs if word_count(p) > 100)
    return long_paras >= 3


def section_needs_designer_compact(section: ProposalSection) -> bool:
    if section_skip_compact(section):
        return False
    content = (section.content or "").strip()
    if not content:
        return False
    wc = word_count(content)
    ceiling = section_compact_ceiling(section)
    threshold = max(int(ceiling * _OVERSHOOT_RATIO), ceiling + 40)
    essay = _content_is_essay_wall(content)
    if wc >= threshold:
        return True
    # Essay layout at or above target — compact for designer even if "only" at wordTarget.
    if essay and wc >= max(_ESSAY_WALL_MIN_WORDS, int(ceiling * 0.75)):
        return True
    return False


def list_sections_needing_compact(draft: ProposalDraft) -> list[ProposalSection]:
    return [s for s in draft.sections if section_needs_designer_compact(s)]


def build_designer_compact_brief(section: ProposalSection) -> str:
    ceiling = section_compact_ceiling(section)
    wc = word_count(section.content or "")
    # Allow full wordTarget when many RFP asks — compact means DENSE layout, not dropped facts.
    aim = ceiling if wc > ceiling else max(220, int(ceiling * 0.82))
    return (
        f"DESIGNER-COMPACT REWRITE — «{section.title}».\n"
        f"Current draft: ~{wc} words. Layout budget: up to {ceiling} words — use what you "
        f"need (~{aim} typical) but NEVER drop an RFP ask to save space.\n\n"
        "RULE: Concise LAYOUT, COMPLETE substance. Compress format — not requirements.\n\n"
        "MANDATORY:\n"
        "• Cover EVERY scored/required ask for this tab (same coverage as the long draft).\n"
        "• If the RFP lists many items, put them in a dense table or Q&A grid — one row per "
        "requirement — do NOT omit rows to shorten.\n"
        "• Preserve every open [VERIFY] / [MANUAL FILL] and every numeric/date/fee the RFP needs.\n\n"
        "FORMAT (designer paste-ready for InDesign):\n"
        "1. Lead: 1–3 tight sentences — what this tab proves.\n"
        "2. Body: markdown tables, short bullets, or labeled rows (matrix, phases, references, "
        "Q&A). Same fact once — not in prose AND bullets.\n"
        "3. Visual handoff: [DESIGNER NOTE: …] with exact columns/data for timelines, grids, "
        "comparisons — replace essay paragraphs the designer would layout as graphics.\n"
        "4. Cut ONLY filler, RFP restatement, and facts owned by other tabs — never cut substance.\n"
        "5. No repeated subsection essays (*Activities:* walls under every heading).\n"
        "6. Set designerNote in JSON for the primary layout hint.\n"
        "7. zö voice (we/our in narrative tabs)."
    )


def _has_compact_layout(content: str) -> bool:
    if "|" in content and content.count("|") >= 4:
        return True
    if "[DESIGNER NOTE" in content.upper():
        return True
    bullets = len(re.findall(r"(?m)^[-*]\s+\S", content))
    return bullets >= 3


def is_designer_compact_improvement(
    before: ProposalSection,
    after: ProposalSection,
) -> bool:
    """True when a repair intentionally shortened essay prose into layout-ready copy."""
    prior = (before.content or "").strip()
    new = (after.content or "").strip()
    if not prior or not new or new == prior:
        return False

    bw = word_count(prior)
    aw = word_count(new)
    if aw < 25:
        return False

    ceiling = section_compact_ceiling(before)
    before_over = bw > int(ceiling * 1.05) or _content_is_essay_wall(prior)
    if not before_over:
        return False

    layout = _has_compact_layout(new)
    if aw < 80 and not layout:
        return False
    closer = abs(aw - ceiling) < abs(bw - ceiling)
    at_ceiling = aw <= int(ceiling * 1.1)
    shorter = aw <= int(bw * 0.82)

    if shorter and (layout or at_ceiling or closer):
        return True
    if at_ceiling and layout and closer:
        return True
    return False


def user_requests_designer_compact(message: str) -> bool:
    m = (message or "").casefold()
    return any(
        phrase in m
        for phrase in (
            "designer compact",
            "designer-compact",
            "designer ready",
            "designer-ready",
            "make it compact",
            "too long",
            "essay",
            "shorter",
            "concise",
            "layout ready",
            "table format",
        )
    )


def merge_compact_tickets(
    tickets: dict[str, Any],
    draft: ProposalDraft,
) -> dict[str, Any]:
    """Inject compactFormatTickets for any overlong, non-checklist tab."""
    existing_ids = {
        str(t.get("sectionId") or "")
        for bucket in (
            tickets.get("compactFormatTickets") or [],
            tickets.get("dedupeTickets") or [],
            tickets.get("coverageTickets") or [],
            tickets.get("complianceTickets") or [],
        )
        for t in bucket
        if isinstance(t, dict)
    }
    compact: list[dict[str, str]] = list(tickets.get("compactFormatTickets") or [])
    for section in list_sections_needing_compact(draft):
        if section.id in existing_ids:
            continue
        compact.append(
            {
                "sectionId": section.id,
                "reason": (
                    f"{word_count(section.content or '')}w vs "
                    f"{section_compact_ceiling(section)}w target"
                ),
                "rewriteBrief": build_designer_compact_brief(section),
            }
        )
        existing_ids.add(section.id)
    if compact:
        tickets = dict(tickets)
        tickets["compactFormatTickets"] = compact
    return tickets
