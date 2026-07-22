"""Per-section improve: refined KB re-query + targeted re-draft from user chat feedback."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import EvidenceItem, ProposalDraft, ProposalResearchCache, ProposalSection, RfpSectionMap
from app.models.rfp import RfpRecord
from app.services import llm, proposal_knowledge_base_tools, supermemory
from app.services.go_no_go_service import RfpContentInfo, _assess_rfp_content, _build_rfp_context
from app.services.llm import LlmError
from app.services.proposal_common import ProposalError, aload_rfp_for_proposal
from app.services.proposal_presubmit_autofix import STATIC_SECTION_IDS
from app.services.proposal_langchain import _provider_name
from app.services.proposal_section_quality import (
    prior_content_for_redraft,
    redraft_is_inadequate,
    word_count,
)
from app.services.proposal_brand_voice import (
    classify_section_register,
    format_brand_voice_block,
    resolve_voice_context,
)
from app.services.proposal_loss_lessons import format_avoidance_block
from app.services.proposal_voice_enforcement import enforce_narrative_voice
from app.services.proposal_draft_snapshots import push_after_section_edit_snapshot
from app.services.proposal_repository import (
    aget_proposal_draft,
    aget_research_cache,
    asave_proposal_draft,
    asave_research_cache,
)
from app.services.proposal_manual_flags import (
    VERIFY_TAG_RE,
    _EMAIL_RE,
    _PHONE_RE,
    _replace_verify_tags_from_blob,
    _section_corpus_blob,
)
from app.services.proposal_evidence_corpus import merge_hits_into_corpus
from app.services.proposal_retrieval_graph import (
    EXCERPT_MAX_CHARS,
    SEARCH_LIMIT,
    _hit_excerpt,
    _hit_key,
    _hit_label,
)
from app.services.proposal_budget_playbook import (
    BUDGET_EXPLAIN_ADVISORY_RULES,
    budget_playbook_prompt_block,
    refuse_noncompliant_budget_edit,
    should_apply_budget_playbook,
    user_asks_budget_explanation,
)

logger = logging.getLogger(__name__)

SECTION_CHAT_ADVISORY_PROMPT = """You are a zö agency proposal editor assistant in a chat with the user.

You may receive the FULL proposal manuscript digest plus one focus section and optionally a highlighted excerpt.

Rules:
1. Answer from the RFP requirements and the proposal as a whole — do not invent compliance facts.
2. If the user asks about another section or the whole draft, use the manuscript digest.
3. If the user asks whether something meets the RFP, cite specific RFP asks and gaps.
4. You may disagree or push back when their request would weaken compliance or accuracy.
5. Do NOT rewrite the section in this turn — explain what you would change and why, or answer the question.
6. Be concise (2–6 short paragraphs max). Use **bold** for key RFP requirements.
7. If they need an edit, tell them to ask explicitly (e.g. "update 1.1 to…" or use Revise content on an excerpt).
8. Budget/pricing/fees: follow the pricing playbook when provided — refuse invented numbers and reverse-engineered totals (option C); flag out-of-guide scope with [PRICING FLAG: … — Sonja review required].

