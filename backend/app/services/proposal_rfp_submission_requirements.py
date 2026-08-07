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
from app.services.proposal_rfp_compliance import (
    MANUAL_FILL_MARKER,
    OPEN_TAG_MARKERS,
    requirement_likely_covered,
)

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
                        "submit — narrative sections, forms, attachments, exhibits, appendices, "
                        "signatures, addenda ack, insurance certificates, W-9, pricing forms.\n\n"
                        "Rules:\n"
                        "- Use the RFP's own titles/labels.\n"
                        "- Be thorough: if Section/Exhibit/Attachment/Form is named as required "
                        "for submission, include it.\n"
                        "- signed_form / notarized form → kind=signed_form "
                        "(proposal gets a MANUAL FILL checklist tab).\n"
                        "- narrative_proposal → mustInManuscript=true (needs prose in PDF).\n"
                        "- attachment (Excel, COI PDF, exhibits) → kind=attachment "
                        "(proposal gets a checklist tab with [MANUAL FILL: attach file]).\n"
                        "- Do NOT invent items not in the excerpt.\n"
                        "- Prefer more items over fewer when the RFP checklist is dense.\n\n"
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
                    "content": f"RFP: {rfp_title}\n\nSubmission excerpt:\n{excerpt[:46000]}",
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
    """Missing required items vs LLM inventory — narratives + form/attachment tabs."""
    manuscript = _manuscript_blob(draft)
    mapped_titles = {
        (m.title or "").casefold()
        for m in (research.rfp_sections if research else [])
    }
    missing: list[SubmissionDeliverable] = []

    for item in inventory:
        # Always surface demanded forms/attachments as checklist tabs so Scan
        # cannot silently drop them. Narratives still require must_in_manuscript.
        wants_tab = item.must_in_manuscript or item.kind in (
            "signed_form",
            "attachment",
            "signature_block",
        )
        if not wants_tab:
            continue
        if item.section_id in {s.id for s in draft.sections}:
            sec = next(s for s in draft.sections if s.id == item.section_id)
            if len((sec.content or "").strip()) > 80:
                continue
        if _title_covered_in_draft(item.title, draft):
            continue
        req_text = item.draft_instructions or item.title
        if item.must_in_manuscript and requirement_likely_covered(req_text, manuscript):
            continue
        if item.title.casefold() in mapped_titles and any(
            s.content.strip()
            for s in draft.sections
            if (s.title or "").casefold() == item.title.casefold()
        ):
            continue
        # Forms/attachments that are already covered by closing package tabs
        # (references, addenda, COI, signature) should not duplicate.
        if item.kind in ("signed_form", "attachment", "signature_block"):
            title_cf = item.title.casefold()
            closing_needles = (
                "addenda",
                "non-collusion",
                "certificate of insurance",
                "authorized signature",
                "w-9",
                "pricing proposal form",
            )
            if any(n in title_cf for n in closing_needles) and any(
                n in " | ".join(s.title.casefold() for s in draft.sections)
                for n in closing_needles
                if n in title_cf
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
    if item.kind in ("signed_form", "attachment"):
        stub = (
            f"## {item.title}\n\n"
            f"This RFP requires **{item.title}** as a submission deliverable.\n\n"
            f"- Status: **[MANUAL FILL: attach signed/complete file on buyer template]**\n"
            f"- RFP cite: {item.rfp_citation or 'see submission instructions'}\n"
            f"- Notes: {item.draft_instructions or 'Follow buyer form exactly; do not invent fields.'}\n"
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
                        "Use only RFP + verified zö facts.\n"
                        "If kind is signed_form or attachment: write a short compliance checklist "
                        "with [MANUAL FILL: attach …] — do NOT invent signatures or file contents.\n"
                        "If kind is narrative_proposal: write substantive prose.\n"
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


# Task 15: kind classification for the submission checklist. Zero LLM calls —
# every entry is regex over RFP text, same as before this dataclass existed.
#
# "narrative" — a section the pipeline can draft from the KB (prose the
# writer/reviewer can produce and keep improving).
# "attachment" — a signed/scanned/notarized PHYSICAL document (or an
# external form/exhibit) a human must obtain and attach. Never satisfiable
# by prose: a drafted section ABOUT a W-9 is not a W-9. See
# outstanding_submission_checklist_for_scan's module note below for why the
# scan must never paper over one of these with a [MANUAL FILL] stub that
# reads like the real thing was handled.


@dataclass(frozen=True)
class SubmissionChecklistItem:
    label: str
    kind: str  # "narrative" | "attachment"
    pattern: str


_SUBMISSION_CHECKLIST_PATTERNS: tuple[SubmissionChecklistItem, ...] = (
    SubmissionChecklistItem(
        "Physically signed cover letter / letter of transmittal (attachment)",
        "attachment",
        r"(?:physically\s+)?signed\s+cover\s+letter|"
        r"cover\s+letter\s+(?:must\s+be\s+)?signed|"
        r"signed\s+letter\s+of\s+transmittal|"
        r"original\s+signature\s+on\s+(?:the\s+)?cover\s+letter",
    ),
    SubmissionChecklistItem(
        "Acknowledgement of Addenda (return with proposal)",
        "attachment",
        r"acknowledg(?:e|ement|ment)s?\s+of\s+addenda|addenda\s+acknowledg|receipt\s+of\s+addenda",
    ),
    SubmissionChecklistItem(
        "Affirmative Action Questionnaire (signed)", "attachment", r"affirmative action"
    ),
    SubmissionChecklistItem(
        "Assurance of Compliance (signed)", "attachment", r"assurance of compliance"
    ),
    SubmissionChecklistItem(
        "Non-Collusion Affidavit (often notarized)", "attachment", r"non[- ]?collusion"
    ),
    SubmissionChecklistItem(
        "Statement of Ownership Disclosure",
        "attachment",
        r"statement of ownership|ownership disclosure",
    ),
    SubmissionChecklistItem(
        "Vendor / Contractor Questionnaire",
        "attachment",
        r"vendor questionnaire|contractor questionnaire",
    ),
    SubmissionChecklistItem(
        "Financial stability narrative (in proposal body)",
        "narrative",
        r"financial stability",
    ),
    SubmissionChecklistItem(
        "Awards & recognitions (in proposal body)",
        "narrative",
        r"awards?\s*(?:and|&)\s*recognition",
    ),
    SubmissionChecklistItem(
        "Offeror commitment / closing statement",
        "narrative",
        r"closing\s+statement|offeror.?s?\s+statement|commitment\s+to\s+(?:perform|deliver)",
    ),
    SubmissionChecklistItem(
        "Certificate(s) of Insurance", "attachment", r"certificate(?:s)?\s+of\s+insurance|\bCOI\b"
    ),
    SubmissionChecklistItem("IRS Form W-9", "attachment", r"\bW[- ]?9\b"),
    SubmissionChecklistItem(
        "Official pricing / quotation form",
        "attachment",
        r"pricing\s+proposal\s+form|cost\s+proposal\s+form|quotation\s*/?\s*pricing",
    ),
    SubmissionChecklistItem(
        "Authorized signature page",
        "attachment",
        r"authorized\s+(?:representative|signatory|signature)|signature\s+(?:block|page)",
    ),
    SubmissionChecklistItem(
        "Contract / agreement acknowledgment",
        "attachment",
        r"exemplar\s+agreement|sample\s+(?:agreement|contract)|exceptions?\s+to\s+(?:the\s+)?(?:agreement|contract)",
    ),
    SubmissionChecklistItem(
        "Contractor Vendor Certification / Exhibit H",
        "attachment",
        r"contractor vendor certification|\bCVC\b|exhibit\s+h\b",
    ),
    SubmissionChecklistItem(
        "Required attachments checklist",
        "attachment",
        r"required\s+attachments?|documents?\s+to\s+(?:be\s+)?(?:submitted|included|attached)|submission\s+checklist",
    ),
    SubmissionChecklistItem(
        "Named exhibits / appendices / attachments",
        "attachment",
        r"\bexhibit\s+[A-Z0-9]+\b|\bappendix\s+[A-Z0-9]+\b|\battachment\s+\d+\b",
    ),
    SubmissionChecklistItem(
        "E-Verify affidavit / enrollment",
        "attachment",
        r"e-?verify(?:\s+(?:affidavit|enrollment|certification|compliance))?",
    ),
    SubmissionChecklistItem(
        "Bid / performance / payment bond",
        "attachment",
        r"(?:bid|performance|payment)\s+bond|bonding\s+required|surety\s+bond",
    ),
    SubmissionChecklistItem(
        "Sealed package / original signature",
        "attachment",
        r"sealed\s+(?:envelope|bid|package)|original\s+signature|wet[\s-]?ink|"
        r"separate\s+(?:sealed\s+)?(?:technical|cost|price)\s+(?:and|&)\s+"
        r"(?:cost|price|technical)",
    ),
)

# Compulsory close always appears on the checklist, whether or not the RFP
# text matched the "closing statement" pattern above — see
# list_submission_checklist_items_from_rfp.
_COMPULSORY_CLOSE = SubmissionChecklistItem(
    "Offeror commitment / closing statement", "narrative", ""
)


def list_submission_checklist_items_from_rfp(rfp_text: str) -> list[SubmissionChecklistItem]:
    """Kind-classified checklist — narrative vs attachment. Zero LLM calls,
    pure regex over RFP text, same patterns list_submission_checklist_from_rfp
    always used; this just keeps each pattern's kind alongside its label so a
    caller can separate "the pipeline can draft this" from "a human must
    attach a physical document" instead of reporting one undifferentiated
    list."""
    text = rfp_text or ""
    found: list[SubmissionChecklistItem] = []
    seen_labels: set[str] = set()
    for item in _SUBMISSION_CHECKLIST_PATTERNS:
        if item.pattern and re.search(item.pattern, text, re.I) and item.label not in seen_labels:
            found.append(item)
            seen_labels.add(item.label)
    if _COMPULSORY_CLOSE.label not in seen_labels:
        found.append(_COMPULSORY_CLOSE)
    return found


def list_submission_checklist_from_rfp(rfp_text: str) -> list[str]:
    """Human-readable lines for ending report — documents to be submitted.

    Backward-compatible label-only view over
    list_submission_checklist_items_from_rfp; existing callers (e.g.
    proposal_presubmit_review.py) only ever needed the label text.
    """
    return [item.label for item in list_submission_checklist_items_from_rfp(rfp_text)]


@dataclass(frozen=True)
class OutstandingSubmissionChecklist:
    """The Task 15 scan-path split: what THIS RFP demands as physical
    documents vs narrative sections, filtered down to what is still
    outstanding in the CURRENT draft — so a re-scan does not nag about an
    attachment the user already resolved. See
    outstanding_submission_checklist_for_scan's docstring for the
    resolution rule.
    """

    needs_drafting: list[str]
    needs_attachment: list[str]


def _content_matches_checklist_item(item: SubmissionChecklistItem, content: str) -> bool:
    """Does this content actually cover the item?

    Prefers the item's OWN detection regex — the exact pattern that decided
    the RFP demands it in the first place — over the fuzzy keyword-overlap
    heuristic (requirement_likely_covered). That heuristic drops stopword-
    filtered tokens under 5 characters and, critically, treats an EMPTY
    token list as "covered" (see its docstring/call sites in
    proposal_rfp_compliance.py) — harmless for a long descriptive label, but
    a short/abbreviated attachment label like "IRS Form W-9" or a "COI"
    pattern reduces to zero eligible tokens and would vacuously match ANY
    non-empty section, silently marking every physical-document requirement
    "resolved" the instant the draft had any content at all. Attachment
    items therefore never fall back to the fuzzy heuristic; narrative items
    do, since their labels are verbose enough to produce real tokens and a
    human may reasonably paraphrase RFP wording when drafting prose.
    """
    if item.pattern and re.search(item.pattern, content, re.I):
        return True
    if item.kind == "attachment":
        return False
    return requirement_likely_covered(item.label, content)


def _checklist_item_resolved_in_manuscript(
    item: SubmissionChecklistItem, draft: ProposalDraft
) -> bool:
    """True when the current draft already resolves this checklist item.

    Narrative items: a match anywhere in the manuscript is enough — the
    pipeline or a human already wrote it.

    Attachment items: a match is NOT enough by itself — a section that
    merely TALKS ABOUT the W-9 (e.g. a [MANUAL FILL: attach signed/complete
    file ...] stub left by ensure_all_rfp_submission_requirements) is not
    the W-9. An attachment only counts as resolved when some section's own
    content covers the topic AND carries no open MANUAL FILL / placeholder
    marker — i.e. a human has since replaced the stub with a real
    confirmation (e.g. "W-9 attached as Exhibit C"). Until then it must
    keep being flagged on every scan; this is the exact failure mode Task
    15 exists to stop (drafted-prose-as-attachment silently reading as
    handled).
    """
    if item.kind != "attachment":
        return _content_matches_checklist_item(item, _manuscript_blob(draft))
    for section in draft.sections:
        content = section.content or ""
        if not content.strip():
            continue
        if not _content_matches_checklist_item(item, content):
            continue
        if MANUAL_FILL_MARKER in content:
            continue
        if any(marker in content.upper() for marker in OPEN_TAG_MARKERS):
            continue
        return True
    return False


def outstanding_submission_checklist_for_scan(
    rfp_text: str, draft: ProposalDraft
) -> OutstandingSubmissionChecklist:
    """Scan-path entry point (Task 15): run the RFP attachment checklist
    inside Scan-RFP itself, independent of whatever the compliance matrix
    happened to capture — this is exactly the gap that let a required W-9
    or Certificate of Insurance go unmentioned in the scan report before a
    human found out the hard way. Zero LLM calls, deterministic, idempotent:
    an item already resolved in the current draft (see
    _checklist_item_resolved_in_manuscript) is dropped from both lists on
    every subsequent scan.
    """
    items = list_submission_checklist_items_from_rfp(rfp_text)
    needs_drafting: list[str] = []
    needs_attachment: list[str] = []
    for item in items:
        if _checklist_item_resolved_in_manuscript(item, draft):
            continue
        if item.kind == "attachment":
            needs_attachment.append(item.label)
        else:
            needs_drafting.append(item.label)
    return OutstandingSubmissionChecklist(
        needs_drafting=needs_drafting, needs_attachment=needs_attachment
    )
