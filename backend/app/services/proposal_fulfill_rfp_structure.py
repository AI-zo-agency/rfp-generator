"""Scan RFP — align manuscript sections to THIS RFP's required outline (Exhibit A, TOC, criteria)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

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


@dataclass
class RfpSectionSpec:
    rfp_title: str
    required_headings: list[str] = field(default_factory=list)
    instructions: str = ""
    evaluation_weight: str = ""


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


async def extract_rfp_scored_section_specs(
    rfp_text: str,
    *,
    rfp_title: str = "",
) -> list[RfpSectionSpec]:
    """LLM: scored narrative sections + required internal outline from THIS RFP only."""
    excerpt = submission_documents_excerpt(rfp_text) or rfp_text[:80000]
    bmp_headings = detect_bmp_exhibit_required_headings(rfp_text)
    specs: list[RfpSectionSpec] = []

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
                        "Read ONE RFP. List every SCORED NARRATIVE proposal section the evaluator "
                        "grades (BMP, qualifications, approach, references narrative, etc.).\n"
                        "For each, give the RFP's required INTERNAL outline (headings, exhibit "
                        "letters, checklist bullets) — not zö's generic template.\n"
                        "Do NOT list signed forms-only items.\n"
                        "Return JSON:\n"
                        '{"sections":[{"rfpTitle":"...","requiredHeadings":["A. ..."],'
                        '"instructions":"how to align prose","evaluationWeight":"35 pts"}]}'
                    ),
                },
                {
                    "role": "user",
                    "content": f"RFP: {rfp_title}\n\nExcerpt:\n{excerpt[:45000]}",
                },
            ],
            max_tokens=3072,
            temperature=0.1,
        )
        for row in (raw or {}).get("sections") or []:
            if not isinstance(row, dict):
                continue
            title = str(row.get("rfpTitle") or "").strip()
            if not title:
                continue
            headings = [
                str(h).strip()
                for h in (row.get("requiredHeadings") or [])
                if str(h).strip()
            ]
            if title.casefold() == "brand marketing plan" and specs:
                # Merge LLM headings with Exhibit A if richer
                existing = specs[0]
                merged = list(dict.fromkeys([*existing.required_headings, *headings]))
                specs[0] = RfpSectionSpec(
                    rfp_title=existing.rfp_title,
                    required_headings=merged,
                    instructions=str(row.get("instructions") or existing.instructions),
                    evaluation_weight=str(row.get("evaluationWeight") or ""),
                )
                continue
            specs.append(
                RfpSectionSpec(
                    rfp_title=title,
                    required_headings=headings,
                    instructions=str(row.get("instructions") or "").strip(),
                    evaluation_weight=str(row.get("evaluationWeight") or "").strip(),
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
    return False


def _match_section_for_spec(
    draft: ProposalDraft,
    spec: RfpSectionSpec,
) -> ProposalSection | None:
    want = spec.rfp_title.casefold()
    want_tokens = {t for t in re.split(r"\W+", want) if len(t) >= 4}
    best: ProposalSection | None = None
    best_score = 0
    for section in draft.sections:
        t = _section_title_cf(section.title)
        if want in t or t in want:
            return section
        tokens = {t for t in re.split(r"\W+", t) if len(t) >= 4}
        score = len(want_tokens & tokens)
        if score > best_score:
            best_score = score
            best = section
    return best if best_score >= 2 else None


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
    skip = skip_section_ids or set()
    logs: list[str] = []
    sections = list(draft.sections)
    existing_ids = {s.id for s in sections}
    changed = False

    for spec in specs:
        if _spec_is_rfp_title_noise(spec):
            continue
        if not spec.required_headings and not spec.instructions and not spec.evaluation_weight:
            continue
        working = draft.model_copy(update={"sections": sections})
        if _match_section_for_spec(working, spec):
            continue
        sid = f"rfp-structure-{_slug_section_id(spec.rfp_title)}"
        if sid in existing_ids or sid in skip:
            n = 2
            while f"{sid}-{n}" in existing_ids:
                n += 1
            sid = f"{sid}-{n}"
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
        "criteria — not zö's generic template.\n"
        "Rules:\n"
        "- Use the required headings/outline exactly (markdown ## with RFP labels).\n"
        "- Preserve verified facts, team names, case studies, and numbers from the current draft.\n"
        "- Do NOT invent clients, case studies, reference contacts, metrics, or Oceania/Hawaii work.\n"
        "- If evidence is missing, use [VERIFY: …] — never fabricate named engagements.\n"
        "- Fold timeline/phases INTO this section when the RFP expects schedule here (e.g. BMP).\n"
        "- Do NOT rewrite team bios (Section 2.x) or static company tabs.\n"
        "- Stay CONCISE: prefer short paragraphs, markdown bullets, and markdown tables over long essays. "
        "Respect the Word target when provided — never pad.\n"
        "- When a table/timeline/swimlane would help evaluators, add "
        "[DESIGNER NOTE: concrete layout hint] near that block.\n"
        'Return JSON: {"content": "full markdown section", "designerNote": "hint or null"}'
    )
    word_target = getattr(section, "word_target", None) or 550
    user = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\n"
        f"Section: {section.title}\n"
        f"Word target: {word_target} (stay at or under)\n"
        f"RFP expects: {spec.rfp_title}\n"
        f"Required headings still missing or weak: {', '.join(missing_headings) or spec.required_headings}\n"
        f"Alignment instructions: {spec.instructions}\n"
        f"Evaluation: {spec.evaluation_weight}\n\n"
        f"RFP excerpt:\n{rfp_excerpt[:35000]}\n\n"
        f"Current section (restructure — keep true facts):\n{stub[:16000]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=8192,
            temperature=0.25,
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


async def run_rfp_structure_alignment_pass(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None,
    skip_section_ids: set[str],
    use_llm: bool,
) -> tuple[ProposalDraft, list[str], list[str]]:
    """Walk scored RFP sections — reframe outline, redraft VERIFY stubs (any RFP)."""
    logs: list[str] = []
    human: list[str] = []
    excerpt = submission_documents_excerpt(rfp_text) or rfp_text[:100_000]

    specs = await extract_rfp_scored_section_specs(rfp_text, rfp_title=rfp.title)
    if not specs:
        logs.append("RFP structure: no scored section outline detected in excerpt.")
    else:
        logs.append(f"RFP structure: {len(specs)} scored section spec(s) from RFP.")

    # Recover tabs the outline lean-filter / Phase 3 skip dropped entirely.
    draft, stub_logs = ensure_missing_scored_section_stubs(
        draft, specs, skip_section_ids=skip_section_ids
    )
    logs.extend(stub_logs)

    sections = list(draft.sections)
    changed = bool(stub_logs)
    reframed_ids: set[str] = set()

    for spec in specs:
        if _spec_is_rfp_title_noise(spec):
            continue
        if not spec.required_headings and not spec.instructions:
            continue
        working = draft.model_copy(update={"sections": sections})
        section = _match_section_for_spec(working, spec)
        if not section or section.id in skip_section_ids:
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
