"""Cross-section consistency checks and patch gates — client-agnostic."""

from __future__ import annotations

import re

from app.models.proposal import (
    PreSubmitIssue,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services.proposal_budget_content import find_budget_section_index
from app.services.proposal_budget_validation import (
    _STALE_RECONCILIATION_FLAG_RE,
    _USD_IN_TEXT_RE,
    sum_line_items_extended,
    validate_budget_canonical,
)
from app.services.proposal_section_quality import is_strict_improvement

_CITED_EVIDENCE_RE = re.compile(r"\[E(\d+)\]")
_NAME_TITLE_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+),\s*([A-Z][^,\n]{3,60})",
)
_PRIOR_CLIENT_CONTEXT_RE = re.compile(
    r"\b(prior|previous|past|former)\s+(client|engagement|contract|relationship|work)\b",
    re.I,
)
_DRAFT_FAILURE_RE = re.compile(
    r"section drafting failed|invalid json|llm returned",
    re.I,
)
_VERIFY_BEFORE_SUBMIT_RE = re.compile(
    r"\bverify\b[^.\n]{0,60}\b(before\s+submission|before\s+submitting|submission)\b",
    re.I,
)


def _parse_usd_amount(text: str) -> float | None:
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def allowed_budget_amounts(budget: ProposalBudget) -> set[float]:
    """Dollar values that may appear outside the canonical budget section."""
    amounts: set[float] = set()
    subtotal = sum_line_items_extended(budget)
    direct = round(float(budget.direct_expenses_total or 0), 2)
    agency_fee = round(float(budget.agency_fee_subtotal or subtotal), 2)
    passthrough = round(float(budget.client_media_passthrough or 0), 2)

    for value in (
        subtotal,
        direct,
        agency_fee,
        passthrough,
        budget.line_item_sum,
        budget.agency_revenue_estimate,
        budget.lump_sum_total,
        budget.total_client_invoicing,
    ):
        if isinstance(value, (int, float)) and float(value) > 0:
            amounts.add(round(float(value), 2))

    if budget.agency_revenue_estimate is not None and budget.agency_revenue_estimate > 0:
        rev = round(float(budget.agency_revenue_estimate), 2)
        amounts.add(rev)
        # Multi-year agency projections from option terms
        for years in (2, 3, 4, 5):
            amounts.add(round(rev * years, 2))

    for item in budget.line_items:
        for field in (item.extended, item.rate):
            if isinstance(field, (int, float)) and float(field) > 0:
                amounts.add(round(float(field), 2))

    expanded: set[float] = set()
    for amount in amounts:
        expanded.add(amount)
        if amount >= 100:
            expanded.add(round(amount / 1000, 2))
    return expanded


def cited_evidence_ids(content: str) -> set[str]:
    return {f"E{n}" for n in _CITED_EVIDENCE_RE.findall(content)}


def regression_vs_prior(before: ProposalSection, after: ProposalSection) -> bool:
    """True when a patch likely degraded a previously acceptable section."""
    prior = (before.content or "").strip()
    new = (after.content or "").strip()
    if not prior:
        return False
    if not new:
        return True

    prior_words = len(prior.split())
    new_words = len(new.split())
    if prior_words >= 120 and new_words < int(prior_words * 0.6):
        return True

    lost_citations = cited_evidence_ids(prior) - cited_evidence_ids(new)
    if lost_citations and prior_words >= 80:
        return True

    return False


def introduces_unauthorized_dollars(
    content: str,
    budget: ProposalBudget,
) -> bool:
    allowed = allowed_budget_amounts(budget)
    if not allowed:
        return False

    for match in _USD_IN_TEXT_RE.finditer(content):
        amount = _parse_usd_amount(match.group(0))
        if amount is None or amount <= 0:
            continue
        if not any(abs(amount - allowed_amt) <= max(1.0, allowed_amt * 0.02) for allowed_amt in allowed):
            return True
    return False


def patch_improves_section(
    before: ProposalSection,
    after: ProposalSection,
    *,
    rfp: RfpRecord,
    budget: ProposalBudget | None = None,
) -> bool:
    from app.services.proposal_presubmit_review import issue_score, scan_section_issues
    from app.services.proposal_section_quality import verify_count
    from app.services.proposal_manuscript_cleanup import has_grammar_glitches

    if regression_vs_prior(before, after):
        return False

    before_verify = verify_count(before.content or "")
    after_verify = verify_count(after.content or "")
    if before_verify > 0 and after_verify < before_verify and after.content.strip():
        if budget and introduces_unauthorized_dollars(after.content, budget):
            return False
        return True

    if has_grammar_glitches(before.content or "") and not has_grammar_glitches(
        after.content or ""
    ):
        return True

    if not is_strict_improvement(before, after):
        return False

    before_issues = scan_section_issues(
        section=before.model_copy(update={"content": before.content}),
        rfp=rfp,
    )
    after_issues = scan_section_issues(
        section=after.model_copy(update={"content": after.content}),
        rfp=rfp,
    )
    if issue_score(after_issues) > issue_score(before_issues):
        return False

    if budget and introduces_unauthorized_dollars(after.content, budget):
        return False

    return True