Return ONLY JSON: {"reply": "markdown message for the chat"}"""

_EDIT_INTENT_RE = re.compile(
    r"\b("
    r"change|fix|update|rewrite|revise|edit|improve|shorten|lengthen|add|remove|replace|fill|"
    r"make it|make this|patch|insert|delete|correct|align"
    r")\b",
    re.I,
)


def _wants_section_edit(user_message: str) -> bool:
    text = user_message.strip()
    if not text:
        return False
    if _EDIT_INTENT_RE.search(text):
        return True
    if text.endswith("?"):
        return False
    lower = text.casefold()
    if re.search(
        r"\b(why|explain|what does|is this|does this|compliant|requirement|argue|push back|should we)\b",
        lower,
    ):
        return False
    return True


def _compose_chat_user_message(
    user_message: str,
    conversation_history: list[dict[str, str]] | None,
) -> str:
    if not conversation_history:
        return user_message
    lines = ["Prior conversation (context only — address the latest message):"]
    for turn in conversation_history[-10:]:
        role = turn.get("role", "user")
        label = "User" if role == "user" else "Assistant"
        content = (turn.get("content") or "").strip()
        if content:
            lines.append(f"{label}: {content[:800]}")
    lines.append(f"\nLatest user message:\n{user_message.strip()}")
    return "\n".join(lines)


def _query_focus_message(
    user_message: str,
    *,
    section: ProposalSection,
    requirements_block: str,
) -> str:
    """Crisp signal for KB query planning — gaps + latest ask, not full chat dump."""
    gaps = _gap_fields_from_text(section.content or "")
    parts = [
        f"Latest edit request: {user_message.strip()}",
        f"Section: {section.title}",
    ]
    if gaps:
        parts.append(
            "Fill these [VERIFY] gaps with KB facts (one query each):\n"
            + "\n".join(f"- {g}" for g in gaps[:12])
        )
    if requirements_block.strip():
        parts.append(requirements_block[:2500])
    return "\n\n".join(parts)


def _seed_gap_queries(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    prior_queries: list[str],
) -> list[str]:
    used = {q.strip().lower() for q in prior_queries}
    seeded: list[str] = []
    for field in _gap_fields_from_text(section.content or "")[:6]:
        q = (
            f"zö agency {field} 01 companyfacts 02 master template "
            f"{rfp.client} {section.title}"
        )[:240]
        key = q.lower()
        if key not in used:
            seeded.append(q)
            used.add(key)
    return seeded


def _rfp_section_requirements_block(
    research: ProposalResearchCache | None,
    section_id: str,
) -> str:
    if not research or not research.rfp_sections:
        return ""
    for sec in research.rfp_sections:
        if sec.id == section_id:
            parts = [f"Section map — {sec.title or section_id}"]
            if sec.requirements:
                parts.append("Requirements:\n" + "\n".join(f"- {r}" for r in sec.requirements[:24]))
            if sec.uncovered_requirements:
                parts.append(
                    "Uncovered:\n"
                    + "\n".join(f"- {r}" for r in sec.uncovered_requirements[:12])
                )
            if sec.evaluation_weight:
                parts.append(f"Evaluation weight hint: {sec.evaluation_weight}")
            if sec.page_limit:
                parts.append(f"Page limit hint: {sec.page_limit}")
            return "\n".join(parts)
    return ""


def _manuscript_digest(draft: ProposalDraft, *, max_chars: int = 12000) -> str:
    """Compact full-proposal context for chat (TOC + section snippets)."""
    lines: list[str] = ["FULL PROPOSAL MANUSCRIPT (for cross-section context):"]
    used = 0
    for section in draft.sections:
        title = section.title or section.id
        body = (section.content or "").strip()
        if not body:
            block = f"\n### {title}\n(empty)\n"
        else:
            snippet = body[:900] + ("…" if len(body) > 900 else "")
            block = f"\n### {title}\n{snippet}\n"
        if used + len(block) > max_chars:
            lines.append("\n…(additional sections omitted)")
            break
        lines.append(block)
        used += len(block)
    return "".join(lines)


def _resolve_section_from_message(
    draft: ProposalDraft,
    user_message: str,
    default_section_id: str,
) -> ProposalSection | None:
    default = _find_draft_section(draft, default_section_id)
    text = user_message.strip()
    if not text:
        return default
    lower = text.casefold()
    ranked = sorted(
        draft.sections,
        key=lambda s: len(s.title or ""),
        reverse=True,
    )
    for section in ranked:
        title = (section.title or "").strip()
        if len(title) >= 4 and title.casefold() in lower:
            return section

    named_hits: list[ProposalSection] = []
    for section in ranked:
        title = (section.title or "").strip()
        if "—" in title:
            name = title.split("—", 1)[-1].strip()
        elif "–" in title:
            name = title.split("–", 1)[-1].strip()
        else:
            name = ""
        name = re.sub(r"^\d+\.\d+\s*[—\-–:]\s*", "", name).strip()
        if len(name) >= 4 and name.casefold() in lower:
            named_hits.append(section)
    if len(named_hits) == 1:
        return named_hits[0]
    if len(named_hits) > 1:
        instead = re.search(
            r"\b(?:instead\s+of|replace|remove|swap\s+out)\s+([^,.]+?)(?:\s+bio|\s+resume|\s+with|\s+for|$)",
            text,
            re.I,
        )
        if instead:
            needle = instead.group(1).strip().casefold()
            for section in named_hits:
                title = section.title or ""
                name = title.split("—", 1)[-1].strip() if "—" in title else title
                if needle and needle in name.casefold():
                    return section
        return named_hits[0]

    num_match = re.search(
        r"\b(?:section\s*)?(\d+\.\d+)\b",
        lower,
    )
    if num_match:
        num = num_match.group(1)
        for section in draft.sections:
            t = (section.title or "").casefold()
            if t.startswith(f"{num} ") or t.startswith(num):
                return section

    if re.search(r"\b(bio|bios|resume|resumes|team\s*bios?|team\s*member)\b", text, re.I):
        bios = [
            s
            for s in draft.sections
            if s.id.startswith("section-2-bio-") and s.id != "section-2-bio-placeholder"
        ]
        if bios:
            if default and any(b.id == default.id for b in bios):
                return default
            return bios[-1]

    return default


async def _section_chat_advisory_reply(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    rfp_context: str,
    user_message: str,
    conversation_history: list[dict[str, str]] | None,
    selection_text: str | None,
    requirements_block: str,
    manuscript_digest: str = "",
    research: ProposalResearchCache | None = None,
) -> str:
    excerpt = (selection_text or "").strip()
    excerpt_block = f"\n\nHighlighted excerpt:\n\"{excerpt[:2000]}\"\n" if excerpt else ""
    history_block = ""
    if conversation_history:
        history_block = "\n\nRecent chat:\n" + "\n".join(
            f"{'User' if t.get('role') == 'user' else 'Assistant'}: {(t.get('content') or '')[:400]}"
            for t in conversation_history[-6:]
        )
    guide_block = ""
    if should_apply_budget_playbook(section, user_message):
        from app.services.proposal_pricing_service import fetch_pricing_guide_context

        stage_two = ""
        if research and research.rfp_sections:
            stage_two = "\n".join(
                f"{s.title}: {', '.join((s.requirements or [])[:5])}"
                for s in research.rfp_sections[:12]
            )
        guide_text, guide_sources = await fetch_pricing_guide_context(
            rfp,
            stage_two=stage_two,
            focus_hint=user_message[:300],
        )
        src_note = ", ".join(guide_sources[:8]) if guide_sources else "(no sources)"
        guide_block = (
            f"\n\n=== 00_Guide_Pricing (Supermemory — cite menu ids from here) ===\n"
            f"{guide_text[:20000]}\n\nKB sources: {src_note}\n"
        )
    prompt = (
        f"RFP: {rfp.title} — {rfp.client}\n\n"
        f"RFP context (rescan):\n{rfp_context[:6000]}\n\n"
        f"{requirements_block}\n\n"
        f"{manuscript_digest[:12000]}\n\n"
        f"{guide_block}"
        f"Focus section: {section.title}\n\n"
        f"Focus section draft:\n{(section.content or '')[:8000]}"
        f"{excerpt_block}"
        f"{history_block}\n\n"
        f"User message:\n{user_message.strip()}"
    )
    system_prompt = SECTION_CHAT_ADVISORY_PROMPT
    if should_apply_budget_playbook(section, user_message):
        full_detail = user_asks_budget_explanation(user_message)
        system_prompt = (
            f"{system_prompt}\n\n"
            f"{budget_playbook_prompt_block(research=research, full_budget_detail=full_detail)}"
        )
        if full_detail:
            system_prompt = f"{system_prompt}\n\n{BUDGET_EXPLAIN_ADVISORY_RULES}"
    max_tokens = 2000 if user_asks_budget_explanation(user_message) else 1200
    raw, _ = await llm.chat_json(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.25 if user_asks_budget_explanation(user_message) else 0.35,
    )
    reply = str(raw.get("reply", "")).strip()
    return reply or (
        "I reviewed the RFP context for this section — ask me to change specific text when you are ready."
    )

REFINE_QUERIES_PROMPT = """Plan 5-6 NEW Supermemory search queries to improve ONE proposal section.
Prior queries failed or returned insufficient evidence. User feedback describes what is wrong or missing.

Rules:
- Queries must be MORE SPECIFIC and DIFFERENT from all prior queries (never repeat or lightly rephrase).
- Use document-type hints where relevant: 01 companyfacts, 02 master template, 03_CS case studies, 04 bio, certifications, org chart, references.
- Target the exact gaps: firm legal name, Bend address, phone/email contacts, employee count, philosophy, tourism/DMO experience, org structure, case studies, fees, etc.
- Include "zö agency" + field name + doc hint in each query. Add client name and sector when relevant.
- If [VERIFY: ...] fields or RFP requirements are listed, dedicate at least one query per missing field.

Return ONLY JSON: {"queries": ["detailed query 1", "detailed query 2", "detailed query 3", "detailed query 4", "detailed query 5"]}"""

SECTION_REDRAFT_PROMPT = """Rewrite ONE zö agency proposal section based on user feedback and evidence.

Rules:
1. Directly address the user's edit request.
2. Use ONLY facts from the evidence corpus. Do NOT put citation markers like [E1] or [E2] in the prose — write clean client-facing sentences.
3. Improve substantially on the previous draft — never return the same placeholder or [VERIFY] block if evidence now supports the content.
4. Use [VERIFY: ...] only for requirements still missing from evidence.
5. Follow the REGISTER block: narrative sections use first person we/our — NEVER "The Vendor", "The Offeror", or third-person agency distance.
6. PRESERVE the full BRAND VOICE block — zö core voice + RFP adaptation. User edits must NOT flatten tone into generic consultant/corporate prose.
7. Keep rhythm, confidence, warmth, and client-centered framing from the previous draft unless the user explicitly requests a tone change.
8. Apply WRITING AVOIDANCES from lost bids when provided — do not repeat past loss patterns.
9. Write submission-ready prose in zö's voice.

Return ONLY JSON:
{
  "content": "full section prose",
  "kbRefs": ["E1", "E3"],
  "designerNote": null
}"""

SELECTION_EDIT_PROMPT = """You revise ONE selected excerpt inside a zö agency proposal section.

The user highlighted a span of text. You receive the FULL section for context (voice, headings, flow).
Return ONLY the replacement text for that span — not the full section.

