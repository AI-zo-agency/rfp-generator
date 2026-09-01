"""Pre-submission copy-paste scan + compliance checklist."""

from __future__ import annotations

import logging
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
from app.services.proposal_common import load_rfp_for_proposal
from app.services.proposal_rfp_excerpt import (
    extract_reference_requirement_summary,
    evaluation_and_kpi_excerpt,
    rfp_forbids_quotation_form_changes,
)
from app.services.proposal_fulfill_rfp_accuracy import (
    parse_scoring_facts_from_rfp,
    scan_draft_accuracy_findings,
)
from app.services.proposal_rfp_submission_requirements import (
    detect_narrative_submission_gaps,
)
from app.services.proposal_draft_structure_stubs import section_is_rfp_draft_stub
from app.services.proposal_manuscript_cleanup import (
    GRAMMAR_GLITCH_RE,
    budget_mentions_subcontractors,
    deny_subcontractors_claimed,
)
from app.services.proposal_evaluation_coverage import find_response_char_limit
from app.services.proposal_voice_enforcement import contains_vendor_language
from app.services.proposal_hallucination_detector import (
    detect_hallucinations,
    filter_high_severity_hallucinations,
)

logger = logging.getLogger(__name__)

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
    r"\[(?:VERIFY|FLAG|DESIGNER NOTE|TBD|INSERT|PLACEHOLDER|PRICING FLAG|MANUAL FILL)[^\]]*\]",
    re.IGNORECASE,
)
_TEMPLATE_LEAK_RE = re.compile(
    r"\b(?:lorem ipsum|client name|insert (?:here|client)|xxx+|tbd\b|todo:)\b",
    re.IGNORECASE,
)


_REF_DENIAL_RE = re.compile(
    r"(?:rfp|excerpt|solicitation).{0,80}(?:does not|did not|do not)\s+specify.{0,160}"
    r"(?:reference|number of references|institution type)",
    re.I | re.S,
)
_QUOTATION_FORM_REWRITE_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"\bsection\s+[a-d]\b|"
    r"\bpart\s+[ivx]+\b.{0,40}(?:fee|rate|pricing)|"
    r"invented\s+(?:fee|rate)\s+table|"
    r"custom\s+(?:pricing|quotation)\s+(?:schedule|form)"
    r")",
)


def _scan_rfp_contradictions(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
) -> list[PreSubmitIssue]:
    """Flag prose that denies RFP requirements or rewrites mandatory forms."""
    issues: list[PreSubmitIssue] = []
    try:
        _, _, rfp_text = load_rfp_for_proposal(rfp.id)
    except Exception:  # noqa: BLE001
        return issues

    ref_spec = extract_reference_requirement_summary(rfp_text)
    for section in draft.sections:
        content = section.content or ""
        title_cf = (section.title or "").casefold()
        if ref_spec and "reference" in title_cf and (
            _REF_DENIAL_RE.search(content)
            or "does not specify" in content.casefold()
            and "reference" in content.casefold()
        ):
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="compliance",
                    message=(
                        "References section incorrectly states the RFP does not specify "
                        "reference requirements — the RFP text requires specific references."
                    ),
                    sectionId=section.id,
                    sectionTitle=section.title,
                    excerpt=ref_spec[:200],
                )
            )
        if (
            rfp_forbids_quotation_form_changes(rfp_text)
            and any(k in title_cf for k in ("pricing", "quotation", "cost proposal", "budget"))
            and _QUOTATION_FORM_REWRITE_RE.search(content)
        ):
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="compliance",
                    message=(
                        "Pricing section restructures the official Quotation/Pricing Proposal Form "
                        "(Section A–D). This RFP disqualifies altered forms — fill the buyer's "
                        "form verbatim and move rationale to a supporting page."
                    ),
                    sectionId=section.id,
                    sectionTitle=section.title,
                )
            )
        if "reference" in title_cf and re.search(
            r"(?i)(?:available|provided|contact).{0,40}(?:upon|on)\s+request|"
            r"upon\s+request",
            content,
        ):
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="compliance",
                    message=(
                        "References section withholds contact details ('upon request'). "
                        "Many RFPs prohibit withholding reference contacts — put name, "
                        "title, phone, and email from KB or use [VERIFY: contact fields]."
                    ),
                    sectionId=section.id,
                    sectionTitle=section.title,
                )
            )
        if re.search(
            r"(?i)pre-?cleared|agreed\s+to\s+respond\s+to\s+reference",
            content,
        ):
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="accuracy",
                    message=(
                        "Unverified claim that references were pre-cleared or agreed to "
                        "respond — cut unless KB evidence confirms it."
                    ),
                    sectionId=section.id,
                    sectionTitle=section.title,
                )
            )

    excerpt = evaluation_and_kpi_excerpt(rfp_text)
    facts = parse_scoring_facts_from_rfp(excerpt or rfp_text)
    seen_kinds: set[str] = set()
    for finding in scan_draft_accuracy_findings(draft, facts, rfp_text):
        if finding.kind in seen_kinds:
            continue
        seen_kinds.add(finding.kind)
        sid = finding.section_ids[0] if finding.section_ids else ""
        section = next((s for s in draft.sections if s.id == sid), None)
        issues.append(
            PreSubmitIssue(
                severity="critical",
                category="compliance",
                message=finding.message,
                sectionId=section.id if section else None,
                sectionTitle=section.title if section else None,
            )
        )
    return issues


