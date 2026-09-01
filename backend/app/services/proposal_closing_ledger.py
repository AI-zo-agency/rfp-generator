"""Closing requirement ledger — RFP set-membership, not title-synonym regex.

Authority: an LLM extract of THIS RFP's documents-to-submit / forms / attachments
asks (obligation-aware). Downstream Generate + Scan audit each row against the draft.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_intelligence.agent_base import safe_chat_json
from app.services.proposal_rfp_excerpt import (
    closing_package_excerpt,
    submission_documents_excerpt,
)

logger = logging.getLogger(__name__)

AGENT = "closing_requirement_ledger"

ClosingAuditState = Literal[
    "missing",
    "stub_empty",
    "fabricated_ready",
    "complete",
    "manual_fill",
]

ClosingKind = Literal["narrative", "form", "attachment", "signature"]

_EXTRACT_SYSTEM = """You extract the closing / submission REQUIREMENT LEDGER for THIS RFP.

Return only items the vendor must SUBMIT with the proposal (forms, attachments,
disclosures, reference packages, pricing/cost forms, signature blocks, portal
questionnaires). Do NOT invent items. Do NOT copy another client's forms.

CRITICAL — mention ≠ submit:
- Procedural clauses ("County may issue addenda", "vendors must monitor BidNet")
  are NOT ledger rows.
- Only include an item when the RFP obliges the vendor to return / submit /
  acknowledge / complete / attach / include it with the proposal.
- Standing post-award obligations (PERA notice, sex-offender registration) are NOT
  proposal contents — omit them.
- For insurance / COI / W-9 / exemplar-agreement items: draftInstructions MUST tell
  the writer to cross-reference Section 1.5 and NOT restate limits, carriers, or
  coverage types.

For each item:
- id: stable snake_case key unique within this RFP (e.g. attachment_02, w9,
  non_collusion_affidavit, generative_ai_disclosure, references, pricing_proposal_form).
  Prefer RFP labels when present (Attachment 02 → attachment_02).
- title: buyer-facing section/checklist title using THIS RFP's wording.
- kind: narrative | form | attachment | signature
- rfpLabel: exact phrase from the RFP when available (e.g. "Attachment 02 — …").
- sectionId: "rfp-closing-" + id (hyphenated).
- draftInstructions: how to draft or checklist this item (no invented facts;
  use [MANUAL FILL] for signatures / attach-PDF).

Return JSON only:
{
  "requirements": [
    {
      "id": "w9",
      "title": "W-9",
      "kind": "attachment",
      "rfpLabel": "IRS Form W-9",
      "sectionId": "rfp-closing-w9",
      "draftInstructions": "…"
    }
  ],
  "confidence": 0.0
}
"""


class ClosingRequirement(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    kind: ClosingKind = "form"
    rfp_label: str = Field(default="", alias="rfpLabel")
    section_id: str = Field(default="", alias="sectionId")
    draft_instructions: str = Field(default="", alias="draftInstructions")

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        if not (self.section_id or "").strip() and self.id:
            slug = re.sub(r"[^a-z0-9]+", "-", self.id.strip().casefold()).strip("-")
            self.section_id = f"rfp-closing-{slug}"


class ClosingRequirementLedger(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    requirements: list[ClosingRequirement] = Field(default_factory=list)
    confidence: float = 0.0


class ClosingRequirementAudit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    requirement_id: str = Field(alias="requirementId")
    title: str = ""
    state: ClosingAuditState
    section_id: str | None = Field(default=None, alias="sectionId")
    note: str = ""


_READY_STATUS_RE = re.compile(
    r"(?im)(?:^\s*[-*]\s*.{0,80}\bready\b|\|\s*ready\s*\||status\s*[:\-]\s*ready\b)",
)
_MANUAL_FILL_RE = re.compile(r"\[MANUAL FILL", re.I)
_ATTACH_HANDOFF_RE = re.compile(
    r"\[DESIGNER NOTE:\s*Attach|attach\s+(?:the\s+)?(?:signed\s+)?PDF|\bMANUAL FILL:[^\]]*attach",
    re.I,
)
_STOP = {"the", "and", "for", "with", "from", "form", "of", "to", "a", "an", "or"}


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", (text or "").casefold())
        if len(t) >= 3 and t not in _STOP
    }


def normalize_requirement_id(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (raw or "").strip().casefold()).strip("_")
    return slug or "closing_item"


async def extract_closing_requirement_ledger(
    rfp_text: str,
    *,
    always_include_commitment: bool = False,
) -> ClosingRequirementLedger:
    """LLM extract of THIS RFP's closing/submission ledger (authority)."""
    body = (rfp_text or "").strip()
    if not body:
        ledger = ClosingRequirementLedger(confidence=0.0)
        if always_include_commitment:
            ledger.requirements.append(_commitment_requirement())
        return ledger

    excerpt = submission_documents_excerpt(body, max_chars=24_000)
    closing = closing_package_excerpt(body, max_chars=16_000)
    # This whole prompt is a pure function of rfp_text, and this function is
    # called from 6 sites across the pipeline on the same rfp_text — cache the
    # entire thing rather than re-extracting fresh (and re-billing) every time.
    raw, _provider = await safe_chat_json(
        [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": ""},
        ],
        max_tokens=3072,
        temperature=0.1,
        agent_name=AGENT,
        cache_prefix=(
            "Build the closing requirement ledger for THIS RFP only.\n\n"
            f"Submission / documents excerpt:\n{excerpt}\n\n"
            f"Closing / forms excerpt:\n{closing}"
        ),
    )
    ledger = _parse_ledger_payload(raw if isinstance(raw, dict) else {})
    if always_include_commitment and not any(
        r.id == "offeror_commitment" for r in ledger.requirements
    ):
        ledger.requirements.append(_commitment_requirement())
    logger.info(
        "Closing ledger for this RFP (%d): %s",
        len(ledger.requirements),
        ", ".join(r.id for r in ledger.requirements) or "(none)",
    )
    return ledger