Rules:
1. Change ONLY what the user asked for in the selected excerpt.
2. Match the surrounding section's voice, rhythm, and register (first person we/our in narrative sections).
3. Preserve BRAND VOICE from the voice block — warm, proof-led, client-centered.
4. Use ONLY facts from KB excerpts when provided. Use [VERIFY: specific field] if a fact is still missing.
5. Do NOT invent reference contacts, phone numbers, or metrics.
6. Keep markdown structure inside the excerpt (lists, table rows) if the selection had them.
7. Return ONLY JSON: {"replacement": "revised excerpt text only"}
8. Budget/pricing excerpts: NEVER change agency revenue or commission lines to $0 — use commission rate × pass-through or canonical fee from section context; if unknown use [VERIFY: Sonja confirm commission rate and annual media estimate].
9. Do NOT reverse-engineer dollar amounts to hit a user-requested total — each line must trace to the Pricing Guide; suggest tier/scope changes instead (option C).
10. One-time setup/development lines must not be multiplied by 12 unless the excerpt is explicitly a monthly recurring service from the guide.
11. Reference excerpts: include name, title, phone, and email — never "contact on request" or deferral language.
12. PSA/compliance excerpts: add specific acknowledgment language when user asks — cover insurance, living wage, MacBride, Title VI, Chapter 63, audit rights as applicable.
13. NEVER shorten the excerpt. Preserve every paragraph, heading, list item, and sentence the user did not ask to change.
14. When the user asks to fill gaps, placeholders, or [VERIFY] tags: ONLY replace those tags with KB facts — do not rewrite or summarize the surrounding prose."""

SELECTION_KB_PLAN_PROMPT = """You plan a surgical edit to ONE highlighted excerpt inside a zö agency proposal section.

Read the user's instruction and the selected excerpt. Understand what they want changed.

Return ONLY JSON:
{
  "editorInstruction": "One clear instruction for the editor. If they want gaps/VERIFY tags filled, say to replace only those tags from KB and preserve every other sentence verbatim.",
  "kbQueries": ["2-5 targeted Supermemory queries for missing facts — use names, fields, and doc hints like 04 bio, 01 companyfacts"],
  "preserveFullExcerpt": true
}

Rules:
- preserveFullExcerpt must be true when the selection is long or the user wants gaps/placeholders filled — the editor must NOT shorten or summarize.
- kbQueries must target the specific missing facts in the excerpt, not repeat the user's chat message verbatim."""

STATIC_SECTION_REDRAFT_PROMPT = """Improve ONE static zö proposal section (company overview, team bios, or case studies).

Use ONLY the knowledge-base excerpts provided. For pull/select sections, include [DESIGNER NOTE: ...] where layout applies.
Address the user's feedback. Do not invent clients, metrics, addresses, phones, or emails.

NARRATIVE REGISTER: first person we/our — never "The Vendor" or third-person procurement language.
PRESERVE the full BRAND VOICE block — zö core voice + RFP adaptation are mandatory.
- Keep warm, confident, proof-led rhythm — not generic consultant prose.
- Prefer concrete facts from KB over vague claims.
- Fill [VERIFY: ...] tags when KB has the fact; otherwise keep a precise [VERIFY: ...] tag.
- Do not flatten the previous draft's voice unless the user explicitly asked for a tone change.
Apply WRITING AVOIDANCES when provided.

Return ONLY JSON:
{
  "content": "...",
  "kbRefs": ["source filenames"],
  "designerNote": "..."
}"""

_search_semaphore = asyncio.Semaphore(4)

_NEAR_FULL_SELECTION_RATIO = 0.85
_MIN_EXCERPT_WORDS_FOR_REGRESSION_GUARD = 40
_MAX_EXCERPT_WORD_LOSS_RATIO = 0.12


def _gap_fields_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    fields: list[str] = []
    for match in VERIFY_TAG_RE.finditer(text):
        field = match.group(1).strip()
        key = field.casefold()
        if key and key not in seen:
            seen.add(key)
            fields.append(field)
    return fields


def _draft_supplemental_blob(draft: ProposalDraft) -> str:
    """Reuse contact/firm facts already drafted in static sections — not hardcoded."""
    parts: list[str] = []
    for section in draft.sections:
        if section.id in STATIC_SECTION_IDS and (section.content or "").strip():
            parts.append(section.content[:8000])
    return "\n\n".join(parts)


def _selection_covers_most_of_section(content: str, start: int, end: int) -> bool:
    if not content:
        return False
    return (end - start) / max(len(content), 1) >= _NEAR_FULL_SELECTION_RATIO


def _selection_replacement_regressed(excerpt: str, replacement: str) -> bool:
    excerpt_words = word_count(excerpt)
    replacement_words = word_count(replacement)
    if excerpt_words < _MIN_EXCERPT_WORDS_FOR_REGRESSION_GUARD:
        return replacement_words < max(8, int(excerpt_words * 0.65))
    min_words = int(excerpt_words * (1 - _MAX_EXCERPT_WORD_LOSS_RATIO))
    return replacement_words < min_words


async def _plan_selection_edit(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    user_message: str,
    excerpt: str,
    full_content: str,
    selection_start: int,
    selection_end: int,
) -> tuple[str, list[str]]:
    """LLM understands user intent and plans KB queries + editor instruction."""
    near_full = _selection_covers_most_of_section(full_content, selection_start, selection_end)
    raw, _ = await llm.chat_json(
        [
            {"role": "system", "content": SELECTION_KB_PLAN_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Client: {rfp.client}\n"
                    f"Section: {section.title}\n"
                    f"User instruction:\n{user_message.strip()}\n\n"
                    f"Selected excerpt ({word_count(excerpt)} words, "
                    f"{'near-full section' if near_full else 'partial'}):\n"
                    f"\"\"\"{excerpt[:6000]}\"\"\"\n\n"
                    f"Full section length: {word_count(full_content)} words\n"
                    f"VERIFY tags in excerpt: {_gap_fields_from_text(excerpt) or '(none)'}"
                ),
            },
        ],
        max_tokens=1024,
        temperature=0.2,
    )
    editor_instruction = str(raw.get("editorInstruction") or user_message).strip()
    queries_raw = raw.get("kbQueries") or raw.get("queries") or []
    queries = [str(q).strip()[:240] for q in queries_raw if str(q).strip()][:5]
    if not queries:
        gap_hint = _gap_fields_from_text(excerpt)[:1]
        queries = [
            f"zö agency {section.title} {rfp.client} {gap_hint[0] if gap_hint else user_message}"[
                :240
            ],
        ]
    if near_full:
        editor_instruction = (
            f"{editor_instruction}\n\n"
            "CRITICAL: The user selected most or all of this section. Preserve ALL existing "
            "paragraphs, headings, and prose. Change ONLY what the instruction requires — never "
            "replace the section with a short summary or contact block."
        )
    return editor_instruction, queries


async def _fetch_kb_blob_for_selection(
    queries: list[str],
    *,
    evidence_blob: str = "",
    supplemental_blob: str = "",
) -> tuple[str, str]:
    """Return (llm_context_blob, contact_fact_blob). All KB reads via v4 search."""
    llm_parts: list[str] = []
    if evidence_blob.strip():
        llm_parts.append(evidence_blob)
    if supplemental_blob.strip():
        llm_parts.append(supplemental_blob)

    async def _hits_for_query(query: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        async with _search_semaphore:
            hybrid, chunks = await asyncio.gather(
                supermemory.search_hybrid(
                    query=query,
                    limit=SEARCH_LIMIT,
                    include_full_docs=True,
                    filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
                ),
                supermemory.search_document_chunks(
                    query=query,
                    limit=SEARCH_LIMIT,
                    filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
                ),
            )
            kb_filter = supermemory.is_knowledge_base_hit
            return (
                [h for h in hybrid if kb_filter(h)],
                [h for h in chunks if kb_filter(h)],
            )

    query_results = await asyncio.gather(*[_hits_for_query(q) for q in queries])
    hybrid_hits = supermemory.merge_search_hits([h for h, _ in query_results])
    chunk_hits = supermemory.merge_search_hits([c for _, c in query_results])

    chunk_fact_text = ""
    if chunk_hits:
        chunk_fact_text = await supermemory.fetch_hits_fact_text(
            chunk_hits,
            max_hits=12,
            max_chars=32_000,
        )

    hybrid_text = ""
    if hybrid_hits:
        hybrid_text = supermemory.format_search_hits(hybrid_hits, max_chars=12_000)

    if chunk_fact_text.strip():
        llm_parts.append(chunk_fact_text)
    elif hybrid_text.strip():
        llm_parts.append(hybrid_text)

    if not hybrid_hits and not chunk_hits:
        for query in queries:
            text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
                query,
                limit=8,
                max_chars=8_000,
            )
            if text.strip():
                llm_parts.append(text[:8000])

    fact_parts = [part for part in (supplemental_blob, chunk_fact_text) if part.strip()]
    return "\n\n".join(llm_parts), "\n\n".join(fact_parts)


