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
    fact_contradiction_count: int = 0
    fact_contradiction_rewrites: int = 0
    fact_contradiction_unresolved: int = 0
    fact_contradiction_unresolved_titles: list[str] = field(default_factory=list)
    budget_contradiction_count: int = 0
    budget_contradiction_rewrites: int = 0
    budget_contradiction_unresolved: int = 0
    budget_contradiction_unresolved_titles: list[str] = field(default_factory=list)


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

    # Always — every generate + Complete Scan. Signed insurance certifications
    # must match Section 1.5 inventory before LLM contradiction passes.
    try:
        from app.services.proposal_scan_insurance_certification import (
            gate_draft_insurance_certifications,
        )

        draft, ins_logs, ins_human = gate_draft_insurance_certifications(draft)
        logs.extend(ins_logs)
        for gap in ins_human:
            logs.append(f"HUMAN_GAP: {gap}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Insurance certification gate skipped: %s", exc)
        logs.append(f"insurance certification gate skipped: {exc}")

    contradiction_count = 0
    contradiction_rewrites = 0
    contradiction_unresolved = 0
    contradiction_unresolved_titles: list[str] = []
    fact_contradiction_count = 0
    fact_contradiction_rewrites = 0
    fact_contradiction_unresolved = 0
    fact_contradiction_unresolved_titles: list[str] = []
    budget_contradiction_count = 0
    budget_contradiction_rewrites = 0
    budget_contradiction_unresolved = 0
    budget_contradiction_unresolved_titles: list[str] = []

    # ONE combined detection call for all three dimensions (fact / RFP / budget),
    # instead of three separate full-manuscript audits. Each pass below then
    # APPLIES its own findings via precomputed_raw — the per-finding rewrite
    # logic (and its tests) are unchanged; only the detection is consolidated.
    # If the combined call fails, precomputed stays None and each pass runs its
    # own detection exactly as before (safe fallback, no behavior change).
    fact_precomputed: dict | None = None
    rfp_precomputed: dict | None = None
    budget_precomputed: dict | None = None
    if use_llm_contradiction and rfp is not None:
        try:
            from app.services.proposal_combined_contradiction_audit import (
                detect_all_contradictions,
            )

            combined = await detect_all_contradictions(
                draft, rfp=rfp, rfp_text=rfp_text, research=research
            )
            if combined is not None:
                fact_precomputed, rfp_precomputed, budget_precomputed = combined
                logs.append("Contradiction detection: one combined LLM pass (fact + RFP + budget).")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Combined contradiction detection skipped: %s", exc)

    if use_llm_contradiction and rfp is not None:
        try:
            from app.services.proposal_manuscript_fact_contradictions import (
                run_manuscript_fact_contradiction_pass,
            )

            fact = await run_manuscript_fact_contradiction_pass(
                draft,
                rfp=rfp,
                use_llm=True,
                precomputed_raw=fact_precomputed,
            )
            draft = fact.draft
            logs.extend(fact.logs)
            fact_contradiction_count = len(fact.findings)
            fact_contradiction_rewrites = fact.rewrites_applied
            fact_contradiction_unresolved = len(fact.unresolved_findings)
            fact_contradiction_unresolved_titles = [
                f.banner_line() for f in fact.unresolved_findings[:8]
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Manuscript fact-contradiction pass skipped: %s", exc)
            logs.append(f"fact-contradiction suite skipped: {exc}")

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
                precomputed_raw=rfp_precomputed,
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

    if use_llm_contradiction and rfp is not None:
        try:
            from app.services.proposal_manuscript_budget_contradictions import (
                run_manuscript_budget_contradiction_pass,
            )

            budget_audit = await run_manuscript_budget_contradiction_pass(
                draft,
                rfp=rfp,
                research=research,
                use_llm=True,
                precomputed_raw=budget_precomputed,
            )
            draft = budget_audit.draft
            logs.extend(budget_audit.logs)
            budget_contradiction_count = len(budget_audit.findings)
            budget_contradiction_rewrites = budget_audit.rewrites_applied
            budget_contradiction_unresolved = len(budget_audit.unresolved_findings)
            budget_contradiction_unresolved_titles = [
                f.banner_line() for f in budget_audit.unresolved_findings[:8]
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Budget cross-section pass skipped: %s", exc)
            logs.append(f"budget cross-section suite skipped: {exc}")

    total_unresolved = (
        fact_contradiction_unresolved
        + contradiction_unresolved
        + budget_contradiction_unresolved
    )
    all_unresolved_titles = (
        fact_contradiction_unresolved_titles
        + contradiction_unresolved_titles
        + budget_contradiction_unresolved_titles
    )[:12]

    return BlockerPreventionResult(
        draft=draft,
        logs=logs,
        contradiction_count=(
            fact_contradiction_count + contradiction_count + budget_contradiction_count
        ),
        contradiction_rewrites=(
            fact_contradiction_rewrites
            + contradiction_rewrites
            + budget_contradiction_rewrites
        ),
        contradiction_unresolved=total_unresolved,
        contradiction_unresolved_titles=all_unresolved_titles,
        fact_contradiction_count=fact_contradiction_count,
        fact_contradiction_rewrites=fact_contradiction_rewrites,
        fact_contradiction_unresolved=fact_contradiction_unresolved,
        fact_contradiction_unresolved_titles=fact_contradiction_unresolved_titles,
        budget_contradiction_count=budget_contradiction_count,
        budget_contradiction_rewrites=budget_contradiction_rewrites,
        budget_contradiction_unresolved=budget_contradiction_unresolved,
        budget_contradiction_unresolved_titles=budget_contradiction_unresolved_titles,
    )
