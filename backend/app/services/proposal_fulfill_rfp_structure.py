"""Scan RFP — align manuscript sections to THIS RFP's required outline (Exhibit A, TOC, criteria)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_fulfill_guard import fulfill_scan_preserves_section
from app.services.proposal_fulfill_truncation_repair import looks_truncated_for_fulfill
from app.services.proposal_rfp_excerpt import submission_documents_excerpt

logger = logging.getLogger(__name__)

_VERIFY_STUB_RE = re.compile(
    r"\[VERIFY:\s*Draft content for .+ — insufficient evidence|\[VERIFY:\s*Draft content for",
    re.I,
)

# HTA / destination-brand BMP Exhibit A pattern (generic lettered outline also detected via LLM).
_EXHIBIT_A_BMP_RE = re.compile(
    r"exhibit\s+a.{0,400}?"
    r"(?:vision|market\s+analysis|kpi\s+target|target\s+audience|strateg|campaign|activity\s+measure)",
    re.I | re.S,
)

# A TOC ask for personnel resumes/CVs/key-staff qualifications is already met by
# the per-person Section 2 bio tabs (section-2-bio-*) — those ARE the resumes,
# just filed one per person instead of under one combined heading. Title-string
# matching against a single bio never catches this (bio titles are people's
# names), which used to mint a redundant, forever-empty stub section for it.
_PERSONNEL_RESUME_ASK_PHRASES = (
    "resumes of key personnel",
    "resumes of personnel",
    "resumes of staff",
    "resumes of the project team",
    "resumes of project team",
    "key personnel",
    "key staff",
    "staff qualifications",
    "staff resumes",
    "team resumes",
    "personnel resumes",
    "staff cvs",
    "personnel cvs",
    "project team qualifications",
    "project team bios",
)


def _spec_is_personnel_resume_ask(title: str) -> bool:
    t = (title or "").casefold()
    return any(phrase in t for phrase in _PERSONNEL_RESUME_ASK_PHRASES)

_DEFAULT_BMP_HEADINGS = (
    "A. Vision",
    "B. Market Analysis",
    "C. KPI Targets",
    "D. Target Audience",
    "E. Strategies",
    "F. Campaigns",
    "G. Activity Measures Methodology",
)

_BMP_TITLE_HINTS = (
    "brand marketing plan",
    "bmp",
    "marketing plan",
)

_QUAL_TITLE_HINTS = (
    "qualification",
    "offeror qualification",
    "contractor reference",
    "experience & contractor",
)


COMPANY_BLOCK_HEADER_ID = "rfp-structure-company-block-header"

_POINTER_DELEGATION_RE = re.compile(
    r"company background for this submission is sections?\s*1\.1",
    re.IGNORECASE,
)


def is_pointer_only_company_delegation(content: str) -> bool:
    """True when a tab is only a cross-ref to Sections 1–3 — not a real RFP answer."""
    t = (content or "").strip()
    if not t:
        return True
    if _POINTER_DELEGATION_RE.search(t):
        return True
    if "do not duplicate this block elsewhere" in t.casefold():
        return True
    if "covered in sections 1" in t.casefold() and len(t) < 450:
        return True
    if "sections 1.1–1.5 below" in t.casefold() and len(t) < 450:
        return True
    return False


def _section_has_substantive_body(section: ProposalSection) -> bool:
    from app.services.proposal_section_quality import word_count

    body = (section.content or "").strip()
    if not body or is_pointer_only_company_delegation(body):
        return False
    return word_count(body) >= 60


@dataclass
class RfpSectionSpec:
    rfp_title: str
    required_headings: list[str] = field(default_factory=list)
    instructions: str = ""
    evaluation_weight: str = ""
    same_ask_as: list[str] = field(default_factory=list)
    satisfied_by_static_company_block: bool = False
    mandated_submission_format: bool = False


def _section_title_cf(title: str) -> str:
    return (title or "").casefold()


def _find_section(draft: ProposalDraft, title_hints: tuple[str, ...]) -> ProposalSection | None:
    for section in draft.sections:
        t = _section_title_cf(section.title)
        if any(h in t for h in title_hints):
            return section
    return None


def _headings_present(content: str, required: list[str]) -> list[str]:
    missing: list[str] = []
    cf = (content or "").casefold()
    for heading in required:
        key = heading.casefold().strip()
        # Accept "## B. Market" or "B. Market Analysis" or partial token match
        tokens = [t for t in re.split(r"\W+", key) if len(t) >= 4]
        if key in cf:
            continue
        if tokens and all(tok in cf for tok in tokens[:2]):
            continue
        missing.append(heading)
    return missing


def detect_bmp_exhibit_required_headings(rfp_text: str) -> list[str]:
    text = rfp_text or ""
    if _EXHIBIT_A_BMP_RE.search(text) or re.search(
        r"brand marketing plan.{0,200}exhibit\s+a", text, re.I | re.S
    ):
        return list(_DEFAULT_BMP_HEADINGS)
    return []


def _row_to_rfp_section_spec(
    row: dict,
    *,
    mandated_submission_format: bool = False,
) -> RfpSectionSpec | None:
    title = str(row.get("rfpTitle") or row.get("title") or "").strip()
    if not title:
        return None
    headings = [
        str(h).strip() for h in (row.get("requiredHeadings") or []) if str(h).strip()
    ]
    same_ask = [
        str(x).strip()
        for x in (row.get("sameAskAs") or row.get("same_ask_as") or [])
        if str(x).strip()
    ]
    satisfied_static = bool(
        row.get("satisfiedByStaticCompanyBlock")
        or row.get("satisfied_by_static_company_block")
    )
    instructions = str(row.get("instructions") or "").strip()
    if not instructions:
        instructions = (
            "Required by this RFP's submission format — use the buyer's exact "
            "section label and include every element the RFP lists."
        )
    candidate = RfpSectionSpec(
        rfp_title=title,
        required_headings=headings,
        instructions=instructions,
        evaluation_weight=str(row.get("evaluationWeight") or "").strip(),
        same_ask_as=same_ask,
        satisfied_by_static_company_block=satisfied_static,
        mandated_submission_format=mandated_submission_format,
    )
    if _spec_is_rfp_title_noise(candidate):
        return None
    return candidate


async def extract_rfp_submission_format_specs(
    rfp_text: str,
    *,
    rfp_title: str = "",
    existing_section_titles: list[str] | None = None,
) -> list[RfpSectionSpec]:
    """LLM: read PROPOSAL CONTENT FORMAT / layout mandates — no regex excerpting."""
    from app.services.proposal_rfp_excerpt import (
        closing_package_excerpt,
        submission_documents_excerpt,
    )

    body = (rfp_text or "").strip()
    if not body or not llm.is_configured():
        return []

    submission_excerpt = submission_documents_excerpt(body) or body[:50000]
    closing_excerpt = closing_package_excerpt(body)[:20000]
    rfp_context = (
        f"Submission-documents excerpt:\n{submission_excerpt[:36000]}\n\n"
        f"Closing/forms excerpt:\n{closing_excerpt}"
    )

    existing_titles = [t for t in (existing_section_titles or []) if t.strip()]
    specs: list[RfpSectionSpec] = []
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Read ONE RFP. Find whatever section(s) define the mandatory "
                        "proposal content format, submission layout, or required packet "
                        "structure — use the buyer's own section labels from THIS RFP only.\n"
                        "Return the EXACT sequence that section mandates, in order. "
                        "That sequence outranks evaluation-criteria numbering or labels "
                        "from other parts of the RFP when they conflict.\n\n"
                        "Include EVERY row the format/layout section requires the offeror "
                        "to submit — narrative sections, signed forms, exhibits, attachments, "
                        "and compliance statements — using the buyer's verbatim headings.\n"
                        "When the format groups sub-asks under lettered or numbered items, "
                        "put those labels in requiredHeadings or separate rows as the RFP does.\n"
                        "Use the buyer's OWN wording in rfpTitle — never substitute generic "
                        "agency tab names or evaluation-point labels from a different section.\n"
                        "For items that are signed forms or attach-PDF submittals: instructions "
                        "must say [DESIGNER NOTE: Attach signed PDF] / [MANUAL FILL: signature] "
                        "— do not invent form field content.\n"
                        "sameAskAs = existing draft tab titles that already cover the same "
                        "mandated item (by meaning, not keyword matching).\n"
                        "If this RFP has no dedicated format/layout section, return "
                        '{"sections":[]}.\n'
                        "Return JSON:\n"
                        '{"sections":[{"rfpTitle":"<buyer heading from THIS RFP>",'
                        '"requiredHeadings":[],"instructions":"...",'
                        '"sameAskAs":[],"satisfiedByStaticCompanyBlock":false}]}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"RFP: {rfp_title}\n"
                        f"Existing draft tabs:\n"
                        + "\n".join(f"- {t}" for t in existing_titles[:80])
                        + f"\n\n{rfp_context}\n\nReturn the JSON object."
                    ),
                },
            ],
            max_tokens=16000,
            temperature=0.05,
            cache_prefix=rfp_context[:45000],
        )
        for row in (raw or {}).get("sections") or []:
            if not isinstance(row, dict):
                continue
            spec = _row_to_rfp_section_spec(
                row,
                mandated_submission_format=True,
            )
            if spec is not None:
                specs.append(spec)
    except Exception as exc:  # noqa: BLE001
        logger.warning("RFP submission format spec extract failed: %s", exc)
    return specs


def merge_specs_submission_format_first(
    primary: list[RfpSectionSpec],
    secondary: list[RfpSectionSpec],
) -> list[RfpSectionSpec]:
    """Keep mandated format order; append non-duplicate scored/submittal specs."""
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    merged = list(primary)
    known = [s.rfp_title for s in merged]
    for spec in secondary:
        if any(
            outline_titles_near_duplicate(spec.rfp_title, title) for title in known
        ):
            continue
        if any(
            outline_titles_near_duplicate(spec.rfp_title, alias)
            for s in merged
            for alias in (s.same_ask_as or [])
        ):
            continue
        merged.append(spec)
        known.append(spec.rfp_title)
    return merged


async def build_rfp_structure_specs(
    rfp_text: str,
    *,
    rfp_title: str = "",
    existing_section_titles: list[str] | None = None,
    include_missing_submittals: bool = True,
) -> tuple[list[RfpSectionSpec], list[str]]:
    """Union submission format + TOC + missing submittals for Generate and Scan."""
    logs: list[str] = []
    format_specs = await extract_rfp_submission_format_specs(
        rfp_text,
        rfp_title=rfp_title,
        existing_section_titles=existing_section_titles,
    )
    if format_specs:
        logs.append(
            "RFP structure: "
            f"{len(format_specs)} submission-format spec(s): "
            + " → ".join(s.rfp_title for s in format_specs[:10])
        )
    scored_specs = await extract_rfp_scored_section_specs(
        rfp_text,
        rfp_title=rfp_title,
        existing_section_titles=existing_section_titles,
    )
    specs = merge_specs_submission_format_first(format_specs, scored_specs)
    if include_missing_submittals:
        missing = await specs_from_missing_submittals(
            rfp_text, existing_section_titles=existing_section_titles
        )
        if missing:
            logs.append(
                f"RFP structure: +{len(missing)} missing-submittal spec(s) from completeness check"
            )
        specs = merge_specs_submission_format_first(specs, missing)
    return specs, logs


# Anchors so Align's "already covered by Sections 1–3" checks fire on an
# Intelligence outline that does not yet have those tabs (they are drafted
# in Phase 1, not by the dynamic-section planner).
_INTELLIGENCE_STATIC_ANCHORS: tuple[tuple[str, str], ...] = (
    ("section-1-who-we-are", "1.1 — Who We Are"),
    ("section-1-org-structure", "1.2 — Organizational Structure"),
    ("section-1-business-info", "1.3 — Business Information"),
    ("section-1-certifications", "1.4 — Certifications"),
    ("section-1-insurance", "1.5 — Insurance Information"),
    ("section-2-team-overview", "2 — Team Overview"),
    ("section-2-bio-anchor", "Team Bios"),
    ("section-3-our-work", "3 — Our Work"),
)


def static_company_block_titles() -> list[str]:
    """Titles Align treats as already-covered company/team/work identity."""
    return [title for _sid, title in _INTELLIGENCE_STATIC_ANCHORS]


def format_rfp_structure_specs_for_planner(specs: list[RfpSectionSpec]) -> str:
    """Hard list the Align extract produced — planner must emit these tabs."""
    if not specs:
        return ""
    from app.services.proposal_voice_enforcement import is_duplicate_static_rfp_section

    lines = [
        "REQUIRED SUBMISSION TABS (same extract as Align to RFP outline).",
        "Emit EXACTLY these tabs, in this order. Copy the buyer's wording.",
        "Do not rename, drop, or invent a parallel stack. Company/team/experience "
        "identity is already static Sections 1–3 — do not re-emit those.",
    ]
    index = 1
    for spec in specs:
        if _spec_is_rfp_title_noise(spec):
            continue
        if _spec_is_acknowledge_only(spec):
            continue
        if spec.satisfied_by_static_company_block:
            continue
        if is_duplicate_static_rfp_section(spec.rfp_title or ""):
            continue
        title = _clean_spec_title((spec.rfp_title or "").strip())
        if not title:
            continue
        extra = ""
        if spec.required_headings:
            extra = " | sub-headings: " + "; ".join(spec.required_headings[:8])
        weight = f" | {spec.evaluation_weight}" if spec.evaluation_weight else ""
        lines.append(f"{index}. {title}{weight}{extra}")
        index += 1
    if index == 1:
        return ""
    return "\n".join(lines)


def outline_sections_from_rfp_specs(
    specs: list[RfpSectionSpec],
    *,
    section_factory: Any,
) -> list[Any]:
    """Turn Align extract specs into Intelligence outline tabs — no extra LLM."""
    from app.services.proposal_voice_enforcement import is_duplicate_static_rfp_section

    sections: list[Any] = []
    order = 1
    for spec in specs:
        if _spec_is_rfp_title_noise(spec):
            continue
        if _spec_is_acknowledge_only(spec):
            continue
        if spec.satisfied_by_static_company_block:
            continue
        if is_duplicate_static_rfp_section(spec.rfp_title or ""):
            continue
        title = _clean_spec_title((spec.rfp_title or "").strip())
        if not title:
            continue
        raw = {
            "id": f"rfp-structure-{_slug_section_id(title)}",
            "title": title,
            "order": order,
            "required": True,
            "conditionalReason": (spec.instructions or "")[:240],
            "parentId": None,
            "children": list(spec.required_headings or []),
            "dependencies": [],
            "evaluationWeight": _parse_spec_weight(spec.evaluation_weight),
            "protectFromCap": True,
            "submissionInstrument": None,
        }
        sections.append(section_factory(raw) if section_factory is not None else raw)
        order += 1
    return sections


def _parse_spec_weight(raw: str) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _outline_section_id(section: Any) -> str:
    if hasattr(section, "id"):
        return str(getattr(section, "id") or "")
    if isinstance(section, dict):
        return str(section.get("id") or "")
    return ""


def _outline_section_title(section: Any) -> str:
    if hasattr(section, "title"):
        return str(getattr(section, "title") or "")
    if isinstance(section, dict):
        return str(section.get("title") or "")
    return ""


def _outline_section_children(section: Any) -> list[str]:
    if hasattr(section, "children"):
        return list(getattr(section, "children") or [])
    if isinstance(section, dict):
        return [str(x) for x in (section.get("children") or []) if str(x).strip()]
    return []


def _copy_outline_section(section: Any, **updates: Any) -> Any:
    if hasattr(section, "model_copy"):
        return section.model_copy(update=updates)
    if isinstance(section, dict):
        out = dict(section)
        out.update(updates)
        return out
    return section


def align_outline_sections_to_rfp_specs(
    sections: list[Any],
    specs: list[RfpSectionSpec],
    *,
    section_factory: Any,
) -> tuple[list[Any], list[str]]:
    """Same stub + reorder + mandated titles as Align to RFP outline, on a plan outline.

    Static 1–3 anchors are used only so company-identity specs do not mint
    duplicate RFP tabs. They are stripped before the outline is returned.
    Newly added tabs are protectFromCap so Phase 3 drafts them on generate
    instead of leaving empty Align stubs for a later button click.
    """
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    if not specs:
        return list(sections), []

    original_by_id = {
        _outline_section_id(section): section
        for section in sections
        if _outline_section_id(section)
    }
    dynamic = [
        ProposalSection(
            id=_outline_section_id(section),
            title=_outline_section_title(section),
            source="generated",
            mode="write",
            status="outline",
        )
        for section in sections
        if _outline_section_id(section)
    ]
    anchors = [
        ProposalSection(
            id=section_id,
            title=title,
            source="template",
            mode="pull",
            status="generated",
        )
        for section_id, title in _INTELLIGENCE_STATIC_ANCHORS
    ]
    draft = ProposalDraft(
        rfpId="intelligence-outline",
        sections=anchors + dynamic,
        updatedAt=datetime.now(timezone.utc).isoformat(),
    )
    draft, stub_logs = ensure_missing_scored_section_stubs(draft, specs)
    draft, order_logs = order_draft_to_rfp_sequence(draft, specs)
    draft, relabel_logs = apply_rfp_mandated_section_titles(draft, specs)
    logs = list(stub_logs) + list(order_logs) + list(relabel_logs)

    out: list[Any] = []
    seen: set[str] = set()
    order = 1
    for proposal_section in draft.sections:
        if (
            _is_static_1_3_section(proposal_section)
            or proposal_section.id == COMPANY_BLOCK_HEADER_ID
        ):
            continue
        section_id = proposal_section.id or ""
        if not section_id or section_id in seen:
            continue
        seen.add(section_id)
        spec = next(
            (
                candidate
                for candidate in specs
                if outline_titles_near_duplicate(
                    candidate.rfp_title, proposal_section.title or ""
                )
            ),
            None,
        )
        existing = original_by_id.get(section_id)
        if existing is not None:
            updates: dict[str, Any] = {"order": order}
            if _outline_section_title(existing) != (proposal_section.title or ""):
                updates["title"] = proposal_section.title
            if spec and spec.required_headings and not _outline_section_children(existing):
                updates["children"] = list(spec.required_headings)
            out.append(_copy_outline_section(existing, **updates))
        else:
            raw = {
                "id": section_id,
                "title": proposal_section.title,
                "order": order,
                "required": True,
                "conditionalReason": (
                    (spec.instructions or "")[:240]
                    if spec
                    else "Required by this RFP's submission format"
                ),
                "parentId": None,
                "children": list(spec.required_headings) if spec else [],
                "dependencies": [],
                "evaluationWeight": _parse_spec_weight(
                    spec.evaluation_weight if spec else ""
                ),
                "protectFromCap": True,
                "submissionInstrument": None,
            }
            out.append(section_factory(raw) if section_factory is not None else raw)
        order += 1
    return out, logs


def apply_rfp_mandated_section_titles(
    draft: ProposalDraft,
    specs: list[RfpSectionSpec],
) -> tuple[ProposalDraft, list[str]]:
    """Relabel matched tabs to the RFP spec title when labels differ by meaning."""
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    logs: list[str] = []
    sections = list(draft.sections)
    changed = False
    for spec in specs:
        working = draft.model_copy(update={"sections": sections})
        section = _match_section_for_spec(working, spec)
        if not section or _is_static_1_3_section(section):
            continue
        current = (section.title or "").strip()
        target = (spec.rfp_title or "").strip()
        if not target or current == target:
            continue
        if outline_titles_near_duplicate(current, target):
            continue
        aliases = list(spec.same_ask_as or [])
        alias_match = any(
            outline_titles_near_duplicate(alias, current) for alias in aliases
        )
        if not spec.mandated_submission_format and not alias_match:
            continue
        idx = next(i for i, s in enumerate(sections) if s.id == section.id)
        sections[idx] = section.model_copy(update={"title": target})
        changed = True
        logs.append(f"RFP structure: retitled “{current}” → “{target}”")
    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


async def extract_rfp_scored_section_specs(
    rfp_text: str,
    *,
    rfp_title: str = "",
    existing_section_titles: list[str] | None = None,
) -> list[RfpSectionSpec]:
    """LLM: scored narrative sections + required internal outline from THIS RFP only."""
    excerpt = submission_documents_excerpt(rfp_text) or rfp_text[:80000]
    bmp_headings = detect_bmp_exhibit_required_headings(rfp_text)
    specs: list[RfpSectionSpec] = []
    existing_titles = [t for t in (existing_section_titles or []) if t.strip()]

    if bmp_headings:
        specs.append(
            RfpSectionSpec(
                rfp_title="Brand Marketing Plan",
                required_headings=bmp_headings,
                instructions=(
                    "Restructure the Brand Marketing Plan to follow Exhibit A exactly "
                    "(lettered A–G sections). Include phased timeline/work plan INSIDE "
                    "the BMP — do not use a generic agency-only framework without RFP headings."
                ),
            )
        )

    if not llm.is_configured():
        return specs

    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Read ONE RFP. Return the proposer's submission sequence IN ORDER — "
                        "this RFP's TOC / 'proposal shall include' / numbered contents list.\n"
                        "Use only titles this RFP actually names. Do not invent a default stack "
                        "or generic agency section labels the RFP's own TOC does not use.\n"
                        "Never invent a mega-section that bundles multiple separately-named RFP "
                        "items — that duplicates content under two titles.\n"
                        "When THIS RFP has a mandatory content-format or submission-layout section, "
                        "prefer its headings and order over evaluation-criteria labels from "
                        "elsewhere when they conflict.\n"
                        "Skip text that describes the BUYER's own internal review process — "
                        "not something the proposer submits.\n"
                        "Every exhibit, appendix, or attachment THIS RFP's submittal list marks "
                        "as required gets its own tab.\n"
                        "If this RFP states an order, the JSON array must match that order.\n"
                        "Do NOT list evaluation-category labels that duplicate a TOC tab.\n"
                        "Do NOT emit two titles that are the same ask (near-duplicate labels for "
                        "one requirement) — keep the RFP's primary TOC wording only.\n"
                        "The draft already keeps static company/team/experience tabs. Those stay "
                        "as-is — do not ask to rewrite them.\n"
                        "If a TOC item is ONLY the firm's identity block already covered by those "
                        "static tabs, set satisfiedByStaticCompanyBlock true — header label only.\n"
                        "If a TOC item is a scored narrative evaluators read as its own tab, set "
                        "satisfiedByStaticCompanyBlock false — substance is required.\n"
                        "Dynamic tabs are whatever else THIS RFP asks the proposer to submit.\n"
                        "Mandatory compliance items that require a detailed narrative response and "
                        "evidence — not only a signed form — are dynamic tabs; do not skip them.\n"
                        "sameAskAs = existing draft titles that are the SAME ask by meaning — "
                        "not a keyword synonym list.\n"
                        "Return JSON:\n"
                        '{"sections":[{"rfpTitle":"...","requiredHeadings":["A. ..."],'
                        '"instructions":"how to align prose","evaluationWeight":"35 pts",'
                        '"sameAskAs":["existing draft title if same ask"],'
                        '"satisfiedByStaticCompanyBlock":false}]}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"RFP: {rfp_title}\n"
                        f"Existing draft tabs:\n"
                        + "\n".join(f"- {t}" for t in existing_titles[:80])
                        + "\nUse the cached RFP excerpt. Return the JSON object."
                    ),
                },
            ],
            max_tokens=16000,
            temperature=0.1,
            cache_prefix=excerpt[:45000],
        )
        rfp_text_cf = rfp_text.casefold()
        for row in (raw or {}).get("sections") or []:
            if not isinstance(row, dict):
                continue
            title = str(row.get("rfpTitle") or "").strip()
            if not title:
                continue
            # Deterministic backstop for a canned-mega-section phrase this app's
            # OTHER clients' RFPs genuinely ask for often enough that the "do
            # not invent a default stack" instruction alone isn't reliable —
            # the model still emits it even when told not to. Plain substring
            # check: if the RFP text itself never says this phrase, it wasn't
            # this RFP's own ask.
            title_cf = title.casefold()
            if "brand marketing plan" in title_cf and "brand marketing plan" not in rfp_text_cf:
                logger.info(
                    "extract_rfp_scored_section_specs: dropped invented "
                    "'%s' — not named anywhere in this RFP's text",
                    title,
                )
                continue
            headings = [
                str(h).strip()
                for h in (row.get("requiredHeadings") or [])
                if str(h).strip()
            ]
            same_ask = [
                str(x).strip()
                for x in (row.get("sameAskAs") or row.get("same_ask_as") or [])
                if str(x).strip()
            ]
            satisfied_static = bool(
                row.get("satisfiedByStaticCompanyBlock")
                or row.get("satisfied_by_static_company_block")
            )
            instructions = str(row.get("instructions") or "").strip()
            if not instructions:
                instructions = "Required in this RFP's submission sequence."
            candidate = RfpSectionSpec(
                rfp_title=title,
                required_headings=headings,
                instructions=instructions,
                evaluation_weight=str(row.get("evaluationWeight") or "").strip(),
                same_ask_as=same_ask,
                satisfied_by_static_company_block=satisfied_static,
            )
            if _spec_is_rfp_title_noise(candidate):
                continue
            if title.casefold() == "brand marketing plan" and specs:
                # Merge LLM headings with Exhibit A if richer
                existing = specs[0]
                merged = list(dict.fromkeys([*existing.required_headings, *headings]))
                specs[0] = RfpSectionSpec(
                    rfp_title=existing.rfp_title,
                    required_headings=merged,
                    instructions=str(row.get("instructions") or existing.instructions),
                    evaluation_weight=str(row.get("evaluationWeight") or ""),
                    same_ask_as=list(dict.fromkeys([*existing.same_ask_as, *same_ask])),
                    satisfied_by_static_company_block=(
                        existing.satisfied_by_static_company_block or satisfied_static
                    ),
                )
                continue
            specs.append(
                RfpSectionSpec(
                    rfp_title=title,
                    required_headings=headings,
                    instructions=instructions,
                    evaluation_weight=str(row.get("evaluationWeight") or "").strip(),
                    same_ask_as=same_ask,
                    satisfied_by_static_company_block=satisfied_static,
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("RFP section spec extract failed: %s", exc)

    return specs


def _title_is_qual_or_reference(title: str) -> bool:
    """True for project/reference qualification tabs we must not invent.

    Explicitly excludes Team / Respondent / Personnel Qualifications — those are
    scored narrative sections that Complete & Clean must draft from bios/KB.
    """
    t = _section_title_cf(title)
    if any(
        token in t
        for token in (
            "team qualification",
            "respondent team",
            "personnel",
            "key personnel",
            "staff qualification",
            "staffing",
        )
    ):
        return False
    return any(h in t for h in _QUAL_TITLE_HINTS) or "contractor reference" in t


def _spec_is_rfp_title_noise(spec: RfpSectionSpec) -> bool:
    """LLM sometimes returns the full RFP title as a 'section' — do not reframe against it."""
    title = (spec.rfp_title or "").strip()
    if len(title) > 85:
        return True
    if title.count(" ") > 14:
        return True
    # FAQ / web-search residue ("How much does Remodeling Design cost?") is not a TOC tab.
    if title.endswith("?"):
        return True
    return False


# extract_rfp_submission_format_specs is deliberately told to "Include EVERY
# row the format/layout section requires the offeror to submit — narrative
# sections, signed forms, exhibits, attachments, and compliance statements" —
# so a pure "read this RFP-authored clause" acknowledgment (Definitions,
# Background, Notice and Authority, Terms and Conditions, Non-Collusion) comes
# back as its own row exactly like a real deliverable. Nothing downstream used
# to distinguish them: outline_sections_from_rfp_specs stamped every row
# protectFromCap=True (immune to the 8-18 section cap) and
# format_rfp_structure_specs_for_planner told the LLM planner "Do not rename,
# drop, or invent a parallel stack" for the same list — so even the planner's
# own anti-bloat instincts got overridden. One real RFP produced 20+ empty
# stub tabs this way. The extraction LLM consistently frames these rows with
# a "Review ..." title (it has to invent some title for an inline compliance
# paragraph that has no natural heading of its own) — a real deliverable is
# framed as an ask ("Provide...", "Describe...", a form/exhibit name), never
# as an instruction to read something. Narrow and phrase-anchored on purpose,
# same reasoning as _spec_is_rfp_title_noise above: catch the acknowledgment
# framing without swallowing a genuine "Review and complete this schedule"
# deliverable that happens to share the first word.
_ACKNOWLEDGE_ONLY_TITLE_PREFIXES = (
    "review the ",
    "review and accept ",
    "review and agree to ",
    "review and acknowledge ",
)


def _spec_is_acknowledge_only(spec: RfpSectionSpec) -> bool:
    """True when the spec only tells the offeror to read/accept an RFP clause —
    no proposer-authored content, so it should not become (or protect) an
    outline tab."""
    title = (spec.rfp_title or "").strip().casefold()
    return title.startswith(_ACKNOWLEDGE_ONLY_TITLE_PREFIXES)


def _strip_trailing_bare_point_value(title: str) -> str:
    """Drop a trailing bare number that is an evaluation-table point value
    linearized onto the criterion text ("...commissions 20" -> "...commissions"),
    never a number that's actually part of the phrase (a currency figure, a
    percent, a dated/section reference)."""
    t = title.rstrip()
    idx = t.rfind(" ")
    if idx == -1:
        return title
    tail = t[idx + 1 :]
    if tail.isdigit() and 1 <= len(tail) <= 3:
        prefix = t[:idx].rstrip()
        if prefix and prefix[-1] not in "$%-–—":
            return prefix
    return title


def _strip_leading_duplicate_label(title: str) -> str:
    """Drop a short leading label that just restates the start of the rest of
    the title ("Cost Cost effectiveness..." -> "Cost effectiveness...",
    "References References" -> "References")."""
    words = title.split(" ")
    if len(words) < 2:
        return title
    first = words[0].casefold().rstrip(",.:;")
    rest = " ".join(words[1:])
    rest_first_word = rest.split(" ", 1)[0].casefold().rstrip(",.:;")
    if first and (first == rest_first_word or rest.casefold().startswith(f"{first} ")):
        return rest
    return title


# extract_rfp_submission_format_specs reads whatever the RFP's evaluation
# criteria / scoring table looks like once linearized to plain text. A table
# with columns like [Category | Criterion | Points] reads left-to-right per
# row with no column boundary, so "Cost | Cost effectiveness, value, and
# transparency of fees and commissions | 20" comes out as one run-on string:
# "Cost Cost effectiveness, value, and transparency of fees and commissions
# 20" — the category label duplicated onto the front, the point value stuck
# on the end. The extraction prompt's own "use the buyer's OWN wording"
# instruction (necessary so it doesn't invent a nicer paraphrase — see
# _spec_is_acknowledge_only above) means it faithfully copies this garbling
# instead of cleaning it up. Deterministic, not a rewrite of the prompt: the
# prompt still owns "don't paraphrase the RFP", this only removes a value
# (the point score) and a duplicated word that clearly aren't part of the
# criterion's own name.
def _clean_spec_title(title: str) -> str:
    return _strip_leading_duplicate_label(_strip_trailing_bare_point_value(title))


def _is_static_1_3_section(section: ProposalSection) -> bool:
    """zö company / team / work tabs — keep in place; never rewrite for TOC labels."""
    sid = section.id or ""
    return (
        sid.startswith("section-1-")
        or sid.startswith("section-2-bio-")
        or sid.startswith("section-3-work-")
        or sid in {"section-2-team-overview", "section-3-our-work"}
    )


def _titles_are_same_ask(rfp_title: str, section_title: str, aliases: list[str]) -> bool:
    from app.services.proposal_outline_dedup import (
        outline_title_tokens,
        outline_titles_near_duplicate,
    )

    if outline_titles_near_duplicate(rfp_title, section_title):
        return True
    # An alias only proves rfp_title and section_title are the same ask if it
    # actually links the two — the LLM extracting these can hallucinate one
    # (e.g. claiming an existing "Qualifications and Experience" tab is the
    # same ask as an RFP-mandated "Response File" label, sharing not one
    # word). Require the alias to share at least one real token with
    # rfp_title before trusting it enough to relabel a section by it — a
    # missed relabel just keeps the section's current (still reasonable)
    # title; a wrong one silently renames real content to something
    # unrelated and orphans whatever the RFP title actually asked for.
    rfp_tokens = outline_title_tokens(rfp_title)
    if any(
        outline_titles_near_duplicate(alias, section_title)
        and (not rfp_tokens or rfp_tokens & outline_title_tokens(alias))
        for alias in aliases
    ):
        return True
    # Buyer labels for the same offer-letter tab (not a KB evidence synonym table).
    rfp_cf = (rfp_title or "").casefold()
    sec_cf = (section_title or "").casefold()
    if _title_is_cover_letter_family(rfp_cf) and _title_is_cover_letter_family(sec_cf):
        return True
    if any(
        _title_is_cover_letter_family((alias or "").casefold())
        for alias in aliases
    ) and _title_is_cover_letter_family(sec_cf):
        return True
    return False


def _title_is_cover_letter_family(title_cf: str) -> bool:
    t = (title_cf or "").casefold()
    if not t:
        return False
    if "cover" in t and "letter" in t:
        return True
    if "transmittal" in t:
        return True
    if "letter" in t and "offer" in t:
        return True
    return False


def _match_section_for_spec(
    draft: ProposalDraft,
    spec: RfpSectionSpec,
) -> ProposalSection | None:
    """Match by title meaning (outline near-duplicate + LLM sameAskAs), not keyword regex."""
    aliases = list(spec.same_ask_as or [])
    for section in draft.sections:
        if _titles_are_same_ask(spec.rfp_title, section.title or "", aliases):
            return section
    return None


def _leading_concept_tokens(title: str, n: int = 2) -> list[str]:
    """First ``n`` significant tokens of a title (marker/number/stopwords stripped)."""
    from app.services.proposal_outline_dedup import _STOPWORDS, normalize_outline_title

    toks = [
        w
        for w in normalize_outline_title(title).split()
        if len(w) >= 3 and w not in _STOPWORDS
    ]
    return toks[:n]


def _spec_covered_by_closing_tab(
    sections: list[ProposalSection],
    spec: RfpSectionSpec,
) -> bool:
    """True when a closing-package tab (rfp-closing-*) already covers this ask."""
    from app.services.proposal_closing_ledger import _tokens

    needles = _tokens(spec.rfp_title or "")
    for alias in spec.same_ask_as or []:
        needles |= _tokens(alias)
    if len(needles) < 2:
        return False
    for section in sections:
        sid = section.id or ""
        if not sid.startswith("rfp-closing-"):
            continue
        if len(needles & _tokens(section.title or "")) >= 2:
            return True
    return False


def _spec_title_already_in_draft(
    sections: list[ProposalSection],
    spec: RfpSectionSpec,
) -> bool:
    """True when any tab already uses this RFP title (or sameAskAs alias) — no twin."""
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    title = (spec.rfp_title or "").strip()
    if not title:
        return False
    aliases = list(spec.same_ask_as or [])
    for section in sections:
        st = (section.title or "").strip()
        if not st:
            continue
        if _titles_are_same_ask(title, st, aliases):
            return True
        if outline_titles_near_duplicate(title, st):
            return True
    return False


def _spec_covered_by_filled_section(
    sections: list[ProposalSection],
    spec: RfpSectionSpec,
) -> bool:
    """True when an already-DRAFTED section opens with the same concept as this
    scored spec, so the stub step must not mint a duplicate.

    ``_match_section_for_spec`` only catches near-duplicate titles (jaccard ≥
    0.72); long RFP titles like "Stakeholder Coordination and Economic
    Development Through Tourism" fall under that against an existing
    "Stakeholder Coordination and Community Partnership", so a redundant empty
    stub was being added next to the writer's filled section. This is the
    coverage backstop: same leading concept ("stakeholder coordination",
    "strategic approach") + an existing section that is actually drafted. It only
    ever SUPPRESSES a stub — it never deletes or edits a section.
    """
    spec_head = _leading_concept_tokens(spec.rfp_title or "")
    if len(spec_head) < 2:
        return False
    from app.services.proposal_draft_structure_stubs import section_is_rfp_draft_stub
    from app.services.proposal_section_quality import word_count

    for section in sections:
        body = section.content or ""
        if word_count(body) < 40 or section_is_rfp_draft_stub(section):
            continue
        if _leading_concept_tokens(section.title or "") == spec_head:
            return True
    return False


def _spec_is_static_company_ask(draft: ProposalDraft, spec: RfpSectionSpec) -> bool:
    """TOC item already satisfied by Sections 1.1–1.5 / 2 / 3 — header wrap only."""
    if spec.satisfied_by_static_company_block:
        return True
    from app.services.proposal_voice_enforcement import is_duplicate_static_rfp_section

    if is_duplicate_static_rfp_section(spec.rfp_title or ""):
        return True
    matched = _match_section_for_spec(draft, spec)
    return matched is not None and _is_static_1_3_section(matched)


def _dedupe_sections_by_id(sections: list[ProposalSection]) -> list[ProposalSection]:
    """Keep the first row per section id — duplicate ids break the sidebar."""
    seen: set[str] = set()
    out: list[ProposalSection] = []
    for section in sections:
        sid = section.id or ""
        if sid and sid in seen:
            continue
        if sid:
            seen.add(sid)
        out.append(section)
    return out


def drop_duplicate_company_identity_tabs(
    draft: ProposalDraft,
    specs: list[RfpSectionSpec],
) -> tuple[ProposalDraft, list[str]]:
    """Remove intelligence pointer/essay tabs that duplicate Sections 1.1–1.5."""
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate
    from app.services.proposal_voice_enforcement import is_duplicate_static_rfp_section

    logs: list[str] = []
    wrap_specs = [
        spec
        for spec in specs
        if not _spec_is_rfp_title_noise(spec) and _spec_is_static_company_ask(draft, spec)
    ]
    drop_ids: set[str] = set()
    for section in draft.sections:
        if _is_static_1_3_section(section):
            continue
        if section.id == COMPANY_BLOCK_HEADER_ID:
            continue
        if _section_has_substantive_body(section):
            continue
        title = section.title or ""
        if is_duplicate_static_rfp_section(title):
            drop_ids.add(section.id)
            continue
        if wrap_specs and any(
            outline_titles_near_duplicate(spec.rfp_title, title) for spec in wrap_specs
        ):
            drop_ids.add(section.id)
    kept = _dedupe_sections_by_id(
        [s for s in draft.sections if s.id not in drop_ids]
    )
    if kept == list(draft.sections):
        return draft, logs
    logs.append(
        "RFP structure: dropped duplicate company-identity tab(s) — Sections 1.1–1.5 stay."
    )
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": kept, "updated_at": now}), logs


def specs_from_intelligence_outline(
    mapped_titles: list[tuple[str, str]],
    *,
    static_titles: list[str] | None = None,
) -> list[RfpSectionSpec]:
    """Build TOC specs from Phase 2 maps (title, duplicate_of_static_section)."""
    static = [t for t in (static_titles or []) if t.strip()]
    specs: list[RfpSectionSpec] = []
    for title, dup in mapped_titles:
        title = (title or "").strip()
        if not title:
            continue
        covered = bool((dup or "").strip())
        specs.append(
            RfpSectionSpec(
                rfp_title=title,
                instructions="Required in this RFP's submission sequence.",
                same_ask_as=list(static) if covered else [],
                satisfied_by_static_company_block=covered,
            )
        )
    return specs


def specs_from_scored_criteria(
    research: ProposalResearchCache | None,
) -> list[RfpSectionSpec]:
    """Section specs for every scored criterion the RFP publishes — no LLM call.

    Complete & clean already recovers missing scored tabs, but it learned the
    section list from one LLM read of the TOC. An RFP whose scoring lives in an
    evaluation-criteria response form rather than its TOC therefore lost whole
    scored sections, and the only fix on offer was regenerating the proposal
    from scratch.

    The scoreboard is already persisted on the requirement ledger from the
    intelligence phase, so reading it here is free: an existing draft gains the
    scored sections it is missing without re-running Phase 2, and sections that
    already cover a criterion are matched and left untouched by
    ``ensure_missing_scored_section_stubs``.
    """
    ledger = getattr(research, "requirement_ledger", None) if research else None
    requirements = list(getattr(ledger, "requirements", []) or []) if ledger else []
    specs: list[RfpSectionSpec] = []
    seen: set[str] = set()
    for req in requirements:
        if getattr(req, "source", "") != "scored_criterion":
            continue
        title = _clean_spec_title(str(getattr(req, "text", "") or "").strip())
        if not title:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        points = getattr(req, "points", None)
        specs.append(
            RfpSectionSpec(
                rfp_title=title,
                instructions=(
                    "Scored evaluation criterion — this RFP awards points for it, so it "
                    "needs its own section answering the buyer's ask directly."
                ),
                evaluation_weight=f"{points:g} pts" if points else "scored",
            )
        )
    return specs


async def specs_from_missing_submittals(
    rfp_text: str,
    *,
    existing_section_titles: list[str] | None = None,
) -> list[RfpSectionSpec]:
    """Section specs for whatever the completeness-check agent reports missing.

    Closing-component detection (the LLM pass that normally finds exhibits to
    return) is one call juggling ~20 instructions at once and was observed to
    vary run-to-run on the SAME RFP — 9 sections one Phase 2 run, 8 the next,
    never landing on all of them. This reuses the SAME focused, single-purpose
    completeness agent the live Intelligence path now runs (see
    proposal_evaluation_coverage.find_missing_submittals_via_llm) — one small
    targeted call, not a keyword parser and not a full re-derivation — so
    Complete & clean can add whatever an earlier run missed to an ALREADY
    drafted proposal, without re-running Phase 2 or touching a single
    already-drafted section. Proposals generated before this fix existed
    don't retroactively benefit from the live-path check, which is what this
    Complete & clean recovery is for.
    """
    from app.services.proposal_evaluation_coverage import find_missing_submittals_via_llm

    titles = [t for t in (existing_section_titles or []) if (t or "").strip()]
    reported = await find_missing_submittals_via_llm(rfp_text, titles)
    return [
        RfpSectionSpec(
            rfp_title=item["title"],
            instructions=(
                (item["reason"] or "Required submittal flagged by completeness check.")
                + " Emit a short checklist plus [DESIGNER NOTE: Attach …] / "
                "[MANUAL FILL: …] for any signed form the buyer supplies — do "
                "not invent form content."
            ),
        )
        for item in reported
        if item["mandatory"]
    ]


def apply_rfp_toc_layout(
    draft: ProposalDraft,
    specs: list[RfpSectionSpec],
) -> tuple[ProposalDraft, list[str]]:
    """Order intelligence tabs + Company Background header. No prose rewrite."""
    logs: list[str] = []
    draft, drop_logs = drop_duplicate_company_identity_tabs(draft, specs)
    logs.extend(drop_logs)
    draft, order_logs = order_draft_to_rfp_sequence(draft, specs)
    logs.extend(order_logs)
    draft, wrap_logs = ensure_company_block_wrapper_heading(draft, specs)
    logs.extend(wrap_logs)
    draft, pointer_logs = repair_pointer_only_rfp_sections(draft)
    logs.extend(pointer_logs)
    from app.services.proposal_budget_content import collapse_duplicate_cost_proposal_tabs

    collapsed, cost_logs = collapse_duplicate_cost_proposal_tabs(list(draft.sections))
    if cost_logs:
        now = datetime.now(timezone.utc).isoformat()
        draft = draft.model_copy(update={"sections": collapsed, "updated_at": now})
        logs.extend(cost_logs)
    draft, relabel_logs = apply_rfp_mandated_section_titles(draft, specs)
    logs.extend(relabel_logs)
    return draft, logs


def ensure_company_block_wrapper_heading(
    draft: ProposalDraft,
    specs: list[RfpSectionSpec],
) -> tuple[ProposalDraft, list[str]]:
    """Insert the RFP's company-background title as a header above 1.1–1.5.

    Does not rewrite Who We Are / org / business / certs / insurance.
    Certifications and insurance stay nested in that block (not duplicated).
    """
    logs: list[str] = []
    sections = _dedupe_sections_by_id(list(draft.sections))
    if any(s.id == COMPANY_BLOCK_HEADER_ID for s in sections):
        if sections != list(draft.sections):
            now = datetime.now(timezone.utc).isoformat()
            return draft.model_copy(update={"sections": sections, "updated_at": now}), logs
        return draft, logs
    wrap_spec: RfpSectionSpec | None = None
    for spec in specs:
        if _spec_is_rfp_title_noise(spec):
            continue
        if _spec_is_static_company_ask(draft, spec):
            wrap_spec = spec
            break
    if wrap_spec is None:
        if sections != list(draft.sections):
            now = datetime.now(timezone.utc).isoformat()
            return draft.model_copy(update={"sections": sections, "updated_at": now}), logs
        return draft, logs
    first_company = next(
        (i for i, s in enumerate(sections) if (s.id or "").startswith("section-1-")),
        None,
    )
    if first_company is None:
        return draft, logs
    header = ProposalSection(
        id=COMPANY_BLOCK_HEADER_ID,
        title=wrap_spec.rfp_title,
        content=(
            f"## {wrap_spec.rfp_title}\n\n"
            "[DESIGNER NOTE: Sections 1.1–1.5 follow immediately below — "
            "this header matches the RFP TOC label only.]"
        ),
        status="generated",
        source="generated",
        mode="write",
        required=True,
        word_target=40,
    )
    sections.insert(first_company, header)
    logs.append(
        f"RFP structure: labeled Sections 1.1–1.5 as “{wrap_spec.rfp_title}” "
        "(header only — company tabs unchanged)."
    )
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


def repair_pointer_only_rfp_sections(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Replace cross-ref-only RFP tabs with draftable stubs — evaluators need substance."""
    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        if _is_static_1_3_section(section) or section.id == COMPANY_BLOCK_HEADER_ID:
            sections.append(section)
            continue
        body = (section.content or "").strip()
        if not is_pointer_only_company_delegation(body):
            sections.append(section)
            continue
        title = (section.title or "this section").strip()
        replacement = (
            f"## {title}\n\n"
            f"[MANUAL FILL: Draft full «{title}» for this RFP. Cover every scored ask "
            "in the RFP for this tab — firm background, relevant experience, and proof. "
            "One brief cross-reference to Section 1 or 2 is fine; this tab must stand alone "
            "for evaluators and may be long.]"
        )
        sections.append(section.model_copy(update={"content": replacement, "status": "generated"}))
        logs.append(f"RFP structure: replaced pointer-only tab “{title}” with draft stub")
        changed = True
    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