async def _bio_kb_context_for_section(section: ProposalSection) -> str:
    """Authoritative 04_Bio PDF text for Section 2 team member bios."""
    if not section.id.startswith("section-2-bio"):
        return ""
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section
    from app.services.proposal_sections_graph import _fetch_member_bio_kb

    member = _member_name_from_bio_section(section.title or "")
    if not member.strip():
        return ""
    bio_text, _sources = await _fetch_member_bio_kb(member)
    if not bio_text.strip() or bio_text.startswith("("):
        return ""
    return bio_text


async def _merge_bio_kb_into_blobs(
    section: ProposalSection,
    *,
    kb_block: str,
    fact_blob: str,
) -> tuple[str, str]:
    bio_text = await _bio_kb_context_for_section(section)
    if not bio_text:
        return kb_block, fact_blob
    header = f"=== 04_Bio approved file ({section.title}) ===\n{bio_text[:80_000]}"
    merged_kb = f"{kb_block}\n\n{header}".strip() if kb_block.strip() else header
    merged_fact = f"{fact_blob}\n\n{bio_text}".strip() if fact_blob.strip() else bio_text
    return merged_kb, merged_fact


def _apply_bio_work_history_kb_fill(
    section: ProposalSection,
    content: str,
    kb_text: str,
) -> tuple[str, int]:
    if not section.id.startswith("section-2-bio") or not kb_text.strip():
        return content, 0
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section
    from app.services.proposal_sections_graph import replace_bio_work_history_verify_from_kb

    member = _member_name_from_bio_section(section.title or "")
    if not member.strip():
        return content, 0
    return replace_bio_work_history_verify_from_kb(content, member, kb_text)


async def _search_hits(query: str) -> list[dict[str, Any]]:
    if not supermemory.is_configured():
        return []
    try:
        hits = await supermemory.search_hybrid(
            query=query,
            limit=SEARCH_LIMIT,
            include_full_docs=True,
            filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
        )
        return [hit for hit in hits if supermemory.is_knowledge_base_hit(hit)]
    except supermemory.SupermemoryError:
        return []


def _merge_hits_into_corpus(
    corpus: list[EvidenceItem],
    hits: list[dict[str, Any]],
    section_id: str,
) -> list[EvidenceItem]:
    return merge_hits_into_corpus(
        corpus,
        hits,
        section_id,
        hit_key=_hit_key,
        hit_label=_hit_label,
        hit_excerpt=_hit_excerpt,
        excerpt_max_chars=EXCERPT_MAX_CHARS,
    )


def _evidence_for_section(section_id: str, corpus: list[EvidenceItem]) -> list[EvidenceItem]:
    tagged = [item for item in corpus if section_id in item.section_ids]
    if tagged:
        return tagged[:16]
    return corpus[:8]


def _format_evidence(items: list[EvidenceItem]) -> str:
    lines = []
    for item in items:
        lines.append(f"[{item.id}] {item.source}\n{item.excerpt[:1800]}")
    return "\n\n".join(lines) if lines else "(No evidence yet.)"


def _find_rfp_section(research: ProposalResearchCache, section_id: str) -> RfpSectionMap | None:
    for section in research.rfp_sections:
        if section.id == section_id:
            return section
    return None


def _find_draft_section(draft: ProposalDraft, section_id: str) -> ProposalSection | None:
    for section in draft.sections:
        if section.id == section_id:
            return section
    return None


def _selection_bounds_valid(
    content: str,
    *,
    start: int,
    end: int,
    selection_text: str | None,
) -> bool:
    if start < 0 or end > len(content) or start >= end:
        return False
    if selection_text is not None and content[start:end] != selection_text:
        return False
    return True


def _splice_selection(
    content: str,
    *,
    start: int,
    end: int,
    replacement: str,
) -> str:
    return content[:start] + replacement + content[end:]