async def get_or_extract_closing_ledger(
    rfp_text: str,
    *,
    research: Any | None = None,
    force: bool = False,
    always_include_commitment: bool = False,
    persist: bool = True,
) -> tuple[ClosingRequirementLedger, Any | None]:
    """Return cached ledger from research when present; else extract and optionally save.

    Returns ``(ledger, research)`` — research may be an updated copy with the
    ledger payload stamped when ``persist`` is True and research was provided.
    """
    if research is not None and not force:
        cached = getattr(research, "closing_requirement_ledger", None)
        if cached is None and hasattr(research, "model_dump"):
            # Tolerate camelCase payload keys from older/partial loads.
            dumped = research.model_dump(by_alias=True)
            cached = dumped.get("closingRequirementLedger") or dumped.get(
                "closing_requirement_ledger"
            )
        if isinstance(cached, ClosingRequirementLedger):
            logger.info(
                "Closing ledger cache hit (%d row(s))",
                len(cached.requirements),
            )
            return cached, research
        if isinstance(cached, dict) and (
            cached.get("requirements") is not None
            or cached.get("Requirements") is not None
            or "confidence" in cached
            or "Confidence" in cached
        ):
            try:
                ledger = ClosingRequirementLedger.model_validate(cached)
                if ledger.requirements:
                    logger.info(
                        "Closing ledger cache hit (%d row(s))",
                        len(ledger.requirements),
                    )
                    return ledger, research
                logger.info(
                    "Closing ledger cache empty — re-extracting for this RFP"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Closing ledger cache invalid — re-extracting: %s", exc)

    ledger = await extract_closing_requirement_ledger(
        rfp_text,
        always_include_commitment=always_include_commitment,
    )
    if research is not None and persist:
        from datetime import datetime, timezone

        from app.services.proposal_repository import asave_research_cache

        now = datetime.now(timezone.utc).isoformat()
        research = research.model_copy(
            update={
                "closing_requirement_ledger": ledger.model_dump(by_alias=True),
                "updated_at": now,
            }
        )
        await asave_research_cache(research)
    return ledger, research


def ledger_from_fixture(
    items: list[dict[str, Any]] | list[ClosingRequirement],
    *,
    confidence: float = 1.0,
) -> ClosingRequirementLedger:
    """Build a ledger without LLM — tests and offline callers."""
    reqs: list[ClosingRequirement] = []
    for item in items:
        if isinstance(item, ClosingRequirement):
            reqs.append(item)
        else:
            reqs.append(ClosingRequirement.model_validate(item))
    return ClosingRequirementLedger(requirements=reqs, confidence=confidence)


def _commitment_requirement() -> ClosingRequirement:
    return ClosingRequirement(
        id="offeror_commitment",
        title="Offeror Commitment & Closing Statement",
        kind="narrative",
        rfpLabel="(compulsory proposal close)",
        sectionId="rfp-closing-commitment",
        draftInstructions=(
            "Write a concise closing for THIS proposal. Restate fit, capacity, "
            "and validity period if stated. No invented awards/clients/metrics. "
            "[MANUAL FILL: authorized signature if required]."
        ),
    )


def _parse_ledger_payload(raw: dict[str, Any]) -> ClosingRequirementLedger:
    rows = raw.get("requirements") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return ClosingRequirementLedger(confidence=0.0)
    reqs: list[ClosingRequirement] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            rid = normalize_requirement_id(str(row.get("id") or row.get("title") or ""))
            if not rid or rid in seen:
                continue
            title = str(row.get("title") or row.get("rfpLabel") or rid).strip()
            if not title:
                continue
            kind_raw = str(row.get("kind") or "form").strip().casefold()
            kind: ClosingKind = (
                kind_raw  # type: ignore[assignment]
                if kind_raw in {"narrative", "form", "attachment", "signature"}
                else "form"
            )
            section_id = str(row.get("sectionId") or row.get("section_id") or "").strip()
            if not section_id:
                section_id = f"rfp-closing-{rid.replace('_', '-')}"
            req = ClosingRequirement(
                id=rid,
                title=title[:200],
                kind=kind,
                rfpLabel=str(row.get("rfpLabel") or row.get("rfp_label") or "")[:240],
                sectionId=section_id[:120],
                draftInstructions=str(
                    row.get("draftInstructions") or row.get("draft_instructions") or ""
                )[:4000],
            )
            reqs.append(req)
            seen.add(rid)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skip closing ledger row: %s", exc)
            continue
    confidence = 0.0
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return ClosingRequirementLedger(
        requirements=reqs,
        confidence=max(0.0, min(1.0, confidence)),
    )


def find_covering_section(
    draft: ProposalDraft,
    requirement: ClosingRequirement,
) -> ProposalSection | None:
    """Match by section id or shared title/label tokens — no synonym catalogs."""
    by_id = {s.id: s for s in draft.sections}
    if requirement.section_id in by_id:
        return by_id[requirement.section_id]
    needles = _tokens(requirement.title) | _tokens(requirement.rfp_label) | _tokens(
        requirement.id.replace("_", " ")
    )
    if not needles:
        return None
    best: ProposalSection | None = None
    best_score = 0
    for section in draft.sections:
        title_toks = _tokens(section.title or "")
        if not title_toks:
            continue
        score = len(needles & title_toks)
        title_cf = (section.title or "").casefold()
        label_cf = (requirement.rfp_label or requirement.title or "").casefold()
        if label_cf and (label_cf in title_cf or title_cf in label_cf):
            score += 3
        if score > best_score and score >= 2:
            best = section
            best_score = score
    return best


def classify_closing_requirement_state(
    requirement: ClosingRequirement,
    draft: ProposalDraft,
) -> ClosingRequirementAudit:
    section = find_covering_section(draft, requirement)
    if section is None:
        return ClosingRequirementAudit(
            requirementId=requirement.id,
            title=requirement.title,
            state="missing",
            note="No manuscript section covers this RFP closing requirement.",
        )
    content = (section.content or "").strip()
    if len(content) < 48:
        return ClosingRequirementAudit(
            requirementId=requirement.id,
            title=requirement.title,
            state="stub_empty",
            sectionId=section.id,
            note="Covering section exists but is empty/stub.",
        )
    has_manual = bool(_MANUAL_FILL_RE.search(content))
    has_attach = bool(_ATTACH_HANDOFF_RE.search(content))
    claims_ready = bool(_READY_STATUS_RE.search(content))
    if claims_ready and not has_manual and not has_attach and len(content) < 500:
        return ClosingRequirementAudit(
            requirementId=requirement.id,
            title=requirement.title,
            state="fabricated_ready",
            sectionId=section.id,
            note="Marked Ready without MANUAL FILL / attach handoff or substance.",
        )
    if has_manual or has_attach:
        return ClosingRequirementAudit(
            requirementId=requirement.id,
            title=requirement.title,
            state="manual_fill",
            sectionId=section.id,
            note="Present with MANUAL FILL / attach handoff — human action remains.",
        )
    return ClosingRequirementAudit(
        requirementId=requirement.id,
        title=requirement.title,
        state="complete",
        sectionId=section.id,
        note="Covering section has substantive content.",
    )


def audit_draft_against_closing_ledger(
    draft: ProposalDraft,
    ledger: ClosingRequirementLedger,
) -> list[ClosingRequirementAudit]:
    return [
        classify_closing_requirement_state(req, draft) for req in ledger.requirements
    ]


def audit_issues_for_dq(
    audits: list[ClosingRequirementAudit],
) -> list[str]:
    """Human-readable DQ / decision-gap lines for incomplete ledger rows."""
    lines: list[str] = []
    for row in audits:
        if row.state == "complete":
            continue
        lines.append(
            f"Closing ledger [{row.requirement_id}] {row.title}: {row.state}"
            + (f" — {row.note}" if row.note else "")
        )
    return lines


_FABRICATED_READY_FIX = (
    "[MANUAL FILL: Sonja — attach signed/buyer-template PDF before export. "
    "Do not mark Ready until the file is attached.]"
)


def repair_fabricated_ready_in_draft(
    draft: ProposalDraft,
    ledger: ClosingRequirementLedger,
) -> tuple[ProposalDraft, list[str]]:
    """Replace fabricated Ready checklists with MANUAL FILL handoffs.

    Generate must not ship 'Ready' with nothing behind it.
    """
    from datetime import datetime, timezone

    audits = audit_draft_against_closing_ledger(draft, ledger)
    logs: list[str] = []
    sections = list(draft.sections)
    by_id = {s.id: i for i, s in enumerate(sections)}
    changed = False
    for row in audits:
        if row.state != "fabricated_ready" or not row.section_id:
            continue
        idx = by_id.get(row.section_id)
        if idx is None:
            continue
        section = sections[idx]
        content = (section.content or "").rstrip()
        # Strip bare Ready status claims; append explicit handoff.
        cleaned = _READY_STATUS_RE.sub(
            lambda m: m.group(0).replace("Ready", "MANUAL FILL").replace("ready", "manual fill"),
            content,
        )
        if _FABRICATED_READY_FIX.casefold() not in cleaned.casefold():
            cleaned = f"{cleaned}\n\n{_FABRICATED_READY_FIX}\n"
        sections[idx] = section.model_copy(
            update={"content": cleaned, "status": "generated"}
        )
        changed = True
        logs.append(
            f"Closing ledger: demoted fabricated Ready → MANUAL FILL ({row.requirement_id})"
        )
    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


def ensure_missing_closing_stubs(
    draft: ProposalDraft,
    ledger: ClosingRequirementLedger,
) -> tuple[ProposalDraft, list[str]]:
    """Add short MANUAL FILL stubs for missing ledger rows (Generate path)."""
    from datetime import datetime, timezone

    from app.models.proposal import ProposalSection

    audits = audit_draft_against_closing_ledger(draft, ledger)
    logs: list[str] = []
    sections = list(draft.sections)
    existing_ids = {s.id for s in sections}
    for row in audits:
        if row.state != "missing":
            continue
        req = next((r for r in ledger.requirements if r.id == row.requirement_id), None)
        if not req or req.section_id in existing_ids:
            continue
        stub = (
            f"## {req.title}\n\n"
            f"THIS RFP requires: {req.rfp_label or req.title}.\n\n"
            f"{_FABRICATED_READY_FIX}\n"
        )
        sections.append(
            ProposalSection(
                id=req.section_id,
                title=req.title,
                content=stub,
                status="generated",
                source="rfp",
                mode="write",
                required=True,
            )
        )
        existing_ids.add(req.section_id)
        logs.append(f"Closing ledger: stubbed missing requirement ({req.id})")
    if not logs:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs
