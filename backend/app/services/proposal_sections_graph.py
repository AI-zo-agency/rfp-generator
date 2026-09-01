"""LangGraph pipeline for static proposal Sections 1–3 (KB pull/select + dual-layer voice)."""

from __future__ import annotations

import contextvars
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict


def _last_str(left: str | None, right: str | None) -> str:
    """LangGraph reducer — parallel nodes may both report provider."""
    if right:
        return right
    return left or ""

from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.models.proposal import ProposalBrandVoice, ProposalSection, Section1EditorialReview, Section1EditorialRecommendation
from app.services import llm, proposal_knowledge_base_tools, supermemory, team_personas_service
from app.services.proposal_repository import aget_proposal_draft
from app.services.agency_facts import (
    agency_tenure_block,
    agency_years_in_operation,
    enforce_agency_tenure,
)
from app.services.proposal_intelligence.log import log_intel_event
from app.services.company_qualification.agents.capability_prioritization import (
    run_capability_prioritization_agent,
)
from app.services.company_qualification.agents.company_truth import run_company_truth_agent
from app.services.company_qualification.agents.editorial_validation import (
    editorial_reviewed_at,
    run_editorial_validation_agent,
)
from app.services.company_qualification.agents.evidence_selection import run_evidence_selection_agent
from app.services.company_qualification.agents.proposal_context import run_proposal_context_agent
from app.services.company_qualification.agents.section_1_agent import run_section_1_agent
from app.services.company_qualification.agents.section_1_builder import run_section_1_builder_agent
from app.services.company_qualification.agents.team_selection import (
    MAX_TEAM_SIZE,
    normalize_selected_members,
    run_team_selection_agent,
)
from app.services.company_qualification.schemas import (
    CompanyTruth,
    EvidenceCandidate,
    EvidenceSelectionResult,
    PrioritizedCapabilities,
    ProposalContext,
    Section1CompositionResult,
    Section1PlanResult,
    TeamMemberSelection,
    TeamSelectionResult,
)
from app.services.llm import LlmError
from app.services.proposal_brand_voice import format_brand_voice_block, format_register_block
from app.services.proposal_generation_cancel import ProposalGenerationCancelled
from app.services.proposal_manuscript_locks import strip_leaked_markdown_wrappers
from app.services.proposal_voice_enforcement import enforce_narrative_voice
from app.services.proposal_langchain import _provider_name
from app.services.sections_agent_log import (
    get_langgraph_log_path,
    log_graph_event,
    log_pipeline_complete,
    log_pipeline_start,
    with_agent_logging,
)

logger = logging.getLogger(__name__)

SectionsPartialCallback = Callable[
    [list[ProposalSection], str, ProposalBrandVoice | None],
    Awaitable[None],
]

# Per-request context variable so builder nodes can call the partial callback
# after EACH individual subsection without needing it threaded through state.
_partial_cb_var: contextvars.ContextVar[SectionsPartialCallback | None] = contextvars.ContextVar(
    "_sections_partial_cb", default=None
)


def _log_section_generate_next(state: SectionsGraphState, *, section_id: str, title: str) -> None:
    log_intel_event(
        "SECTION_GENERATE_NEXT",
        rfp_id=state.get("rfp_id"),
        section_id=section_id,
        title=title,
    )


async def _emit_partial(state: SectionsGraphState, accumulated_sections: list[dict[str, Any]]) -> None:
    """Fire the partial callback after each individual subsection is generated.
    This saves to DB immediately so the frontend sees each card appear one by one."""
    cb = _partial_cb_var.get(None)
    if not cb:
        return
    try:
        ps = [ProposalSection.model_validate(s) for s in accumulated_sections]
        bv_raw = state.get("brand_voice")
        bv = ProposalBrandVoice.model_validate(bv_raw) if isinstance(bv_raw, dict) else None
        provider = str(state.get("provider") or _provider_name())
        await cb(ps, provider, bv)
    except ProposalGenerationCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.debug("_emit_partial failed (non-fatal): %s", exc)


def _trim_to_max_words(text: str, max_words: int) -> str:
    """Hard-cap word count while keeping whole sentences and newlines when possible.

    Never flatten the manuscript with ``" ".join(words)`` — that turns
    ``## Our Promise`` headings into mid-paragraph junk like ``**## Our Promise**``.
    """
    text = (text or "").strip()
    if not text:
        return text
    if len(text.split()) <= max_words:
        return text

    tokens = re.findall(r"\S+|\s+", text)
    kept: list[str] = []
    word_n = 0
    for tok in tokens:
        if tok.isspace():
            if word_n > 0:
                kept.append(tok)
            continue
        word_n += 1
        if word_n > max_words:
            break
        kept.append(tok)
    clipped = "".join(kept).strip()
    # Prefer ending on a sentence boundary inside the clip
    for end in (".", "!", "?"):
        idx = clipped.rfind(end)
        if idx >= 0 and len(clipped[: idx + 1].split()) >= int(max_words * 0.55):
            return clipped[: idx + 1].strip()
    return clipped.strip()