async def _improve_section_selection(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    rfp_context: str,
    user_message: str,
    selection_start: int,
    selection_end: int,
    selection_text: str | None,
    brand_voice: dict[str, Any] | None,
    kb_zo_voice: str,
    evidence: list[EvidenceItem] | None = None,
    kb_block: str = "",
    fact_blob: str = "",
    avoidance_block: str = "",
    working_excerpt: str | None = None,
    research: ProposalResearchCache | None = None,
    compliance_user_message: str = "",
) -> tuple[ProposalSection, str, int]:
    """Surgical excerpt edit — full section context, splice replacement only."""
    content = section.content or ""
    if not _selection_bounds_valid(
        content,
        start=selection_start,
        end=selection_end,
        selection_text=selection_text,
    ):
        raise ProposalError(
            "Selection no longer matches section text — re-highlight the excerpt and try again.",
            status_code=400,
        )

    excerpt = working_excerpt if working_excerpt is not None else content[selection_start:selection_end]
    blob_for_facts = fact_blob or kb_block
    register = classify_section_register(
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )
    voice_block = format_brand_voice_block(
        brand_voice,
        kb_zo_voice=kb_zo_voice,
        rfp_client=rfp.client,
        register=register,
    )

    user_block = (
        f"BRAND VOICE (mandatory):\n{voice_block}\n\n"
        f"Client: {rfp.client}\n"
        f"Sector: {rfp.sector}\n"
        f"RFP: {rfp.title}\n"
        f"Section: {section.title}\n"
        f"Register: {register}\n\n"
        f"User instruction:\n{user_message.strip()}\n\n"
        f"Selected excerpt (replace ONLY this span):\n\"\"\"{excerpt}\"\"\"\n\n"
        f"Full section (context — do NOT rewrite outside the excerpt):\n\"\"\"{content[:14000]}\"\"\"\n\n"
        f"RFP excerpt:\n{rfp_context[:3000]}\n\n"
    )
    if evidence:
        user_block += f"Evidence corpus:\n{_format_evidence(evidence)}\n\n"
    if kb_block.strip():
        user_block += f"KB excerpts:\n{kb_block[:8000]}\n\n"
    if avoidance_block:
        user_block += f"{avoidance_block}\n\n"
    ask_for_compliance = compliance_user_message.strip() or user_message.strip()
    if should_apply_budget_playbook(section, ask_for_compliance):
        user_block += f"{budget_playbook_prompt_block(research=research)}\n\n"

    raw, provider = await llm.chat_json(
        [
            {"role": "system", "content": SELECTION_EDIT_PROMPT},
            {"role": "user", "content": user_block},
        ],
        max_tokens=2048,
        temperature=0.25,
    )
    replacement = str(raw.get("replacement") or raw.get("content") or "").strip()
    if not replacement:
        raise ProposalError(
            "Selection edit did not return replacement text. Try a more specific instruction.",
            status_code=422,
        )

    refusal = refuse_noncompliant_budget_edit(ask_for_compliance, replacement)
    if refusal:
        raise ProposalError(refusal, status_code=422)

    kb_fills = 0
    if blob_for_facts.strip() and VERIFY_TAG_RE.search(replacement):
        replacement, kb_fills = _replace_verify_tags_from_blob(replacement, blob_for_facts)

    if _selection_replacement_regressed(excerpt, replacement):
        raise ProposalError(
            "Selection edit would remove too much content — rejected to protect the section. "
            "Try selecting only the passage with [VERIFY] tags, or ask to fill a specific gap.",
            status_code=422,
        )
    if replacement.strip() == excerpt.strip() and kb_fills == 0:
        remaining_gaps = _gap_fields_from_text(replacement)
        if remaining_gaps:
            blob_has_phones = bool(_PHONE_RE.search(blob_for_facts))
            blob_has_emails = bool(_EMAIL_RE.search(blob_for_facts))
            needs_phone = any(
                any(k in g.casefold() for k in ("phone", "line", "fax", "telephone"))
                for g in remaining_gaps
            )
            needs_email = any("email" in g.casefold() or "e-mail" in g.casefold() for g in remaining_gaps)
            if (needs_phone and blob_has_phones) or (needs_email and blob_has_emails):
                raise ProposalError(
                    "KB returned contact facts but could not map them to the [VERIFY] tags. "
                    f"Still missing: {', '.join(remaining_gaps)}. "
                    "Try selecting only the contact line with the tag.",
                    status_code=422,
                )
            raise ProposalError(
                "Knowledge base did not contain verified values for: "
                f"{', '.join(remaining_gaps)}. Add the fact to Supermemory or enter it manually.",
                status_code=422,
            )
        raise ProposalError(
            "Selection edit did not change the excerpt. Try a more specific instruction.",
            status_code=422,
        )

    replacement = enforce_narrative_voice(
        replacement,
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )
    new_content = enforce_narrative_voice(
        _splice_selection(
            content,
            start=selection_start,
            end=selection_end,
            replacement=replacement,
        ),
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )

    if new_content[:selection_start] != content[:selection_start]:
        raise ProposalError(
            "Selection edit changed text before the highlight — rejected.",
            status_code=422,
        )
    expected_suffix_start = selection_start + len(replacement)
    if new_content[expected_suffix_start:] != content[selection_end:]:
        raise ProposalError(
            "Selection edit changed text after the highlight — rejected.",
            status_code=422,
        )

    updated = section.model_copy(
        update={
            "content": new_content,
            "status": "generated",
        }
    )
    return updated, provider, kb_fills


async def _plan_refined_queries(
    *,
    section: ProposalSection,
    rfp_section: RfpSectionMap | None,
    rfp: RfpRecord,
    prior_queries: list[str],
    user_message: str,
    current_content: str,
) -> list[str]:
    from app.services.proposal_langchain_agents import AgentRole, plan_section_queries_agent

    requirements = rfp_section.requirements if rfp_section else []
    retrieval_focus = rfp_section.retrieval_focus if rfp_section else []

    planned = await plan_section_queries_agent(
        role=AgentRole.USER_REVISE,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector,
        section_title=section.title,
        requirements=requirements,
        retrieval_focus=retrieval_focus,
        prior_queries=prior_queries,
        user_message=user_message,
        current_content=current_content,
    )
    if planned:
        return planned

    raw, _ = await llm.chat_json(
        [
            {"role": "system", "content": REFINE_QUERIES_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Client: {rfp.client}\n"
                    f"Sector: {rfp.sector}\n"
                    f"Section: {section.title}\n"
                    f"Requirements: {requirements}\n"
                    f"Retrieval focus: {retrieval_focus}\n"
                    f"Prior queries (DO NOT repeat):\n"
                    + "\n".join(f"- {q}" for q in prior_queries)
                    + f"\n\nUser feedback:\n{user_message}\n\n"
                    f"Current draft (insufficient):\n{current_content[:2000]}"
                ),
            },
        ],
        max_tokens=1024,
        temperature=0.35,
    )
    queries = raw.get("queries", [])
    if not isinstance(queries, list):
        return []
    used = {q.strip().lower() for q in prior_queries}
    cleaned: list[str] = []
    for query in queries:
        text = str(query).strip()
        if text and text.lower() not in used:
            cleaned.append(text[:240])
            used.add(text.lower())
    return cleaned[:6]


async def _redraft_rfp_section(
    *,
    section: ProposalSection,
    rfp_section: RfpSectionMap | None,
    rfp: RfpRecord,
    rfp_context: str,
    evidence: list[EvidenceItem],
    brand_voice: dict[str, Any] | None,
    kb_zo_voice: str,
    user_message: str,
    prior_content: str,
    zo_context: str,
    avoidance_block: str = "",
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalSection, str]:
    requirements = rfp_section.requirements if rfp_section else []
    register = classify_section_register(
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )
    voice_block = format_brand_voice_block(
        brand_voice,
        kb_zo_voice=kb_zo_voice,
        rfp_client=rfp.client,
        register=register,
    )

    original_content = (section.content or "").strip()
    prior_for_agent, full_rewrite = prior_content_for_redraft(section)
    rewrite_note = ""
    bio_kb = await _bio_kb_context_for_section(section)
    if full_rewrite:
        rewrite_note = (
            "\n\nIMPORTANT: Prior draft is below the word target or not marked generated. "
            "Write the COMPLETE section for every listed requirement from evidence and KB tools. "
            "Do not return stubs, error text, or unchanged placeholder content.\n"
        )

    user_block = (
        f"BRAND VOICE (mandatory — maintain throughout):\n{voice_block}\n\n"
        f"Client: {rfp.client}\n"
        f"Sector: {rfp.sector}\n"
        f"RFP: {rfp.title}\n"
        f"Section: {section.title}\n"
        f"Word target: {section.word_target}\n"
        f"Requirements:\n"
        + "\n".join(f"- {r}" for r in requirements)
        + rewrite_note
        + f"\n\nUser edit request:\n{user_message}\n\n"
        f"Previous draft:\n{prior_for_agent[:3000] if prior_for_agent else '(none — write from scratch)'}\n\n"
        f"RFP excerpt:\n{rfp_context[:4000]}\n\n"
        f"Evidence corpus:\n{_format_evidence(evidence)}\n\n"
        + (f"{avoidance_block}\n\n" if avoidance_block else "")
        + (f"zö Sections 1–3 reference:\n{zo_context[:3000]}\n" if zo_context else "")
    )
    if bio_kb.strip():
        user_block += (
            f"\n\n=== 04_Bio approved file (use for Work History, education, accounts) ===\n"
            f"{bio_kb[:50_000]}\n"
        )
    if should_apply_budget_playbook(section, user_message):
        user_block += f"\n{budget_playbook_prompt_block(research=research)}\n"

    max_tokens = 8192 if section.word_target >= 1500 else 6144

    try:
        from app.services.proposal_langchain_agents import (
            AgentRole,
            content_from_agent_payload,
            redraft_section_agent,
        )

        raw, provider, _tools = await redraft_section_agent(
            role=AgentRole.USER_REVISE,
            rfp_id=rfp.id,
            rfp_title=rfp.title,
            rfp_client=rfp.client,
            user_content=user_block,
        )
    except Exception as exc:
        logger.warning("User Revise agent failed, falling back to chat_json: %s", exc)
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": SECTION_REDRAFT_PROMPT},
                {"role": "user", "content": user_block},
            ],
            max_tokens=max_tokens,
            temperature=0.4,
        )

    content = enforce_narrative_voice(
        content_from_agent_payload(raw if isinstance(raw, dict) else {}),
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )

    refusal = refuse_noncompliant_budget_edit(user_message, content)
    if refusal:
        raise ProposalError(refusal, status_code=422)

    if redraft_is_inadequate(section, content, original_content=original_content):
        logger.warning(
            "User Revise output too short for %s (%d words, keys=%s) — retrying chat_json",
            section.id,
            word_count(content),
            list(raw.keys()) if isinstance(raw, dict) else [],
        )
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": SECTION_REDRAFT_PROMPT},
                {"role": "user", "content": user_block},
            ],
            max_tokens=max_tokens,
            temperature=0.35,
        )
        content = enforce_narrative_voice(
            content_from_agent_payload(raw if isinstance(raw, dict) else {}),
            section_id=section.id,
            title=section.title,
            zo_mode=section.mode,
        )

    if redraft_is_inadequate(section, content, original_content=original_content):
        raise ProposalError(
            f"Section revise did not produce enough content ({word_count(content)} words). "
            "Try a more specific instruction or re-run Phase 3 for this section.",
            status_code=422,
        )
    if bio_kb.strip():
        content, _ = _apply_bio_work_history_kb_fill(section, content, bio_kb)
        content = enforce_narrative_voice(
            content,
            section_id=section.id,
            title=section.title,
            zo_mode=section.mode,
        )
    # KB references removed - not included in proposals
    
    updated = section.model_copy(
        update={
            "content": content,
            "designer_note": raw.get("designerNote") or raw.get("designer_note"),
            "status": "generated",
            "kb_refs": [],
        }
    )
    return updated, provider


