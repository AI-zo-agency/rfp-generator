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
from app.services.proposal_budget_validation import (
    _STALE_RECONCILIATION_FLAG_RE,
    _USD_IN_TEXT_RE,
    sum_line_items_extended,
    validate_budget_canonical,
)
from app.services.proposal_section_quality import (
    is_integrity_verify_flagging,
    is_strict_improvement,
)

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
_TEAM_SIZE_RE = re.compile(
    r"\b(?:team\s+of\s+(\d+)|(\d+)[\s-]*person\s+team|(\d+)\s+team\s+members)\b",
    re.I,
)

# Context that indicates a dollar amount is a policy limit / bond / statutory
# threshold — not a bid fee that must match the canonical budget ledger.
_NON_BID_CURRENCY_CONTEXT_RE = re.compile(
    r"("
    r"per\s+occurrence|each\s+occurrence|aggregate|"
    r"general\s+liabilit|cgl|umbrella|"
    r"workers['’]?\s*compensation|professional\s+liabilit|"
    r"coverage\s+limit|policy\s+limit|liability\s+limit|"
    r"insurance\s+limit|bonded?\b|surety|deductible|"
    r"statutory|threshold|not\s+to\s+exceed\s+\$?|"
    r"allocation|ceiling|budget\s+cap|program[-\s]?specific|"
    r"tuition|per\s+year|/year|in[-\s]?state|"
    r"reallocat|optimization\s+recommendation|sample\s+(?:shift|move)|"
    r"for\s+example|e\.g\.|illustrative|"
    r"from\s+\w+\s+to\s+\w+"  # "from LinkedIn to Google Search"
    r")",
    re.I,
)

# Compact tuition / marketing figures like $12K — not bid ledger amounts.
_COMPACT_CURRENCY_SUFFIX_RE = re.compile(r"\$[\d,]+(?:\.\d+)?\s*[KMB]\b", re.I)


