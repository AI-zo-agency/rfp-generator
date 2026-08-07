"""Single feedback-blocker prevention suite for Scan RFP + Generate-from-scratch.

One entry point so the same guards always run: primary contact, duplicate refs,
schedule/calendar, cert overclaims, signed-cover designer note, case-study
filename titles, and LLM manuscript-vs-RFP contradictions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord

logger = logging.getLogger(__name__)

_FILE_EXT_RE = re.compile(r"(?i)\.(pdf|docx?|pptx?)$")
_PREFIX_RE = re.compile(
    r"(?i)^(0[0-9]_)?(?:cs_|won_|fin_|case[_ ]?study[_ ]?)+"
)
_JUNK_TOKEN_RE = re.compile(
    r"(?i)\b(?:compressed|proposal|finalist|final|won|rfp|draft|v\d+)\b"
)
_SECTION3_ID_RE = re.compile(r"(?i)^section-3-work")
_FILENAMEISH_RE = re.compile(
    r"(?i)(?:0[0-9]_)?(?:cs_|won_|fin_)|_|compressed|\.pdf"
)


def clean_case_study_label(study: str, *, index: int | None = None) -> str:
    """Human case-study title — never raw 06_WON_…_compressed.pdf chips."""
    name = (study or "").strip()
    name = _FILE_EXT_RE.sub("", name)
    # Strip repeated bucket prefixes (06_WON_, 03_CS_, etc.)
    for _ in range(4):
        lower = name.casefold()
        stripped = False
        for prefix in (
            "06_won_",
            "07_fin_",
            "03_cs_",
            "02_cs_",
            "01_cs_",
            "cs_",
            "won_",
            "fin_",
            "06_",
            "07_",
            "03_",
            "02_",
            "01_",
        ):
            if lower.startswith(prefix):
                name = name[len(prefix) :]
                stripped = True
                break
        if not stripped:
            break
    name = name.replace("_", " ").replace("-", " ")
    name = _JUNK_TOKEN_RE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" -–—")
    # CamelCase → spaces
    spaced: list[str] = []
    for i, ch in enumerate(name):
        if i and ch.isupper() and name[i - 1].islower():
            spaced.append(" ")
        spaced.append(ch)
    name = "".join(spaced).strip()
    name = re.sub(r"(?i)\bcityof\b", "City of", name)
    name = re.sub(r"(?i)\bcountyof\b", "County of", name)
    name = re.sub(r"\s+", " ", name).strip()
    if name and (name == name.upper() or name.islower()):
        name = name.title()
    if not name:
        name = f"Case study {index}" if index is not None else "Case study"
    if index is not None:
        return f"3.{index} — {name}"
    return name


def scrub_case_study_section_titles(draft: ProposalDraft) -> tuple[ProposalDraft, list[str]]:
    """Rename filename-like Section 3 titles to clean display labels."""
    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    cs_index = 0
    for section in draft.sections:
        title = section.title or ""
        is_cs = bool(_SECTION3_ID_RE.match(section.id or "")) or (
            "3." in title[:4] and _FILENAMEISH_RE.search(title)
        )
        if not is_cs or not _FILENAMEISH_RE.search(title):
            sections.append(section)
            continue
        cs_index += 1
        # Prefer raw study slug from id when title is already mangled
        raw = title
        m = re.search(r"(?i)3\.\d+\s*[—\-]\s*(.+)$", title)
        if m:
            raw = m.group(1).strip()
        elif section.id and "work-" in section.id:
            raw = section.id.split("work-", 1)[-1].replace("-", " ")
        cleaned = clean_case_study_label(raw, index=cs_index)
        if cleaned != title:
            changed = True
            logs.append(f"{section.id}: renamed case-study title → {cleaned}")
            sections.append(section.model_copy(update={"title": cleaned}))
        else:
            sections.append(section)
    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


@dataclass
class BlockerPreventionResult:
    draft: ProposalDraft
    logs: list[str] = field(default_factory=list)
    contradiction_count: int = 0
    contradiction_rewrites: int = 0
    contradiction_unresolved: int = 0
    contradiction_unresolved_titles: list[str] = field(default_factory=list)


async def apply_feedback_blocker_suite(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord | None = None,
    research: ProposalResearchCache | None = None,
    rfp_text: str = "",
    use_llm_contradiction: bool = True,
    skip_section_ids: set[str] | None = None,
) -> BlockerPreventionResult:
    """Run the full feedback-blocker suite (deterministic + optional LLM)."""
    from app.services.proposal_consistency_enforcement import (
        apply_first_pass_manuscript_polish,
    )

    logs: list[str] = []
    draft, title_logs = scrub_case_study_section_titles(draft)
    logs.extend(title_logs)

    draft, polish_logs = apply_first_pass_manuscript_polish(
        draft,
        research=research,
        rfp_text=rfp_text,
        skip_section_ids=skip_section_ids,
    )
    logs.extend(polish_logs)

    contradiction_count = 0
    contradiction_rewrites = 0
    contradiction_unresolved = 0
    contradiction_unresolved_titles: list[str] = []
    if use_llm_contradiction and rfp is not None and (rfp_text or "").strip():
        try:
            from app.services.proposal_scan_rfp_contradictions import (
                run_scan_rfp_contradiction_pass,
            )

            contra = await run_scan_rfp_contradiction_pass(
                draft,
                rfp=rfp,
                rfp_text=rfp_text,
                use_llm=True,
            )
            draft = contra.draft
            logs.extend(contra.logs)
            contradiction_count = len(contra.findings)
            contradiction_rewrites = contra.rewrites_applied
            contradiction_unresolved = len(contra.unresolved_findings)
            contradiction_unresolved_titles = [
                f.banner_line() for f in contra.unresolved_findings[:8]
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Feedback blocker contradiction pass skipped: %s", exc)
            logs.append(f"contradiction suite skipped: {exc}")

    return BlockerPreventionResult(
        draft=draft,
        logs=logs,
        contradiction_count=contradiction_count,
        contradiction_rewrites=contradiction_rewrites,
        contradiction_unresolved=contradiction_unresolved,
        contradiction_unresolved_titles=contradiction_unresolved_titles,
    )