def self_edit_exhausted_issues(
    section_logs: list[dict[str, str]],
    draft: ProposalDraft,
) -> list[PreSubmitIssue]:
    """Surface sections that exhausted self-edit without improvement."""
    exhausted_ids: set[str] = set()
    for entry in section_logs:
        detail = (entry.get("detail") or "").lower()
        if entry.get("status") == "self_edit_exhausted" or (
            "reverted" in detail or "no improvement" in detail or "agent error" in detail
        ):
            sid = entry.get("sectionId") or ""
            if sid:
                exhausted_ids.add(sid)

    issues: list[PreSubmitIssue] = []
    for section in draft.sections:
        if section.id not in exhausted_ids:
            continue
        issues.append(
            PreSubmitIssue(
                severity="warning",
                category="self_edit",
                message="Self-edit attempted but section may still be weak — manual review recommended",
                sectionId=section.id,
                sectionTitle=section.title,
            )
        )
    return issues


def scan_manuscript_consistency(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> list[PreSubmitIssue]:
    issues: list[PreSubmitIssue] = []
    budget = research.budget if research else None
    budget_idx = find_budget_section_index(draft.sections)
    client_lower = rfp.client.strip().casefold()

    from app.services.proposal_manuscript_locks import scan_manuscript_lock_issues

    issues.extend(scan_manuscript_lock_issues(draft=draft, research=research))

    if budget and budget.agency_revenue_estimate:
        allowed = allowed_budget_amounts(budget)
        for index, section in enumerate(draft.sections):
            if budget_idx is not None and index == budget_idx:
                continue
            if not section.content.strip():
                continue
            for match in _USD_IN_TEXT_RE.finditer(section.content):
                amount = _parse_usd_amount(match.group(0))
                if amount is None or amount <= 0:
                    continue
                if not any(
                    abs(amount - allowed_amt) <= max(1.0, allowed_amt * 0.02)
                    for allowed_amt in allowed
                ):
                    issues.append(
                        PreSubmitIssue(
                            severity="warning",
                            category="consistency",
                            message=(
                                f"Dollar amount {match.group(0)} does not match canonical budget "
                                f"(verified total ${_usd_display(budget)})"
                            ),
                            sectionId=section.id,
                            sectionTitle=section.title,
                            excerpt=match.group(0),
                        )
                    )

    name_titles: dict[str, set[str]] = {}
    for section in draft.sections:
        if not section.content.strip():
            continue
        for name, title in _NAME_TITLE_RE.findall(section.content):
            key = name.strip().casefold()
            name_titles.setdefault(key, set()).add(title.strip())

    for name_key, titles in name_titles.items():
        if len(titles) < 2:
            continue
        display_name = name_key.title()
        issues.append(
            PreSubmitIssue(
                severity="warning",
                category="consistency",
                message=(
                    f"Team member '{display_name}' has conflicting titles across sections: "
                    f"{'; '.join(sorted(titles)[:4])}"
                ),
            )
        )

    if client_lower:
        for section in draft.sections:
            content = section.content
            if not content.strip():
                continue
            lower = content.casefold()
            if client_lower not in lower:
                continue
            for match in _PRIOR_CLIENT_CONTEXT_RE.finditer(content):
                start = max(0, match.start() - 80)
                end = min(len(content), match.end() + 80)
                window = content[start:end].casefold()
                if client_lower in window:
                    issues.append(
                        PreSubmitIssue(
                            severity="warning",
                            category="consistency",
                            message=(
                                f"Prospect client name may appear in a prior-client context "
                                f"({match.group(0)})"
                            ),
                            sectionId=section.id,
                            sectionTitle=section.title,
                            excerpt=content[max(0, match.start() - 30) : match.end() + 40].strip(),
                        )
                    )
                    break

    for section in draft.sections:
        if _DRAFT_FAILURE_RE.search(section.content or ""):
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="consistency",
                    message="Section contains unresolved system drafting error text",
                    sectionId=section.id,
                    sectionTitle=section.title,
                    excerpt=(section.content or "")[:200],
                )
            )

    if budget:
        blob = " ".join(
            part
            for part in (
                budget.fee_structure,
                budget.qualifying_language,
                budget.option_term_notes,
                " ".join(budget.pricing_flags),
            )
            if part
        )
        if _VERIFY_BEFORE_SUBMIT_RE.search(blob):
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="consistency",
                    message="Budget still contains unresolved verify-before-submission language",
                )
            )
        for flag in budget.pricing_flags:
            if _STALE_RECONCILIATION_FLAG_RE.search(flag):
                issues.append(
                    PreSubmitIssue(
                        severity="warning",
                        category="consistency",
                        message=f"Stale budget reconciliation flag: {flag[:120]}",
                    )
                )

    if budget:
        for err in validate_budget_canonical(budget):
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="consistency",
                    message=f"Budget canonical validation: {err}",
                )
            )

    mapped_ids = {s.id for s in (research.rfp_sections if research else [])}
    for section in draft.sections:
        if section.id in mapped_ids and not section.content.strip():
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="compliance",
                    message="Required RFP section is blank or missing content",
                    sectionId=section.id,
                    sectionTitle=section.title,
                )
            )

    return issues


def _usd_display(budget: ProposalBudget) -> str:
    value = budget.agency_revenue_estimate or 0
    return f"{value:,.0f}"