def order_draft_to_rfp_sequence(
    draft: ProposalDraft,
    specs: list[RfpSectionSpec],
) -> tuple[ProposalDraft, list[str]]:
    """Order intelligence / dynamic RFP tabs to the buyer's sequence.

    Sections 1–3 stay where they are (content and titles unchanged).
    Matching uses outline title meaning + LLM sameAskAs — not keyword regex.
    """
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    logs: list[str] = []
    if not specs or not draft.sections:
        return draft, logs
    static = [s for s in draft.sections if _is_static_1_3_section(s)]
    dynamic = [s for s in draft.sections if not _is_static_1_3_section(s)]
    if not dynamic:
        return draft, logs
    working = draft.model_copy(update={"sections": dynamic})
    used: set[str] = set()
    ordered: list[ProposalSection] = []
    for spec in specs:
        if _spec_is_rfp_title_noise(spec):
            continue
        if _spec_is_static_company_ask(draft, spec):
            continue
        section = _match_section_for_spec(working, spec)
        if section is None or section.id in used:
            continue
        # No relabeling here — deliberately. _match_section_for_spec matches
        # via direct title similarity OR an LLM sameAskAs alias, and only the
        # alias path can find a section whose title differs from spec.rfp_title
        # (a direct match, by definition, already IS a near-duplicate title).
        # An alias match means "this section covers a different, related ask
        # already — don't add a duplicate tab for it", a coverage signal, not
        # an identity signal. Relabeling on it previously took a correctly
        # drafted "SECTION I — Background and Qualifications" tab and
        # overwrote its title with "EXHIBIT 1: Evaluation Criteria Response
        # Form" (the alias for a DIFFERENT spec) — and did so again on every
        # subsequent Complete & clean run, since the same alias re-matches
        # the same way each time. This function orders tabs; it never retitles
        # one, because it cannot tell "same ask, buyer's fuller wording" apart
        # from "different ask, already covered" from title similarity alone.
        ordered.append(section)
        used.add(section.id)
    rest_dynamic = [s for s in dynamic if s.id not in used]
    new_sections = static + ordered + rest_dynamic
    before_ids = [s.id for s in draft.sections]
    after_ids = [s.id for s in new_sections]
    if before_ids == after_ids:
        return draft, logs
    if ordered:
        logs.append(
            "RFP structure: ordered intelligence tabs to the RFP submission sequence: "
            + " → ".join((s.title or s.id) for s in ordered[:12])
        )
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": new_sections, "updated_at": now}), logs


