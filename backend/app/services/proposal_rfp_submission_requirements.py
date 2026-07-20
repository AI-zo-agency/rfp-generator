"""Map RFP submission checklists + vendor qualification narratives — generic per RFP."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection, RfpSectionMap
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_rfp_excerpt import submission_documents_excerpt
from app.services.proposal_rfp_compliance import requirement_likely_covered

logger = logging.getLogger(__name__)


def _slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").casefold()).strip("-")
    return s[:48] or "item"


@dataclass
class SubmissionDeliverable:
    """One required submission item from THIS RFP — any sector, any wording."""

    id: str
    title: str
    section_id: str
    kind: str  # narrative_proposal | signed_form | attachment | signature_block | other
    must_in_manuscript: bool
    draft_instructions: str
    rfp_citation: str = ""


@dataclass(frozen=True)
class NarrativeSubmissionItem:
    id: str
    title: str
    section_id: str
    patterns: tuple[str, ...]
    covered_keywords: tuple[str, ...]
    draft_instructions: str


_NARRATIVE_SUBMISSION_CATALOG: tuple[NarrativeSubmissionItem, ...] = (
    NarrativeSubmissionItem(
        id="financial_stability",
        title="Financial Stability",
        section_id="rfp-qual-financial-stability",
        patterns=(
            r"financial\s+stability",
            r"vendor\s+qualification",
            r"company\s+history\s+and\s+vendor",
            r"demonstrated\s+financial",
        ),
        covered_keywords=(
            "financial stability",
            "bonding capacity",
            "d&b",
            "dun & bradstreet",
            "years of continuous operation",
            "fiscal health",
            "financial health",
        ),
        draft_instructions=(
            "Address financial stability as THIS RFP's vendor qualification section requires. "
            "Use verified facts only: founded August 2013, years in operation, stable WBENC/WOSB "
            "small business, no bankruptcy — from company facts KB. "
            "Use [VERIFY: D&B rating / bonding letter] for anything not in KB. "
            "Do NOT invent revenue figures or credit scores."
        ),
    ),
    NarrativeSubmissionItem(
        id="awards_recognitions",
        title="Awards & Recognitions",
        section_id="rfp-qual-awards",
        patterns=(
            r"awards?\s*(?:and|&)\s*recognition",
            r"recognitions?\s*(?:and|&)\s*awards?",
            r"vendor\s+qualification",
            r"company\s+history\s+and\s+vendor",
        ),
        covered_keywords=(
            "creative excellence",
            "netty",
            "nyx award",
            "vega digital",
            "enterprising women",
            "awards & recognition",
            "awards and recognition",
        ),
        draft_instructions=(
            "List verified zö agency awards ONLY (Creative Excellence 2024, Netty 2024, "
            "NYX 2024, Vega Digital 2024, Enterprising Women 2026 — omit any not verified). "
            "One line per award with year and granting body. Tie briefly to quality commitment "
            "for THIS RFP client. No invented awards."
        ),
    ),
    NarrativeSubmissionItem(
        id="higher_ed_commitment",
        title="Commitment to Higher Education",
        section_id="rfp-qual-higher-ed",
        patterns=(
            r"demonstrated\s+commitment\s+to\s+higher\s+education",
            r"community\s+college",
            r"higher\s+education\s+experience",
        ),
        covered_keywords=(
            "higher education",
            "community college",
            "college and university",
        ),
        draft_instructions=(
            "Describe zö's commitment to higher education / community colleges using ONLY "
            "verified case studies and clients from KB — no invented NJ/in-state work. "
            "If the RFP requires geography you lack, disclose honestly."
        ),
    ),
)


def detect_narrative_submission_gaps(
    draft: ProposalDraft,
    rfp_text: str,
) -> list[NarrativeSubmissionItem]:
    """Return qualification narratives the RFP asks for but manuscript does not cover."""
    text = rfp_text or ""
    manuscript = "\n\n".join(
        f"{s.title}\n{s.content}" for s in draft.sections if (s.content or "").strip()
    ).casefold()

    gaps: list[NarrativeSubmissionItem] = []
    for item in _NARRATIVE_SUBMISSION_CATALOG:
        if not any(re.search(p, text, re.I) for p in item.patterns):
            continue
        if any(kw in manuscript for kw in item.covered_keywords):
            continue
        # Section already exists with substantive content
        existing = next((s for s in draft.sections if s.id == item.section_id), None)
        if existing and len((existing.content or "").strip()) > 120:
            continue
        gaps.append(item)
    return gaps


async def _draft_narrative_submission_section(
    *,
    item: NarrativeSubmissionItem,
    rfp: RfpRecord,
    rfp_excerpt: str,
    kb_awards_blob: str = "",
) -> str:
    stub = (
        f"## {item.title}\n\n"
        f"[MANUAL FILL: complete {item.title} per RFP vendor qualification instructions.]\n"
    )
    if not llm.is_configured():
        return stub
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Draft ONE vendor-qualification subsection for a zö agency proposal.\n"
                        f"{item.draft_instructions}\n"
                        "Return JSON: {\"content\": \"markdown\"}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"RFP: {rfp.title}\nClient/buyer context: {rfp.client}\n\n"
                        f"RFP submission / vendor qualification excerpt:\n{rfp_excerpt[:28000]}\n\n"
                        f"Verified awards/facts (use only if relevant):\n{kb_awards_blob[:8000]}"
                    ),
                },
            ],
            max_tokens=2048,
            temperature=0.2,
        )
        content = str((raw or {}).get("content") or "").strip()
        return content or stub
    except Exception as exc:  # noqa: BLE001
        logger.warning("Narrative submission draft failed for %s: %s", item.id, exc)
        return stub


def _title_covered_in_draft(title: str, draft: ProposalDraft) -> bool:
    """True if a section title already matches this deliverable closely enough."""
    want = (title or "").casefold()
    if not want:
        return False
    want_tokens = {t for t in re.split(r"\W+", want) if len(t) >= 4}
    for section in draft.sections:
        got = (section.title or "").casefold()
        if want in got or got in want:
            return True
        if want_tokens and want_tokens.issubset(set(re.split(r"\W+", got))):
            return True
    return False


def _manuscript_blob(draft: ProposalDraft) -> str:
    return "\n\n".join(
        f"## {s.title}\n{s.content}" for s in draft.sections if (s.content or "").strip()
    )


async def inventory_rfp_submission_requirements(
    rfp_excerpt: str,
    *,
    rfp_title: str = "",
) -> list[SubmissionDeliverable]:
    """LLM pass: every submission item from THIS RFP (any buyer, any checklist wording)."""
    excerpt = (rfp_excerpt or "").strip()
    if len(excerpt) < 150:
        return []
    if not llm.is_configured():
        return []

    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You read ONE RFP's submission instructions (any government, college, "
                        "nonprofit, or commercial buyer). List EVERY deliverable the proposer must "
                        "submit — narrative sections, forms, attachments, signatures, addenda ack.\n\n"
                        "Rules:\n"
                        "- Use the RFP's own titles/labels.\n"
                        "- signed_form / notarized form → mustInManuscript=false (checklist only).\n"
                        "- narrative_proposal → mustInManuscript=true (needs prose or ack in PDF).\n"
                        "- attachment (Excel, COI PDF) → mustInManuscript=false unless RFP wants a "
                        "cover paragraph in the proposal.\n"
                        "- Do NOT invent items not in the excerpt.\n\n"
                        "Return JSON:\n"
                        "{\n"
                        '  "items": [\n'
                        "    {\n"
                        '      "id": "del-1",\n'
                        '      "title": "RFP label",\n'
                        '      "kind": "narrative_proposal|signed_form|attachment|signature_block|other",\n'
                        '      "mustInManuscript": true,\n'
                        '      "draftInstructions": "what zö should write or [MANUAL FILL]",\n'
                        '      "rfpCitation": "short quote from RFP"\n'
                        "    }\n"
                        "  ]\n"
                        "}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"RFP: {rfp_title}\n\nSubmission excerpt:\n{excerpt[:42000]}",
                },
            ],
            max_tokens=4096,
            temperature=0.1,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Submission inventory LLM failed: %s", exc)
        return []

    items: list[SubmissionDeliverable] = []
    for row in (raw or {}).get("items") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        del_id = str(row.get("id") or f"del-{len(items)+1}")
        slug = _slugify(title)
        kind = str(row.get("kind") or "other").casefold()
        must = bool(row.get("mustInManuscript", kind == "narrative_proposal"))
        if kind in ("signed_form", "attachment") and row.get("mustInManuscript") is None:
            must = False
        items.append(
            SubmissionDeliverable(
                id=del_id,
                title=title,
                section_id=f"rfp-req-{slug}",
                kind=kind,
                must_in_manuscript=must,
                draft_instructions=str(row.get("draftInstructions") or "").strip()
                or f"Address: {title} per RFP instructions.",
                rfp_citation=str(row.get("rfpCitation") or "")[:400],
            )
        )
    return items


def detect_missing_submission_deliverables(
    draft: ProposalDraft,
    inventory: list[SubmissionDeliverable],
    *,
    research: ProposalResearchCache | None = None,
) -> list[SubmissionDeliverable]:
    """Missing required manuscript items vs LLM inventory + Phase 2 map."""
    manuscript = _manuscript_blob(draft)
    mapped_titles = {
        (m.title or "").casefold()
        for m in (research.rfp_sections if research else [])
    }
    missing: list[SubmissionDeliverable] = []

    for item in inventory:
        if not item.must_in_manuscript:
            continue
        if item.section_id in {s.id for s in draft.sections}:
            sec = next(s for s in draft.sections if s.id == item.section_id)
            if len((sec.content or "").strip()) > 80:
                continue
        if _title_covered_in_draft(item.title, draft):
            continue
        req_text = item.draft_instructions or item.title
        if requirement_likely_covered(req_text, manuscript):
            continue
        if item.title.casefold() in mapped_titles and any(
            s.content.strip()
            for s in draft.sections
            if (s.title or "").casefold() == item.title.casefold()
        ):
            continue
        missing.append(item)

    return missing


async def _draft_generic_deliverable(
    *,
    item: SubmissionDeliverable,
    rfp: RfpRecord,
    rfp_excerpt: str,
) -> str:
    stub = (
        f"## {item.title}\n\n"
        f"{item.draft_instructions}\n\n"
        f"[MANUAL FILL: complete per RFP — {item.title}]"
    )
    if not llm.is_configured():
        return stub
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Draft ONE proposal section for zö agency matching THIS RFP submission item.\n"
                        "Use only RFP + verified zö facts. Signed external forms → checklist + [MANUAL FILL].\n"
                        'Return JSON: {"content": "markdown"}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"RFP: {rfp.title}\nItem: {item.title} ({item.kind})\n"
                        f"Instructions:\n{item.draft_instructions}\n"
                        f"RFP cite: {item.rfp_citation}\n\n"
                        f"Excerpt:\n{rfp_excerpt[:30000]}"
                    ),
                },
            ],
            max_tokens=2048,
            temperature=0.2,
        )
        content = str((raw or {}).get("content") or "").strip()
        return content or stub
    except Exception as exc:  # noqa: BLE001
        logger.warning("Generic deliverable draft failed: %s", exc)
        return stub


async def ensure_all_rfp_submission_requirements(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None,
) -> tuple[ProposalDraft, list[SubmissionDeliverable], list[str], list[str]]:
    """Any RFP: inventory → add missing manuscript sections + catalog narratives + checklist."""
    excerpt = submission_documents_excerpt(rfp_text)
    logs: list[str] = []
    checklist: list[str] = list_submission_checklist_from_rfp(rfp_text)

    inventory = await inventory_rfp_submission_requirements(
        excerpt or rfp_text[:50000],
        rfp_title=rfp.title,
    )
    for item in inventory:
        label = item.title
        if item.kind in ("signed_form", "attachment"):
            label = f"{item.title} (signed/attach — buyer template)"
        if label not in checklist:
            checklist.append(label)

    missing = detect_missing_submission_deliverables(draft, inventory, research=research)

    for cat in detect_narrative_submission_gaps(draft, rfp_text):
        if any(m.section_id == cat.section_id for m in missing):
            continue
        missing.append(
            SubmissionDeliverable(
                id=cat.id,
                title=cat.title,
                section_id=cat.section_id,
                kind="narrative_proposal",
                must_in_manuscript=True,
                draft_instructions=cat.draft_instructions,
            )
        )

    if not missing and not inventory:
        logs.append(
            "Submission scan: no extra deliverables detected (excerpt may be thin — confirm PDF uploaded)."
        )
        return draft, [], logs, checklist

    sections = list(draft.sections)
    ids = {s.id for s in sections}
    added: list[SubmissionDeliverable] = []

    for item in missing:
        if item.section_id in ids:
            existing = next(s for s in sections if s.id == item.section_id)
            from app.services.proposal_fulfill_guard import fulfill_scan_preserves_section

            if fulfill_scan_preserves_section(existing):
                continue
            if len((existing.content or "").strip()) > 80:
                continue
        content = await _draft_generic_deliverable(
            item=item, rfp=rfp, rfp_excerpt=excerpt or rfp_text
        )
        if item.section_id in ids:
            idx = next(i for i, s in enumerate(sections) if s.id == item.section_id)
            sections[idx] = sections[idx].model_copy(
                update={"content": content, "status": "generated"}
            )
        else:
            sections.append(
                ProposalSection(
                    id=item.section_id,
                    title=item.title,
                    content=content,
                    status="generated",
                    source="rfp",
                    mode="write" if item.kind == "narrative_proposal" else "pull",
                    required=True,
                )
            )
            ids.add(item.section_id)
        added.append(item)
        logs.append(f"Added RFP deliverable: {item.title}")

    if not added:
        if inventory:
            logs.append(
                f"Submission inventory: {len(inventory)} item(s) — all manuscript items appear covered."
            )
        return draft, [], logs, checklist

    now = datetime.now(timezone.utc).isoformat()
    return (
        draft.model_copy(update={"sections": sections, "updated_at": now}),
        added,
        logs,
        checklist,
    )


def merge_deliverables_into_research(
    research: ProposalResearchCache | None,
    added: list[SubmissionDeliverable],
) -> ProposalResearchCache | None:
    if not research or not added:
        return research
    existing = list(research.rfp_sections or [])
    existing_ids = {s.id for s in existing}
    for item in added:
        if item.section_id in existing_ids:
            continue
        existing.append(
            RfpSectionMap(
                id=item.section_id,
                title=item.title,
                requirements=[item.draft_instructions, item.rfp_citation]
                if item.rfp_citation
                else [item.draft_instructions],
                retrievalFocus=["company facts", "compliance", "forms"],
                zoMode="write" if item.kind == "narrative_proposal" else "pull",
            )
        )
        existing_ids.add(item.section_id)
    return research.model_copy(update={"rfp_sections": existing})


async def ensure_submission_narrative_sections(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, list[NarrativeSubmissionItem], list[str]]:
    """Backward-compatible wrapper — prefer ensure_all_rfp_submission_requirements."""
    updated, added_del, logs, _checklist = await ensure_all_rfp_submission_requirements(
        draft=draft,
        rfp=rfp,
        rfp_text=rfp_text,
        research=research,
    )
    # Map deliverables back to narrative items for callers expecting NarrativeSubmissionItem
    narrative_added: list[NarrativeSubmissionItem] = []
    for d in added_del:
        if d.kind != "narrative_proposal" and not d.section_id.startswith("rfp-qual-"):
            continue
        narrative_added.append(
            NarrativeSubmissionItem(
                id=d.id,
                title=d.title,
                section_id=d.section_id,
                patterns=(),
                covered_keywords=(),
                draft_instructions=d.draft_instructions,
            )
        )
    return updated, narrative_added, logs


def merge_submission_items_into_research(
    research: ProposalResearchCache | None,
    added: list[NarrativeSubmissionItem],
) -> ProposalResearchCache | None:
    if not research or not added:
        return research
    existing = list(research.rfp_sections or [])
    existing_ids = {s.id for s in existing}
    for item in added:
        if item.section_id in existing_ids:
            continue
        existing.append(
            RfpSectionMap(
                id=item.section_id,
                title=item.title,
                requirements=[item.draft_instructions],
                retrievalFocus=["company facts", "awards", "financial stability"],
                zoMode="pull",
            )
        )
        existing_ids.add(item.section_id)
    return research.model_copy(update={"rfp_sections": existing})


def list_submission_checklist_from_rfp(rfp_text: str) -> list[str]:
    """Human-readable lines for ending report — documents to be submitted."""
    text = rfp_text or ""
    found: list[str] = []
    patterns = (
        (r"acknowledg(?:e|ement|ment)s?\s+of\s+addenda|addenda\s+acknowledg|receipt\s+of\s+addenda", "Acknowledgement of Addenda (return with proposal)"),
        (r"affirmative action", "Affirmative Action Questionnaire (signed)"),
        (r"assurance of compliance", "Assurance of Compliance (signed)"),
        (r"non[- ]?collusion", "Non-Collusion Affidavit (often notarized)"),
        (r"statement of ownership", "Statement of Ownership Disclosure"),
        (r"vendor questionnaire", "Vendor / Contractor Questionnaire"),
        (r"financial stability", "Financial stability narrative (in proposal body)"),
        (r"awards?\s*(?:and|&)\s*recognition", "Awards & recognitions (in proposal body)"),
        (r"closing\s+statement|offeror.?s?\s+statement|commitment\s+to\s+(?:perform|deliver)", "Offeror commitment / closing statement"),
    )
    for pat, label in patterns:
        if re.search(pat, text, re.I) and label not in found:
            found.append(label)
    return found