def _is_non_bid_currency_context(content: str, match_start: int, match_end: int) -> bool:
    """True when amount sits in insurance/bond/statutory/tuition language, not bid totals."""
    # $12K / $1.2M style — matcher often captures only $12 from $12K
    tail = content[match_end : match_end + 2]
    if tail[:1].upper() in {"K", "M", "B"}:
        return True
    window = content[max(0, match_start - 80) : min(len(content), match_end + 80)]
    if _COMPACT_CURRENCY_SUFFIX_RE.search(window):
        return True
    return bool(_NON_BID_CURRENCY_CONTEXT_RE.search(window))


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
        budget.rfp_budget_cap,
        budget.rfp_media_or_program_envelope,
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
        if _is_non_bid_currency_context(content, match.start(), match.end()):
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
    designer_compact: bool = False,
) -> bool:
    from app.services.proposal_presubmit_review import issue_score, scan_section_issues
    from app.services.proposal_section_quality import verify_count
    from app.services.proposal_manuscript_cleanup import has_grammar_glitches

    if designer_compact:
        from app.services.proposal_manuscript_compact import is_designer_compact_improvement

        if is_designer_compact_improvement(before, after):
            if budget and introduces_unauthorized_dollars(after.content or "", budget):
                return False
            return True

    if regression_vs_prior(before, after):
        return False

    # Intentional integrity flagging: replace unsourced %/figures with [VERIFY].
    # Weakness scorer treats more VERIFY tags as worse — override that here.
    if is_integrity_verify_flagging(before, after):
        return True

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
        # More [VERIFY] from integrity flagging can raise issue score — still OK.
        if is_integrity_verify_flagging(before, after):
            return True
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
    client_lower = rfp.client.strip().casefold()

    from app.services.proposal_manuscript_locks import scan_manuscript_lock_issues

    issues.extend(scan_manuscript_lock_issues(draft=draft, research=research))

    if budget and budget.agency_revenue_estimate:
        # Regex free_currency criticals removed — Pass A (proposal_money_intelligence)
        # owns bid-claim triage. Deterministic labeled mismatches + RFP-authority
        # checks below still run synchronously.
        from app.services.proposal_budget_sync import collect_deterministic_budget_mismatches

        for mismatch in collect_deterministic_budget_mismatches(draft, budget):
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="consistency",
                    message=(
                        mismatch.note
                        or (
                            f"Budget claim '{mismatch.claimed_field}' contradicts canonical "
                            f"{mismatch.canonical_value}"
                        )
                    ),
                    sectionId=mismatch.section_id,
                    sectionTitle=mismatch.section_title,
                    excerpt=(mismatch.sentence or "")[:160],
                )
            )

        for mismatch in budget.narrative_mismatches or []:
            if mismatch.matches:
                continue
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="consistency",
                    message=(
                        mismatch.note
                        or (
                            f"Stored budget grounding mismatch on '{mismatch.claimed_field}'"
                        )
                    ),
                    sectionId=mismatch.section_id,
                    sectionTitle=mismatch.section_title,
                    excerpt=(mismatch.sentence or "")[:160],
                )
            )

    name_titles: dict[str, set[str]] = {}
    team_sizes: dict[int, list[str]] = {}
    for section in draft.sections:
        if not section.content.strip():
            continue
        for name, title in _NAME_TITLE_RE.findall(section.content):
            key = name.strip().casefold()
            name_titles.setdefault(key, set()).add(title.strip())
        for match in _TEAM_SIZE_RE.finditer(section.content):
            raw = next((g for g in match.groups() if g), None)
            if not raw:
                continue
            try:
                size = int(raw)
            except ValueError:
                continue
            if size < 2 or size > 200:
                continue
            team_sizes.setdefault(size, []).append(section.title or section.id)

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

    if len(team_sizes) > 1:
        summary = "; ".join(
            f"team of {size} ({', '.join(titles[:2])})"
            for size, titles in sorted(team_sizes.items())
        )
        issues.append(
            PreSubmitIssue(
                severity="warning",
                category="consistency",
                message=f"Conflicting team-size claims across sections: {summary}",
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

    # T1 deterministic gates — always reported; blocking is gated in pipeline_status.
    from app.services.proposal_t1_validators import scan_all_t1

    for finding in scan_all_t1(draft):
        issues.append(
            PreSubmitIssue(
                severity="critical" if finding["severity"] == "critical" else "warning",
                category=str(finding["category"]),
                message=f"[T1:{finding['code']}] {finding['message']}",
                sectionId=finding.get("section_id"),
                sectionTitle=finding.get("section_title"),
                excerpt=finding.get("excerpt"),
            )
        )

    # T2 Fact Ledger consistency — detection always; blocking via consistency_criticals_block.
    from app.services.fact_ledger_store import ledger_from_research
    from app.services.proposal_t2_validators import scan_all_t2

    ledger = ledger_from_research(research)
    for finding in scan_all_t2(draft, ledger):
        issues.append(
            PreSubmitIssue(
                severity="critical" if finding["severity"] == "critical" else "warning",
                category=str(finding["category"]),
                message=f"[T2:{finding['code']}] {finding['message']}",
                sectionId=finding.get("section_id"),
                sectionTitle=finding.get("section_title"),
                excerpt=finding.get("excerpt"),
            )
        )

    # T5.3 — orphan commission narrative (derived fee without media base in manuscript).
    from app.services.proposal_budget_validation import find_orphan_commission_in_manuscript

    manuscript_blob = "\n".join(s.content or "" for s in draft.sections)
    for msg in find_orphan_commission_in_manuscript(manuscript_blob):
        issues.append(
            PreSubmitIssue(
                severity="critical",
                category="budget",
                message=msg,
            )
        )

    # T6.3 — cross-section n-gram overlap on full bodies (not truncated digests).
    from app.services.proposal_overlap_detector import detect_section_overlaps

    overlap_findings = detect_section_overlaps(
        (s.id, s.content or "") for s in draft.sections
    )
    for finding in overlap_findings:
        issues.append(
            PreSubmitIssue(
                severity="critical" if finding.severity == "critical" else "warning",
                category="duplication",
                message=f"[T6:overlap] {finding.message}",
                sectionId=finding.section_a_id,
                excerpt=(
                    f"vs {finding.section_b_id}; jaccard={finding.jaccard}; "
                    f"shared={finding.shared_ngrams}"
                ),
            )
        )

    # T5.4 — unresolved money slots in manuscript.
    from app.services.proposal_budget_slots import find_unresolved_budget_slots

    for section in draft.sections:
        unresolved = find_unresolved_budget_slots(section.content or "")
        for key in unresolved:
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="budget",
                    message=f"[T5:money_slot] Unresolved budget slot {{{{budget.{key}}}}}",
                    sectionId=section.id,
                    sectionTitle=section.title,
                    excerpt=f"{{{{budget.{key}}}}}",
                )
            )

    return issues


def _usd_display(budget: ProposalBudget) -> str:
    value = (
        budget.total_client_invoicing
        or budget.agency_revenue_estimate
        or budget.lump_sum_total
        or 0
    )
    return f"{value:,.0f}"