def _slug_section_id(title: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", (title or "").casefold()).strip("-")
    return (raw or "section")[:48]


def ensure_missing_scored_section_stubs(
    draft: ProposalDraft,
    specs: list[RfpSectionSpec],
    *,
    skip_section_ids: set[str] | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """Append VERIFY stubs for RFP-scored tabs that have no manuscript section.

    Structure Scan historically only reframed existing tabs — missing TOC/scored
    sections never appeared. Stubs make the gap visible and draftable.
    """
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    skip = skip_section_ids or set()
    logs: list[str] = []
    sections = list(draft.sections)
    existing_ids = {s.id for s in sections}
    changed = False

    for spec in specs:
        if _spec_is_rfp_title_noise(spec):
            continue
        if (
            not spec.required_headings
            and not spec.instructions
            and not spec.evaluation_weight
            and not spec.mandated_submission_format
        ):
            continue
        working = draft.model_copy(update={"sections": sections})
        if _spec_is_static_company_ask(working, spec):
            continue
        if _match_section_for_spec(working, spec):
            continue
        if _spec_title_already_in_draft(sections, spec):
            logs.append(
                f"RFP structure: “{spec.rfp_title}” title already in draft — no stub added"
            )
            continue
        if _spec_covered_by_closing_tab(sections, spec):
            logs.append(
                f"RFP structure: “{spec.rfp_title}” covered by closing tab — no stub added"
            )
            continue
        # Coverage backstop: don't mint a duplicate stub next to a section the
        # writer already drafted under a slightly different (sub-threshold) title.
        if _spec_covered_by_filled_section(sections, spec):
            logs.append(
                f"RFP structure: “{spec.rfp_title}” already covered by a drafted "
                "section — no stub added"
            )
            continue
        # A TOC ask for personnel resumes/CVs/key-staff qualifications is
        # already met by the per-person Section 2 bio tabs — those ARE the
        # resumes, just filed one per person instead of under one combined
        # heading. Scoped to stub creation only (never the shared
        # _spec_is_static_company_ask predicate, which a different caller
        # also uses to decide whether to DELETE an existing thin section —
        # this check must never be able to cause that).
        if _spec_is_personnel_resume_ask(spec.rfp_title or "") and any(
            (s.id or "").startswith("section-2-bio-") for s in sections
        ):
            continue
        sid = f"rfp-structure-{_slug_section_id(spec.rfp_title)}"
        # Never mint a -2 twin of an existing stub id — that produced duplicate
        # sidebar tabs with the same RFP title.
        if sid in existing_ids:
            continue
        if sid in skip:
            n = 2
            while f"{sid}-{n}" in existing_ids or f"{sid}-{n}" in skip:
                n += 1
            sid = f"{sid}-{n}"
            # If an alternate id still collides on title meaning, skip entirely.
            if any(
                outline_titles_near_duplicate(spec.rfp_title, s.title or "")
                for s in sections
            ):
                continue
        heading_lines = "\n".join(f"- {h}" for h in (spec.required_headings or [])[:12])
        body_parts = [
            f"## {spec.rfp_title}",
            "",
            f"[MANUAL FILL: Draft this RFP-required section — {spec.rfp_title}]",
            "",
        ]
        if heading_lines:
            body_parts.extend(["RFP-required outline:", heading_lines, ""])
        if spec.instructions:
            body_parts.extend([f"RFP instructions: {spec.instructions.strip()[:800]}", ""])
        if spec.evaluation_weight:
            body_parts.append(f"Evaluation weight: {spec.evaluation_weight}")
        sections.append(
            ProposalSection(
                id=sid,
                title=spec.rfp_title,
                content="\n".join(body_parts).strip(),
                status="generated",
                source="generated",
                mode="write",
                required=True,
                word_target=550,
            )
        )
        existing_ids.add(sid)
        logs.append(f"RFP structure: added missing scored section stub “{spec.rfp_title}”")
        changed = True

    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


async def _reframe_section_to_rfp_spec(
    *,
    section: ProposalSection,
    spec: RfpSectionSpec,
    rfp: RfpRecord,
    rfp_excerpt: str,
    missing_headings: list[str],
) -> str:
    stub = section.content or ""
    if not llm.is_configured():
        return stub

    system = (
        "Rewrite ONE proposal section so it matches THIS RFP's required structure and scoring "
        "criteria — not a generic agency template.\n"
        "Rules:\n"
        "- Use the required headings/outline exactly (markdown ## with RFP labels).\n"
        "- Preserve verified facts, team names, and numbers from the current draft.\n"
        "- Do NOT recopy full case-study narratives or the same example in new words — "
        "one-line cross-ref to the draft's dedicated portfolio / past-work tab, then NEW detail only.\n"
        "- Do NOT invent clients, case studies, reference contacts, metrics, or geographies "
        "not evidenced in the draft or knowledge base.\n"
        "- If evidence is missing, use [VERIFY: …] — never fabricate named engagements.\n"
        "- Fold timeline/phases INTO this section when THIS RFP embeds schedule in this tab.\n"
        "- Do NOT rewrite team bios or static company-identity tabs already in the draft.\n"
        "- HARD LENGTH CEILING = the Word target below. Fully answer THIS tab's ask "
        "within it and STOP — never exceed it. Shorter is better when the ask is "
        "covered. Cut any sentence that does not add RFP-specific substance; do not "
        "add subsections the RFP did not ask for. Prefer tight paragraphs, markdown "
        "bullets, and markdown tables over long essays. Never pad for length.\n"
        "- When a table/timeline/swimlane would help evaluators, add "
        "[DESIGNER NOTE: concrete layout hint] near that block.\n"
        "- These rules govern how you write; they are never content. Never write "
        "sentences about submission requirements, pass/fail status, what cannot be "
        "submitted, or what must be verified or confirmed with anyone — apply the "
        "rule silently instead of narrating it. The [VERIFY: ...] / [DESIGNER NOTE: ...] "
        "tag is the only trace of a gap; never explain or preface it.\n"
        'Return JSON: {"content": "full markdown section", "designerNote": "hint or null"}'
    )
    word_target = getattr(section, "word_target", None) or 550
    # Cap output near the target so the model physically cannot write a
    # multi-page essay it later gets crudely truncated to. ~1.6 tokens/word of
    # prose + headroom for markdown tables/bullets, floored so short targets
    # still fit a complete answer.
    max_out = min(16000, max(1400, int(word_target * 2.4)))
    user = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\n"
        f"Section: {section.title}\n"
        f"Word target: {word_target} (HARD ceiling — answer fully within it, do not exceed)\n"
        f"RFP expects: {spec.rfp_title}\n"
        f"Required headings still missing or weak: {', '.join(missing_headings) or spec.required_headings}\n"
        f"Alignment instructions: {spec.instructions}\n"
        f"Evaluation: {spec.evaluation_weight}\n\n"
        f"Current section (restructure — keep true facts):\n{stub[:16000]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_out,
            temperature=0.25,
            cache_prefix=rfp_excerpt[:35000],
        )
        content = str((raw or {}).get("content") or "").strip()
        note = str((raw or {}).get("designerNote") or (raw or {}).get("designer_note") or "").strip()
        if content and note and "[DESIGNER NOTE:" not in content.upper():
            content = f"{content.rstrip()}\n\n[DESIGNER NOTE: {note}]"
        return content or stub
    except Exception as exc:  # noqa: BLE001
        logger.warning("RFP structure reframe failed for %s: %s", section.id, exc)
        return stub


async def _redraft_verify_stub_section(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    rfp_excerpt: str,
    requirements: list[str],
) -> str:
    """Disabled — Scan must not LLM-fill qualification stubs (fabrication risk)."""
    _ = (rfp, rfp_excerpt, requirements)
    return section.content or ""


def _requirements_for_section(
    research: ProposalResearchCache | None,
    section_id: str,
) -> list[str]:
    if not research:
        return []
    for mapped in research.rfp_sections or []:
        if mapped.id == section_id:
            return list(mapped.requirements or [])
    return []


async def apply_rfp_section_order_pass(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None,
    skip_section_ids: set[str] | None = None,
    add_missing_mandated_stubs: bool = False,
    include_missing_submittals: bool = False,
) -> tuple[ProposalDraft, list[str]]:
    """Reorder manuscript tabs to the RFP TOC; optionally stub mandated gaps first.

    Closing / ledger / compulsory paths append new tabs at the list tail — this
    pass is the shared fix so Cover Letter and certification forms land in RFP
    order, not after References.
    """
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    logs: list[str] = []
    specs, build_logs = await build_rfp_structure_specs(
        rfp_text,
        rfp_title=rfp.title,
        existing_section_titles=[s.title for s in draft.sections if s.title],
        include_missing_submittals=include_missing_submittals or add_missing_mandated_stubs,
    )
    logs.extend(build_logs)
    if not specs:
        logs.append("RFP order pass: no section specs extracted — layout skipped.")
        return draft, logs

    criterion_specs = specs_from_scored_criteria(research)
    if criterion_specs:
        known = [s.rfp_title for s in specs]
        added = [
            spec
            for spec in criterion_specs
            if not any(
                outline_titles_near_duplicate(spec.rfp_title, title) for title in known
            )
        ]
        if added:
            specs = merge_specs_submission_format_first(specs, added)
            logs.append(
                f"RFP order pass: +{len(added)} scored criterion spec(s) from ledger"
            )

    if add_missing_mandated_stubs:
        draft, stub_logs = ensure_missing_scored_section_stubs(
            draft,
            specs,
            skip_section_ids=skip_section_ids or set(),
        )
        logs.extend(stub_logs)

    draft, layout_logs = apply_rfp_toc_layout(draft, specs)
    logs.extend(layout_logs)
    return draft, logs


async def run_rfp_structure_alignment_pass(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None,
    skip_section_ids: set[str],
    use_llm: bool,
    include_missing_submittals: bool = False,
) -> tuple[ProposalDraft, list[str], list[str]]:
    """Walk scored RFP sections — reframe outline, redraft VERIFY stubs (any RFP)."""
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    logs: list[str] = []
    human: list[str] = []
    excerpt = submission_documents_excerpt(rfp_text) or rfp_text[:100_000]

    specs, build_logs = await build_rfp_structure_specs(
        rfp_text,
        rfp_title=rfp.title,
        existing_section_titles=[s.title for s in draft.sections if s.title],
        include_missing_submittals=include_missing_submittals,
    )
    logs.extend(build_logs)
    if not specs:
        logs.append("RFP structure: no section outline detected from RFP.")
    else:
        logs.append(f"RFP structure: {len(specs)} combined section spec(s) from RFP.")

    # Union scored criterion specs from ledger when not already covered.
    # case the TOC read misses: an RFP that scores against a criteria response
    # form rather than its table of contents.
    criterion_specs = specs_from_scored_criteria(research)
    if criterion_specs:
        known = [s.rfp_title for s in specs]
        added = [
            spec
            for spec in criterion_specs
            if not any(
                outline_titles_near_duplicate(spec.rfp_title, title) for title in known
            )
        ]
        if added:
            specs = merge_specs_submission_format_first(specs, added)
            logs.append(
                f"RFP structure: +{len(added)} scored criterion spec(s) from the "
                f"requirement ledger: {[s.rfp_title for s in added][:8]}"
            )

    # Recover tabs the outline lean-filter / Phase 3 skip dropped entirely.
    draft, stub_logs = ensure_missing_scored_section_stubs(
        draft, specs, skip_section_ids=skip_section_ids
    )
    logs.extend(stub_logs)

    draft, layout_logs = apply_rfp_toc_layout(draft, specs)
    logs.extend(layout_logs)

    sections = list(draft.sections)
    changed = bool(stub_logs or layout_logs)
    reframed_ids: set[str] = set()

    for spec in specs:
        if _spec_is_rfp_title_noise(spec):
            continue
        if _spec_is_static_company_ask(draft, spec):
            continue
        if not spec.required_headings and not spec.instructions:
            continue
        working = draft.model_copy(update={"sections": sections})
        section = _match_section_for_spec(working, spec)
        if not section or section.id in skip_section_ids or _is_static_1_3_section(section):
            continue
        if section.id in reframed_ids:
            continue
        if fulfill_scan_preserves_section(section):
            continue
        if _title_is_qual_or_reference(section.title or ""):
            continue
        body = section.content or ""
        missing = _headings_present(body, spec.required_headings) if spec.required_headings else []
        generic_only = (
            "phase 1" in body.casefold()
            and "discover" in body.casefold()
            and spec.required_headings
            and len(missing) >= max(2, len(spec.required_headings) // 2)
        )
        if not use_llm:
            if missing:
                human.append(
                    f"“{section.title}” missing RFP outline: {', '.join(missing[:6])} "
                    "— re-run Scan with LLM to reframe."
                )
            continue
        if _VERIFY_STUB_RE.search(body):
            continue
        # Complete & Clean must not rewrite a real drafted section just to
        # restamp headings. Only reframe hollow stubs / generic templates.
        substantial = len(body.strip()) >= 400 and "[MANUAL FILL:" not in body[:500]
        if substantial and not generic_only:
            continue
        if missing or generic_only:
            idx = next(i for i, s in enumerate(sections) if s.id == section.id)
            new_content = await _reframe_section_to_rfp_spec(
                section=section,
                spec=spec,
                rfp=rfp,
                rfp_excerpt=excerpt,
                missing_headings=missing or spec.required_headings,
            )
            if new_content.strip() and new_content != body:
                if looks_truncated_for_fulfill(new_content):
                    human.append(
                        f"“{section.title}” reframe may have truncated — re-run Scan or restore snapshot."
                    )
                    continue
                from app.services.proposal_consistency import regression_vs_prior

                if regression_vs_prior(
                    section, section.model_copy(update={"content": new_content})
                ):
                    human.append(
                        f"“{section.title}” reframe would have thinned an already-drafted "
                        f"section — kept the existing content. Add {', '.join(missing[:6]) or 'the missing headings'} "
                        "directly (or via section chat) instead of re-running Scan."
                    )
                    continue
                sections[idx] = section.model_copy(
                    update={"content": new_content, "status": "generated"}
                )
                logs.append(
                    f"RFP structure: reframed “{section.title}” to {spec.rfp_title} outline"
                )
                reframed_ids.add(section.id)
                changed = True

    # Qualifications: never LLM-invent — keep [VERIFY] until KB/Sonja fills.
    for section in sections:
        if section.id in skip_section_ids or fulfill_scan_preserves_section(section):
            continue
        if not _title_is_qual_or_reference(section.title or ""):
            continue
        if _VERIFY_STUB_RE.search(section.content or ""):
            human.append(
                f"“{section.title}” remains [VERIFY] — add verified Section 3 / KB content manually; "
                "Scan will not fabricate case studies or references."
            )

    if not changed:
        return draft, logs, human

    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs, human