def _scan_submission_document_gaps(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str | None = None,
) -> list[PreSubmitIssue]:
    """Flag missing RFP-required narrative + unresolved physical attachments.

    Physical forms / signatures with only ``[MANUAL FILL]`` stubs are critical
    disqualification risks — a stub is not a signed W-9 / COI / affidavit.
    """
    issues: list[PreSubmitIssue] = []
    text = (rfp_text or "").strip()
    if not text:
        try:
            _, _, text = load_rfp_for_proposal(rfp.id)
        except Exception:  # noqa: BLE001
            return issues
    if not text.strip():
        return issues

    manuscript = "\n\n".join(
        f"{s.title}\n{s.content}" for s in draft.sections if s.content.strip()
    ).casefold()

    if re.search(r"acknowledgement\s+of\s+addenda|acknowledgment\s+of\s+addenda", text, re.I):
        if not any(
            k in manuscript
            for k in (
                "acknowledgement of addenda",
                "acknowledgment of addenda",
                "addenda acknowledgment",
                "no addenda received",
            )
        ) and "rfp-closing-addenda" not in {s.id for s in draft.sections}:
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="compliance",
                    message=(
                        "RFP requires Acknowledgement of Addenda returned with the proposal — "
                        "no addenda section or statement found."
                    ),
                    sectionId=None,
                    sectionTitle=None,
                )
            )

    for item in detect_narrative_submission_gaps(draft, text):
        issues.append(
            PreSubmitIssue(
                severity="critical",
                category="compliance",
                message=f"RFP requires narrative: {item.title} — not found in manuscript.",
                sectionId=item.section_id,
                sectionTitle=item.title,
            )
        )

    # Fail closed on physical docs the RFP demands — stubs do not clear DQ.
    from app.services.proposal_rfp_submission_requirements import (
        outstanding_submission_checklist_for_scan,
    )

    outstanding = outstanding_submission_checklist_for_scan(text, draft)
    # Broad catalog rows are advisory — specific forms (W-9, COI, bonds, etc.)
    # are hard disqualification risks.
    _BROAD_ATTACHMENT = {
        "named exhibits / appendices / attachments",
        "required attachments checklist",
    }
    for label in outstanding.needs_attachment[:12]:
        if label.casefold() in _BROAD_ATTACHMENT:
            issues.append(
                PreSubmitIssue(
                    severity="warning",
                    category="compliance",
                    message=(
                        f"RFP attachment checklist still open: “{label}” — confirm each "
                        "named exhibit is attached before submit."
                    ),
                    sectionId=None,
                    sectionTitle=label,
                )
            )
            continue
        issues.append(
            PreSubmitIssue(
                severity="critical",
                category="compliance",
                message=(
                    f"DISQUALIFICATION RISK: RFP requires physical/signed attachment "
                    f"“{label}” — manuscript still has only a stub or is missing it. "
                    "Attach the official buyer form / signed original before submit."
                ),
                sectionId=None,
                sectionTitle=label,
            )
        )
    for label in outstanding.needs_drafting[:8]:
        # Skip compulsory close if already handled elsewhere as soft — still critical
        # when truly missing from body.
        if "closing statement" in label.casefold() and any(
            "closing" in (s.title or "").casefold() and (s.content or "").strip()
            for s in draft.sections
        ):
            continue
        issues.append(
            PreSubmitIssue(
                severity="critical",
                category="compliance",
                message=(
                    f"RFP requires submission item “{label}” — not resolved in manuscript."
                ),
                sectionId=None,
                sectionTitle=label,
            )
        )

    return issues


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