def _normalize_who_we_are_markdown(text: str) -> str:
    """Repair common Who We Are LLM formatting failures before they hit the UI."""
    if not text or not text.strip():
        return text
    out = text.strip()

    # Whole-section bold wrapper → normal prose
    if out.startswith("**") and out.endswith("**") and out.count("**") == 2:
        out = out[2:-2].strip()

    # Headings glued into a paragraph: "...action. ## Our Promise We promise..."
    out = re.sub(
        r"[ \t]*\*{0,2}[ \t]*(##)\s*(Who We Are|Our Promise)\s*\*{0,2}[ \t]*",
        r"\n\n\1 \2\n\n",
        out,
        flags=re.IGNORECASE,
    )
    # Bolded heading lines: **## Our Promise** / **Our Promise**
    out = re.sub(
        r"(?m)^\s*\*\*\s*(#{1,3}\s+.+?)\s*\*\*\s*$",
        r"\1",
        out,
    )
    out = re.sub(
        r"(?m)^\s*\*\*\s*((?:Who We Are|Our Promise))\s*\*\*\s*$",
        r"## \1",
        out,
        flags=re.IGNORECASE,
    )

    # Drop the redundant ## Who We Are heading — the UI card already titles 1.1
    out = re.sub(
        r"(?im)^\s*##\s*Who We Are\s*\n+",
        "",
        out,
        count=1,
    )
    # Title glued to opening sentence: "Who We Are We are more than…"
    out = re.sub(
        r"(?i)^\s*Who We Are\s+(?=We\b)",
        "",
        out,
        count=1,
    )

    # Unwrap paragraph-length bold (more than ~12 words inside one **...**)
    def _unwrap_long_bold(match: re.Match[str]) -> str:
        inner = match.group(1)
        if len(inner.split()) > 12:
            return inner
        return match.group(0)

    out = re.sub(r"\*\*([^*]+)\*\*", _unwrap_long_bold, out)
    out = _scrub_ops_from_our_promise(out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


_OUR_PROMISE_OPS_RE = re.compile(
    r"(?i)("
    r"ron\s+comer|sonja\s+anderson|curt\s+schultz|letitia\s+hopper|gil\s+aranowitz|"
    r"senior\s+account\s+manager|dedicated\s+client\s+manager|executive\s+sponsor|"
    r"\bSEM\b|\bSEO\b|\bPPC\b|paid\s+social|remarketing|\bCRM\b|"
    r"real[- ]time\s+dashboard|campaign\s+reports?|within\s+two\s+weeks|"
    r"\bEIN\b|\bFEIN\b"
    r")"
)


def _scrub_ops_from_our_promise(text: str) -> str:
    """Drop ops/staff sentences from Our Promise — that block is tone only."""
    parts = re.split(r"(?im)^(##\s*Our Promise)\s*$", text, maxsplit=1)
    if len(parts) < 3:
        # No clean heading — still try after an inline marker
        m = re.search(r"(?i)##\s*Our Promise", text)
        if not m:
            return text
        head, promise = text[: m.start()], text[m.end() :]
        heading = "## Our Promise"
    else:
        head, heading, promise = parts[0], parts[1], parts[2]

    kept: list[str] = []
    for para in re.split(r"\n\s*\n", promise.strip()):
        para = para.strip()
        if not para:
            continue
        sentences = re.split(r"(?<=[.!?])\s+", para)
        clean = [s for s in sentences if s and not _OUR_PROMISE_OPS_RE.search(s)]
        if clean:
            kept.append(" ".join(clean))
    promise_body = "\n\n".join(kept).strip()
    if not promise_body:
        promise_body = (
            "Excellence is a guarantee, not a goal. We meet and beat deadlines and budgets, "
            "keep every conversation transparent, and give you direct access to the people "
            "doing the work — no surprise bills, no black boxes. We ambassador your brand "
            "like family, because when you win, we win."
        )
    return f"{head.rstrip()}\n\n{heading}\n\n{promise_body}".strip()


def _sanitize_content(text: str) -> str:
    """Normalize Unicode and strip non-ASCII/non-Latin garbage characters that
    sometimes bleed in from KB PDFs (e.g. Gujarati or other Indic scripts
    mixed into English names like 'V\\u0ac3\\u0ab5ek Patel').

    Strategy:
    1. NFKC normalize (handles ligatures, compat chars, etc.)
    2. Preserve newlines/tabs; map other Unicode spaces / zero-width separators
       to a normal ASCII space (dropping them glues words: "systems\\u200bmust").
    3. For each other character: if basic Latin, keep it; else try NFKD ASCII base;
       otherwise drop non-Latin script characters.
    4. Strip internal KB filename citations (never show Source: *.pdf / *.docx in prose).
    """
    if not text:
        return text
    text = strip_leaked_markdown_wrappers(text)
    # NFKC first — normalizes composed forms
    text = unicodedata.normalize("NFKC", text)
    result: list[str] = []
    for ch in text:
        if ch in "\n\r\t":
            result.append(ch)
            continue
        # Unicode spaces / separators → ASCII space (do not drop — that glues words).
        category = unicodedata.category(ch)
        if ch.isspace() or category in {"Zs", "Zl", "Zp"}:
            result.append(" ")
            continue
        # Soft hyphen: mid-word hyphenation marker — drop without inserting a space.
        if ch == "\u00ad":
            continue
        # Other zero-width / format chars often sit between words in PDF text.
        if category == "Cf":
            result.append(" ")
            continue
        if ord(ch) < 128:
            result.append(ch)
            continue
        # Try to get the ASCII base via NFKD decomposition (e.g. é → e)
        decomposed = unicodedata.normalize("NFKD", ch)
        ascii_equiv = decomposed.encode("ascii", errors="ignore").decode("ascii")
        if ascii_equiv:
            result.append(ascii_equiv)
        # else: silently drop non-Latin script characters (Gujarati, Devanagari, etc.)
    clean = "".join(result)
    # Collapse any double spaces created by dropped/mapped chars
    clean = re.sub(r" {2,}", " ", clean)
    # Never ship internal knowledge-base filenames into the client-facing proposal.
    clean = re.sub(
        r"(?im)^\s*(?:\*+\s*)?(?:source|kb\s*ref|knowledge\s*base)\s*:\s*.+$",
        "",
        clean,
    )
    clean = re.sub(
        r"(?im)\s*\*+\s*Source:\s*[^*\n]+\*+\s*",
        "",
        clean,
    )
    from app.services.proposal_manuscript_locks import strip_internal_proposal_meta

    clean = strip_internal_proposal_meta(clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean


VERIFIED_NAME_CORRECTIONS = {
    "ron corner": "Ron Comer",
    "dyetola doyewunmi": "Oyetola Oyewunmi",
    "shawn dicrisio": "Shawn DiCriscio",
}


def _canonicalize_verified_name(name: str) -> str:
    normalized = " ".join(name.strip().split())
    return VERIFIED_NAME_CORRECTIONS.get(normalized.casefold(), normalized)


def _apply_verified_corrections(text: str, rfp_client: str = "") -> str:
    """Deterministic post-processing: fix known spelling/template errors in all generated content.

    These corrections are programmatic (no LLM) and 100% safe to apply universally.
    """
    if not text:
        return text

    # 1. Legal name — must have apostrophe (LLM often garbles Z'Onion → Zohman/ZOnion)
    text = re.sub(r"\bZohman\b", "Z'Onion", text, flags=re.I)
    text = re.sub(r"\bZ[- ]?[Oo]nion\b", "Z'Onion", text)
    text = text.replace("ZOnion Creative", "Z'Onion Creative")

    # 2. Vivek Patel name — never "Vince Patel"
    text = re.sub(r"\bVince\s+Patel\b", "Vivek Patel", text, flags=re.I)

    # 3. Team names — correct known OCR/LLM garbling from roster extraction.
    for garbled, canonical in VERIFIED_NAME_CORRECTIONS.items():
        text = re.sub(
            rf"\b{re.escape(garbled)}\b",
            canonical,
            text,
            flags=re.I,
        )

    # 4. Miguel Pérez / Miguel Perez title correction
    text = re.sub(
        r"(Miguel\s+P[eé]rez\b.*?)\bproduction\s+assistant\b",
        r"\1production designer",
        text,
        flags=re.I | re.DOTALL,
    )

    # 5. Do NOT replace portfolio client names (e.g. City of Bend) with rfp_client globally —
    # that mail-merges Section 3 case studies when client is a service title like
    # "Digital Advertising Services". Insurance placeholders use [VERIFY] / buyer label in prompts.

    # 6. Strip Benedictine University hallucinated percentage metrics
    # The real KB has only qualitative KPIs for Benedictine — no percentages
    text = re.sub(r"\b1[0-9]\s*%\s*(increase|uplift|improvement|growth)\s+(in\s+)?website\s+traffic",
                  "increase in website traffic", text, flags=re.I)
    text = re.sub(r"\b2[0-9]\s*%\s*(uplift|increase|improvement|growth)\s+(in\s+)?(qualified\s+)?admissions\s+inquiries",
                  "uplift in admissions inquiries", text, flags=re.I)
    text = re.sub(r"\b1[0-9]\s*%\s*(improvement|increase|uplift|growth)\s+(in\s+)?social\s+media\s+engagement",
                  "improvement in social media engagement", text, flags=re.I)

    return text


class SectionsGraphState(TypedDict, total=False):
    rfp_id: str
    rfp_title: str
    rfp_client: str
    rfp_sector: str
    rfp_location: str | None
    rfp_context: str
    page_limit: int | None
    brand_voice: dict[str, Any]
    kb_zo_voice: str
    kb_zo_voice_sources: list[str]
    kb_company: str
    kb_company_sources: list[str]
    kb_master_roster: str
    kb_master_roster_sources: list[str]
    kb_bios: str
    kb_bio_sources: list[str]
    kb_case_studies: str
    kb_case_sources: list[str]
    sections: Annotated[list[dict[str, Any]], _merge_sections_state]
    skip_section_1: bool
    skip_section_2: bool
    skip_section_3: bool
    provider: Annotated[str, _last_str]
    error: str | None
    company_truth: dict[str, Any]
    proposal_context: dict[str, Any]
    prioritized_capabilities: dict[str, Any]
    section1_plan: dict[str, Any]
    team_selection: dict[str, Any]
    evidence_selection: dict[str, Any]
    section1_editorial_review: dict[str, Any]
    manuscript_locks: dict[str, Any]
    prefetched_case_studies: dict[str, Any]


def _proposal_voice_block(state: SectionsGraphState) -> str:
    from app.models.proposal import ManuscriptLocks
    from app.services.proposal_manuscript_locks import format_manuscript_locks_block

    base = format_brand_voice_block(
        state.get("brand_voice"),
        kb_zo_voice=state.get("kb_zo_voice") or "",
        rfp_client=state.get("rfp_client") or "",
        register="narrative",
    )
    locks_raw = state.get("manuscript_locks")
    locks = None
    if isinstance(locks_raw, dict):
        try:
            locks = ManuscriptLocks.model_validate(locks_raw)
        except Exception:
            locks = None
    locks_block = format_manuscript_locks_block(locks)
    if locks_block:
        return f"{base}\n\n{locks_block}"
    return base


def _section_group_has_content(
    sections: list[dict[str, Any]] | None,
    prefix: str,
    *,
    min_count: int = 1,
) -> bool:
    if not sections:
        return False
    with_content = [
        section
        for section in sections
        if str(section.get("id") or "").startswith(prefix)
        and str(section.get("content") or "").strip()
    ]
    return len(with_content) >= min_count


def _sections_to_state(existing: list[ProposalSection]) -> list[dict[str, Any]]:
    return [section.model_dump(by_alias=True) for section in existing]


SECTION_1_STUB_SPECS: tuple[tuple[str, str, int], ...] = (
    ("section-1-who-we-are", "1.1 — Who We Are", 200),
    ("section-1-org-structure", "1.2 — Organizational Structure", 800),
    ("section-1-business-info", "1.3 — Business Information", 200),
    ("section-1-certifications", "1.4 — Certifications", 150),
    ("section-1-insurance", "1.5 — Insurance Information", 100),
)


def _section_1_stub_payloads(state: SectionsGraphState) -> list[dict[str, Any]]:
    """Empty Section 1 subsection cards so the UI tree shows all 1.x slots while drafting."""
    return [
        _section_payload(
            section_id=sec_id,
            title=title,
            mode="pull",
            word_target=word_target,
            page_limit=state.get("page_limit"),
            page_ratio=0.03,
            designer_note_default=f"Section 1 subsection: {title}.",
            raw={"content": "", "kbRefs": []},
            kb_sources=[],
        )
        for sec_id, title, word_target in SECTION_1_STUB_SPECS
    ]


def _merge_section_list(
    base: list[dict[str, Any]],
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(s.get("id")): s for s in base if s.get("id")}
    for section in updates:
        sid = str(section.get("id") or "")
        if sid:
            by_id[sid] = section
    ordered_ids = [str(s.get("id")) for s in base if s.get("id")]
    for section in updates:
        sid = str(section.get("id") or "")
        if sid and sid not in ordered_ids:
            ordered_ids.append(sid)
    return [by_id[sid] for sid in ordered_ids if sid in by_id]


def _merge_sections_state(
    left: list[dict[str, Any]] | None,
    right: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """LangGraph reducer — parallel S1/S2/S3 builders merge subsection updates."""
    return _merge_section_list(left or [], right or [])


BIO_FILE_SLUG_ALIASES: dict[str, str] = {
    "rachael rice": "RachelRice",
    "rachel rice": "RachelRice",
}


def _bio_file_slug(member: str) -> str:
    """04_Bio_RachelRice.pdf style slug from 'Rachael Rice' or 'Rachel Rice'."""
    key = " ".join(member.strip().lower().split())
    if key in BIO_FILE_SLUG_ALIASES:
        return BIO_FILE_SLUG_ALIASES[key]
    parts = [p for p in member.strip().split() if p]
    if len(parts) >= 2:
        return f"{parts[0].capitalize()}{parts[-1].capitalize()}"
    return parts[0].capitalize() if parts else member.replace(" ", "")


def _hit_file_name(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(
        metadata.get("fileName")
        or hit.get("customId")
        or hit.get("title")
        or ""
    )


def _is_member_bio_file_hit(hit: dict[str, Any], member: str) -> bool:
    """True when hit is from this person's 04_Bio_* PDF (not proposals/case studies)."""
    from app.services.proposal_bio_stub import bio_filename_matches_member

    file_name = _hit_file_name(hit)
    expected = f"04_Bio_{_bio_file_slug(member)}.pdf".casefold()
    if file_name.strip().casefold() == expected:
        return True
    return bio_filename_matches_member(file_name, member)


async def _find_member_bio_document(member: str) -> dict[str, Any] | None:
    """Exact 04_Bio_FirstLast.pdf, then any 04_Bio file whose name contains the person."""
    from app.services.proposal_bio_stub import (
        bio_filename_matches_member,
        expected_bio_pdf_filename,
    )

    candidates = [
        f"04_Bio_{_bio_file_slug(member)}.pdf",
        expected_bio_pdf_filename(member),
    ]
    seen: set[str] = set()
    for name in candidates:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            doc = await supermemory.find_document_by_file_name(name)
        except supermemory.SupermemoryError:
            doc = None
        if doc:
            return doc
    try:
        docs = await supermemory.list_all_container_documents()
    except supermemory.SupermemoryError:
        return None
    for doc in docs or []:
        file_name = _hit_file_name(doc)
        if bio_filename_matches_member(file_name, member):
            return doc
    return None


def _prefer_full_bio_text(full_text: str, search_text: str) -> str:
    """The exact full 04_Bio document is authoritative over search snippets."""
    return full_text.strip() or search_text.strip()


async def _rag_bio_section_chunks(member: str, section: str) -> str:
    """RAG: retrieve chunks from this person's 04_Bio file for one section only."""
    if not supermemory.is_configured():
        return ""
    slug = _bio_file_slug(member)
    query = f"04_Bio_{slug}.pdf {section}"
    try:
        hits = await supermemory.search_document_chunks(
            query=query,
            limit=8,
            filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
        )
    except supermemory.SupermemoryError:
        return ""
    section_hits = [h for h in hits if _is_member_bio_file_hit(h, member)]
    if not section_hits:
        return ""
    return supermemory.format_search_hits(section_hits, max_chars=40_000)


def _extract_member_block_from_roster(roster_text: str, member: str) -> str:
    """Pull one person's bio block from Master Template AllTeamBios text."""
    parts = [p for p in member.strip().split() if p]
    if len(parts) < 2:
        return ""
    first, last = parts[0], parts[-1]
    header_re = re.compile(
        rf"(?im)^#{{1,3}}\s*{re.escape(first)}\s+{re.escape(last)}\s*$"
    )
    blocks: list[str] = []
    for match in header_re.finditer(roster_text):
        start = match.start()
        rest = roster_text[start + 1 :]
        end_match = re.search(
            r"\n(?:---\s*\n|#{1,3}\s*YOUR KEY TEAM\b)",
            rest,
            re.I,
        )
        end = start + 1 + end_match.start() if end_match else min(len(roster_text), start + 9000)
        blocks.append(roster_text[start:end].strip())
    if not blocks:
        return ""
    # Prefer the block that actually has employment / credentials sections.
    def _score(block: str) -> tuple[int, int]:
        upper = block.upper()
        return (
            int("WORK HISTORY" in upper) + int("CERTIFICATIONS" in upper) + int("LICENSES" in upper),
            len(block),
        )

    return max(blocks, key=_score)


async def _fetch_member_roster_bio_excerpt(member: str) -> tuple[str, list[str]]:
    """04_Bio OCR often truncates WORK HISTORY/CERTS — Master Template keeps clean copy."""
    if not supermemory.is_configured():
        return "", []
    roster_name = proposal_knowledge_base_tools.MASTER_TEAM_ROSTER_DOC
    try:
        doc = await supermemory.find_document_by_file_name(roster_name)
        if not doc:
            # Fallback short name used in some ingest paths.
            doc = await supermemory.find_document_by_file_name("02_MasterTemplate_OrgStructure")
            if doc:
                roster_name = _hit_file_name(doc) or "02_MasterTemplate_OrgStructure"
        if not doc:
            return "", []
        custom_id = supermemory.document_fetch_key(doc)
        if not custom_id:
            return "", []
        roster_text = await supermemory.get_document_content(custom_id=custom_id)
    except supermemory.SupermemoryError as exc:
        logger.warning("Master roster bio excerpt failed for %s: %s", member, exc)
        return "", []

    block = _extract_member_block_from_roster(roster_text, member)
    if not block.strip():
        return "", []
    return block, [roster_name]


def _section_has_usable_facts(section_name: str, block: str) -> bool:
    """True when a section block has more than a repeated title/photo stub."""
    text = (block or "").strip()
    if len(text) < 40:
        return False
    upper = text.upper()
    if section_name == "WORK HISTORY":
        return bool(_DATE_RANGE_RE.search(text))
    if section_name in {"CERTIFICATIONS", "LICENSES"}:
        return bool(
            re.search(r"(license|certificat|credential|power user|coursera|clickup)", text, re.I)
        )
    if section_name == "KEY ACCOUNTS":
        return "[logo]" in text.casefold() or len(
            [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(">")]
        ) >= 1
    # Reject stubs that are only the long ### Name – Role (Key Team Lead…) line.
    if "YOUR KEY TEAM" in upper and "WORK HISTORY" not in upper:
        return False
    return True


async def _bio_stub_kb_inputs(
    member: str, *, inline_required: bool
) -> tuple[str, list[str], bool, str]:
    """KB text + PDF name for a bio stub. Skip the full 04_Bio fetch unless inline."""
    from app.services.proposal_bio_stub import (
        expected_bio_pdf_filename,
        resolve_bio_pdf_filename,
    )

    if not inline_required:
        pdf = expected_bio_pdf_filename(member)
        return "", [pdf], True, pdf

    kb_text, sources = await _fetch_member_bio_kb(member)
    kb_available = bool(kb_text.strip() and len(kb_text) >= 200)
    pdf = resolve_bio_pdf_filename(member, sources)
    return kb_text, sources, kb_available, pdf


async def _fetch_member_bio_kb(member: str) -> tuple[str, list[str]]:
    """Assemble bio from 04_Bio_{Name}.pdf, section RAG, and Master Template fallback."""
    from app.services.proposal_bio_stub import (
        expected_bio_pdf_filename,
        is_plausible_person_name,
    )

    if not is_plausible_person_name(member):
        logger.info("Skip 04_Bio fetch for non-person label %r", member)
        return "", []
    if not supermemory.is_configured():
        return "(Supermemory not configured.)", []

    slug = _bio_file_slug(member)
    target_file = expected_bio_pdf_filename(member)
    sources: list[str] = []
    full_text = ""
    doc = None

    # 1) Authoritative full document by filename (not fuzzy RFP-mixed search).
    #    Also accept 04_Bio_First_Last.pdf / "04_Bio_First Last.pdf" variants.
    try:
        doc = await _find_member_bio_document(member)
        if doc:
            custom_id = supermemory.document_fetch_key(doc)
            if custom_id:
                full_text = await supermemory.get_document_content(custom_id=custom_id)
                if _hit_file_name(doc):
                    sources.append(_hit_file_name(doc))
    except supermemory.SupermemoryError as exc:
        logger.warning("Full 04_Bio fetch failed for %s: %s", member, exc)

    roster_excerpt, roster_sources = await _fetch_member_roster_bio_excerpt(member)
    sources = sorted(set(sources) | set(roster_sources))

    # No real 04_Bio file and no Master Template roster hit → never invent
    # six section searches for 04_Bio_EconomicGrowth.pdf etc. (case-study /
    # TOC titles that only look like "First Last").
    if not doc and not (roster_excerpt or "").strip():
        logger.info(
            "Skip 04_Bio section RAG — no KB file or roster for %r (refusing %s)",
            member,
            target_file,
        )
        return "", []

    # 2) Section-scoped assembly: 04_Bio header → roster section → RAG chunks.
    #    Skip per-section Supermemory when the full PDF already loaded (common path).
    section_parts: list[str] = []
    use_section_rag = bool(doc) and len(full_text.strip()) < 1200
    for section in (
        "YEARS OF EXPERIENCE",
        "WORK HISTORY",
        "KEY ACCOUNTS",
        "EDUCATION",
        "LICENSES",
        "CERTIFICATIONS",
    ):
        rag = ""
        if use_section_rag:
            rag = await _rag_bio_section_chunks(member, section)
        header_block = _section_block_text(full_text, section) if full_text else ""
        roster_block = (
            _section_block_text(roster_excerpt, section) if roster_excerpt else ""
        )
        combined = header_block.strip()
        if not _section_has_usable_facts(section, combined) and roster_block.strip():
            combined = roster_block.strip()
        elif roster_block.strip() and _section_has_usable_facts(section, roster_block):
            # Merge roster facts when 04_Bio OCR truncated the section.
            if not _section_has_usable_facts(section, combined):
                combined = roster_block.strip()
            elif section in {"WORK HISTORY", "CERTIFICATIONS", "LICENSES", "KEY ACCOUNTS"}:
                combined = f"{combined}\n\n{roster_block.strip()}"
        if rag.strip():
            if combined:
                if section.casefold() in rag.casefold() or "[logo]" in rag.casefold():
                    if _section_has_usable_facts(section, rag) or "[logo]" in rag.casefold():
                        combined = f"{combined}\n\n{rag.strip()}"
            elif _section_has_usable_facts(section, rag) or "[logo]" in rag.casefold():
                combined = rag.strip()
        if combined:
            section_parts.append(f"# {section}\n\n{combined}")

    # 3) Keep identity / overview from full doc (before first ## section).
    preamble = full_text
    if full_text:
        first_section = re.search(
            r"\n#{1,6}\s*(?:YEARS OF EXPERIENCE|WORK HISTORY|KEY ACCOUNTS|EDUCATION|LICENSES|CERTIFICATIONS)",
            full_text,
            re.I,
        )
        if first_section:
            preamble = full_text[: first_section.start()]
    if not (preamble or "").strip() and roster_excerpt:
        # Overview often sits above YEARS OF EXPERIENCE in the roster block.
        roster_pre = re.split(
            r"\n#{1,6}\s*(?:YEARS OF EXPERIENCE|WORK HISTORY|KEY ACCOUNTS|EDUCATION|LICENSES|CERTIFICATIONS)",
            roster_excerpt,
            maxsplit=1,
            flags=re.I,
        )[0]
        preamble = roster_pre

    if section_parts:
        text = f"{(preamble or '').strip()}\n\n" + "\n\n".join(section_parts)
    elif doc:
        # Fallback: hybrid search limited to this bio file name — only when file exists.
        query = f"04_Bio_{slug}.pdf {member}"
        try:
            hits = await supermemory.search_documents(
                query=query,
                limit=6,
                include_full_docs=True,
                search_mode="hybrid",
                filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
            )
        except supermemory.SupermemoryError:
            hits = []
        bio_hits = [h for h in hits if _is_member_bio_file_hit(h, member)]
        search_text = supermemory.format_search_hits(bio_hits, max_chars=80_000) if bio_hits else ""
        text = _prefer_full_bio_text(full_text, search_text)
        if roster_excerpt:
            text = f"{text}\n\n{roster_excerpt}".strip()
        sources = sorted(
            set(sources)
            | {_hit_file_name(h) for h in bio_hits if _hit_file_name(h)}
        )
    else:
        text = (roster_excerpt or "").strip()

    if target_file not in sources and text.strip() and doc:
        sources = sorted(set(sources) | {target_file})

    text = _normalize_bio_text_dates(text)

    logger.info(
        "Bio fetch for %s: full_doc=%d roster=%d assembled=%d from %s",
        member,
        len(full_text),
        len(roster_excerpt),
        len(text),
        sources or "none",
    )
    return text, sources


def _normalize_list_item(item: Any) -> str:
    if isinstance(item, dict):
        parts = [
            str(item.get(key) or "").strip()
            for key in ("degree", "school", "institution", "year", "title", "name")
            if item.get(key)
        ]
        if parts:
            return ", ".join(parts)
        return ", ".join(str(v).strip() for v in item.values() if v)
    return str(item).strip()


_JUNK_KEY_ACCOUNT_RE = re.compile(
    r"^(?:since|est\.?|founded|established)\s+\d{4}$|^\d{4}$|"
    r"^(?:logo|image|the|a|an|and|with|for)$",
    re.I,
)


def _normalize_account_key(name: str) -> str:
    """Casefold key for dedupe — strip leading 'the ' so Idaho variants collapse."""
    key = re.sub(r"^the\s+", "", name.strip(), flags=re.I)
    return re.sub(r"\s+", " ", key).casefold()


def _accounts_are_near_duplicate(a: str, b: str) -> bool:
    """True when names refer to the same client (Sayfe / Sayfe Families)."""
    a_key = _normalize_account_key(a)
    b_key = _normalize_account_key(b)
    if not a_key or not b_key:
        return False
    if a_key == b_key:
        return True
    a_tokens = a_key.split()
    b_tokens = b_key.split()
    # Prefer token-prefix: "sayfe" ⊂ "sayfe families"
    if len(a_tokens) <= len(b_tokens) and b_tokens[: len(a_tokens)] == a_tokens:
        return True
    if len(b_tokens) <= len(a_tokens) and a_tokens[: len(b_tokens)] == b_tokens:
        return True
    return False


def _is_junk_key_account(name: str) -> bool:
    """Drop OCR taglines (SINCE 1884) and non-client fragments."""
    cleaned = name.strip()
    if not cleaned or len(cleaned) < 2:
        return True
    if _JUNK_KEY_ACCOUNT_RE.match(cleaned):
        return True
    # Never list the agency itself as a "key account".
    if re.fullmatch(r"z[öo]\s*agency", cleaned, re.I):
        return True
    # Pure caption noise: founding years / taglines.
    if re.fullmatch(r"SINCE\s+\d{4}", cleaned, re.I):
        return True
    if cleaned.isupper() and re.search(r"\d{4}", cleaned) and len(cleaned.split()) <= 3:
        return True
    return False


def _dedupe_string_list(items: list[str]) -> list[str]:
    """Exact case-insensitive dedupe, preserving first-seen display form."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        name = _normalize_list_item(raw)
        if not name:
            continue
        key = re.sub(r"\s+", " ", name).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _dedupe_key_accounts(accounts: list[str]) -> list[str]:
    """Preserve order; collapse 'the University of Idaho' / 'University of Idaho'
    and near-duplicates like 'Sayfe' / 'Sayfe Families' (keep the longer name)."""
    out: list[str] = []
    for raw in accounts:
        name = _normalize_list_item(raw)
        if not name or _is_junk_key_account(name):
            continue
        # Prefer form without leading "the " when both appear.
        display = re.sub(r"^the\s+", "", name, flags=re.I).strip() or name
        replaced = False
        for idx, existing in enumerate(out):
            if not _accounts_are_near_duplicate(display, existing):
                continue
            # Keep the more specific (longer) label.
            if len(display) > len(existing):
                out[idx] = display
            replaced = True
            break
        if not replaced:
            out.append(display)
    return out


def _normalize_work_history_item(item: Any) -> dict[str, str] | None:
    if isinstance(item, dict):
        company = str(item.get("company") or item.get("employer") or "").strip()
        title = str(item.get("title") or item.get("role") or "").strip()
        dates = str(item.get("dates") or item.get("period") or "").strip()
        if company or title:
            return {"company": company or "[VERIFY: company]", "title": title, "dates": dates}
    if isinstance(item, str) and item.strip():
        return {"company": item.strip(), "title": "", "dates": ""}
    return None


def _item_appears_in_kb(item: str, kb_text: str) -> bool:
    """Loose check: at least one significant token from item appears in bio KB."""
    if not item or not kb_text:
        return False
    kb_lower = kb_text.lower()
    tokens = [t for t in re.split(r"[\s,|/\-]+", item.lower()) if len(t) > 3]
    if not tokens:
        return item.lower() in kb_lower
    return any(token in kb_lower for token in tokens)


def _account_appears_in_kb(account: str, kb_text: str) -> bool:
    if account.strip() and account.strip().lower() in kb_text.lower():
        return True
    return _item_appears_in_kb(account, kb_text)


_BIO_SECTION_HEADERS = (
    "YEARS OF EXPERIENCE",
    "WORK HISTORY",
    "KEY ACCOUNTS",
    "EDUCATION",
    "LICENSES",
    "CERTIFICATIONS",
)


def _section_block_text(kb_text: str, header: str) -> str:
    """Raw text of one bio section (bounded by major section headers only).

    Master Template uses ### Company under WORK HISTORY — do NOT treat those as
    section boundaries.
    """
    headers_alt = "|".join(re.escape(h) for h in _BIO_SECTION_HEADERS)
    match = re.search(
        rf"#{{1,6}}\s*{re.escape(header)}\s*(?:\n|$)(.*?)(?=\n#{{1,6}}\s*(?:{headers_alt})\b|\Z)",
        kb_text,
        re.I | re.S,
    )
    return match.group(1) if match else ""


_BIO_YEAR_RANGE_INNER = r"\s*[-\u2013\u2014–—]\s*"


def _normalize_bio_text_dates(text: str) -> str:
    """PDF/OCR often uses en-dash; bio parsers expect ASCII hyphen in ranges."""
    if not text:
        return text
    return re.sub(
        rf"(\b(?:19|20)\d{{2}}){_BIO_YEAR_RANGE_INNER}(Present|(?:19|20)\d{{2}})",
        r"\1 - \2",
        text,
        flags=re.I,
    )


_DATE_RANGE_RE = re.compile(
    rf"\b(?:19|20)\d{{2}}{_BIO_YEAR_RANGE_INNER}(?:Present|(?:19|20)\d{{2}})\b",
    re.I,
)


def _is_valid_work_history_entry(entry: dict[str, str], key_accounts_text: str = "") -> bool:
    """Employment rows must include a real year range from the WORK HISTORY section."""
    del key_accounts_text  # kept for call-site compatibility
    company = (entry.get("company") or "").strip()
    dates = (entry.get("dates") or "").strip()
    if not company or company.isdigit() or len(company) <= 1:
        return False
    return bool(_DATE_RANGE_RE.search(dates))


def _extract_logo_account_names(block: str) -> list[str]:
    """Pull client names from Supermemory logo image captions in KEY ACCOUNTS."""
    names: list[str] = []
    patterns = [
        re.compile(
            r"logo\s+for\s+(?:['\"]([^'\"]+)['\"]|((?:the\s+)?[A-Z][^.\n,]{1,80}))",
            re.I,
        ),
        re.compile(
            r"featuring\s+the\s+(?:text|word)\s+['\"]([^'\"]+)['\"]",
            re.I,
        ),
        re.compile(
            r"text\s+reads\s+['\"]([^'\"]+)['\"]",
            re.I,
        ),
        re.compile(
            r"(?:containing\s+the\s+word|the\s+word)\s+(?:['\"]([^'\"]+)['\"]|([A-Za-z][A-Za-z0-9&'-]{1,40}))",
            re.I,
        ),
    ]
    seen: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(block):
            name = next((g.strip() for g in match.groups() if g and g.strip()), "")
            if not name:
                continue
            name = re.sub(r"\s+(in|with|featuring|and)\b.*$", "", name, flags=re.I).strip()
            if name.casefold() in {"the", "a", "an", "logo", "image", "for"}:
                continue
            if "'" in name or '"' in name:
                continue
            if _is_junk_key_account(name):
                continue
            key = _normalize_account_key(name)
            if key in seen:
                continue
            seen.add(key)
            names.append(re.sub(r"^the\s+", "", name, flags=re.I).strip() or name)
    return names


def _sanitize_bio_extraction(extracted: dict[str, Any], kb_text: str) -> dict[str, Any]:
    """Keep only facts grounded in the assembled bio text (section-scoped RAG)."""
    clean = dict(extracted)
    work_section = _section_block_text(kb_text, "WORK HISTORY")
    accounts_section = _section_block_text(kb_text, "KEY ACCOUNTS")

    expertise: list[dict[str, str]] = []
    for exp in clean.get("expertise") or []:
        if not isinstance(exp, dict):
            continue
        area = str(exp.get("area") or "").strip()
        years = str(exp.get("years") or "").strip()
        if area and _item_appears_in_kb(area, kb_text):
            # Colon form gives the field a human-readable description; bare
            # "[VERIFY]" (recognized the same as this by every resolver — see
            # proposal_manual_flags.VERIFY_TAG_RE) works too but is less clear
            # to a reader than naming the fact directly.
            expertise.append(
                {"area": area, "years": years or "[VERIFY: years of experience]"}
            )
    clean["expertise"] = expertise

    work_history: list[dict[str, str]] = []
    seen_jobs: set[tuple[str, str, str]] = set()
    for job in clean.get("work_history") or []:
        normalized = _normalize_work_history_item(job)
        if not normalized or not _is_valid_work_history_entry(normalized):
            continue
        # Ground employment in the WORK HISTORY section when that section exists.
        ground = work_section or kb_text
        if not _item_appears_in_kb(normalized["company"], ground):
            continue
        key = (
            normalized["company"].casefold(),
            normalized["title"].casefold(),
            normalized["dates"].casefold(),
        )
        if key in seen_jobs:
            continue
        seen_jobs.add(key)
        work_history.append(normalized)
    clean["work_history"] = work_history

    licenses = [
        lic for lic in (_normalize_list_item(x) for x in (clean.get("licenses") or []))
        if lic and _item_appears_in_kb(lic, kb_text)
    ]
    clean["licenses"] = _dedupe_string_list(licenses)

    certifications = [
        cert for cert in (_normalize_list_item(x) for x in (clean.get("certifications") or []))
        if cert and _item_appears_in_kb(cert, kb_text)
    ]
    clean["certifications"] = _dedupe_string_list(certifications)

    key_accounts: list[str] = []
    ground_accounts = accounts_section or kb_text
    for acct in (_normalize_list_item(x) for x in (clean.get("key_accounts") or [])):
        if acct and not _is_junk_key_account(acct) and _account_appears_in_kb(acct, ground_accounts):
            key_accounts.append(acct)
    clean["key_accounts"] = _dedupe_key_accounts(key_accounts)

    education = [
        edu for edu in (_normalize_list_item(x) for x in (clean.get("education") or []))
        if edu and _item_appears_in_kb(edu, kb_text)
    ]
    clean["education"] = _dedupe_string_list(education)

    overview = str(clean.get("overview") or "").strip()
    if overview and not _item_appears_in_kb(overview, kb_text):
        clean["overview"] = ""

    return clean


def _parse_bio_sections_from_text(kb_text: str, member: str) -> dict[str, Any]:
    """Deterministic parser for 04_Bio PDF section headers (no LLM)."""
    kb_text = _normalize_bio_text_dates(kb_text or "")
    empty: dict[str, Any] = {
        "title": "",
        "overview": "",
        "expertise": [],
        "work_history": [],
        "licenses": [],
        "certifications": [],
        "key_accounts": [],
        "education": [],
    }
    if not kb_text.strip():
        return empty

    text = kb_text

    # Title: line after # FIRST LAST (lowercase role line)
    name_header = re.search(
        rf"#{{1,6}}\s*{re.escape(member.split()[0])}\s*{re.escape(member.split()[-1])}",
        text,
        re.I,
    )
    if not name_header:
        last = member.split()[-1] if member.split() else member
        name_header = re.search(
            rf"#{{1,6}}\s*[A-Z]+\s*{re.escape(last.upper())}", text
        )
    if name_header:
        after = text[name_header.end():].lstrip()
        title_line, _, body_after_title = after.partition("\n")
        title_match = re.fullmatch(r"([a-z][a-z\s/&+\-]+)", title_line.strip(), re.I)
        if title_match:
            empty["title"] = title_match.group(1).strip()
        overview_after_title = re.search(
            r"^\s*(.+?)(?=\n#{1,6}\s*(?:YEARS OF EXPERIENCE|WORK HISTORY|EDUCATION|LICENSES|CERTIFICATIONS|KEY ACCOUNTS))",
            body_after_title,
            re.I | re.S,
        )
        if overview_after_title:
            overview = re.sub(r"\s+", " ", overview_after_title.group(0).strip())
            if "photo" not in overview.lower()[:30]:
                empty["overview"] = overview

    if not empty["overview"]:
        overview_match = re.search(
            r"([A-Z][a-z]+ serves as .+?)(?=\n#{1,6}\s*(?:YEARS OF EXPERIENCE|WORK HISTORY|EDUCATION|LICENSES|CERTIFICATIONS|KEY ACCOUNTS))",
            text,
            re.S,
        )
        if overview_match:
            empty["overview"] = re.sub(r"\s+", " ", overview_match.group(1).strip())

    years_block = re.search(
        r"#{1,6}\s*YEARS OF EXPERIENCE(.*?)(?=\n#{1,6}\s|\Z)",
        text,
        re.I | re.S,
    )
    if years_block:
        for row in years_block.group(1).split("\n"):
            if "|" not in row or "---" in row:
                continue
            cells = [c.strip() for c in row.split("|") if c.strip()]
            for i in range(0, len(cells) - 1, 2):
                area, years = cells[i], cells[i + 1]
                if area and years and "year" in years.lower():
                    empty["expertise"].append({"area": area, "years": years})

    work_block = re.search(
        rf"#{{1,6}}\s*WORK HISTORY\s*(?:\n|$)(.*?)(?=\n#{{1,6}}\s*(?:{'|'.join(_BIO_SECTION_HEADERS)})\b|\Z)",
        text,
        re.I | re.S,
    )
    work_block_text = work_block.group(1) if work_block else ""
    if work_block_text:
        # Master Template format: ### Company\nTitle 2025 - Present
        for match in re.finditer(
            r"#{1,3}\s*(.+?)\s*\n+([^\n#]+?)\s+"
            r"(\b(?:19|20)\d{2}\s*-\s*(?:Present|(?:19|20)\d{2})\b)",
            work_block_text,
            re.I,
        ):
            company = match.group(1).strip().strip("*").strip()
            title = match.group(2).strip().strip("*").strip(" ,-")
            dates = match.group(3).strip()
            if company and title:
                empty["work_history"].append(
                    {"company": company, "title": title, "dates": dates}
                )

        for group in re.split(r"\n\s*\n", work_block_text.strip()):
            lines = [
                re.sub(r"^#+\s*", "", ln.strip().strip("*").strip())
                for ln in group.split("\n")
                if ln.strip() and not ln.startswith(">")
            ]
            # Drop leftover markdown heading-only lines already captured above.
            lines = [ln for ln in lines if ln]
            if not lines:
                continue
            company = lines[0]
            details = " ".join(lines[1:]).strip()
            # Title + dates may be on the company line itself after strip.
            date_match = re.search(
                r"\b(?:19|20)\d{2}\s*-\s*(?:Present|(?:19|20)\d{2})\b",
                details or company,
                re.I,
            )
            # Undated groups are Key Account OCR bleed — never employment.
            if not date_match:
                continue
            dates = date_match.group(0)
            if details:
                title = details[: date_match.start()].strip(" ,") or "[VERIFY: title]"
            else:
                title = company[: date_match.start()].strip(" ,") or "[VERIFY: title]"
                company = "[VERIFY: company]"
            # Skip duplicates from ### parser above.
            key = (company.casefold(), title.casefold(), dates.casefold())
            if any(
                (
                    j.get("company", "").casefold(),
                    j.get("title", "").casefold(),
                    j.get("dates", "").casefold(),
                )
                == key
                for j in empty["work_history"]
            ):
                continue
            empty["work_history"].append(
                {
                    "company": company,
                    "title": title,
                    "dates": dates,
                }
            )

    licenses_block = re.search(
        rf"#{{1,6}}\s*LICENSES\s*(?:\n|$)(.*?)(?=\n#{{1,6}}\s*(?:{'|'.join(_BIO_SECTION_HEADERS)})\b|\Z)",
        text,
        re.I | re.S,
    )
    if licenses_block:
        for ln in licenses_block.group(1).split("\n"):
            ln = ln.strip().lstrip("-•")
            if ln and "license" in ln.lower() and not ln.startswith(">"):
                empty["licenses"].append(ln)

    certifications_block = re.search(
        rf"#{{1,6}}\s*CERTIFICATIONS\s*(?:\n|$)(.*?)(?=\n#{{1,6}}\s*(?:{'|'.join(_BIO_SECTION_HEADERS)})\b|\Z)",
        text,
        re.I | re.S,
    )
    if certifications_block:
        cert_body = certifications_block.group(1)
        for group in re.split(r"\n\s*\n", cert_body.strip()):
            lines = [
                re.sub(r"^#+\s*", "", ln.strip().lstrip("-•").strip("*").strip())
                for ln in group.split("\n")
                if ln.strip() and not ln.strip().startswith(">")
            ]
            lines = [ln for ln in lines if ln and not ln.isdigit()]
            if lines:
                empty["certifications"].append(", ".join(lines))
        # Logo captions often hold the credential name when OCR skips the label.
        for name in _extract_logo_account_names(cert_body):
            if name and name not in empty["certifications"]:
                empty["certifications"].append(name)

    accounts_block = re.search(
        rf"#{{1,6}}\s*KEY ACCOUNTS\s*(?:\n|$)(.*?)(?=\n#{{1,6}}\s*(?:{'|'.join(_BIO_SECTION_HEADERS)})\b|\Z)",
        text,
        re.I | re.S,
    )
    if accounts_block:
        block = accounts_block.group(1)
        # Plain text lines before logo captions.
        text_part = re.split(r">\s*\*\*\[logo\]", block, maxsplit=1, flags=re.I)[0]
        for ln in text_part.split("\n"):
            ln = ln.strip()
            if not ln or ln.isdigit() or ln.startswith(">") or _is_junk_key_account(ln):
                continue
            empty["key_accounts"].append(ln)
        for name in _extract_logo_account_names(block):
            empty["key_accounts"].append(name)
        empty["key_accounts"] = _dedupe_key_accounts(empty["key_accounts"])

    education_block = re.search(
        rf"#{{1,6}}\s*EDUCATION\s*(?:\n|$)(.*?)(?=\n#{{1,6}}\s*(?:{'|'.join(_BIO_SECTION_HEADERS)})\b|\Z)",
        text,
        re.I | re.S,
    )
    if education_block:
        edu_lines = [ln.strip() for ln in education_block.group(1).split("\n") if ln.strip()]
        edu_parts = [re.sub(r",\s*,", ",", ln) for ln in edu_lines if not ln.startswith(">")]
        if edu_parts:
            empty["education"].append(", ".join(edu_parts).replace(",,", ","))

    return empty


def _merge_bio_extractions(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    """Merge two extractions; primary wins, secondary fills gaps."""
    merged = dict(primary)
    for key in ("title", "overview"):
        if not merged.get(key) and secondary.get(key):
            merged[key] = secondary[key]
    for key in (
        "expertise",
        "work_history",
        "licenses",
        "certifications",
        "key_accounts",
        "education",
    ):
        if not merged.get(key) and secondary.get(key):
            merged[key] = secondary[key]
    return merged


async def _extract_key_accounts_via_rag_llm(accounts_section: str) -> list[str]:
    """LLM extract client names from KEY ACCOUNTS section text only (no work history)."""
    if not accounts_section.strip():
        return []
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract key account / client names from this KEY ACCOUNTS bio section only.\n"
                        "Include brand names from logo captions (e.g. SCHOTT, University of Idaho, Hampton Lumber).\n"
                        "Do NOT invent names. Do NOT include employers from Work History.\n"
                        "Do NOT include taglines or founding years (e.g. 'SINCE 1884').\n"
                        "Do NOT duplicate the same client under different wording "
                        "('the University of Idaho' and 'University of Idaho' = one entry; "
                        "'Sayfe' and 'Sayfe Families' = one entry — keep the fuller name).\n"
                        'Return JSON: {"keyAccounts": ["Name 1", "Name 2"]}'
                    ),
                },
                {"role": "user", "content": accounts_section[:40_000]},
            ],
            max_tokens=16000,
            temperature=0.0,
        )
    except LlmError:
        return []
    accounts = raw.get("keyAccounts") or raw.get("key_accounts") or []
    return _dedupe_key_accounts([str(a).strip() for a in accounts if str(a).strip()])


async def _extract_member_bio_facts(member: str, kb_text: str) -> dict[str, Any]:
    """Extract structured bio facts ONLY from 04_Bio file content."""
    empty: dict[str, Any] = {
        "title": "",
        "overview": "",
        "expertise": [],
        "work_history": [],
        "licenses": [],
        "certifications": [],
        "key_accounts": [],
        "education": [],
    }
    if not kb_text.strip():
        return empty

    parsed = _parse_bio_sections_from_text(kb_text, member)
    accounts_section = _section_block_text(kb_text, "KEY ACCOUNTS")
    work_section = _section_block_text(kb_text, "WORK HISTORY")

    # Supplement key accounts from section-scoped LLM over KEY ACCOUNTS only.
    if accounts_section.strip():
        rag_accounts = await _extract_key_accounts_via_rag_llm(accounts_section)
        seen = {a.casefold() for a in parsed.get("key_accounts") or []}
        for acct in rag_accounts:
            if acct.casefold() not in seen and _account_appears_in_kb(acct, accounts_section):
                parsed.setdefault("key_accounts", []).append(acct)
                seen.add(acct.casefold())
        parsed["key_accounts"] = _dedupe_key_accounts(parsed.get("key_accounts") or [])

    has_bio_sections = any(
        marker in kb_text
        for marker in (
            "# YEARS OF EXPERIENCE",
            "# WORK HISTORY",
            "# KEY ACCOUNTS",
            "# EDUCATION",
            "# LICENSES",
            "# CERTIFICATIONS",
        )
    )
    if has_bio_sections and (
        parsed.get("overview")
        or parsed.get("expertise")
        or parsed.get("work_history")
        or parsed.get("key_accounts")
    ):
        return _sanitize_bio_extraction(parsed, kb_text)

    extraction_prompt = (
        f"Extract facts about '{member}' from their approved 04_Bio PDF ONLY.\n\n"
        "CRITICAL RULES:\n"
        "- Use ONLY text from the document below (sections: # YEARS OF EXPERIENCE, # WORK HISTORY, "
        "# LICENSES, # CERTIFICATIONS, # KEY ACCOUNTS, # EDUCATION).\n"
        "- WORK HISTORY: only employers with year ranges (e.g. 2013 - Present). "
        "Never treat logo/client names without dates as jobs.\n"
        "- KEY ACCOUNTS: only client names from the KEY ACCOUNTS section / logo captions.\n"
        "- Do NOT list the same client twice (e.g. 'Sayfe' and 'Sayfe Families' = one entry).\n"
        "- Do NOT invent clients, degrees, licenses, or employers.\n"
        "- If a section is missing in the document, return an empty array for that field.\n"
        "- education, licenses, and certifications must be arrays of plain strings (not objects).\n"
        "- Never repeat the same license, certification, or education line.\n\n"
        "Return ONLY JSON:\n"
        '{"title":"role at zo agency","overview":"bio paragraph from doc",'
        '"expertise":[{"area":"...","years":"..."}],'
        '"work_history":[{"company":"...","title":"...","dates":"..."}],'
        '"licenses":["..."],"certifications":["..."],'
        '"key_accounts":["..."],"education":["..."]}'
    )

    # Scope LLM to section-bounded text when available.
    scoped_user = kb_text
    if work_section or accounts_section:
        scoped_user = (
            f"{kb_text[:4000]}\n\n"
            f"# WORK HISTORY\n{work_section}\n\n"
            f"# KEY ACCOUNTS\n{accounts_section}\n"
        )

    try:
        extracted, _ = await llm.chat_json(
            [
                {"role": "system", "content": extraction_prompt},
                {"role": "user", "content": f"04_Bio approved file content:\n{scoped_user}"},
            ],
            max_tokens=16000,
            temperature=0.0,
        )
        parsed = _parse_bio_sections_from_text(kb_text, member)
        llm_extraction = {**empty, **extracted}
        if parsed.get("work_history"):
            llm_extraction["work_history"] = []
        if parsed.get("key_accounts"):
            llm_extraction["key_accounts"] = []
        merged = _merge_bio_extractions(parsed, llm_extraction)
        return _sanitize_bio_extraction(merged, kb_text)
    except LlmError as exc:
        logger.warning("Bio JSON extraction failed for %s: %s", member, exc)
        parsed = _parse_bio_sections_from_text(kb_text, member)
        if any(parsed.get(k) for k in ("overview", "expertise", "work_history", "key_accounts")):
            return _sanitize_bio_extraction(parsed, kb_text)

    return _sanitize_bio_extraction(_parse_bio_sections_from_text(kb_text, member), kb_text)


def _format_member_bio_content(member: str, extracted: dict[str, Any]) -> str:
    """Render bio from KB facts only — omit empty blocks; never invent VERIFY padding."""
    raw_title = str(extracted.get("title") or "").strip()
    title = "" if raw_title.upper().startswith("[VERIFY") else raw_title
    overview = str(extracted.get("overview") or "").strip()
    if overview.upper().startswith("[VERIFY"):
        overview = ""
    expertise = extracted.get("expertise") or []
    work_history = extracted.get("work_history") or []
    licenses = extracted.get("licenses") or []
    certifications = extracted.get("certifications") or []
    key_accounts = extracted.get("key_accounts") or []
    education = extracted.get("education") or []

    heading = f"### {member} — {title}\n" if title else f"### {member}\n"
    parts = [heading]

    if overview:
        parts.append(f"{overview}\n")

    expertise_rows: list[str] = []
    for exp in expertise:
        if isinstance(exp, dict) and exp.get("area"):
            area = str(exp.get("area") or "").strip()
            years = str(exp.get("years") or "").strip()
            if years.upper().startswith("[VERIFY"):
                years = ""
            if area:
                expertise_rows.append(f"| {area} | {years or '—'} |")
    if expertise_rows:
        parts.append("**Years of Experience**")
        parts.append("| Area of Expertise | Years |")
        parts.append("| --- | --- |")
        parts.extend(expertise_rows)
        parts.append("")

    work_lines: list[str] = []
    for job in work_history:
        if not isinstance(job, dict):
            continue
        company = str(job.get("company") or "").strip()
        job_title = str(job.get("title") or "").strip()
        dates = str(job.get("dates") or "").strip()
        if company.upper().startswith("[VERIFY"):
            company = ""
        if job_title.upper().startswith("[VERIFY"):
            job_title = ""
        if dates.upper().startswith("[VERIFY"):
            dates = ""
        if not company and not job_title:
            continue
        label = f"**{company}**" if company else "**Role**"
        detail_bits = [b for b in (job_title, dates) if b]
        if detail_bits:
            work_lines.append(f"- {label} — {', '.join(detail_bits)}")
        else:
            work_lines.append(f"- {label}")
    if work_lines:
        parts.append("**Work History**")
        parts.extend(work_lines)
        parts.append("")

    # Credentials: only when KB has them — never invent VERIFY noise.
    if licenses:
        parts.append("**Licenses**")
        for credential in _dedupe_string_list(
            [_normalize_list_item(c) for c in licenses]
        ):
            parts.append(f"- {credential}")
        parts.append("")
    if certifications:
        parts.append("**Certifications**")
        for credential in _dedupe_string_list(
            [_normalize_list_item(c) for c in certifications]
        ):
            parts.append(f"- {credential}")
        parts.append("")

    accounts = _dedupe_key_accounts(
        [_normalize_list_item(a) for a in key_accounts]
    )
    if accounts:
        parts.append("**Key Accounts**")
        for acct in accounts:
            parts.append(f"- {acct}")
        parts.append("")

    edu_lines = _dedupe_string_list(
        [_normalize_list_item(e) for e in education]
    )
    if edu_lines:
        parts.append("**Education**")
        for edu in edu_lines:
            parts.append(f"- {edu}")
        parts.append("")

    return "\n".join(parts)


_WORK_HISTORY_VERIFY_RE = re.compile(
    r"\[VERIFY:[^\]]*work\s*history",
    re.I,
)


def replace_bio_work_history_verify_from_kb(
    content: str,
    member: str,
    kb_text: str,
) -> tuple[str, int]:
    """Replace a Work History VERIFY stub with parsed 04_Bio employment rows."""
    if not (content or "").strip() or not (kb_text or "").strip():
        return content, 0
    if not _WORK_HISTORY_VERIFY_RE.search(content):
        return content, 0

    normalized = _normalize_bio_text_dates(kb_text)
    parsed = _sanitize_bio_extraction(
        _parse_bio_sections_from_text(normalized, member),
        normalized,
    )
    jobs = parsed.get("work_history") or []
    if not jobs:
        return content, 0

    block_lines = ["**Work History**"]
    for job in jobs:
        if not isinstance(job, dict):
            continue
        company = str(job.get("company") or "").strip() or "[VERIFY: company]"
        job_title = str(job.get("title") or "").strip()
        dates = str(job.get("dates") or "").strip() or "[VERIFY: dates]"
        if job_title and not job_title.startswith("[VERIFY"):
            block_lines.append(f"- **{company}** — {job_title}, {dates}")
        else:
            block_lines.append(f"- **{company}**, {dates}")
    new_block = "\n".join(block_lines)

    pattern = re.compile(
        r"\*\*Work History\*\*\s*\n.*?(?=\n\*\*[^*]+\*\*|\Z)",
        re.I | re.S,
    )
    if not pattern.search(content):
        return content, 0
    updated = pattern.sub(new_block + "\n\n", content, count=1)
    if updated == content:
        return content, 0
    return updated.rstrip() + ("\n" if content.endswith("\n") else ""), 1


def _narrative_section_preamble(state: SectionsGraphState) -> str:
    from app.services.proposal_section_dedup import format_anti_duplication_rules

    return (
        "You write zö agency NARRATIVE proposal content.\n"
        f"{format_register_block('narrative')}\n\n"
        "Facts (clients, certs, team, case studies) must come ONLY from knowledge-base excerpts.\n"
        "Voice must follow BOTH zö core brand voice AND the RFP-specific adaptation block.\n"
        f"{format_anti_duplication_rules()}\n"
        "Within Sections 1–3: Who We Are = brand essence only; Org/Business/Certs/Insurance = facts only; "
        "Team = bios only; Case Studies = proof only — never repeat the same company pitch across subsections.\n"
        f"Client: {state['rfp_client']} | Sector: {state['rfp_sector']}\n"
    )


async def _fetch_knowledge_base(state: SectionsGraphState) -> dict[str, Any]:
    skip_company = settings.use_company_qualification_s1
    bundles = await proposal_knowledge_base_tools.gather_proposal_kb_for_sections(
        rfp_title=state["rfp_title"],
        rfp_client=state["rfp_client"],
        rfp_sector=state["rfp_sector"],
        rfp_location=state.get("rfp_location"),
        rfp_context=state["rfp_context"],
        skip_company=skip_company,
    )
    roster_text, roster_sources = await proposal_knowledge_base_tools.fetch_master_team_roster(
        rfp_client=state["rfp_client"],
        rfp_sector=state["rfp_sector"],
        rfp_context=state["rfp_context"],
    )
    zo_voice_text, zo_voice_sources = bundles["zo_voice"]
    company_text, company_sources = bundles["company"]
    bios_text, bio_sources = bundles["bios"]
    cases_text, case_sources = bundles["case_studies"]

    return {
        "kb_zo_voice": _sanitize_content(zo_voice_text),
        "kb_zo_voice_sources": zo_voice_sources,
        "kb_company": _sanitize_content(company_text),
        "kb_company_sources": company_sources,
        "kb_master_roster": _sanitize_content(roster_text),
        "kb_master_roster_sources": roster_sources,
        "kb_bios": _sanitize_content(bios_text),
        "kb_bio_sources": bio_sources,
        "kb_case_studies": _sanitize_content(cases_text),
        "kb_case_sources": case_sources,
    }


async def _synthesize_proposal_voice(state: SectionsGraphState) -> dict[str, Any]:
    """Merge zö KB brand voice with RFP-specific tone adaptation."""
    zo_kb = state.get("kb_zo_voice") or ""
    if settings.use_company_qualification_s1 and not zo_kb.strip():
        voice_text, _voice_sources = await proposal_knowledge_base_tools.search_knowledge_base(
            "zö agency brand voice tone proposal writing public sector",
            limit=3,
            max_chars=8000,
        )
        zo_kb = voice_text
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You define how zö agency should write THIS proposal.\n\n"
                        "Two layers (both required):\n"
                        "1. zö core voice — from the knowledge-base brand voice excerpt (identity, "
                        "personality, how zö always sounds). zö narrative proposals use FIRST PERSON "
                        "(we/our/us), active voice, outcome-focused proof — never third-person "
                        "'The Vendor' procurement language.\n"
                        "2. RFP adaptation — how tone/formality/terminology should shift for THIS "
                        "client, sector, and solicitation (read the RFP closely).\n\n"
                        "CRITICAL: Government RFPs often say 'Vendor' in instructions. That register "
                        "is for attachments/forms only. Narrative sections (cover letter, company "
                        "overview, team, case studies, approach) must sound like zö speaking directly "
                        "to the client — not a legal brief about a vendor.\n\n"
                        "Different RFPs MUST produce different adaptations. Never generic one-size-fits-all.\n"
                        "Never contradict verified zö KB facts or invent zö positioning not in KB.\n\n"
                        "Return JSON only:\n"
                        '{"zoCoreVoice":"1-2 sentences from KB",'
                        '"tone":"combined label for this proposal",'
                        '"formality":"formal|semi-formal|conversational",'
                        '"voiceGuidelines":["specific writing rules for this RFP"],'
                        '"keyTerms":["terms to mirror from RFP"],'
                        '"clientExpectations":"what evaluators want to hear",'
                        '"rfpAdaptationNotes":"how this RFP voice differs from a generic zö proposal"}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Client: {state['rfp_client']}\n"
                        f"Sector: {state['rfp_sector']}\n"
                        f"Title: {state['rfp_title']}\n\n"
                        f"zö brand voice (knowledge base):\n{zo_kb}\n\n"
                        f"RFP text:\n{state['rfp_context'][:12000]}"
                    ),
                },
            ],
            temperature=0.4,
            max_tokens=16000,
        )
        # Ensure string fields are strings to avoid validation errors
        for field in ("clientExpectations", "zoCoreVoice", "rfpAdaptationNotes", "tone", "formality"):
            val = raw.get(field)
            if isinstance(val, list):
                raw[field] = "\n".join(str(x) for x in val)
            elif val is None:
                raw[field] = ""
            else:
                raw[field] = str(val)
        raw["kbZoVoice"] = zo_kb
        return {"brand_voice": raw, "provider": _provider_name()}
    except LlmError as exc:
        logger.warning("Proposal voice synthesis failed: %s", exc)
        fallback = {
            "zoCoreVoice": "zö agency writes in first person (we/our) as a confident, human-centered marketing partner.",
            "tone": "professional",
            "formality": "semi-formal",
            "voiceGuidelines": [
                "Write in first person: we, our, us — never 'The Vendor' or third-person agency distance.",
                "Lead with verified zö capabilities and client outcomes.",
                f"Match {state['rfp_client']} public-sector formality without sounding like a legal form.",
            ],
            "keyTerms": [],
            "clientExpectations": "",
            "rfpAdaptationNotes": "Fallback voice — re-run when LLM available.",
            "kbZoVoice": zo_kb,
        }
        return {"brand_voice": fallback, "provider": _provider_name()}



def _section_system_preamble(state: SectionsGraphState) -> str:
    return _narrative_section_preamble(state)


# Per-subsection KB slices. The bucket budgets in proposal_knowledge_base_tools
# already bound these; the point here is the *relative* narrowing — "who we are"
# deliberately sees less company detail than the default so it stays narrative.
_KB_SLICE_VOICE = 12_000
_KB_SLICE_COMPANY_MINIMAL = 12_000
_KB_SLICE_COMPANY_REGISTRATION = 20_000


def _section_kb_user_content(state: SectionsGraphState, sec_id: str) -> str:
    """Pick KB context per subsection — org structure uses master roster only."""
    if sec_id == "section-1-org-structure":
        return (
            "Master Team Roster "
            f"(SOURCE: {proposal_knowledge_base_tools.MASTER_TEAM_ROSTER_DOC} — "
            "use ONLY names and titles from this document):\n"
            f"{state.get('kb_master_roster', '')}"
        )
    if sec_id == "section-1-who-we-are":
        return (
            f"Brand Voice KB:\n{state.get('kb_zo_voice', '')[:_KB_SLICE_VOICE]}\n\n"
            f"Company facts (minimal — no client lists or certifications):\n"
            f"{state.get('kb_company', '')[:_KB_SLICE_COMPANY_MINIMAL]}"
        )
    if sec_id == "section-1-business-info":
        return (
            "Company facts (REGISTRATION, LEGAL IDENTITY, AND CONTACT ONLY — "
            "ignore narrative marketing copy, certifications, awards, and team bios):\n"
            f"{state.get('kb_company', '')[:_KB_SLICE_COMPANY_REGISTRATION]}"
        )
    return f"Company Knowledge Base:\n{state.get('kb_company', '')}"


_SECTION1_PAGE_RATIOS: dict[str, tuple[float, int]] = {
    "section-1-who-we-are": (0.03, 200),
    "section-1-org-structure": (0.08, 800),
    "section-1-business-info": (0.03, 200),
    "section-1-certifications": (0.03, 150),
    "section-1-insurance": (0.03, 100),
}


def _composition_to_section_payloads(
    state: SectionsGraphState,
    composition: Section1CompositionResult,
    *,
    kb_sources: list[str],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for generated in composition.generated_sections:
        ratio, default_words = _SECTION1_PAGE_RATIOS.get(generated.id, (0.03, 400))
        word_target = generated.word_count or default_words
        content = _sanitize_content(generated.content.strip())
        if generated.id in {"section-1-who-we-are", "section-1-business-info"}:
            content = enforce_agency_tenure(content)
        if generated.id == "section-1-who-we-are":
            content = _normalize_who_we_are_markdown(content)
            content = _trim_to_max_words(content, 200)
            content = _normalize_who_we_are_markdown(content)
        payloads.append(
            _section_payload(
                section_id=generated.id,
                title=generated.title,
                mode="pull",
                word_target=word_target,
                page_limit=state.get("page_limit"),
                page_ratio=ratio,
                designer_note_default=f"Section 1 subsection: {generated.title}.",
                raw={"content": content, "kbRefs": kb_sources[:8]},
                kb_sources=kb_sources,
            )
        )
    return payloads


async def _fetch_company_truth(state: SectionsGraphState) -> dict[str, Any]:
    if state.get("skip_section_1"):
        return {}
    truth, _provider = await run_company_truth_agent(
        rfp_client=state["rfp_client"],
        rfp_sector=state["rfp_sector"],
        rfp_context=state["rfp_context"],
    )
    return {"company_truth": truth.model_dump(by_alias=True)}


async def _fetch_proposal_context(state: SectionsGraphState) -> dict[str, Any]:
    if state.get("skip_section_1") and state.get("skip_section_2") and state.get("skip_section_3"):
        return {}
    context, _provider = await run_proposal_context_agent(
        rfp_title=state["rfp_title"],
        rfp_client=state["rfp_client"],
        rfp_sector=state["rfp_sector"],
        rfp_location=state.get("rfp_location"),
        rfp_context=state["rfp_context"],
    )
    return {"proposal_context": context.model_dump(by_alias=True)}


async def _prioritize_capabilities(state: SectionsGraphState) -> dict[str, Any]:
    if state.get("skip_section_1"):
        return {}
    truth_raw = state.get("company_truth")
    context_raw = state.get("proposal_context")
    if not truth_raw or not context_raw:
        return {}
    prioritized, provider = await run_capability_prioritization_agent(
        company_truth=CompanyTruth.model_validate(truth_raw),
        proposal_context=ProposalContext.model_validate(context_raw),
    )
    return {
        "prioritized_capabilities": prioritized.model_dump(by_alias=True),
        "provider": provider,
    }


async def _ensure_brand_voice(state: SectionsGraphState) -> SectionsGraphState:
    """JIT brand voice merged into state for builders."""
    merged: SectionsGraphState = dict(state)
    if not merged.get("brand_voice"):
        merged.update(await _synthesize_proposal_voice(merged))
    return merged


async def _plan_section_1(state: SectionsGraphState) -> dict[str, Any]:
    """Section 1 Agent — budgets + inclusion plan JSON only."""
    if state.get("skip_section_1"):
        return {}
    truth_raw = state.get("company_truth")
    context_raw = state.get("proposal_context")
    caps_raw = state.get("prioritized_capabilities")
    if not all([truth_raw, context_raw, caps_raw]):
        return {}
    plan, provider = await run_section_1_agent(
        company_truth=CompanyTruth.model_validate(truth_raw),
        proposal_context=ProposalContext.model_validate(context_raw),
        prioritized_capabilities=PrioritizedCapabilities.model_validate(caps_raw),
    )
    return {
        "section1_plan": plan.model_dump(by_alias=True),
        "provider": provider,
    }


async def _build_section_1_cq(state: SectionsGraphState) -> dict[str, Any]:
    """Section 1 Builder — assembles prose from plan + Company Truth."""
    if state.get("skip_section_1"):
        return {}

    plan_raw = state.get("section1_plan")
    truth_raw = state.get("company_truth")
    context_raw = state.get("proposal_context")
    caps_raw = state.get("prioritized_capabilities")
    if not all([plan_raw, truth_raw, context_raw, caps_raw]):
        return {"error": "Section 1 builder missing upstream agent outputs"}

    merged_state = await _ensure_brand_voice(state)

    section1_plan = Section1PlanResult.model_validate(plan_raw)
    company_truth = CompanyTruth.model_validate(truth_raw)
    proposal_context = ProposalContext.model_validate(context_raw)
    prioritized = PrioritizedCapabilities.model_validate(caps_raw)

    roster_text = ""
    # Always pull Master Team Roster for full org chart in 1.2
    roster_text, _ = await proposal_knowledge_base_tools.fetch_master_team_roster(
        rfp_client=merged_state["rfp_client"],
        rfp_sector=merged_state["rfp_sector"],
        rfp_context=merged_state["rfp_context"],
    )

    kb_sources = company_truth.sources or merged_state.get("kb_company_sources") or []
    existing = _merge_section_list(
        _section_1_stub_payloads(merged_state),
        merged_state.get("sections") or [],
    )
    accumulated: list[dict[str, Any]] = []

    async def _on_subsection(generated: Any) -> None:
        from app.services.company_qualification.schemas import GeneratedSubsection

        sec = (
            generated
            if isinstance(generated, GeneratedSubsection)
            else GeneratedSubsection.model_validate(generated)
        )
        payloads = _composition_to_section_payloads(
            merged_state,
            Section1CompositionResult(
                sectionPlan=dict(section1_plan.section_plan),
                generatedSections=[sec],
            ),
            kb_sources=kb_sources,
        )
        if not payloads:
            return
        accumulated.append(payloads[0])
        partial = _merge_section_list(existing, accumulated)
        await _emit_partial(merged_state, partial)

    composition, provider = await run_section_1_builder_agent(
        section1_plan=section1_plan,
        company_truth=company_truth,
        proposal_context=proposal_context,
        prioritized_capabilities=prioritized,
        brand_voice_block=_proposal_voice_block(merged_state),
        rfp_client=merged_state["rfp_client"],
        rfp_sector=merged_state["rfp_sector"],
        master_roster_excerpt=roster_text,
        on_subsection=_on_subsection,
    )

    new_sections = _composition_to_section_payloads(
        merged_state, composition, kb_sources=kb_sources
    )
    if not accumulated and new_sections:
        for idx in range(len(new_sections)):
            partial = _merge_section_list(existing, new_sections[: idx + 1])
            await _emit_partial(merged_state, partial)

    return {"sections": new_sections, "provider": provider}


def _persona_name_from_id(persona_id: str) -> str:
    """Best-effort display name when a saved Key Persona id is missing from KB."""
    cleaned = re.sub(r"[-_]+", " ", (persona_id or "").strip())
    return " ".join(part.capitalize() for part in cleaned.split()) if cleaned else persona_id


async def _selected_key_personas_for_rfp(rfp_id: str | None) -> list[dict[str, Any]]:
    """User-selected Key Personas for this RFP, if any were chosen in the UI."""
    if not rfp_id:
        return []
    try:
        draft = await aget_proposal_draft(rfp_id)
    except ProposalGenerationCancelled:
        raise
    except Exception:
        logger.warning("Could not load proposal draft %s to check selected Key Personas", rfp_id)
        return []
    # Preserve save order — never convert to set (silent reordering / harder diffs).
    selected_ids = [
        str(pid).strip()
        for pid in (getattr(draft, "selected_key_personas", None) or [])
        if str(pid).strip()
    ]
    if not selected_ids:
        return []
    try:
        all_personas = await team_personas_service.get_all_key_personas()
    except ProposalGenerationCancelled:
        raise
    except Exception:
        logger.warning("Could not load Key Personas roster for rfp %s", rfp_id)
        return []
    by_id = {p["id"]: p for p in all_personas}
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    from app.services.evidence_trust.personnel_grounding import is_retired_team_member
    from app.services.retired_staff_store import is_retired_id

    for pid in selected_ids:
        if is_retired_id(pid):
            logger.info("Skipping retired Key Persona id %s for rfp %s", pid, rfp_id)
            continue
        persona = by_id.get(pid)
        if persona:
            if persona.get("retired") or is_retired_team_member(
                str(persona.get("name") or "")
            ):
                continue
            resolved.append(persona)
            continue
        # Never silently drop a user pick — stub so Section 2 still gets a bio card.
        # But never stub a retired id as if they were current staff.
        stub_name = _persona_name_from_id(pid)
        if is_retired_team_member(stub_name):
            logger.info(
                "Skipping retired Key Persona stub %s (%s) for rfp %s",
                pid,
                stub_name,
                rfp_id,
            )
            continue
        missing.append(pid)
        resolved.append(
            {
                "id": pid,
                "name": stub_name,
                "title": "Team Specialist",
                "hasResume": False,
                "sourceFile": "",
                "bioSnippet": "Selected Key Persona — resume lookup missed this id.",
            }
        )
    if missing:
        logger.warning(
            "Key Personas for %s: %d selected, %d unresolved in KB (%s) — stubbing missing",
            rfp_id,
            len(selected_ids),
            len(missing),
            ", ".join(missing),
        )
    return resolved


def _team_selection_from_personas(personas: list[dict[str, Any]]) -> TeamSelectionResult:
    """Build a TeamSelectionResult straight from the user's Key Persona picks —
    no RFP-needs matching, no roster re-scoring, no owner auto-insert."""
    from app.services.evidence_trust.personnel_grounding import is_retired_team_member

    members = [
        TeamMemberSelection(
            name=str(p["name"]),
            role=str(p.get("title") or ""),
            rationale="Selected by the user as a Key Persona for this proposal.",
        )
        for p in personas
        if str(p.get("name") or "").strip()
        and not p.get("retired")
        and not is_retired_team_member(str(p.get("name") or ""))
    ]
    return TeamSelectionResult(members=members)


async def _select_team(state: SectionsGraphState) -> dict[str, Any]:
    """Team Selection Agent — roster query + skill-based pick, no bios."""
    if state.get("skip_section_2"):
        return {}

    roster_text, roster_sources = await proposal_knowledge_base_tools.fetch_master_team_roster(
        rfp_client=state["rfp_client"],
        rfp_sector=state["rfp_sector"],
        rfp_context=state["rfp_context"],
    )

    # A user-selected Key Personas list takes over completely: fetch bios for
    # exactly those people, never the RFP-needs-driven roster match below.
    selected_personas = await _selected_key_personas_for_rfp(state.get("rfp_id"))
    if selected_personas:
        selection = _team_selection_from_personas(selected_personas)
        logger.info(
            "Team Selection: using %d user-selected Key Personas for rfp %s (skipping RFP-needs matching)",
            len(selected_personas),
            state.get("rfp_id"),
        )
        return {
            "team_selection": selection.model_dump(by_alias=True),
            "kb_master_roster": roster_text,
            "kb_master_roster_sources": roster_sources,
        }

    context_raw = state.get("proposal_context")
    if not context_raw:
        return {}

    selection, provider = await run_team_selection_agent(
        proposal_context=ProposalContext.model_validate(context_raw),
        rfp_context=state["rfp_context"],
        roster_text=roster_text,
        roster_doc_label=proposal_knowledge_base_tools.MASTER_TEAM_ROSTER_DOC,
    )

    return {
        "team_selection": selection.model_dump(by_alias=True),
        "kb_master_roster": roster_text,
        "kb_master_roster_sources": roster_sources,
        "provider": provider,
    }


async def _build_bios(state: SectionsGraphState) -> dict[str, Any]:
    """Bio Builder — select members, emit stub + designer note (no full rewrite).

    Option B (2026-07-31): do not re-format Key Accounts / full career history into
    the manuscript. Designer inserts approved 04_Bio_*.pdf. Optional short verified
    bullets only when the RFP requires inline bio/resume text.
    """
    if state.get("skip_section_2"):
        return {}

    selection_raw = state.get("team_selection")
    if not selection_raw:
        return {}

    from app.services.proposal_bio_stub import (
        rfp_requires_inline_bios,
        stub_from_extraction,
    )

    team = TeamSelectionResult.model_validate(selection_raw)
    # User Key Persona picks must not be collapsed by last-name dedupe or the
    # skill-based 5-person cap — honor every selected name (full-name dedupe only).
    user_picked = any(
        "key persona" in (m.rationale or "").casefold() for m in team.members
    )
    members = normalize_selected_members(
        [m.name for m in team.members],
        max_members=max(MAX_TEAM_SIZE, len(team.members)),
        dedupe_by_last_name=not user_picked,
    )
    if not members:
        return {}

    if user_picked and len(members) < len(team.members):
        logger.warning(
            "Bio builder: user selected %d Key Personas but only %d unique names after normalize",
            len(team.members),
            len(members),
        )

    role_by_name: dict[str, str] = {}
    for m in team.members:
        norm = normalize_selected_members(
            [m.name],
            max_members=1,
            dedupe_by_last_name=False,
        )
        if norm:
            role_by_name[norm[0]] = (m.role or "").strip()

    inline_required = rfp_requires_inline_bios(state.get("rfp_context") or "")
    existing = state.get("sections") or []
    new_sections: list[dict[str, Any]] = []

    for i, member in enumerate(members, 1):
        safe_id = member.lower().replace(" ", "-").replace("'", "")
        sec_id = f"section-2-bio-{safe_id}"
        sec_title = f"2.{i} — {member}"
        _log_section_generate_next(state, section_id=sec_id, title=sec_title)

        logger.info(
            "Building bio stub for %s (inline_required=%s)",
            member,
            inline_required,
        )
        kb_text_for_extraction, bio_sources, kb_available, pdf_name = (
            await _bio_stub_kb_inputs(member, inline_required=inline_required)
        )
        if inline_required and not kb_available:
            logger.warning(
                "No 04_Bio content for %s — stub with Ella MANUAL FILL",
                member,
            )

        extracted: dict[str, Any] = {}
        if inline_required and kb_available:
            # Title / bullets only — stub_from_extraction ignores key_accounts.
            extracted = await _extract_member_bio_facts(member, kb_text_for_extraction)

        content = stub_from_extraction(
            member=member,
            role=role_by_name.get(member, "")
            or next(
                (m.role for m in team.members if m.name.strip().casefold() == member.casefold()),
                "",
            ),
            pdf_filename=pdf_name,
            kb_text=kb_text_for_extraction if inline_required else "",
            kb_available=kb_available,
            inline_required=inline_required,
            extracted=extracted,
        )
        content = _apply_verified_corrections(
            _sanitize_content(content),
            rfp_client=state.get("rfp_client", ""),
        )
        raw = {
            "content": content,
            "kbRefs": bio_sources or [pdf_name],
        }

        section = _section_payload(
            section_id=sec_id,
            title=sec_title,
            mode="select",
            word_target=120 if not inline_required else 220,
            page_limit=state.get("page_limit"),
            page_ratio=0.02,
            designer_note_default=(
                f"Insert approved bio PDF — {pdf_name}. Do not rewrite Key Accounts."
            ),
            raw=raw,
            kb_sources=bio_sources,
            extra_refs=[member],
        )
        if isinstance(section, dict) and "id" in section and "title" in section:
            new_sections.append(section)
            await _emit_partial(state, _merge_section_list(existing, new_sections))

    return {"sections": new_sections}


async def _select_evidence(state: SectionsGraphState) -> dict[str, Any]:
    """Evidence Selection Agent — score candidate index, select 3–5 titles only."""
    if state.get("skip_section_3"):
        return {}

    context_raw = state.get("proposal_context")
    if not context_raw:
        return {}

    proposal_context = ProposalContext.model_validate(context_raw)
    candidate_rows = await proposal_knowledge_base_tools.search_evidence_candidate_index(
        rfp_client=state["rfp_client"],
        rfp_sector=state["rfp_sector"],
        rfp_context=state["rfp_context"],
        services_requested=list(proposal_context.services_requested or []),
    )
    candidates = [EvidenceCandidate.model_validate(row) for row in candidate_rows]

    selection, provider = await run_evidence_selection_agent(
        proposal_context=proposal_context,
        rfp_context=state["rfp_context"],
        rfp_client=state["rfp_client"],
        candidates=candidates,
        rfp_sector=state.get("rfp_sector") or "",
        rfp_title=state.get("rfp_title") or "",
        rfp_id=state.get("rfp_id"),
    )

    return {
        "evidence_selection": selection.model_dump(by_alias=True),
        "provider": provider,
    }


def _case_study_relevance_map(fit_report: Any) -> dict[str, str]:
    """title -> the capability that made it a match, from the fit report.

    Read, never written: the resolver already decided why each study was picked,
    and a freshly generated "why this fits" line would be exactly the invented
    claim the stub exists to avoid.
    """
    out: dict[str, str] = {}
    results = getattr(fit_report, "results", None) or []
    for result in results:
        capability = str(getattr(result, "capability", "") or "").strip()
        if not capability:
            continue
        for candidate in getattr(result, "candidates", None) or []:
            source = str(getattr(candidate, "source", "") or "").strip()
            if source:
                out.setdefault(source.casefold(), capability)
    return out


def _case_study_display_title(index: int, study: str) -> str:
    """Human title for Section 3 cards — never raw PDF filenames in the UI chip."""
    from app.services.proposal_blocker_prevention import clean_case_study_label

    return clean_case_study_label(study, index=index)


async def _build_case_studies(state: SectionsGraphState) -> dict[str, Any]:
    """Case Study Builder — full retrieval per selected study only."""
    if state.get("skip_section_3"):
        return {}

    selection_raw = state.get("evidence_selection")
    if not selection_raw:
        return {}

    evidence = EvidenceSelectionResult.model_validate(selection_raw)
    selected_studies = evidence.selected_studies
    if not selected_studies:
        return {}

    from app.services.proposal_case_study_eligibility import (
        is_eligible_section3_case_study_title,
    )

    selected_studies = [
        s
        for s in selected_studies
        if is_eligible_section3_case_study_title(
            s,
            rfp_title=str(state.get("rfp_title") or ""),
            rfp_sector=str(state.get("rfp_sector") or ""),
        )
    ]
    if not selected_studies:
        logger.warning(
            "Section 3: all evidence selections were ineligible dumps/templates"
        )
        return {}

    merged_state = await _ensure_brand_voice(state)
    existing = merged_state.get("sections") or []
    new_sections: list[dict[str, Any]] = []
    from app.services.proposal_case_study_fit import case_study_display_name
    from app.services.proposal_case_study_stub import (
        case_study_asset_filename,
        format_case_study_stub_content,
    )

    # Why each study was picked — produced by evidence selection, not written here.
    relevance_by_title = {
        (score.title or "").casefold(): (score.rationale or "").strip()
        for score in evidence.scores
        if (score.rationale or "").strip()
    }

    for i, study in enumerate(selected_studies, 1):
        safe_id = study.lower()[:40].replace(" ", "-").replace("/", "-")
        sec_id = f"section-3-work-{i:02d}-{safe_id}"
        sec_title = _case_study_display_title(i, study)
        _log_section_generate_next(merged_state, section_id=sec_id, title=sec_title)

        # Option B: hand the approved asset to design instead of redrafting it.
        # The selection above is the work; re-narrating the PDF is not.
        content = format_case_study_stub_content(
            display_name=case_study_display_name(study),
            asset_filename=case_study_asset_filename(study),
            relevance=relevance_by_title.get(study.casefold(), ""),
        )
        section = _section_payload(
            section_id=sec_id,
            title=sec_title,
            mode="select",
            word_target=40,
            page_limit=merged_state.get("page_limit"),
            page_ratio=0.03,
            designer_note_default=f"Our Work example: {study}.",
            raw={"content": content, "kbRefs": [study]},
            kb_sources=[study],
            extra_refs=[study],
        )
        if isinstance(section, dict) and "id" in section and "title" in section:
            new_sections.append(section)
            await _emit_partial(merged_state, _merge_section_list(existing, new_sections))

    return {"sections": _merge_section_list(existing, new_sections)}


async def _join_sections(state: SectionsGraphState) -> dict[str, Any]:
    """Synchronization node after parallel S1 / S2 / S3 tracks."""
    return {}


async def _validate_sections_editorial(state: SectionsGraphState) -> dict[str, Any]:
    """Editorial Validation — review all Sections 1–3 before return."""
    if state.get("skip_section_1") and state.get("skip_section_2") and state.get("skip_section_3"):
        return {}

    truth_raw = state.get("company_truth")
    context_raw = state.get("proposal_context")
    if not truth_raw or not context_raw:
        return {}

    plan_raw = state.get("section1_plan")
    team_raw = state.get("team_selection")
    evidence_raw = state.get("evidence_selection")

    # Editorial review is non-critical (review-only). It must NEVER fail
    # Sections 1–3 generation, so any error here degrades to "no review".
    try:
        review, provider = await run_editorial_validation_agent(
            sections=state.get("sections") or [],
            company_truth=CompanyTruth.model_validate(truth_raw),
            proposal_context=ProposalContext.model_validate(context_raw),
            section1_plan=Section1PlanResult.model_validate(plan_raw) if plan_raw else None,
            team_selection=TeamSelectionResult.model_validate(team_raw) if team_raw else None,
            evidence_selection=EvidenceSelectionResult.model_validate(evidence_raw) if evidence_raw else None,
        )

        editorial = Section1EditorialReview(
            reviewedAt=editorial_reviewed_at(),
            recommendations=[
                Section1EditorialRecommendation.model_validate(rec.model_dump(by_alias=True))
                for rec in review.recommendations
            ],
            provider=provider,
        )
        return {
            "section1_editorial_review": editorial.model_dump(by_alias=True),
            "provider": provider,
        }
    except ProposalGenerationCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 — review must not crash generation
        logger.warning("Editorial validation node failed (non-fatal): %s", str(exc)[:200])
        return {}


async def _build_section_1(state: SectionsGraphState) -> dict[str, Any]:
    """Emit each Section 1 subsection as its own highlighted section card."""
    if state.get("skip_section_1"):
        logger.info("Section 1 already complete — skipping regeneration")
        return {}

    voice = _proposal_voice_block(state)
    existing = state.get("sections") or []
    tenure = agency_tenure_block()
    years = agency_years_in_operation()

    subsections = [
        (
            "section-1-who-we-are",
            "1.1 — Who We Are",
            (
                "Write the 'Who We Are' section for zö agency. Structure it into TWO required parts "
                "with REAL markdown line breaks (blank line before/after each heading):\n"
                "1) Opening brand paragraphs (NO '# Who We Are' heading — the UI already titles this section)\n"
                "2) Then a separate line exactly: ## Our Promise\n"
                "   Then the promise paragraphs. Our Promise is mandatory — never skip it.\n\n"
                "MARKDOWN RULES (non-negotiable — bad formatting = reject):\n"
                "- Put ## Our Promise on its OWN line. Never inline it mid-sentence "
                "(FORBIDDEN: '…action. ## Our Promise We promise…').\n"
                "- Body text is normal weight. Do NOT wrap whole paragraphs or the whole section in **bold**.\n"
                "- At most TWO short bold phrases total (a few words each). Never bold headings.\n"
                "- Do not re-print the section title 'Who We Are' as the first line.\n\n"
                "HARD WORD LIMIT: MAX 250 words total. Prefer 180–220. Never exceed 250.\n\n"
                f"{tenure}\n\n"
                "VOICE (non-negotiable — brand essence, NOT a capability pitch):\n"
                "- Open with signature energy: we are more than an agency — we are their strongest advocate "
                "and an extension of their team.\n"
                "- Explain that 'zö' means family, kindred, clan, community — a force of collaboration in action.\n"
                "- Sound raw, real, street-smart, warm, and human. First person we/our/us.\n"
                "- FORBIDDEN bland corporate filler ('leveraging synergies', 'comprehensive solutions', "
                "'full-service partner committed to excellence' without zö-specific soul).\n"
                "- Brand first, then a TINY client bridge (1–2 sentences max) that names THIS RFP client "
                "with feeling — why this relationship matters — NOT how you'll run the work.\n"
                "Do not let the RFP pitch crowd out Who We Are + Our Promise.\n\n"
                "🚨 CRITICAL — KEEP SECTION 1.1 CLEAN (put these elsewhere):\n"
                "- DO NOT name staff in Section 1 — bios belong in Section 2\n"
                "- DO NOT name titles/roles as assignments ('dedicated client manager', 'executive sponsor')\n"
                "- DO NOT list channels or tactics (SEM, SEO, PPC, paid social, remarketing, CRM, dashboards)\n"
                "- DO NOT promise report cadences, deliverable SLAs, or mid-campaign process detail\n"
                "- DO NOT list multiple client names (prefer THIS RFP client only)\n"
                "- DO NOT cite audience size numbers (e.g. '4.5 million residents')\n"
                "- DO NOT mention certifications (WBENC, WOSB) — section 1.4 only\n"
                "- DO NOT mention platform certifications (Google Ads, Meta, Spotify, ISO)\n"
                "- DO NOT mention awards, business address, FEIN/EIN, or insurance here\n"
                f"- Agency tenure: ONLY '{years} years as zö agency' / '{years} years of lived experience' — "
                f"never {years - 1} or {years + 1}\n\n"
                "## Our Promise = tone + commitment only (attractive to the client, not an org chart):\n"
                "- Write like a vow: short, warm, confident, memorable.\n"
                "- MUST hit these feelings: excellence is a guarantee not a goal; we meet/beat deadlines "
                "and budgets; full transparency and real access to the people doing the work; "
                "no surprise bills; we ambassador their brand like family.\n"
                "- FORBIDDEN in Our Promise: person names, job titles, channel lists, dashboards, "
                "CRM, reporting schedules, enrollment/KPI process talk, or anything that belongs in "
                "methodology / team / budget tabs."
            ),
            "pull",
            0.03,
            250,
        ),
        (
            "section-1-org-structure",
            "1.2 — Organizational Structure",
            (
                "Write the 'Organizational Structure' for zö agency.\n"
                f"SOURCE: {proposal_knowledge_base_tools.MASTER_TEAM_ROSTER_DOC} ONLY.\n"
                "You MUST list every zö agency team member from the Master Team Roster below.\n"
                "Do NOT skip anyone listed in the roster. Do NOT add anyone not in the roster.\n"
                "For each department, write 1-2 sentences describing what that department does.\n"
                "List every person with their exact name and title as written in the master roster.\n"
                "Use exact spelling from the roster — do not invent, upgrade, or paraphrase titles."
            ),
            "pull",
            0.04,
            600,
        ),
        (
            "section-1-business-info",
            "1.3 — Business Information",
            (
                "Write the 'Business Information' subsection ONLY. This is a factual registration block — NOT marketing copy.\n\n"
                f"{tenure}\n\n"
                "INCLUDE (structured facts only):\n"
                "- Legal business name and DBA\n"
                "- Business type / ownership structure\n"
                "- EIN and state business registration IDs (DUNS/SAM/CAGE if in KB)\n"
                f"- Year founded MUST be August 21, 2013; Years in Operation MUST be {years}\n"
                "- Office, mailing, and remittance addresses\n"
                "- Main phone, email, website\n\n"
                "DO NOT INCLUDE (already covered in other subsections):\n"
                "- 'Who We Are' narrative, brand story, or 'Our Promise' (section 1.1)\n"
                "- Team roster, departments, or leadership bios (sections 1.2 / 2.x)\n"
                "- Certifications such as WBENC or WOSB (section 1.4)\n"
                "- Awards and recognition\n"
                "- Insurance coverage (section 1.5)\n"
                "- Client-specific pitch paragraphs like 'Why This Matters for [client]'\n"
                "- Sector expertise lists, campaign claims, or proposal closing language\n\n"
                "Format as labeled fields (Legal Name:, EIN:, Office:, etc.). Keep concise — 150-250 words max."
            ),
            "pull",
            0.03,
            400,
        ),
        (
            "section-1-certifications",
            "1.4 — Certifications",
            (
                "Write the 'Certifications' section for zö agency. Keep it SHORT, CONCISE, and CLEAR.\n\n"
                "🚨 CRITICAL RULES:\n"
                "- ONLY include agency certifications from verified KB (WBENC, WOSB)\n"
                "- DO NOT include platform certifications (Google Ads, Meta Ads, Spotify API, ISO, Teaching License - these are individual, not agency certs)\n"
                "- DO NOT embellish or add certifications not explicitly in 01_companyfacts_verified KB\n"
                "- Keep it simple: certification name, certifying agency, number if the KB states it\n"
                "- If a certification number is not in KB, omit the number field — never write 'available upon request'\n"
                "- One brief sentence on impact/benefit\n"
                "- Total length: 3-5 sentences maximum\n\n"
                "Format:\n"
                "- **[Certification Name]**\n"
                "  - Certifying Agency: [Agency name]\n"
                "  - Certification Number: [only if stated in KB; otherwise omit this line]\n"
                "  - Impact: [One sentence benefit]\n\n"
                "VERIFIED AGENCY CERTIFICATIONS ONLY: WBENC, WOSB"
            ),
            "pull",
            0.03,
            300,
        ),
        (
            "section-1-insurance",
            "1.5 — Insurance Information",
            (
                "Write the 'Insurance Information' subsection for zö agency. Keep it SHORT and factual.\n\n"
                "CRITICAL RULES:\n"
                "- List coverage TYPES only when they appear in 01_companyfacts (General Liability, Professional Liability, Workers Compensation, Cyber, Auto, etc.).\n"
                "- Do NOT invent dollar limits, carriers, NAIC numbers, policy numbers, or 'Compliant'.\n"
                "- Do NOT write [VERIFY: amount] placeholders or 'per occurrence / aggregate' without a KB figure.\n"
                "- NEVER write 'upon request', 'available on request', or 'will be provided separately'.\n"
                "- If the RFP wants a limits table and KB has no limits, one sentence: "
                "'Policy limits are those on the current certificate of insurance; the COI is issued to the buyer at award.'\n"
                "- Max 80 words. No empty markdown tables.\n\n"
                "Format:\n"
                "We maintain the following insurance coverage:\n"
                "- **General Liability**\n"
                "- **Professional Liability / E&O**\n"
                "- **Workers' Compensation** (as required by law)\n"
                "- other types only if named in companyfacts\n"
            ),
            "pull",
            0.03,
            300,
        ),
    ]

    new_sections: list[dict[str, Any]] = []
    kb_sources = state.get("kb_company_sources") or []

    for sec_id, sec_title, instruction, mode, ratio, word_tgt in subsections:
        _log_section_generate_next(state, section_id=sec_id, title=sec_title)
        section_kb_sources = (
            state.get("kb_master_roster_sources") or []
            if sec_id == "section-1-org-structure"
            else kb_sources
        )
        try:
            raw, _ = await llm.chat_json(
                [
                    {
                        "role": "system",
                        "content": (
                            f"{_section_system_preamble(state)}\n"
                            f"You are writing subsection: '{sec_title}'.\n"
                            f"Task: {instruction}\n\n"
                            "🚨 CRITICAL ANTI-HALLUCINATION RULES:\n"
                            "1. ONLY use facts that are EXPLICITLY stated in the Knowledge Base below\n"
                            "2. NEVER invent certifications, addresses, email addresses, phone numbers, or credentials\n"
                            "3. NEVER upgrade job titles (e.g. 'Graphic Designer' → 'Senior Graphic Designer')\n"
                            "4. NEVER invent office locations or physical presences that aren't documented\n"
                            "5. If a fact is NOT in the KB, write '[VERIFY: field name]' as a placeholder\n"
                            "6. Use EXACT names, titles, and numbers as they appear in the KB - no paraphrasing\n"
                            "7. Do NOT add certifications like 'Google Ads Certification' unless the AGENCY holds it (not just individuals)\n"
                            "8. Do NOT invent email addresses - only use ones explicitly listed in the KB\n"
                            + (
                                "9. For org structure: ONLY list people named in the Master Team Roster — "
                                "never add names from proposals, case studies, or memory\n"
                                if sec_id == "section-1-org-structure"
                                else ""
                            )
                            + "\nWrite in first-person zö voice (we/our/us). Never use 'The Vendor' or third-person.\n"
                            + (
                                "Be thorough — include every roster name and title you find.\n"
                                if sec_id == "section-1-org-structure"
                                else (
                                    "Include ONLY facts for this subsection. "
                                    "Do NOT repeat narrative or facts that belong in other Section 1 subsections.\n"
                                    if sec_id
                                    in {
                                        "section-1-business-info",
                                        "section-1-certifications",
                                        "section-1-insurance",
                                    }
                                    else (
                                        "Lead with zö brand voice from the Brand Voice KB. "
                                        "Do NOT dump every company fact — Who We Are is essence + promise, not a fact sheet.\n"
                                        if sec_id == "section-1-who-we-are"
                                        else "Be thorough and detailed. Include every fact you find in the KB.\n"
                                    )
                                )
                            )
                            + "Return ONE complete JSON object — no markdown fences.\n"
                            + 'Return JSON: {"content": "full detailed content", "kbRefs": ["source1", ...]}'
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Voice:\n{voice}\n\n"
                            f"{_section_kb_user_content(state, sec_id)}"
                        ),
                    },
                ],
                max_tokens=16000,
                # Who We Are needs creative range; factual subsections stay cold.
                temperature=0.55 if sec_id == "section-1-who-we-are" else 0.0,
            )
        except LlmError as exc:
            logger.warning(
                "Legacy Section 1 %s failed (%s); VERIFY stub (no retry)",
                sec_id,
                str(exc)[:160],
            )
            raw = {
                "content": f"[VERIFY: complete {sec_title} from KB — generation interrupted]",
                "kbRefs": section_kb_sources,
            }
        content = _sanitize_content(raw.get("content", "").strip())
        if sec_id in {"section-1-who-we-are", "section-1-business-info"}:
            content = enforce_agency_tenure(content)
        if sec_id == "section-1-who-we-are":
            content = _normalize_who_we_are_markdown(content)
            content = _trim_to_max_words(content, 200)
            content = _normalize_who_we_are_markdown(content)
            if not content.strip():
                logger.warning("Who We Are empty after one call — VERIFY stub (no retry)")
                content = (
                    "[VERIFY: complete Who We Are from Brand Voice + CompanyTruth — "
                    "empty model output]"
                )
        section = _section_payload(
            section_id=sec_id,
            title=sec_title,
            mode=mode,
            word_target=word_tgt,
            page_limit=state.get("page_limit"),
            page_ratio=ratio,
            designer_note_default=f"Section 1 subsection: {sec_title}. Pull from master template layout.",
            raw={"content": content, "kbRefs": raw.get("kbRefs") or []},
            kb_sources=section_kb_sources,
        )
        
        # Validate section before appending
        if not isinstance(section, dict):
            logger.error(f"❌ Section {sec_title} is not a dict: {type(section)}")
            continue
        if "id" not in section or "title" not in section:
            logger.error(f"❌ Section {sec_title} missing required fields")
            continue
            
        new_sections.append(section)
        await _emit_partial(state, [*existing, *new_sections])

    return {"sections": [*existing, *new_sections]}


DEFAULT_KEY_BIO_MEMBERS: tuple[str, ...] = ()


def _normalize_selected_bio_members(raw_members: list[Any]) -> list[str]:
    """Skill-based normalization — dedupe, max 5, no mandatory names."""
    return normalize_selected_members(raw_members, max_members=5)


async def _build_section_2(state: SectionsGraphState) -> dict[str, Any]:
    """Skill-based team selection + JIT bio fetch per selected member."""
    if state.get("skip_section_2"):
        logger.info("Section 2 already complete — skipping regeneration")
        return {}

    existing = state.get("sections") or []
    roster_text = state.get("kb_master_roster") or ""
    if not roster_text.strip():
        roster_text, roster_sources = await proposal_knowledge_base_tools.fetch_master_team_roster(
            rfp_client=state["rfp_client"],
            rfp_sector=state["rfp_sector"],
            rfp_context=state["rfp_context"],
        )
    else:
        roster_sources = state.get("kb_master_roster_sources") or []

    # Honor explicit Key Persona picks even on the legacy (non-CQ) path.
    selected_personas = await _selected_key_personas_for_rfp(state.get("rfp_id"))
    if selected_personas:
        members = normalize_selected_members(
            [str(p.get("name") or "") for p in selected_personas],
            max_members=max(MAX_TEAM_SIZE, len(selected_personas)),
            dedupe_by_last_name=False,
        )
        logger.info(
            "Legacy Section 2: using %d user-selected Key Personas (skipping LLM roster pick)",
            len(members),
        )
    else:
        context_block = state.get("proposal_context") or {}
        try:
            selection, _ = await llm.chat_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are the Team Selection Agent for zö agency Section 2.\n"
                            "Determine required roles from the RFP, then select EXACTLY 5 team members "
                            "from the Master Team Roster whose skills match this solicitation.\n"
                            "Compact JSON only — no markdown fences.\n\n"
                            "STRICT RULES:\n"
                            "- Skill-based selection only — NO mandatory names.\n"
                            "- ONLY select people named in the Master Team Roster.\n"
                            "- Maximum 5 people. Each must have a DISTINCT role.\n"
                            "- Do NOT select the same person twice under different spellings.\n"
                            'Return JSON: {"members": ["Name 1", "Name 2", "Name 3", "Name 4", "Name 5"], '
                            '"requiredRoles": ["role 1", "role 2"]}'
                        )
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Proposal context:\n{context_block}\n\n"
                            f"RFP context:\n{state['rfp_context'][:15000]}\n\n"
                            f"Master Team Roster ({proposal_knowledge_base_tools.MASTER_TEAM_ROSTER_DOC}):\n"
                            f"{roster_text}"
                        )
                    }
                ],
                max_tokens=1536,
                temperature=0.0,
                tier="light",
            )
        except LlmError as exc:
            logger.warning(
                "Legacy team selection failed (%s); empty members (no retry)",
                str(exc)[:160],
            )
            selection = {}
        raw_members = selection.get("members")
        members = _normalize_selected_bio_members(
            raw_members if isinstance(raw_members, list) else []
        )


    from app.services.proposal_bio_stub import (
        rfp_requires_inline_bios,
        stub_from_extraction,
    )

    role_by_name: dict[str, str] = {}
    for persona in selected_personas or []:
        name = str(persona.get("name") or "").strip()
        if name:
            role_by_name[name] = str(
                persona.get("title") or persona.get("role") or ""
            ).strip()

    inline_required = rfp_requires_inline_bios(state.get("rfp_context") or "")
    new_sections: list[dict[str, Any]] = []
    kb_sources = state.get("kb_bio_sources") or []

    for i, member in enumerate(members, 1):
        safe_id = member.lower().replace(" ", "-").replace("'", "")
        sec_id = f"section-2-bio-{safe_id}"
        sec_title = f"2.{i} — {member}"
        _log_section_generate_next(state, section_id=sec_id, title=sec_title)

        logger.info(
            "Building bio stub for %s (legacy path, inline_required=%s)",
            member,
            inline_required,
        )
        kb_text_for_extraction, bio_sources, kb_available, pdf_name = (
            await _bio_stub_kb_inputs(member, inline_required=inline_required)
        )
        if inline_required and not kb_available:
            logger.warning(
                "No 04_Bio content for %s — stub with Ella MANUAL FILL",
                member,
            )

        extracted: dict[str, Any] = {}
        if inline_required and kb_available:
            extracted = await _extract_member_bio_facts(member, kb_text_for_extraction)

        content = stub_from_extraction(
            member=member,
            role=role_by_name.get(member, ""),
            pdf_filename=pdf_name,
            kb_text=kb_text_for_extraction if inline_required else "",
            kb_available=kb_available,
            inline_required=inline_required,
            extracted=extracted,
        )
        content = _apply_verified_corrections(
            _sanitize_content(content),
            rfp_client=state.get("rfp_client", ""),
        )
        raw = {"content": content, "kbRefs": bio_sources or [pdf_name]}

        section = _section_payload(
            section_id=sec_id,
            title=sec_title,
            mode="select",
            word_target=120 if not inline_required else 220,
            page_limit=state.get("page_limit"),
            page_ratio=0.02,
            designer_note_default=(
                f"Insert approved bio PDF — {pdf_name}. Do not rewrite Key Accounts."
            ),
            raw=raw,
            kb_sources=bio_sources or kb_sources,
            extra_refs=[member],
        )
        
        # Validate section structure before appending
        if not isinstance(section, dict):
            logger.error(f"❌ Section for {member} is not a dict: {type(section)} = {section}")
            continue
        if "id" not in section or "title" not in section:
            logger.error(f"❌ Section for {member} missing required fields: {section.keys()}")
            continue
            
        new_sections.append(section)
        await _emit_partial(state, _merge_section_list(existing, new_sections))

    return {
        "sections": _merge_section_list(existing, new_sections),
        "kb_master_roster": roster_text,
        "kb_master_roster_sources": roster_sources,
    }


async def _build_section_3(state: SectionsGraphState) -> dict[str, Any]:
    """RFP-capability fit selection + JIT retrieval per study (legacy S1–3 path)."""
    if state.get("skip_section_3"):
        logger.info("Section 3 already complete — skipping regeneration")
        return {}

    existing = state.get("sections") or []
    rfp_client = state.get("rfp_client", "")

    case_corpus = state.get("kb_case_studies") or ""
    case_sources = state.get("kb_case_sources") or []

    # Use prefetched case studies from Go/No-Go when available
    prefetched = state.get("prefetched_case_studies") or {}

    if not case_corpus.strip() and prefetched.get("corpus"):
        case_corpus = prefetched["corpus"]
        case_sources = prefetched.get("sources", [])
        logger.info("Section 3: using Go/No-Go prefetched case study corpus (%d chars)", len(case_corpus))

    if not case_corpus.strip():
        case_corpus, case_sources = await proposal_knowledge_base_tools.fetch_case_study_candidates_jit(
            rfp_client=rfp_client,
            rfp_sector=state["rfp_sector"],
            rfp_context=state["rfp_context"],
        )

    context_block = state.get("proposal_context") or {}
    services_requested: list[str] = []
    if isinstance(context_block, dict):
        raw_services = context_block.get("servicesRequested") or context_block.get("services_requested") or []
        if isinstance(raw_services, list):
            services_requested = [str(s).strip() for s in raw_services if str(s).strip()]

    # Canonical resolver — same logic as Match studies (strong → closest, RFP min).
    from app.services.proposal_case_study_match import resolve_case_study_selection
    from app.services.proposal_rfp_compulsory_content import (
        required_case_study_minimum,
        stated_case_study_minimum,
    )

    rfp_ctx = state.get("rfp_context") or ""
    required_studies = await required_case_study_minimum(rfp_ctx)
    rfp_stated_studies = await stated_case_study_minimum(rfp_ctx)
    study_cap = max(5, required_studies)

    selected_studies: list[str] = []
    fit_report = None
    if prefetched.get("titles"):
        selected_studies = list(prefetched["titles"])
        logger.info(
            "Section 3: using %d prefetched case study titles",
            len(selected_studies),
        )

    if not selected_studies or len(selected_studies) < required_studies:
        try:
            resolved = await resolve_case_study_selection(
                rfp_client=rfp_client,
                rfp_sector=state.get("rfp_sector") or "",
                rfp_title=state.get("rfp_title") or "",
                rfp_id=state.get("rfp_id"),
                rfp_context=state.get("rfp_context") or "",
                services_requested=services_requested,
                min_count=required_studies,
                max_count=study_cap,
                # Stubs cite the asset; nothing here reads the full narrative.
                fetch_full_text=False,
            )
            selected_studies = resolved.titles
            fit_report = resolved.fit_report
            if selected_studies:
                logger.info(
                    "Section 3 resolver: %d studies (%s) — %s",
                    len(selected_studies),
                    resolved.match_quality,
                    [resolved.display_names.get(t, t) for t in selected_studies],
                )
        except ProposalGenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Section 3 case study resolver failed (%s)", str(exc)[:160])

    selected_studies = list(dict.fromkeys(s.strip() for s in selected_studies if s.strip()))[
        :study_cap
    ]

    from app.services.proposal_case_study_eligibility import (
        is_eligible_section3_case_study_title,
    )

    selected_studies = [
        s
        for s in selected_studies
        if is_eligible_section3_case_study_title(
            s,
            rfp_title=str(state.get("rfp_title") or ""),
            rfp_sector=str(state.get("rfp_sector") or ""),
        )
    ][:study_cap]

    new_sections: list[dict[str, Any]] = []
    kb_sources = case_sources

    from app.services.proposal_case_study_fit import case_study_display_name
    from app.services.proposal_case_study_stub import (
        case_study_asset_filename,
        format_case_study_stub_content,
    )

    # Why each study fits — read off the fit report the resolver already built.
    relevance_by_title = _case_study_relevance_map(fit_report)

    for i, study in enumerate(selected_studies, 1):
        safe_id = study.lower()[:40].replace(" ", "-").replace("/", "-")
        sec_id = f"section-3-work-{i:02d}-{safe_id}"
        sec_title = _case_study_display_title(i, study)

        # Option B: designer note, not a redrafted narrative. No LLM call, so no
        # invented metrics or client quotes to scrub back out afterwards.
        content = format_case_study_stub_content(
            display_name=case_study_display_name(study),
            asset_filename=case_study_asset_filename(study),
            relevance=relevance_by_title.get(study.casefold(), ""),
        )
        section = _section_payload(
            section_id=sec_id,
            title=sec_title,
            mode="select",
            word_target=40,
            page_limit=state.get("page_limit"),
            page_ratio=0.03,
            designer_note_default=f"Our Work example: {study}.",
            raw={"content": content, "kbRefs": [study]},
            kb_sources=kb_sources,
            extra_refs=[study],
        )

        # Validate section before appending
        if not isinstance(section, dict):
            logger.error(f"❌ Work example {study} section is not a dict: {type(section)}")
            continue
        if "id" not in section or "title" not in section:
            logger.error(f"❌ Work example {study} missing required fields")
            continue
            
        new_sections.append(section)
        await _emit_partial(state, _merge_section_list(existing, new_sections))

    if rfp_stated_studies and len(new_sections) < rfp_stated_studies:
        from app.services.proposal_rfp_compulsory_content import (
            CompulsoryContentAsk,
            CompulsoryShortfall,
            shortfall_stub_section,
        )

        stub = shortfall_stub_section(
            CompulsoryShortfall(
                ask=CompulsoryContentAsk(
                    kind="case_studies",
                    minimum=rfp_stated_studies,
                    rfp_quote="",
                    pass_fail=True,
                ),
                found=len(new_sections),
                message="",
            )
        )
        logger.warning(
            "Section 3: RFP requires %d case studies; drafted %d — gap stub added",
            rfp_stated_studies,
            len(new_sections),
        )
        new_sections.append(
            _section_payload(
                section_id=str(stub["id"]),
                title=str(stub["title"]),
                mode="write",
                word_target=80,
                page_limit=state.get("page_limit"),
                page_ratio=0.02,
                designer_note_default="RFP minimum similar-work count — do not invent a case study.",
                raw={"content": stub["content"], "kbRefs": []},
                kb_sources=kb_sources,
            )
        )

    return {
        "sections": _merge_section_list(existing, new_sections),
        "kb_case_studies": case_corpus,
        "kb_case_sources": case_sources,
    }


def _section_payload(
    *,
    section_id: str,
    title: str,
    mode: str,
    word_target: int,
    page_limit: int | None,
    page_ratio: float,
    designer_note_default: str,
    raw: dict[str, Any],
    kb_sources: list[str],
    extra_refs: list[str] | None = None,
) -> dict[str, Any]:
    content = _apply_verified_corrections(
        enforce_narrative_voice(
            str(raw.get("content", "")).strip(),
            section_id=section_id,
            title=title,
            register="narrative",
        )
    )
    designer = str(raw.get("designerNote") or designer_note_default).strip()
    # KB references removed - not included in proposals
    
    budget = page_limit or 30
    return {
        "id": section_id,
        "title": title,
        "pageLimit": max(1, int(budget * page_ratio)),
        "wordTarget": word_target,
        "required": True,
        "custom": False,
        "source": "template",
        "mode": mode,
        "content": content,
        "designerNote": designer,
        "status": "generated" if content else "outline",
        "kbRefs": [],
    }


def _build_graph() -> Any:
    cq_mode = settings.use_company_qualification_s1

    def _wrap(node_id: str, handler: Any) -> Any:
        return with_agent_logging(node_id, handler, cq_mode=cq_mode)

    graph = StateGraph(SectionsGraphState)
    graph.add_node("synthesize_proposal_voice", _wrap("synthesize_proposal_voice", _synthesize_proposal_voice))
    graph.add_node("build_section_2", _wrap("build_section_2", _build_section_2))
    graph.add_node("build_section_3", _wrap("build_section_3", _build_section_3))

    if cq_mode:
        graph.add_node("fetch_company_truth", _wrap("fetch_company_truth", _fetch_company_truth))
        graph.add_node("fetch_proposal_context", _wrap("fetch_proposal_context", _fetch_proposal_context))
        graph.add_node("prioritize_capabilities", _wrap("prioritize_capabilities", _prioritize_capabilities))
        graph.add_node("plan_section_1", _wrap("plan_section_1", _plan_section_1))
        graph.add_node("build_section_1_cq", _wrap("build_section_1_cq", _build_section_1_cq))
        graph.add_node("select_team", _wrap("select_team", _select_team))
        graph.add_node("build_bios", _wrap("build_bios", _build_bios))
        graph.add_node("select_evidence", _wrap("select_evidence", _select_evidence))
        graph.add_node("build_case_studies", _wrap("build_case_studies", _build_case_studies))
        graph.add_node("join_sections", _wrap("join_sections", _join_sections))
        graph.add_node(
            "validate_sections_editorial",
            _wrap("validate_sections_editorial", _validate_sections_editorial),
        )

        # Fully sequential — one node finishes before the next starts.
        # No parallel fan-out (avoids Fireworks 429 and keeps UI order S1→S2→S3).
        graph.add_edge(START, "fetch_proposal_context")
        graph.add_edge("fetch_proposal_context", "fetch_company_truth")
        graph.add_edge("fetch_company_truth", "prioritize_capabilities")
        graph.add_edge("prioritize_capabilities", "plan_section_1")
        graph.add_edge("plan_section_1", "build_section_1_cq")
        graph.add_edge("build_section_1_cq", "select_team")
        graph.add_edge("select_team", "build_bios")
        graph.add_edge("build_bios", "select_evidence")
        graph.add_edge("select_evidence", "build_case_studies")
        graph.add_edge("build_case_studies", "join_sections")
        graph.add_edge("join_sections", "validate_sections_editorial")
        graph.add_edge("validate_sections_editorial", END)
    else:
        graph.add_node("fetch_knowledge_base", _wrap("fetch_knowledge_base", _fetch_knowledge_base))
        graph.add_node("build_section_1", _wrap("build_section_1", _build_section_1))
        graph.add_edge(START, "fetch_knowledge_base")
        graph.add_edge("fetch_knowledge_base", "synthesize_proposal_voice")
        graph.add_edge("synthesize_proposal_voice", "build_section_1")
        graph.add_edge("build_section_1", "build_section_2")
        graph.add_edge("build_section_2", "build_section_3")
        graph.add_edge("build_section_3", END)

    return graph.compile()


_SECTIONS_GRAPH = _build_graph()


async def run_sections_1_3_graph(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
    page_limit: int | None,
    on_sections_partial: SectionsPartialCallback | None = None,
    existing_sections: list[ProposalSection] | None = None,
    skip_section_1: bool = False,
    skip_section_2: bool = False,
    skip_section_3: bool = False,
    manuscript_locks: dict[str, Any] | None = None,
    prefetched_case_studies: dict[str, Any] | None = None,
) -> tuple[list[ProposalSection], ProposalBrandVoice, str, Section1EditorialReview | None]:
    if not llm.is_configured():
        raise LlmError(
            "LLM not configured. Set OPENROUTER_API_KEY or FIREWORKS_API_KEY.",
            status_code=503,
        )

    preserved = _sections_to_state(existing_sections or [])
    stub_seed: SectionsGraphState = {
        "rfp_id": rfp_id,
        "page_limit": page_limit,
    }
    if settings.use_company_qualification_s1 and not skip_section_1:
        preserved = _merge_section_list(_section_1_stub_payloads(stub_seed), preserved)

    initial: SectionsGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "page_limit": page_limit,
        "sections": preserved,
        "skip_section_1": skip_section_1,
        "skip_section_2": skip_section_2,
        "skip_section_3": skip_section_3,
        "manuscript_locks": manuscript_locks or {},
        "prefetched_case_studies": prefetched_case_studies or {},
    }

    cq_mode = settings.use_company_qualification_s1
    log_path = get_langgraph_log_path()
    log_pipeline_start(rfp_id=rfp_id, rfp_client=rfp_client, cq_mode=cq_mode)
    logger.info("LangGraph plot log → %s", log_path)

    pipeline_started = time.perf_counter()
    final: dict[str, Any] = dict(initial)
    token = _partial_cb_var.set(on_sections_partial)
    try:
        # stream_mode="updates" yields each node's RETURN value, not the reduced
        # graph state. Parallel S1/S2/S3 each return only their own sections —
        # naive dict.update would keep whichever track finished last and wipe
        # the other two. Merge sections the same way the LangGraph reducer does.
        async for event in _SECTIONS_GRAPH.astream(initial):
            for node_name, update in event.items():
                update_keys = (
                    sorted(update.keys())
                    if isinstance(update, dict)
                    else [type(update).__name__]
                )
                section_ids: list[str] = []
                if isinstance(update, dict) and isinstance(update.get("sections"), list):
                    section_ids = [
                        str(s.get("id"))
                        for s in update["sections"]
                        if isinstance(s, dict) and s.get("id")
                    ]
                log_graph_event(
                    f"[astream] node={node_name} keys={update_keys}"
                    + (f" sections={section_ids}" if section_ids else "")
                )
                if isinstance(update, dict):
                    sections_update = update.get("sections")
                    for key, value in update.items():
                        if key == "sections":
                            continue
                        final[key] = value
                    if isinstance(sections_update, list):
                        final["sections"] = _merge_section_list(
                            final.get("sections") or [],
                            sections_update,
                        )
                        merged_ids = [
                            str(s.get("id"))
                            for s in (final.get("sections") or [])
                            if isinstance(s, dict) and s.get("id")
                        ]
                        log_graph_event(
                            f"[astream] merged sections after {node_name}: "
                            f"{len(merged_ids)} → {merged_ids}"
                        )
                if node_name not in (
                    "build_section_1",
                    "build_section_1_cq",
                    "build_bios",
                    "build_case_studies",
                ):
                    continue
                if not on_sections_partial:
                    continue
                raw_sections = final.get("sections") or []

                # Filter invalid entries
                valid_raw_sections = []
                for item in raw_sections:
                    if isinstance(item, dict) and "id" in item and "title" in item:
                        valid_raw_sections.append(item)
                    else:
                        logger.warning(f"Skipping invalid partial section: {type(item)}")

                sections = [ProposalSection.model_validate(item) for item in valid_raw_sections]
                brand_voice_raw = final.get("brand_voice")
                brand_voice = (
                    ProposalBrandVoice.model_validate(brand_voice_raw)
                    if isinstance(brand_voice_raw, dict)
                    else None
                )
                provider = str(final.get("provider") or _provider_name())
                await on_sections_partial(sections, provider, brand_voice)
    finally:
        _partial_cb_var.reset(token)

    if final.get("error"):
        raise LlmError(str(final["error"]), status_code=502)

    raw_sections = final.get("sections") or []
    
    # Filter out any invalid entries before validation
    valid_raw_sections = []
    for idx, item in enumerate(raw_sections):
        if not isinstance(item, dict):
            logger.error(f"❌ Skipping invalid section at index {idx}: {type(item)} = {str(item)[:100]}")
            continue
        if "id" not in item or "title" not in item:
            logger.error(f"❌ Skipping section at index {idx} missing required fields: {item.get('title', 'no title')}")
            continue
        valid_raw_sections.append(item)
    
    sections = [ProposalSection.model_validate(item) for item in valid_raw_sections]
    brand_voice = ProposalBrandVoice.model_validate(final.get("brand_voice") or {})
    provider = str(final.get("provider") or _provider_name())

    editorial_raw = final.get("section1_editorial_review")
    editorial_review = (
        Section1EditorialReview.model_validate(editorial_raw)
        if isinstance(editorial_raw, dict)
        else None
    )

    logger.info(
        "LangGraph sections 1–3 complete for %s: %d sections, tone=%r, provider=%s",
        rfp_id,
        len(sections),
        brand_voice.tone,
        provider,
    )
    log_pipeline_complete(
        rfp_id=rfp_id,
        section_count=len(sections),
        provider=provider,
        elapsed_s=time.perf_counter() - pipeline_started,
        section_ids=[s.id for s in sections],
    )
    return sections, brand_voice, provider, editorial_review
