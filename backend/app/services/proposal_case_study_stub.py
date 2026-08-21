"""Case-study stub + designer-note helpers (Option B — no in-manuscript rewrite).

Section 3 used to redraft every selected case study as prose: one LLM call per
study producing Challenge / Solution / Client Voice, then a stack of scrubbers
to strip the metrics, quotes and overbuild that drafting invented on the way.
The approved case study PDF already tells that story better, and design places
it as a card — the manuscript copy was rewritten work competing with the real
asset.

Same shape as :mod:`proposal_bio_stub`: name the asset, say why it was picked,
hand it to design. Selection still does the thinking — the fit matcher runs
unchanged — but nothing here spends tokens re-narrating a story the PDF owns.
"""

from __future__ import annotations

import re

_CASE_STUDY_DESIGNER_NOTE_RE = re.compile(
    r"\[DESIGNER\s+NOTE:[^\]]*(?:Place approved case study|03_CS)[^\]]*\]",
    re.IGNORECASE,
)

# Section ids minted by the Section 3 builders.
_SECTION3_WORK_ID_RE = re.compile(r"(?i)^section-3-work-")


def case_study_asset_filename(title: str) -> str:
    """Approved case-study asset name for a KB title.

    Titles arrive as KB document names ("03_CS_Hampton_Lumber"); design needs
    the file, so the extension is added rather than the name being prettified.
    """
    raw = (title or "").strip()
    if not raw:
        return ""
    if re.search(r"(?i)\.(pdf|docx?|pptx?)$", raw):
        return raw
    return f"{raw}.pdf"


def format_case_study_stub_content(
    *,
    display_name: str,
    asset_filename: str = "",
    relevance: str = "",
    kb_available: bool = True,
) -> str:
    """Build the Our Work card stub. Writes no narrative — by design.

    No heading: the section already carries the study's display title, and a
    duplicate ``### Name`` line only competes with it in the exported document.
    """
    name = (display_name or "").strip() or "this case study"
    parts: list[str] = []

    fit = (relevance or "").strip()
    if fit:
        # Selection already computed this. Never write a new one here — an
        # invented "why this fits" is the fabrication the stub exists to avoid.
        parts.append(f"**Why this work is relevant:** {fit}")
        parts.append("")

    asset = (asset_filename or "").strip()
    if asset:
        parts.append(
            f"[DESIGNER NOTE: Place approved case study — {asset} — in Our Work "
            "(Section 3). Use the approved layout; do not rewrite the narrative, "
            "metrics or client quote in-manuscript.]"
        )
    else:
        parts.append(
            "[DESIGNER NOTE: Place approved case study in Our Work (Section 3). "
            "Use the approved layout; do not rewrite the narrative, metrics or "
            "client quote in-manuscript.]"
        )

    if not kb_available:
        parts.append("")
        parts.append(
            f"[MANUAL FILL: Ella — approved case study asset not found for {name}; "
            "attach when available.]"
        )

    return "\n".join(parts).strip() + "\n"


def is_case_study_designer_note(text: str) -> bool:
    return bool(_CASE_STUDY_DESIGNER_NOTE_RE.search(text or ""))


def looks_like_case_study_stub_body(content: str) -> bool:
    """True when the body is a designer-note card rather than drafted prose."""
    body = (content or "").strip()
    if not body:
        return False
    return is_case_study_designer_note(body)


def is_case_study_stub_section(section_id: str, content: str | None = None) -> bool:
    """True for a Section 3 Our Work card that is an intentional stub.

    Downstream repair passes (hollow-fill, adversarial repair, section editor)
    treat a short body as an unfinished section and try to write into it. These
    cards are finished — the asset is the deliverable.
    """
    if not _SECTION3_WORK_ID_RE.match(section_id or ""):
        return False
    if content is None:
        return True
    return looks_like_case_study_stub_body(content)