def is_service_title_client(client: str) -> bool:
    """True when RFP `client` field is a service category, not the buyer institution."""
    c = (client or "").strip().casefold()
    if not c or len(c) < 4:
        return False
    org_markers = (
        "county",
        "college",
        "university",
        "community college",
        "city of",
        "authority",
        "department of",
        "school district",
        "state of",
    )
    if any(m in c for m in org_markers):
        return False
    service_markers = (
        "services",
        "advertising",
        "marketing",
        "consulting",
        "communications",
        "digital ",
    )
    return any(m in c for m in service_markers)


def proposal_client_label(rfp: RfpRecord) -> str:
    """Best buyer/org label for this RFP — never a service-category title fragment.

    Manual RFPs sometimes store client=\"Digital Advertising Services\" with
    title=\"… for Hudson County Community College\". Prefer the institution after \"for\".
    """
    client = (rfp.client or "").strip()
    title = (rfp.title or "").strip()
    if is_service_title_client(client):
        m = re.search(r"\bfor\s+(.+)$", title, re.I)
        if m and m.group(1).strip():
            return m.group(1).strip()
    if client and title:
        prefix = f"{client} for "
        if title.casefold().startswith(prefix.casefold()):
            extracted = title[len(prefix) :].strip()
            if extracted:
                return extracted
    if client:
        return client
    return title or "the Client"


def is_case_study_section(
    section: ProposalSection | None,
    *,
    section_id: str = "",
    title: str = "",
) -> bool:
    """Past-client proof (Section 3 / Our Work) — portfolio names here are intentional."""
    sid = ((section.id if section else section_id) or "").casefold()
    ttl = ((section.title if section else title) or "").casefold()
    if sid.startswith("section-3-work-") or sid in {
        "section-3-our-work",
        "section-3-work-placeholder",
    }:
        return True
    if "our work" in ttl or "case stud" in ttl:
        return True
    return False


def _is_stale_client_for_rfp(stale: str, rfp: RfpRecord) -> bool:
    """True when a portfolio name should be treated as wrong-client paste for this RFP."""
    client_lower = proposal_client_label(rfp).casefold()
    context_lower = _rfp_context_blob(rfp)
    stale_lower = stale.casefold()

    if stale_lower in context_lower:
        return False
    if stale_lower in client_lower:
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