async def _improve_static_section(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    rfp_context: str,
    queries: list[str],
    user_message: str,
    brand_voice: dict[str, Any] | None,
    kb_zo_voice: str,
    avoidance_block: str = "",
) -> tuple[ProposalSection, str]:
    kb_parts: list[str] = []
    sources: list[str] = []
    for query in queries:
        text, refs = await proposal_knowledge_base_tools.search_knowledge_base(
            query,
            limit=8,
        )
        if text.strip():
            kb_parts.append(text[:4500])
        sources.extend(refs)

    if not kb_parts:
        text, refs = await proposal_knowledge_base_tools.search_knowledge_base(
            f"zö agency {section.title} firm address phone email philosophy {rfp.client} {rfp.sector}",
            limit=10,
        )
        kb_parts.append(text[:5000])
        sources.extend(refs)

    voice_block = format_brand_voice_block(
        brand_voice,
        kb_zo_voice=kb_zo_voice,
        rfp_client=rfp.client,
        register="narrative",
    )

    prior = section.content or ""
    user_content = (
        f"BRAND VOICE (mandatory — maintain throughout; do not genericize):\n{voice_block}\n\n"
        f"Section: {section.title}\n"
        f"Mode: {section.mode}\n"
        f"Client: {rfp.client}\n"
        f"Sector: {rfp.sector}\n"
        f"User request:\n{user_message}\n\n"
        f"Previous content (preserve zö voice while improving — fill gaps from KB):\n"
        f"{prior[:9000]}\n\n"
        f"KB excerpts:\n{'---'.join(kb_parts)[:14000]}\n\n"
        f"RFP excerpt:\n{rfp_context[:5000]}"
    )
    if avoidance_block:
        user_content += f"\n\n{avoidance_block}"

    raw, provider = await llm.chat_json(
        [
            {"role": "system", "content": STATIC_SECTION_REDRAFT_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=4096,
        temperature=0.28,
    )
    content = enforce_narrative_voice(
        str(raw.get("content", "")).strip(),
        section_id=section.id,
        title=section.title,
        register="narrative",
    )
    # Prefer deterministic KB fill for remaining VERIFY tags after rewrite
    bio_kb = await _bio_kb_context_for_section(section)
    if bio_kb.strip():
        kb_parts.insert(0, bio_kb[:50_000])
    if content and kb_parts:
        joined_kb = "\n\n".join(kb_parts)
        content, _ = _apply_bio_work_history_kb_fill(section, content, bio_kb or joined_kb)
        content, _ = _replace_verify_tags_from_blob(content, joined_kb)
        content = enforce_narrative_voice(
            content,
            section_id=section.id,
            title=section.title,
            register="narrative",
        )
    updated = section.model_copy(
        update={
            "content": content or section.content,
            "designer_note": raw.get("designerNote") or section.designer_note,
            "status": "generated",
            "kb_refs": [],
        }
    )
    return updated, provider


async def _persist_section_improve_draft(
    updated_draft: ProposalDraft,
    research: ProposalResearchCache,
    *,
    section_title: str,
) -> ProposalDraft:
    """Save improved manuscript + an After snapshot so versions keep chat content."""
    to_save = push_after_section_edit_snapshot(
        updated_draft,
        section_title=section_title,
    )
    await asave_proposal_draft(to_save)
    await asave_research_cache(research)
    return to_save


async def improve_proposal_section(
    rfp_id: str,
    section_id: str,
    user_message: str,
    *,
    selection_start: int | None = None,
    selection_end: int | None = None,
    selection_text: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    proposal_wide: bool = False,
    persist: bool = True,
) -> tuple[ProposalSection, ProposalDraft, ProposalResearchCache, str, str, bool]:
    """Re-query KB with new detailed queries, expand evidence, re-draft one section only."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)
    if not user_message.strip():
        raise ProposalError("Edit message is required.", status_code=400)

    rfp, _content, rfp_context = await aload_rfp_for_proposal(rfp_id)
    draft = await aget_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError("No proposal draft found. Generate a proposal first.", status_code=400)

    selection_mode = (
        selection_start is not None
        and selection_end is not None
        and selection_end > selection_start
    )

    research = await aget_research_cache(rfp_id)

    # When not pinned to a Revise-content excerpt, resolve structural asks
    # (add/delete sections) before rewriting the focused tab.
    if not selection_mode:
        from app.services.proposal_chat_structure import (
            apply_chat_structure_plan,
            plan_chat_structure_action,
        )

        structure_plan = await plan_chat_structure_action(
            draft=draft,
            user_message=user_message,
            focus_section_id=section_id,
            rfp_title=rfp.title,
            rfp_client=rfp.client,
            rfp_context=rfp_context,
        )
        if structure_plan.action == "clarify":
            provider = _provider_name()
            if research is None:
                research = ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                )
            question = (structure_plan.clarify_question or "").strip() or (
                "Should I edit the current section, add new sidebar sections, or delete something?"
            )
            focus = _find_draft_section(draft, section_id) or draft.sections[0]
            return focus, draft, research, provider, question, False

        if structure_plan.action in {"add_sections", "delete_sections"}:
            updated_draft, focus, assistant_message = await apply_chat_structure_plan(
                draft=draft,
                plan=structure_plan,
                rfp_client=rfp.client,
                rfp_sector=rfp.sector or "",
                rfp_context=rfp_context or "",
            )
            provider = _provider_name()
            if research is None:
                research = ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                )
            if persist:
                updated_draft = await _persist_section_improve_draft(
                    updated_draft,
                    research,
                    section_title=focus.title,
                )
            return focus, updated_draft, research, provider, assistant_message, True

        if structure_plan.edit_section_id:
            section_id = structure_plan.edit_section_id

        resolved = _resolve_section_from_message(draft, user_message, section_id)
        if resolved:
            section_id = resolved.id

    section = _find_draft_section(draft, section_id)
    if not section:
        raise ProposalError(f"Section {section_id} not found in draft.", status_code=404)
    before_section = section.model_copy()

    requirements_block = _rfp_section_requirements_block(research, section_id)
    if requirements_block:
        rfp_context = f"{rfp_context}\n\n--- Mapped section requirements ---\n{requirements_block}"

    manuscript_digest = (
        _manuscript_digest(draft) if proposal_wide or not selection_mode else ""
    )
    if manuscript_digest:
        rfp_context = f"{rfp_context}\n\n{manuscript_digest}"

    if should_apply_budget_playbook(section, user_message):
        from app.services.proposal_pricing_service import fetch_pricing_guide_context

        stage_two = ""
        if research and research.rfp_sections:
            stage_two = "\n".join(
                f"{s.title}: {', '.join((s.requirements or [])[:5])}"
                for s in research.rfp_sections[:12]
            )
        guide_text, _guide_sources = await fetch_pricing_guide_context(
            rfp,
            stage_two=stage_two,
            focus_hint=user_message[:300],
        )
        if guide_text.strip() and not guide_text.startswith("(No 00_Guide"):
            rfp_context = (
                f"{rfp_context}\n\n=== 00_Guide_Pricing (Supermemory) ===\n{guide_text[:20_000]}"
            )

    if not _wants_section_edit(user_message):
        reply = await _section_chat_advisory_reply(
            section=section,
            rfp=rfp,
            rfp_context=rfp_context,
            user_message=user_message,
            conversation_history=conversation_history,
            selection_text=selection_text,
            requirements_block=requirements_block,
            manuscript_digest=manuscript_digest,
            research=research,
        )
        provider = _provider_name()
        if research is None:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
        return section, draft, research, provider, reply, False

    latest_user_ask = user_message.strip()
    query_focus = _query_focus_message(
        latest_user_ask,
        section=section,
        requirements_block=requirements_block,
    )
    user_message = _compose_chat_user_message(user_message, conversation_history)

    # Do not snapshot a pre-chat "undo point" into the version menu — those empty
    # copies were wiping chat improvements when selected. Section revision drawer
    # still keeps before/after for the edited section.
    is_static = section_id in STATIC_SECTION_IDS or section.source == "template"

    brand_voice_dict, kb_zo_voice = await resolve_voice_context(
        rfp=rfp,
        rfp_context=rfp_context,
        brand_voice=(
            research.brand_voice.model_dump(by_alias=True)
            if research and research.brand_voice
            else None
        ),
    )
    # Always refresh KB voice samples for chat revises so tone stays grounded.
    from app.services.proposal_brand_voice import fetch_zo_voice_excerpt

    fresh_voice = await fetch_zo_voice_excerpt(
        rfp_title=rfp.title,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector,
        rfp_location=rfp.location,
        rfp_context=rfp_context,
    )
    if fresh_voice.strip():
        kb_zo_voice = fresh_voice

    if selection_mode:
        logger.info(
            "Section selection edit for %s / %s: chars %d-%d message=%r",
            rfp_id,
            section_id,
            selection_start,
            selection_end,
            user_message[:80],
        )
        excerpt = (section.content or "")[selection_start:selection_end]
        full_content = section.content or ""
        gap_fields = _gap_fields_from_text(excerpt)
        editor_instruction, kb_queries = await _plan_selection_edit(
            section=section,
            rfp=rfp,
            user_message=user_message,
            excerpt=excerpt,
            full_content=full_content,
            selection_start=selection_start,
            selection_end=selection_end,
        )
        evidence_blob = ""
        avoidance_block = ""
        evidence: list[EvidenceItem] = []
        if research:
            avoidance_block = format_avoidance_block(
                research.writing_avoidances,
                research.loss_lessons,
            )
            evidence = _evidence_for_section(section_id, research.evidence_corpus or [])
            if research.evidence_corpus:
                evidence_blob = _section_corpus_blob(research.evidence_corpus, section_id)

        logger.info(
            "Selection KB plan for %s / %s gaps=%r queries=%r",
            rfp_id,
            section_id,
            gap_fields,
            kb_queries,
        )
        supplemental = _draft_supplemental_blob(draft)
        kb_block, contact_fact_blob = await _fetch_kb_blob_for_selection(
            kb_queries,
            evidence_blob=evidence_blob,
            supplemental_blob=supplemental,
        )
        kb_block, contact_fact_blob = await _merge_bio_kb_into_blobs(
            section,
            kb_block=kb_block,
            fact_blob=contact_fact_blob,
        )
        fact_blob = "\n\n".join(
            part for part in (full_content, contact_fact_blob) if part.strip()
        )

        excerpt = (section.content or "")[selection_start:selection_end]
        excerpt, bio_wh_fills = _apply_bio_work_history_kb_fill(
            section,
            excerpt,
            contact_fact_blob,
        )

        logger.info(
            "Selection fact blob for %s / %s: %d chars, phones=%s emails=%s",
            rfp_id,
            section_id,
            len(fact_blob),
            bool(_PHONE_RE.search(fact_blob)),
            bool(_EMAIL_RE.search(fact_blob)),
        )

        working_excerpt, pre_fills = _replace_verify_tags_from_blob(excerpt, fact_blob)
        pre_fills += bio_wh_fills
        if pre_fills > 0 and not _gap_fields_from_text(working_excerpt):
            new_content = enforce_narrative_voice(
                _splice_selection(
                    full_content,
                    start=selection_start,
                    end=selection_end,
                    replacement=working_excerpt,
                ),
                section_id=section.id,
                title=section.title,
                zo_mode=section.mode,
            )
            updated_section = section.model_copy(
                update={"content": new_content, "status": "generated"}
            )
            provider = "kb-fill"
            if research is None:
                research = ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                )
            else:
                research = research.model_copy(update={"provider": provider})
            merged_sections = [
                updated_section if s.id == section_id else s for s in draft.sections
            ]
            now = datetime.now(timezone.utc).isoformat()
            updated_draft = draft.model_copy(
                update={
                    "sections": merged_sections,
                    "updated_at": now,
                    "provider": provider,
                }
            )
            if persist:
                updated_draft = await _persist_section_improve_draft(
                    updated_draft,
                    research,
                    section_title=section.title,
                )
            before_words = word_count(before_section.content or "")
            after_words = word_count(updated_section.content or "")
            filled_labels = ", ".join(gap_fields) if gap_fields else "missing fields"
            assistant_message = (
                f"Filled **{pre_fills}** verified fact(s) in the selected excerpt of "
                f"**{section.title}** from the knowledge base ({filled_labels}). "
                f"({before_words} → {after_words} words)."
            )
            logger.info(
                "Section selection KB fill for %s / %s: %d tag(s)",
                rfp_id,
                section_id,
                pre_fills,
            )
            return updated_section, updated_draft, research, provider, assistant_message, True

        updated_section, provider, kb_fills = await _improve_section_selection(
            section=section,
            rfp=rfp,
            rfp_context=rfp_context,
            user_message=editor_instruction,
            selection_start=selection_start,
            selection_end=selection_end,
            selection_text=selection_text,
            brand_voice=brand_voice_dict,
            kb_zo_voice=kb_zo_voice,
            evidence=evidence,
            kb_block=kb_block,
            fact_blob=fact_blob,
            avoidance_block=avoidance_block,
            working_excerpt=working_excerpt if pre_fills > 0 else None,
            research=research,
            compliance_user_message=user_message,
        )
        if research is None:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
        else:
            research = research.model_copy(update={"provider": provider})

        merged_sections = [
            updated_section if s.id == section_id else s for s in draft.sections
        ]
        now = datetime.now(timezone.utc).isoformat()
        updated_draft = draft.model_copy(
            update={
                "sections": merged_sections,
                "updated_at": now,
                "provider": provider,
            }
        )
        if persist:
            updated_draft = await _persist_section_improve_draft(
                updated_draft,
                research,
                section_title=section.title,
            )

        before_words = word_count(before_section.content or "")
        after_words = word_count(updated_section.content or "")
        assistant_message = (
            f"Updated the selected excerpt in **{section.title}** "
            f"({before_words} → {after_words} words). Surrounding text unchanged."
        )
        if kb_fills > 0:
            assistant_message = (
                f"Filled **{kb_fills}** verified fact(s) and updated the selected excerpt in "
                f"**{section.title}** ({before_words} → {after_words} words)."
            )
        logger.info(
            "Section selection edit complete for %s / %s (%d → %d words)",
            rfp_id,
            section_id,
            before_words,
            after_words,
        )
        return updated_section, updated_draft, research, provider, assistant_message, True

    logger.info(
        "Section improve for %s / %s: static=%s message=%r",
        rfp_id,
        section_id,
        is_static,
        user_message[:80],
    )

    provider = _provider_name()
    evidence_added = 0
    query_count = 0

    if is_static:
        prior_queries = []
        if research:
            prior_queries = (research.section_queries or {}).get(section_id, [])
        rfp_section = _find_rfp_section(research, section_id) if research else None
        seeded = _seed_gap_queries(
            section=section,
            rfp=rfp,
            prior_queries=prior_queries,
        )
        queries = await _plan_refined_queries(
            section=section,
            rfp_section=rfp_section,
            rfp=rfp,
            prior_queries=[*prior_queries, *seeded],
            user_message=query_focus,
            current_content=section.content,
        )
        # Prefer gap seeds first, then planner queries, then fallbacks.
        merged_q: list[str] = []
        used_q = {q.strip().lower() for q in prior_queries}
        for q in [*seeded, *queries]:
            key = q.strip().lower()
            if q.strip() and key not in used_q:
                merged_q.append(q.strip()[:240])
                used_q.add(key)
        queries = merged_q
        if not queries:
            queries = [
                f"zö agency 01 companyfacts firm legal name address Bend Oregon {rfp.client}"[:220],
                f"zö agency contact phone email Sonja 02 master template {section.title}"[:220],
                f"zö agency tourism DMO destination marketing experience {rfp.sector}"[:220],
                f"zö agency company philosophy employees organizational structure {section.title}"[:220],
            ]
        query_count = len(queries)
        avoidance_block = ""
        if research:
            avoidance_block = format_avoidance_block(
                research.writing_avoidances,
                research.loss_lessons,
            )
        updated_section, provider = await _improve_static_section(
            section=section,
            rfp=rfp,
            rfp_context=rfp_context,
            queries=queries,
            user_message=user_message,
            brand_voice=brand_voice_dict,
            kb_zo_voice=kb_zo_voice,
            avoidance_block=avoidance_block,
        )
        new_queries = {
            **(research.section_queries if research else {}),
            section_id: [*prior_queries, *queries],
        }
        if research:
            research = research.model_copy(update={"section_queries": new_queries, "provider": provider})
        else:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                sectionQueries=new_queries,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
    else:
        if not research or not research.evidence_corpus:
            raise ProposalError(
                "Phase 2 research required for RFP sections. Run KB retrieval first.",
                status_code=400,
            )

        prior_queries = (research.section_queries or {}).get(section_id, [])
        rfp_section = _find_rfp_section(research, section_id)

        queries = await _plan_refined_queries(
            section=section,
            rfp_section=rfp_section,
            rfp=rfp,
            prior_queries=prior_queries,
            user_message=query_focus,
            current_content=section.content,
        )
        if not queries:
            title = section.title
            queries = [
                f"zö agency firm history organizational chart employee count {rfp.client} {title}"[:240],
                f"zö agency company philosophy capabilities statement {rfp.sector} {title}"[:240],
                f"zö agency 02 master template certifications WBENC WOSB {title}"[:240],
            ]

        query_count = len(queries)

        all_hits: list[dict[str, Any]] = []
        for query in queries:
            hits = await _search_hits(query)
            all_hits.extend(hits)
            logger.info("Section refine search %s: %d hits for %r", section_id, len(hits), query[:60])

        prior_corpus_len = len(research.evidence_corpus)
        corpus = _merge_hits_into_corpus(research.evidence_corpus, all_hits, section_id)
        evidence_added = len(corpus) - prior_corpus_len
        section_evidence = _evidence_for_section(section_id, corpus)

        from app.services.proposal_generator import _static_sections_from_draft

        static = _static_sections_from_draft(draft, rfp.page_limit)
        zo_context = "\n\n".join(
            f"### {s.title}\n{s.content[:1500]}"
            for s in static[:3]
            if s.content.strip()
        )

        avoidance_block = format_avoidance_block(
            research.writing_avoidances,
            research.loss_lessons,
        )

        updated_section, provider = await _redraft_rfp_section(
            section=section,
            rfp_section=rfp_section,
            rfp=rfp,
            rfp_context=rfp_context,
            evidence=section_evidence,
            brand_voice=brand_voice_dict,
            kb_zo_voice=kb_zo_voice,
            user_message=user_message,
            prior_content=section.content,
            zo_context=zo_context,
            avoidance_block=avoidance_block,
            research=research,
        )

        new_queries = {**research.section_queries, section_id: [*prior_queries, *queries]}
        updated_rfp_sections: list[RfpSectionMap] = []
        for s in research.rfp_sections:
            if s.id == section_id:
                updated_rfp_sections.append(
                    s.model_copy(
                        update={
                            "coverage_percent": min(95, (s.coverage_percent or 0) + 15),
                        }
                    )
                )
            else:
                updated_rfp_sections.append(s)

        research = research.model_copy(
            update={
                "evidence_corpus": corpus,
                "section_queries": new_queries,
                "rfp_sections": updated_rfp_sections,
                "provider": provider,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    merged_sections = [
        updated_section if s.id == section_id else s for s in draft.sections
    ]

    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(
        update={
            "sections": merged_sections,
            "updated_at": now,
            "provider": provider,
        }
    )
    if persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=section.title,
        )

    word_count_result = word_count(updated_section.content)
    remaining_gaps = _gap_fields_from_text(updated_section.content or "")
    if is_static:
        assistant_message = (
            f"Ran **{query_count}** gap-targeted KB queries (VERIFY fields + RFP asks), "
            f"re-applied zö brand voice, and rewrote **{section.title}** "
            f"({word_count_result} words)."
        )
    else:
        assistant_message = (
            f"Ran {query_count} new Supermemory queries (different from prior searches), "
            f"added {evidence_added} evidence item(s) to the corpus, preserved brand voice, "
            f"and rewrote **{section.title}** ({word_count_result} words)."
        )
    if remaining_gaps:
        assistant_message += (
            " Still needs manual/KB fill: "
            + ", ".join(f"`{g}`" for g in remaining_gaps[:6])
            + "."
        )

    logger.info(
        "Section improve complete for %s / %s (%d words)",
        rfp_id,
        section_id,
        word_count_result,
    )
    return updated_section, updated_draft, research, provider, assistant_message, True
