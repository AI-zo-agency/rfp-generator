"""Pre-submission copy-paste scan + compliance checklist."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.proposal import (
    ComplianceCheckItem,
    PreSubmitIssue,
    PreSubmitReview,
    ProposalDraft,
    ProposalSection,
    ProposalResearchCache,
)
from app.models.rfp import RfpRecord
from app.services.proposal_brand_voice import classify_section_register
from app.services.proposal_consistency import scan_manuscript_consistency
from app.services.proposal_rfp_compliance import (
    compliance_gaps_to_presubmit_issues,
    requirement_likely_covered,
    scan_rfp_compliance_gaps,
)
from app.services.proposal_manuscript_cleanup import (
    GRAMMAR_GLITCH_RE,
    budget_mentions_subcontractors,
    deny_subcontractors_claimed,
)
from app.services.proposal_voice_enforcement import contains_vendor_language

# Common stale client names from zö portfolio (copy-paste scan)
_STALE_CLIENT_PATTERNS = (
    "maricopa county",
    "city of bend",
    "deschutes county",
    "santa clara",
    "oregon employment",
    "city of santa",
    "mcminnville",
    "el paso",
    "carbondale",
    "lake oswego",
    "tennessee board",
    "octa",
    "orange county transportation",
)

_PLACEHOLDER_RE = re.compile(
    r"\[(?:VERIFY|FLAG|DESIGNER NOTE|TBD|INSERT|PLACEHOLDER)[^\]]*\]",
    re.IGNORECASE,
)
_TEMPLATE_LEAK_RE = re.compile(
    r"\b(?:lorem ipsum|client name|insert (?:here|client)|xxx+|tbd\b|todo:)\b",
    re.IGNORECASE,
)


def _manuscript_text(draft: ProposalDraft) -> str:
    return "\n\n".join(
        f"## {s.title}\n{s.content}" for s in draft.sections if s.content.strip()
    )


def _rfp_context_blob(rfp: RfpRecord) -> str:
    """Client + title + location for allowlisting geography names in copy-paste scan."""
    return " ".join(
        part.strip()
        for part in (rfp.client, rfp.title, rfp.location or "")
        if part and part.strip()
    ).casefold()


def _is_stale_client_for_rfp(stale: str, rfp: RfpRecord) -> bool:
    """True when a portfolio name should be treated as wrong-client paste for this RFP."""
    client_lower = rfp.client.strip().casefold()
    context_lower = _rfp_context_blob(rfp)
    stale_lower = stale.casefold()

    if stale_lower in context_lower:
        return False

    client_tokens = [t for t in re.split(r"[\s,]+", client_lower) if len(t) > 3]
    if any(tok in stale_lower for tok in client_tokens):
        return False

    return True


def scan_section_issues(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
) -> list[PreSubmitIssue]:
    """Copy-paste + voice findings for a single section (used to gate auto-fix patches)."""
    mini = ProposalDraft(
        rfpId=rfp.id,
        sections=[section],
        updatedAt=datetime.now(timezone.utc).isoformat(),
    )
    issues: list[PreSubmitIssue] = []
    issues.extend(_scan_copy_paste(draft=mini, rfp=rfp))
    issues.extend(_scan_voice(draft=mini))
    return [i for i in issues if i.section_id == section.id]


def issue_score(issues: list[PreSubmitIssue]) -> tuple[int, int]:
    """Lower is better: (critical_count, total_count)."""
    critical = sum(1 for i in issues if i.severity == "critical")
    return critical, len(issues)


def fix_stale_client_references(content: str, rfp: RfpRecord) -> tuple[str, int]:
    """Replace known portfolio client names with the current RFP client when they appear as stale paste."""
    if not content.strip():
        return content, 0

    replacements = 0
    text = content

    for stale in _STALE_CLIENT_PATTERNS:
        if not _is_stale_client_for_rfp(stale, rfp):
            continue
        pattern = re.compile(re.escape(stale), re.IGNORECASE)
        if pattern.search(text):
            text = pattern.sub(rfp.client.strip(), text)
            replacements += 1

    return text, replacements


def _scan_copy_paste(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
) -> list[PreSubmitIssue]:
    issues: list[PreSubmitIssue] = []

    for section in draft.sections:
        if not section.content.strip():
            continue
        content_lower = section.content.casefold()

        for stale in _STALE_CLIENT_PATTERNS:
            if not _is_stale_client_for_rfp(stale, rfp):
                continue
            if stale in content_lower:
                idx = content_lower.find(stale)
                excerpt = section.content[max(0, idx - 20) : idx + len(stale) + 40]
                issues.append(
                    PreSubmitIssue(
                        severity="warning",
                        category="copy_paste",
                        message=f"Possible wrong-client reference: '{stale}'",
                        sectionId=section.id,
                        sectionTitle=section.title,
                        excerpt=excerpt.strip(),
                    )
                )

        for match in _PLACEHOLDER_RE.finditer(section.content):
            tag = match.group(0)
            sev = "critical" if tag.upper().startswith("[VERIFY") else "warning"
            issues.append(
                PreSubmitIssue(
                    severity=sev,
                    category="placeholder",
                    message=f"Unresolved tag: {tag[:80]}",
                    sectionId=section.id,
                    sectionTitle=section.title,
                    excerpt=tag,
                )
            )

        if _TEMPLATE_LEAK_RE.search(section.content):
            issues.append(
                PreSubmitIssue(
                    severity="warning",
                    category="copy_paste",
                    message="Template placeholder language detected",
                    sectionId=section.id,
                    sectionTitle=section.title,
                )
            )

    return issues


def _scan_voice(draft: ProposalDraft) -> list[PreSubmitIssue]:
    issues: list[PreSubmitIssue] = []
    for section in draft.sections:
        if not section.content.strip():
            continue
        reg = classify_section_register(
            section_id=section.id,
            title=section.title,
            zo_mode=section.mode,
        )
        if reg != "narrative":
            continue
        if contains_vendor_language(section.content):
            issues.append(
                PreSubmitIssue(
                    severity="warning",
                    category="voice",
                    message='Narrative section uses "The Vendor" / third-person procurement language',
                    sectionId=section.id,
                    sectionTitle=section.title,
                )
            )
    return issues


def _scan_grammar(draft: ProposalDraft) -> list[PreSubmitIssue]:
    issues: list[PreSubmitIssue] = []
    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            continue
        for match in GRAMMAR_GLITCH_RE.finditer(content):
            start = max(0, match.start() - 30)
            end = min(len(content), match.end() + 40)
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="grammar",
                    message=(
                        "Grammar or pronoun error (e.g. 'of we', 'across we', "
                        "or 'We were …, and is …')"
                    ),
                    sectionId=section.id,
                    sectionTitle=section.title,
                    excerpt=content[start:end].strip(),
                )
            )
            break
    return issues


def _scan_subcontractor_narrative(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> list[PreSubmitIssue]:
    budget = research.budget if research else None
    if not budget_mentions_subcontractors(budget, draft):
        return []

    issues: list[PreSubmitIssue] = []
    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            continue
        if not deny_subcontractors_claimed(content):
            continue
        title_lower = section.title.casefold()
        if "company background" not in title_lower and "company overview" not in title_lower:
            if "self-perform all work" not in content.casefold():
                continue
        issues.append(
            PreSubmitIssue(
                severity="critical",
                category="consistency",
                message=(
                    "Company narrative claims no subcontractors but cost proposal / "
                    "cultural competency sections document translation partners"
                ),
                sectionId=section.id,
                sectionTitle=section.title,
                excerpt=content[:200],
            )
        )
    return issues


def _compliance_checklist(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> list[ComplianceCheckItem]:
    items: list[ComplianceCheckItem] = []
    section_titles = {s.title.strip().casefold() for s in draft.sections}
    section_by_title = {s.title.strip().casefold(): s for s in draft.sections}

    mapped = research.rfp_sections if research else []
    for mapped_section in mapped:
        title_key = mapped_section.title.strip().casefold()
        draft_match = section_by_title.get(title_key)
        has_content = bool(draft_match and draft_match.content.strip())

        if mapped_section.requirements:
            for req in mapped_section.requirements[:3]:
                req_lower = req.casefold()
                if any(
                    sig in req_lower
                    for sig in ("signature", "signed", "notary", "original", "sealed")
                ):
                    items.append(
                        ComplianceCheckItem(
                            item=req[:120],
                            status="manual",
                            notes="Confirm signed/original in submission package",
                        )
                    )
                elif has_content:
                    uncovered = mapped_section.uncovered_requirements or []
                    still_open = [
                        req
                        for req in uncovered[:4]
                        if not requirement_likely_covered(
                            req, draft_match.content if draft_match else ""
                        )
                    ]
                    if still_open:
                        items.append(
                            ComplianceCheckItem(
                                item=req[:120],
                                status="fail",
                                notes=(
                                    f"Phase 2 uncovered requirement may still be missing in "
                                    f"{mapped_section.title}: {still_open[0][:80]}"
                                ),
                            )
                        )
                    else:
                        items.append(
                            ComplianceCheckItem(
                                item=req[:120],
                                status="pass",
                                notes=f"Draft section: {mapped_section.title}",
                            )
                        )
                else:
                    items.append(
                        ComplianceCheckItem(
                            item=req[:120],
                            status="fail",
                            notes=f"Missing content for {mapped_section.title}",
                        )
                    )
        elif title_key in section_titles and has_content:
            items.append(
                ComplianceCheckItem(
                    item=mapped_section.title,
                    status="pass",
                    notes="Section present in manuscript",
                )
            )
        elif mapped_section.title:
            items.append(
                ComplianceCheckItem(
                    item=mapped_section.title,
                    status="fail",
                    notes="No draft content — generate or attach form",
                )
            )

    if rfp.page_limit:
        total_words = sum(
            len(s.content.split()) for s in draft.sections if s.content.strip()
        )
        est_pages = max(1, total_words // 350)
        if est_pages > rfp.page_limit:
            items.append(
                ComplianceCheckItem(
                    item=f"Page limit ({rfp.page_limit} pages)",
                    status="fail",
                    notes=f"Manuscript ~{est_pages} pages ({total_words} words)",
                )
            )
        else:
            items.append(
                ComplianceCheckItem(
                    item=f"Page limit ({rfp.page_limit} pages)",
                    status="pass",
                    notes=f"Manuscript ~{est_pages} pages",
                )
            )

    if research and research.budget:
        items.append(
            ComplianceCheckItem(
                item="Pricing / cost proposal separated",
                status="manual",
                notes="Confirm cost file separate if RFP requires",
            )
        )
    else:
        items.append(
            ComplianceCheckItem(
                item="Budget generated",
                status="fail",
                notes="Run Generate budget before submission",
            )
        )

    return items


_CATEGORY_LABELS = {
    "copy_paste": "Wrong client / copy-paste",
    "placeholder": "Unfilled placeholders",
    "voice": "Voice & tone",
    "compliance": "Compliance",
    "consistency": "Internal consistency",
    "self_edit": "Self-edit incomplete",
}


def generate_issues_markdown(
    *,
    rfp: RfpRecord,
    issues: list[PreSubmitIssue],
    checklist: list[ComplianceCheckItem],
    summary: str,
) -> str:
    """Markdown checklist of findings for auto-fix prompts and copy/export."""
    lines = [
        f"# Issues to fix — {rfp.client}",
        "",
        f"**RFP:** {rfp.title}",
        "",
        summary,
        "",
    ]

    if issues:
        lines.append("## Findings")
        lines.append("")
        by_category: dict[str, list[PreSubmitIssue]] = {}
        for issue in issues:
            by_category.setdefault(issue.category or "other", []).append(issue)

        for category in (
            "copy_paste",
            "placeholder",
            "voice",
            "consistency",
            "self_edit",
            "compliance",
            *sorted(k for k in by_category if k not in _CATEGORY_LABELS),
        ):
            cat_issues = by_category.get(category)
            if not cat_issues:
                continue
            label = _CATEGORY_LABELS.get(category, category.replace("_", " ").title())
            lines.append(f"### {label}")
            lines.append("")
            for issue in cat_issues:
                lines.append(f"- **[{issue.severity.upper()}]** {issue.message}")
                if issue.section_title:
                    lines.append(f"  - **Section:** {issue.section_title}")
                if issue.excerpt:
                    excerpt = issue.excerpt.replace("\n", " ").strip()[:240]
                    lines.append(f"  - **Excerpt:** `{excerpt}`")
            lines.append("")
    else:
        lines.extend(["## Findings", "", "_No automated findings._", ""])

    failing = [row for row in checklist if row.status != "pass"]
    if failing:
        lines.extend(["## Compliance checklist", ""])
        for row in failing:
            lines.append(f"- **[{row.status.upper()}]** {row.item}")
            if row.notes:
                lines.append(f"  - {row.notes}")
        lines.append("")

    return "\n".join(lines).strip()


def issues_markdown_for_llm(issues: list[PreSubmitIssue]) -> str:
    """Compact markdown block for surgical auto-fix LLM prompts."""
    if not issues:
        return "_No issues in this section._"

    lines = ["## Issues to fix", ""]
    for issue in issues[:16]:
        line = f"- **[{issue.severity}/{issue.category}]** {issue.message}"
        if issue.section_title:
            line += f" _(section: {issue.section_title})_"
        lines.append(line)
        if issue.excerpt:
            excerpt = issue.excerpt.replace("\n", " ").strip()[:180]
            lines.append(f"  - Excerpt: `{excerpt}`")
    if len(issues) > 16:
        lines.append(f"- _... and {len(issues) - 16} more_")
    return "\n".join(lines)


def run_presubmit_review(
    *,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    extra_issues: list[PreSubmitIssue] | None = None,
) -> PreSubmitReview:
    issues: list[PreSubmitIssue] = []
    issues.extend(_scan_copy_paste(draft=draft, rfp=rfp))
    issues.extend(_scan_voice(draft=draft))
    issues.extend(_scan_grammar(draft=draft))
    issues.extend(_scan_subcontractor_narrative(draft=draft, research=research))
    issues.extend(scan_manuscript_consistency(draft=draft, research=research, rfp=rfp))
    issues.extend(
        compliance_gaps_to_presubmit_issues(
            scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
        )
    )
    if extra_issues:
        issues.extend(extra_issues)

    empty_narrative = [
        s
        for s in draft.sections
        if not s.content.strip()
        and classify_section_register(section_id=s.id, title=s.title, zo_mode=s.mode)
        == "narrative"
    ]
    for section in empty_narrative[:5]:
        issues.append(
            PreSubmitIssue(
                severity="critical",
                category="compliance",
                message="Narrative section has no content",
                sectionId=section.id,
                sectionTitle=section.title,
            )
        )

    checklist = _compliance_checklist(draft=draft, research=research, rfp=rfp)
    critical_count = sum(1 for i in issues if i.severity == "critical")
    fail_count = sum(1 for c in checklist if c.status == "fail")

    ready = critical_count == 0 and fail_count == 0

    if ready:
        summary = "No critical blockers found. Complete manual signature/compliance items before eVP upload."
    else:
        summary = (
            f"{critical_count} critical issue(s), {fail_count} compliance fail(s), "
            f"{len(issues)} total findings — resolve before submission."
        )

    return PreSubmitReview(
        rfpId=rfp.id,
        issues=issues,
        complianceChecklist=checklist,
        summary=summary,
        issuesMarkdown=generate_issues_markdown(
            rfp=rfp,
            issues=issues,
            checklist=checklist,
            summary=summary,
        ),
        readyToSubmit=ready,
        scannedAt=datetime.now(timezone.utc).isoformat(),
    )


def run_presubmit_review_with_manual_flags(
    *,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    extra_issues: list[PreSubmitIssue] | None = None,
    kb_searched: bool = False,
    finalized: bool = False,
) -> PreSubmitReview:
    """Pre-submit review plus structured manual-fill flags for the UI."""
    from app.services.proposal_manual_flags import build_presubmit_manual_fill_flags
    from app.services.proposal_submission_gap_finalizer import attach_manual_fill_flags_to_review

    review = run_presubmit_review(
        rfp=rfp,
        draft=draft,
        research=research,
        extra_issues=extra_issues,
    )
    if not build_presubmit_manual_fill_flags(
        draft=draft, research=research, rfp=rfp, kb_searched=kb_searched, finalized=finalized
    ):
        return review.model_copy(update={"manual_fill_flags": []})
    return attach_manual_fill_flags_to_review(
        review,
        draft=draft,
        research=research,
        rfp=rfp,
        kb_searched=kb_searched,
        finalized=finalized,
    )