def fix_stale_client_references(
    content: str,
    rfp: RfpRecord,
    *,
    section: ProposalSection | None = None,
) -> tuple[str, int]:
    """Replace wrong-client paste with THIS RFP's buyer — never rewrite case studies.

    Section 3 / Our Work intentionally names past clients (Bend, Santa Clara, etc.).
    Autofix must not mail-merge those into the current RFP client/title.
    """
    if not content.strip():
        return content, 0
    if section is not None and is_case_study_section(section):
        return content, 0

    replacements = 0
    text = content
    replacement = proposal_client_label(rfp)

    for stale in _STALE_CLIENT_PATTERNS:
        if not _is_stale_client_for_rfp(stale, rfp):
            continue
        pattern = re.compile(re.escape(stale), re.IGNORECASE)
        if pattern.search(text):
            text = pattern.sub(replacement, text)
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
        # Case studies intentionally name past portfolio clients — not wrong-client paste.
        if is_case_study_section(section):
            # Detect mail-merge corruption: service-title substituted for real clients.
            bad_label = (rfp.client or "").strip()
            if bad_label and is_service_title_client(bad_label):
                needle = bad_label.casefold()
                body = section.content.casefold()
                if needle in body and any(
                    frag in body
                    for frag in (
                        f"city of {needle}",
                        f"for the {needle}",
                        f"{needle} is one of the largest",
                        f"{needle} county",
                        f"{needle} fair",
                        f"{needle} library",
                        f"{needle} water",
                        f"{needle} department",
                    )
                ):
                    issues.append(
                        PreSubmitIssue(
                            severity="critical",
                            category="copy_paste",
                            message=(
                                "Case study mail-merge corruption: portfolio client names were "
                                f"replaced with RFP service title '{bad_label}'. Re-draft Section 3 "
                                "from KB — do not ship."
                            ),
                            sectionId=section.id,
                            sectionTitle=section.title,
                            excerpt=section.content[:200],
                        )
                    )
            section_is_undrafted_stub = section_is_rfp_draft_stub(section)
            for match in _PLACEHOLDER_RE.finditer(section.content):
                tag = match.group(0)
                # A whole RFP-required section that was never drafted at all is
                # categorically worse than an ordinary human-input placeholder
                # (a signature, a date) inside an otherwise-complete section —
                # every tag in it must block readiness, not just VERIFY ones.
                sev = (
                    "critical"
                    if tag.upper().startswith("[VERIFY") or section_is_undrafted_stub
                    else "warning"
                )
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

        section_is_undrafted_stub = section_is_rfp_draft_stub(section)
        for match in _PLACEHOLDER_RE.finditer(section.content):
            tag = match.group(0)
            sev = (
                "critical"
                if tag.upper().startswith("[VERIFY") or section_is_undrafted_stub
                else "warning"
            )
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
    rfp_text: str | None = None,
) -> list[ComplianceCheckItem]:
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    items: list[ComplianceCheckItem] = []
    section_by_title = {s.title.strip().casefold(): s for s in draft.sections}

    def _find_draft_match(title: str) -> ProposalSection | None:
        exact = section_by_title.get(title.strip().casefold())
        if exact is not None:
            return exact
        # A section renamed after research.rfp_sections was last computed
        # (apply_rfp_mandated_section_titles, a later reorder/relabel pass)
        # must not read as "missing" from the compliance checklist just
        # because its exact title string changed underneath the mapping.
        for section in draft.sections:
            if outline_titles_near_duplicate(title, section.title or "", threshold=0.6):
                return section
        return None

    mapped = research.rfp_sections if research else []
    for mapped_section in mapped:
        draft_match = _find_draft_match(mapped_section.title)
        has_content = bool(draft_match and draft_match.content.strip())

        if mapped_section.requirements:
            for req in mapped_section.requirements[:3]:
                req_lower = req.casefold()
                if any(
                    sig in req_lower
                    for sig in ("signature", "signed", "notary", "original", "sealed")
                ):
                    # Fail closed: signature/sealed asks are not cleared by prose stubs.
                    stub_open = bool(
                        draft_match
                        and (
                            "[MANUAL FILL" in (draft_match.content or "").upper()
                            or "[VERIFY:" in (draft_match.content or "").upper()
                        )
                    )
                    items.append(
                        ComplianceCheckItem(
                            item=req[:120],
                            status="fail" if (not has_content or stub_open) else "manual",
                            notes=(
                                "Unsigned / stub only — attach wet-ink or buyer form "
                                "before submit (disqualification risk)"
                                if (not has_content or stub_open)
                                else "Confirm signed/original in submission package"
                            ),
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
        elif draft_match is not None and has_content:
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

    from app.services.rfp_page_limit import resolve_page_limit

    page_limit = resolve_page_limit(rfp.page_limit, rfp_text)
    if page_limit and page_limit > 0:
        total_words = sum(
            len(s.content.split()) for s in draft.sections if s.content.strip()
        )
        est_pages = max(1, total_words // 350)
        if est_pages > page_limit:
            items.append(
                ComplianceCheckItem(
                    item=f"Page limit ({page_limit} pages)",
                    status="fail",
                    notes=f"Manuscript ~{est_pages} pages ({total_words} words)",
                )
            )
        else:
            items.append(
                ComplianceCheckItem(
                    item=f"Page limit ({page_limit} pages)",
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


def _response_blocks(content: str) -> list[tuple[str, str]]:
    """Split a section into the separately-submitted responses it holds.

    An RFP that caps responses by character does so per FIELD. A tab answering
    five numbered asks fills five fields, so measuring the whole tab against one
    field's cap would report a false failure. Markdown sub-headings are how the
    writer marks those per-item answers, so each heading starts a new block.
    """
    blocks: list[tuple[str, str]] = []
    label = ""
    buffer: list[str] = []
    for line in (content or "").splitlines():
        if line.lstrip().startswith("#"):
            if buffer:
                blocks.append((label, "\n".join(buffer)))
                buffer = []
            label = line.lstrip("# ").strip()
            continue
        buffer.append(line)
    if buffer:
        blocks.append((label, "\n".join(buffer)))
    return [(lbl, text) for lbl, text in blocks if text.strip()]


def _scan_response_char_limits(
    *,
    draft: ProposalDraft,
    rfp_text: str | None,
) -> list[PreSubmitIssue]:
    """Flag responses longer than the per-field character cap the RFP states.

    Portals enforce these caps by rejecting the submission, so an over-limit
    response is a hard blocker rather than a style note. Under-limit is fine —
    the cap is a ceiling, not a target.
    """
    limit = find_response_char_limit(rfp_text or "")
    if not limit:
        return []
    issues: list[PreSubmitIssue] = []
    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            continue
        reg = classify_section_register(
            section_id=section.id,
            title=section.title,
            zo_mode=section.mode,
        )
        if reg != "narrative":
            continue
        for label, block in _response_blocks(content):
            length = len(block.strip())
            if length <= limit:
                continue
            where = f" ({label})" if label else ""
            issues.append(
                PreSubmitIssue(
                    severity="critical",
                    category="compliance",
                    message=(
                        f"Response{where} is {length:,} characters — the RFP caps each "
                        f"response field at {limit:,}. Cut {length - limit:,} characters "
                        "or the portal will reject the submission."
                    ),
                    sectionId=section.id,
                    sectionTitle=section.title,
                    excerpt=block.strip()[:300],
                )
            )
    return issues


def _scan_hallucinations(draft: ProposalDraft) -> list[PreSubmitIssue]:
    """Detect fabricated facts, unverified claims, and hallucinated content."""
    issues: list[PreSubmitIssue] = []
    
    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            continue
        
        # Detect hallucinations in this section
        hallucination_findings = detect_hallucinations(content, section.title)
        
        # Convert high-severity hallucinations to PreSubmitIssues
        high_severity = filter_high_severity_hallucinations(hallucination_findings)
        
        for finding in high_severity:
            # Map hallucination type to appropriate category
            if finding["type"] in ("hallucination", "unverified_certification", "zero_revenue_claim"):
                severity = "critical"
                category = "fabricated_fact"
            else:
                severity = "warning"
                category = "unverified_claim"
            
            issues.append(
                PreSubmitIssue(
                    severity=severity,
                    category=category,
                    message=finding["pattern"],
                    sectionId=section.id,
                    sectionTitle=section.title,
                    excerpt=finding["matched_text"][:200],
                )
            )
    
    if issues:
        logger.warning(
            f"🔴 HALLUCINATION DETECTION: Found {len(issues)} fabricated/unverified claims in proposal"
        )
    
    return issues


_CATEGORY_LABELS = {
    "copy_paste": "Wrong client / copy-paste",
    "placeholder": "Unfilled placeholders",
    "voice": "Voice & tone",
    "compliance": "Compliance",
    "consistency": "Internal consistency",
    "self_edit": "Self-edit incomplete",
    "fabricated_fact": "🔴 Fabricated/Hallucinated Facts",
    "unverified_claim": "⚠️ Unverified Claims",
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
            "fabricated_fact",
            "unverified_claim",
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
    rfp_text = ""
    try:
        _, _, rfp_text = load_rfp_for_proposal(rfp.id)
    except Exception:  # noqa: BLE001
        rfp_text = ""

    issues.extend(_scan_copy_paste(draft=draft, rfp=rfp))
    issues.extend(_scan_rfp_contradictions(draft=draft, rfp=rfp))
    issues.extend(
        _scan_submission_document_gaps(draft=draft, rfp=rfp, rfp_text=rfp_text or None)
    )
    issues.extend(_scan_voice(draft=draft))
    issues.extend(_scan_response_char_limits(draft=draft, rfp_text=rfp_text or None))
    issues.extend(_scan_grammar(draft=draft))
    issues.extend(_scan_subcontractor_narrative(draft=draft, research=research))
    issues.extend(scan_manuscript_consistency(draft=draft, research=research, rfp=rfp))
    issues.extend(
        compliance_gaps_to_presubmit_issues(
            scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
        )
    )
    
    # CRITICAL: Scan for hallucinated/fabricated facts
    issues.extend(_scan_hallucinations(draft=draft))
    
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

    checklist = _compliance_checklist(
        draft=draft,
        research=research,
        rfp=rfp,
        rfp_text=rfp_text or None,
    )
    critical_count = sum(1 for i in issues if i.severity == "critical")
    fail_count = sum(1 for c in checklist if c.status == "fail")

    ready = critical_count == 0 and fail_count == 0

    if ready:
        summary = (
            "No critical blockers found. Still confirm wet signatures / sealed package "
            "per RFP before eVP upload."
        )
    else:
        summary = (
            f"{critical_count} critical issue(s), {fail_count} compliance fail(s), "
            f"{len(issues)} total findings — resolve before submission "
            "(gov / buyer disqualification risks)."
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
