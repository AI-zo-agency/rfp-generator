"""Per-section improve: refined KB re-query + targeted re-draft from user chat feedback."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
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
    extract_manual_fill_tags,
    fill_manual_fill_tags,
    is_manual_fill_request,
    mask_manual_fill_tags,
    missing_manual_fill_placeholders,
    unmask_manual_fill_tags,
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

_MANUAL_FILL_PRESERVE_CONSTRAINT = (
    "CRITICAL HARD CONSTRAINT — PROTECTED MANUAL FILL PLACEHOLDERS:\n"
    "The text contains «MFILL_N» tokens. These stand for protected [MANUAL FILL …] "
    "tags that MUST appear in your output EXACTLY as «MFILL_N» (same index). "
    "Do not resolve, paraphrase, delete, invent content for, or replace them. "
    "Copy every «MFILL_N» token through unchanged.\n"
)


def _mask_manual_fill_for_rewrite(text: str) -> tuple[str, list[str]]:
    """Mask MANUAL FILL tags before an incidental LLM rewrite."""
    if not extract_manual_fill_tags(text or ""):
        return text, []
    return mask_manual_fill_tags(text)


def _unmask_manual_fill_checked(output: str, originals: list[str], *, attempt: int) -> str:
    """Restore masked MANUAL FILL tags; raise if the model dropped any placeholder."""
    if not originals:
        return output
    missing = missing_manual_fill_placeholders(output or "", originals)
    if missing:
        raise ProposalError(
            "Rewrite dropped protected MANUAL FILL tag(s): "
            + ", ".join(missing[:4])
            + f" (attempt {attempt})",
            status_code=422,
        )
    return unmask_manual_fill_tags(output, originals)

logger = logging.getLogger(__name__)

SECTION_CHAT_ADVISORY_PROMPT = """You are a zö agency proposal editor assistant — sharp, thorough, and honest.

You may receive the FULL proposal manuscript digest plus one focus section and optionally a highlighted excerpt.

Rules:
1. Answer from the RFP requirements and the proposal as a whole — do not invent compliance facts.
2. If the user asks about another section or the whole draft, use the manuscript digest.
3. If the user asks whether something meets the RFP, cite specific RFP asks and gaps.
4. When the user asks to check / evaluate / list which case studies (or sections) do NOT meet the RFP:
   - Review EVERY Our Work / case-study section in the manuscript digest (not only the focus tab).
   - Return a clear pass/fail (or partial) list with the sidebar title and the unmet RFP expectations.
   - Do NOT rewrite any section in this turn.
5. You may disagree or push back when their request would weaken compliance or accuracy.
6. Do NOT rewrite the section in this turn — explain what you would change and why, or answer the question.
7. Be concise but thorough (use bullets when auditing). Use **bold** for key RFP requirements.
8. If they need an edit, tell them to ask explicitly (e.g. "replace 3.3 with a tourism case study from KB" or use Revise content on an excerpt).
9. Budget/pricing/fees: follow the pricing playbook when provided — refuse invented numbers and reverse-engineered totals (option C); flag out-of-guide scope with [PRICING FLAG: … — Sonja review required].
10. For duplicates / fabrication / ClientList trust: prefer directing them to say **check duplicates**, **remove duplicates**, or **remove fabricated content** so the system can run the full content→RFP→KB pipeline (you cannot fake that audit from this advisory turn alone).

Return ONLY JSON: {"reply": "markdown message for the chat"}"""

# Explicit mutate verbs — required before we rewrite a section.
_EDIT_INTENT_RE = re.compile(
    r"\b("
    r"change|fix|update|rewrite|revise|edit|improve|shorten|lengthen|"
    r"remove|replace|fill|patch|insert|delete|correct|align|"
    r"make\s+it|make\s+this|swap|redraft|regenerate|"
    r"add\s+(?:a\s+|the\s+|this\s+|new\s+)?"
    r"(?:section|paragraph|sentence|case\s*study|bio|bullet|row|line)"
    r")\b",
    re.I,
)

# Evaluate / list / audit — answer in chat only, never rewrite.
_ADVISORY_INTENT_RE = re.compile(
    r"\b("
    r"check|evaluate|assess|review|audit|analy[sz]e|compare|"
    r"list\s+which|which\s+(?:ones?|don'?t|do\s+not|fail|meet)|"
    r"don'?t\s+meet|do\s+not\s+meet|does\s+not\s+meet|"
    r"meet(?:s)?\s+(?:the\s+)?(?:rfp|expectation|requirement)|"
    r"fit(?:s)?\s+(?:the\s+)?(?:rfp|expectation)|"
    r"gap(?:s)?(?:\s+analysis)?|what'?s\s+missing|"
    r"tell\s+me|show\s+me|explain|why|should\s+we|"
    r"does\s+(?:this|it|they)|do\s+(?:they|these|any)|"
    r"are\s+(?:they|these|any)|is\s+this"
    r")\b",
    re.I,
)

_FOLLOW_WITH_MUTATE_RE = re.compile(
    r"\b(?:then|and)\s+(?:please\s+)?(?:fix|rewrite|replace|update|remove|improve|edit)\b",
    re.I,
)


def _wants_section_edit(user_message: str) -> bool:
    """True only when the user clearly asks to mutate draft text.

    Default is advisory. Phrases like "check all case studies… list which don't"
    must NOT trigger a rewrite of the focused tab.
    """
    text = user_message.strip()
    if not text:
        return False

    advisory = bool(_ADVISORY_INTENT_RE.search(text))
    mutate = bool(_EDIT_INTENT_RE.search(text))

    if advisory and not mutate:
        return False
    if advisory and mutate and not _FOLLOW_WITH_MUTATE_RE.search(text):
        # "check and review" / "evaluate fit then tell me" → still advisory
        # Require an explicit "then fix/replace/…" to mutate after an audit ask.
        return False
    if mutate:
        return True
    if text.endswith("?"):
        return False
    if re.search(
        r"\b(why|explain|what does|is this|does this|compliant|requirement|"
        r"argue|push back|should we)\b",
        text,
        re.I,
    ):
        return False
    # Safe default: answer in chat; do not rewrite.
    return False


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


def _message_needs_case_study_clarify(user_message: str) -> bool:
    """True when the ask is about case studies but no specific sidebar title is required yet."""
    text = user_message or ""
    if re.search(r"\bcase\s*stud(?:y|ies)\b", text, re.I):
        return True
    if re.search(
        r"\breplace\b.{0,80}\b(existing|current|these|those)\b.{0,40}\b(case|work|stud)",
        text,
        re.I,
    ):
        return True
    if re.search(r"\b(existing|current|these|those)\s+\d*\s*case\s*stud", text, re.I):
        return True
    return False


def _is_our_work_section(section: ProposalSection | None) -> bool:
    if section is None:
        return False
    sid = section.id or ""
    return sid.startswith("section-3-work-") and sid != "section-3-work-placeholder"


def _case_study_clarify_reply(
    draft: ProposalDraft,
    *,
    open_section: ProposalSection | None,
) -> str:
    cases = [
        s
        for s in draft.sections
        if _is_our_work_section(s)
    ]
    lines = [f"{i + 1}. **{s.title}**" for i, s in enumerate(cases[:8])]
    open_note = ""
    if open_section and not _is_our_work_section(open_section):
        open_note = (
            f", or say you meant the open section (**{open_section.title}**)"
        )
        lines.append(f"{len(lines) + 1}. **{open_section.title}** (open tab)")
    return (
        "You mentioned case studies, but I won't guess from the open tab. "
        f"Pick an Our Work piece{open_note}.\n\n"
        + "\n".join(lines)
        + "\n\nReply with the section number or title "
        "(e.g. `3.1` or the full sidebar name). "
        "Or use **Revise content** / **Improve full section** to pin the tab yourself."
    )


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

    # Cross-tab: user quotes or paraphrases a claim that lives in another section.
    content_hit = _resolve_section_by_content_needle(draft, text, default_section_id)
    if content_hit is not None:
        return content_hit

    return default


def _content_needles_from_message(user_message: str) -> list[str]:
    """Extract distinctive phrases likely to appear in manuscript content."""
    needles: list[str] = []
    text = user_message or ""
    for match in re.finditer(r"[\"'“”‘']([^\"'“”‘']{10,160})[\"'“”‘']", text):
        phrase = match.group(1).strip()
        if phrase:
            needles.append(phrase)
    for match in re.finditer(
        r"\b(\d{1,2}-year\s+[\w\-]+(?:\s+[\w\-]+){0,8})",
        text,
        re.I,
    ):
        needles.append(match.group(1).strip())
    cleaned: list[str] = []
    meta_tail = re.compile(
        r"\s+\b(claim|claims|statement|wording|phrase|reference|text|language|"
        r"mention|mentions|sentence|paragraph)\b.*$",
        re.I,
    )
    for needle in needles:
        cleaned.append(meta_tail.sub("", needle).strip() or needle)
    # Dedupe while preserving order; longer phrases first for specificity.
    seen: set[str] = set()
    unique: list[str] = []
    for needle in sorted(cleaned, key=len, reverse=True):
        key = needle.casefold()
        if key in seen or len(key) < 10:
            continue
        seen.add(key)
        unique.append(needle)
    return unique


def _resolve_section_by_content_needle(
    draft: ProposalDraft,
    user_message: str,
    default_section_id: str,
) -> ProposalSection | None:
    """Find the section whose body contains a distinctive phrase from the ask."""
    needles = _content_needles_from_message(user_message)
    if not needles:
        return None

    def _candidates(needle: str) -> list[str]:
        words = needle.split()
        out: list[str] = []
        for end in range(len(words), 2, -1):
            out.append(" ".join(words[:end]))
        return out

    hits: list[ProposalSection] = []
    for needle in needles:
        for candidate in _candidates(needle):
            n = candidate.casefold()
            if len(n) < 10:
                continue
            matched = [
                section
                for section in draft.sections
                if n in (section.content or "").casefold()
            ]
            if matched:
                for section in matched:
                    if not any(h.id == section.id for h in hits):
                        hits.append(section)
                break
        if hits:
            break
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    others = [h for h in hits if h.id != default_section_id]
    return others[0] if others else hits[0]


EDIT_SCOPE_PLAN_PROMPT = """You plan how to apply a user's edit to ONE proposal section.

Scan the ENTIRE Current section content. Identify EVERY passage that the user's ask
requires changing — not just the first match.

Default: surgical PATCH(es). Choose full_rewrite ONLY when the user clearly wants the
whole section regenerated or the change cannot be localized.

Return ONLY JSON:
{
  "understoodAsk": "one sentence restating the user goal",
  "mode": "patch" | "full_rewrite",
  "patches": [
    {
      "anchorExcerpt": "verbatim contiguous text from Current section content",
      "editorInstruction": "precise instruction for THIS passage only"
    }
  ],
  "kbQueries": ["0-4 optional Supermemory queries needed for these edits"]
}

Rules:
1. Prefer mode=patch. Emit one patches[] entry per distinct passage that must change.
2. If the user asks to remove/replace unsourced numbers, fabricated figures, percentages,
   year claims, or similar — scan the WHOLE section and list EVERY matching instance
   (hours, %, years, dollar amounts, partnership-tenure claims, etc. that fit the ask).
   Do not stop after the first hit. One ask → one patches[] entry per distinct passage.
3. Each anchorExcerpt MUST be copied verbatim from Current section content — usually one
   paragraph or a few sentences, NEVER the entire section, NEVER overlapping duplicates.
4. Do not invent facts. If sourcing from KB is required, list kbQueries; if the ask is only
   remove / make qualitative, return "kbQueries": [].
5. For full_rewrite, return "patches": [] and put the rewrite instruction in understoodAsk.
"""


@dataclass
class EditScopePatch:
    anchor_excerpt: str
    editor_instruction: str


@dataclass
class EditScopePlan:
    understood_ask: str
    mode: str  # patch | full_rewrite
    patches: list[EditScopePatch]
    kb_queries: list[str]

    @property
    def anchor_excerpt(self) -> str:
        return self.patches[0].anchor_excerpt if self.patches else ""

    @property
    def editor_instruction(self) -> str:
        if not self.patches:
            return self.understood_ask
        if len(self.patches) == 1:
            return self.patches[0].editor_instruction
        return "; ".join(
            p.editor_instruction for p in self.patches if p.editor_instruction.strip()
        )


def _parse_edit_scope_patches(raw: dict[str, Any], user_message: str) -> list[EditScopePatch]:
    """Accept patches[] or legacy single anchorExcerpt shape."""
    patches: list[EditScopePatch] = []
    raw_patches = raw.get("patches")
    if isinstance(raw_patches, list):
        for item in raw_patches:
            if not isinstance(item, dict):
                continue
            anchor = str(
                item.get("anchorExcerpt") or item.get("anchor_excerpt") or ""
            ).strip()
            instr = str(
                item.get("editorInstruction")
                or item.get("editor_instruction")
                or ""
            ).strip()
            if len(anchor) >= 8:
                patches.append(
                    EditScopePatch(
                        anchor_excerpt=anchor,
                        editor_instruction=instr or user_message.strip(),
                    )
                )
    if patches:
        return patches

    anchor = str(raw.get("anchorExcerpt") or raw.get("anchor_excerpt") or "").strip()
    instr = str(
        raw.get("editorInstruction") or raw.get("editor_instruction") or user_message
    ).strip()
    if len(anchor) >= 8:
        patches.append(
            EditScopePatch(
                anchor_excerpt=anchor,
                editor_instruction=instr or user_message.strip(),
            )
        )
    return patches


def _locate_anchor_in_content(
    content: str,
    anchor: str,
    *,
    max_chars: int = 1200,
    tight: bool = False,
) -> tuple[int, int] | None:
    """Map an LLM-provided anchor string to a span in section content.

    tight=True: sentence-bounded around the match (for multi-patch plans so
    neighboring figures in the same paragraph stay separately editable).
    """
    body = content or ""
    needle = (anchor or "").strip()
    if not body.strip() or len(needle) < 8:
        return None

    match_start = -1
    match_end = -1

    idx = body.find(needle)
    if idx < 0:
        idx = body.casefold().find(needle.casefold())
    if idx >= 0:
        match_start = idx
        match_end = idx + len(needle)
    else:
        # LLM may normalize whitespace; match word sequence with flexible gaps.
        words = [w for w in re.split(r"\s+", needle) if w]
        if len(words) >= 3:
            pattern = re.compile(
                r"\s+".join(re.escape(w) for w in words),
                re.IGNORECASE,
            )
            found = pattern.search(body)
            if found:
                match_start = found.start()
                match_end = found.end()
            else:
                # Progressively shorten from the end until a hit.
                for end in range(len(words) - 1, 2, -1):
                    pattern = re.compile(
                        r"\s+".join(re.escape(w) for w in words[:end]),
                        re.IGNORECASE,
                    )
                    found = pattern.search(body)
                    if found:
                        match_start = found.start()
                        match_end = found.end()
                        break
    if match_start < 0:
        return None

    if tight:
        # Sentence containing the match (not the whole paragraph).
        start = 0
        for i in range(match_start - 1, -1, -1):
            if body[i] in ".!?\n":
                start = i + 1
                while start < match_start and body[start].isspace():
                    start += 1
                break
        end = len(body)
        # If the needle already includes terminal punctuation, stop there.
        search_from = max(match_start, match_end - 1)
        for i in range(search_from, len(body)):
            if body[i] in ".!?":
                end = i + 1
                break
            if body[i] == "\n":
                end = i
                break
        if end <= start:
            start, end = match_start, match_end
        if end - start > max_chars:
            start, end = match_start, match_end
        if start >= end:
            return None
        if len(body) >= 40 and (end - start) >= int(len(body) * 0.75):
            # Still too wide — fall back to exact needle span.
            start, end = match_start, match_end
        if len(body) >= 40 and (end - start) >= int(len(body) * 0.75):
            return None
        return start, end

    para_start = body.rfind("\n\n", 0, match_start)
    start = 0 if para_start < 0 else para_start + 2
    para_end = body.find("\n\n", match_end)
    end = len(body) if para_end < 0 else para_end

    if end - start > max_chars:
        left = body.rfind(". ", max(start, match_start - max_chars // 2), match_start)
        right = body.find(". ", match_end, min(end, match_end + max_chars // 2))
        start = start if left < 0 else left + 2
        end = end if right < 0 else right + 1

    if end - start > max_chars:
        mid = (match_start + match_end) // 2
        start = max(0, mid - max_chars // 2)
        end = min(len(body), start + max_chars)
        if start > 0 and not body[start - 1].isspace():
            space = body.find(" ", start, end)
            if space > 0:
                start = space + 1

    if start >= end:
        return None
    # Never treat "almost the whole section" as a patch span.
    if len(body) >= 40 and (end - start) >= int(len(body) * 0.75):
        return None
    return start, end


def _merge_overlapping_located_patches(
    located: list[tuple[int, int, EditScopePatch]],
) -> list[tuple[int, int, EditScopePatch]]:
    """Merge overlapping/abutting spans so one rewrite covers them."""
    if not located:
        return []
    ordered = sorted(located, key=lambda t: (t[0], t[1]))
    merged: list[tuple[int, int, EditScopePatch]] = []
    for start, end, patch in ordered:
        if not merged:
            merged.append((start, end, patch))
            continue
        prev_start, prev_end, prev_patch = merged[-1]
        if start <= prev_end:
            combined = (
                f"{prev_patch.editor_instruction.rstrip('. ')}. "
                f"{patch.editor_instruction}"
            ).strip()
            merged[-1] = (
                prev_start,
                max(prev_end, end),
                EditScopePatch(
                    anchor_excerpt=prev_patch.anchor_excerpt,
                    editor_instruction=combined,
                ),
            )
        else:
            merged.append((start, end, patch))
    return merged


def _locate_planned_patches(
    content: str,
    patches: list[EditScopePatch],
) -> list[tuple[int, int, EditScopePatch]]:
    """Locate every planned patch; use tight spans when multiple patches."""
    if not patches:
        return []
    tight = len(patches) > 1
    located: list[tuple[int, int, EditScopePatch]] = []
    for patch in patches:
        span = _locate_anchor_in_content(
            content,
            patch.anchor_excerpt,
            tight=tight,
        )
        if span is None and tight:
            # Fall back to paragraph expand if tight miss.
            span = _locate_anchor_in_content(content, patch.anchor_excerpt, tight=False)
        if span is None:
            logger.info(
                "Edit-scope patch anchor not found: %r",
                patch.anchor_excerpt[:100],
            )
            continue
        located.append((span[0], span[1], patch))
    return _merge_overlapping_located_patches(located)


async def _plan_edit_scope(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    user_message: str,
) -> EditScopePlan:
    """LLM understands the ask and chooses patch vs full rewrite (no keyword rules)."""
    content = section.content or ""
    raw, _ = await llm.chat_json(
        [
            {"role": "system", "content": EDIT_SCOPE_PLAN_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Client: {rfp.client}\n"
                    f"Section: {section.title}\n\n"
                    f"User message:\n{user_message.strip()}\n\n"
                    f"Current section content:\n{content[:12000]}"
                ),
            },
        ],
        max_tokens=1600,
        temperature=0.1,
        tier="light",
        node_name="chat_edit_scope_plan",
    )
    mode = str(raw.get("mode") or "patch").strip().casefold()
    if mode not in {"patch", "full_rewrite"}:
        mode = "patch"
    queries_raw = raw.get("kbQueries") or raw.get("kb_queries") or []
    queries: list[str] = []
    if isinstance(queries_raw, list):
        queries = [str(q).strip()[:240] for q in queries_raw if str(q).strip()][:4]
    patches = _parse_edit_scope_patches(raw if isinstance(raw, dict) else {}, user_message)
    if mode == "full_rewrite":
        patches = []
    return EditScopePlan(
        understood_ask=str(raw.get("understoodAsk") or raw.get("understood_ask") or "").strip(),
        mode=mode,
        patches=patches,
        kb_queries=queries,
    )


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

FIRST: restate what the user is asking for (one line). THEN map that ask to the listed RFP
requirements and [VERIFY] gaps. ONLY THEN invent search queries that chase those facts.

Prior queries failed or returned insufficient evidence. User feedback describes what is wrong or missing.

Rules:
- Queries must follow from the understood ask + RFP needs — not random keyword mash.
- Queries must be MORE SPECIFIC and DIFFERENT from all prior queries (never repeat or lightly rephrase).
- Use document-type hints where relevant: 01 companyfacts, 02 master template, 03_CS case studies, 04 bio, certifications, org chart, references.
- Target the exact gaps: firm legal name, Bend address, phone/email contacts, employee count, philosophy, tourism/DMO experience, org structure, case studies, fees, etc.
- Include "zö agency" + field name + doc hint in each query. Add client name and sector when relevant.
- If [VERIFY: ...] fields or RFP requirements are listed, dedicate at least one query per missing field.
- Legal attestations (E-Verify, conflicts): search companyfacts only; do not plan queries that assume enrollment is proven.

Return ONLY JSON: {"queries": ["detailed query 1", "detailed query 2", "detailed query 3", "detailed query 4", "detailed query 5"]}"""

SECTION_IMPROVE_PLAN_PROMPT = """You are the first step of a proposal section improve — BEFORE any KB search or rewrite.

Read the user message, the section draft, and the RFP requirements for THIS section.
Understand the ask correctly. Do not jump to rewriting.

Return ONLY JSON:
{
  "understoodAsk": "One sentence: what the user wants done to this section",
  "editorInstruction": "Clear instruction for the rewriter — address the ask, cover listed RFP needs, fill VERIFY from KB only when evidenced, keep unconfirmed legal attestations as [VERIFY: …]",
  "kbQueries": ["3-6 targeted Supermemory queries derived from the understood ask + RFP needs + VERIFY gaps"],
  "rfpNeedsAddressed": ["short phrases of RFP requirements this edit must cover"]
}

Rules:
- understoodAsk must reflect the user's actual request (not a generic 'improve section').
- kbQueries must chase specific facts those needs require (zö agency + field + doc hint like 01 companyfacts).
- Never invent E-Verify enrollment as a searchable 'confirmed' fact — search companyfacts; leave enrollment VERIFY unless facts prove it.
- If the user only wants VERIFY tags filled, say so in editorInstruction and keep surrounding prose intact."""

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
7. NEVER insert citation markers like [E1], [E14], or **[E3]** into the excerpt.
8. Return ONLY JSON: {"replacement": "revised excerpt text only"}
9. Budget/pricing excerpts: NEVER change agency revenue or commission lines to $0 — use commission rate × pass-through or canonical fee from section context; if unknown use [VERIFY: Sonja confirm commission rate and annual media estimate].
10. Do NOT reverse-engineer dollar amounts to hit a user-requested total — each line must trace to the Pricing Guide; suggest tier/scope changes instead (option C).
11. One-time setup/development lines must not be multiplied by 12 unless the excerpt is explicitly a monthly recurring service from the guide.
12. Reference excerpts: include name, title, phone, and email — never "contact on request" or deferral language.
13. PSA/compliance excerpts: add specific acknowledgment language when user asks — cover insurance, living wage, MacBride, Title VI, Chapter 63, audit rights as applicable.
14. NEVER shorten the excerpt. Preserve every paragraph, heading, list item, and sentence the user did not ask to change.
15. When the user asks to fill gaps, placeholders, or [VERIFY] tags: ONLY replace those tags with KB facts — do not rewrite or summarize the surrounding prose."""

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
- kbQueries must target the specific missing facts in the excerpt, not repeat the user's chat message verbatim.
- If the instruction only removes, strips, or makes wording qualitative (no new facts needed), return "kbQueries": []."""

STATIC_SECTION_REDRAFT_PROMPT = """Improve ONE static zö proposal section (company overview, team bios, or case studies).

Use ONLY the knowledge-base excerpts provided. For pull/select sections, include [DESIGNER NOTE: ...] where layout applies.
Address the user's feedback. Do not invent clients, metrics, addresses, phones, or emails.

When rewriting an Our Work / case study to a DIFFERENT client or project from the KB:
- Open the markdown with an H2 for the NEW case study (e.g. `## City of San Leandro: Brand Assessment`).
- Do not keep the old client's name in the leading heading.

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
    if "kbQueries" in raw:
        queries_raw = raw.get("kbQueries") or []
    elif "queries" in raw:
        queries_raw = raw.get("queries") or []
    else:
        queries_raw = None
    if queries_raw is None:
        gap_hint = _gap_fields_from_text(excerpt)[:1]
        queries = [
            f"zö agency {section.title} {rfp.client} {gap_hint[0] if gap_hint else user_message}"[
                :240
            ],
        ]
    else:
        queries = [str(q).strip()[:240] for q in queries_raw if str(q).strip()][:5]
    if near_full:
        editor_instruction = (
            f"{editor_instruction}\n\n"
            "CRITICAL: The user selected most or all of this section. Preserve ALL existing "
            "paragraphs, headings, and prose. Change ONLY what the instruction requires — never "
            "replace the section with a short summary or contact block."
        )
    return editor_instruction, queries


async def _plan_section_improve(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    rfp_section: RfpSectionMap | None,
    user_message: str,
    prior_queries: list[str],
) -> tuple[str, str, list[str]]:
    """LLM understands the user ask + RFP needs first, then plans KB queries.

    Returns (understood_ask, editor_instruction, kb_queries).
    """
    requirements = rfp_section.requirements if rfp_section else []
    retrieval_focus = rfp_section.retrieval_focus if rfp_section else []
    gaps = _gap_fields_from_text(section.content or "")
    raw, _ = await llm.chat_json(
        [
            {"role": "system", "content": SECTION_IMPROVE_PLAN_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Client: {rfp.client}\n"
                    f"Sector: {rfp.sector}\n"
                    f"RFP: {rfp.title}\n"
                    f"Section: {section.title}\n"
                    f"Requirements:\n"
                    + ("\n".join(f"- {r}" for r in requirements) or "- (none mapped)")
                    + f"\nRetrieval focus: {retrieval_focus}\n"
                    f"VERIFY gaps in draft: {gaps or '(none)'}\n"
                    f"Prior queries (DO NOT repeat):\n"
                    + ("\n".join(f"- {q}" for q in prior_queries[:20]) or "- (none)")
                    + f"\n\nUser message:\n{user_message.strip()}\n\n"
                    f"Current draft excerpt:\n{(section.content or '')[:3500]}"
                ),
            },
        ],
        max_tokens=1200,
        temperature=0.2,
    )
    understood = str(raw.get("understoodAsk") or "").strip()
    editor_instruction = str(raw.get("editorInstruction") or user_message).strip()
    queries_raw = raw.get("kbQueries") or raw.get("queries") or []
    used = {q.strip().lower() for q in prior_queries}
    queries: list[str] = []
    for q in queries_raw:
        text = str(q).strip()[:240]
        key = text.lower()
        if text and key not in used:
            queries.append(text)
            used.add(key)
    queries = queries[:6]
    if not understood:
        understood = user_message.strip()[:200] or f"Improve {section.title}"
    if not editor_instruction:
        editor_instruction = user_message.strip() or f"Improve {section.title} against RFP requirements."
    if not queries:
        # Fall back to refined planner if understand-step returned no queries.
        queries = await _plan_refined_queries(
            section=section,
            rfp_section=rfp_section,
            rfp=rfp,
            prior_queries=prior_queries,
            user_message=user_message,
            current_content=section.content or "",
        )
    logger.info(
        "Section improve understood ask for %s: %r → %d KB queries",
        section.id,
        understood[:160],
        len(queries),
    )
    return understood, editor_instruction, queries


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
    lean: bool = False,
) -> tuple[ProposalSection, str, int]:
    """Surgical excerpt edit — splice replacement only.

    lean=True: minimal prompt (excerpt + short neighbors only) — for planned patches
    that do not need a second KB fan-out or full-section dump.
    """
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
        kb_zo_voice="" if lean else kb_zo_voice,
        rfp_client=rfp.client,
        register=register,
    )

    # Protect MANUAL FILL tags from incidental rewrite (mask → validate → unmask).
    masked_excerpt, mfill_originals = _mask_manual_fill_for_rewrite(excerpt)
    masked_content, content_mfill = _mask_manual_fill_for_rewrite(content)
    if content_mfill and not mfill_originals:
        mfill_originals = []
    system_prompt = SELECTION_EDIT_PROMPT
    if mfill_originals or content_mfill:
        system_prompt = f"{SELECTION_EDIT_PROMPT}\n\n{_MANUAL_FILL_PRESERVE_CONSTRAINT}"

    ask_for_compliance = compliance_user_message.strip() or user_message.strip()
    neighbor_before = content[max(0, selection_start - 280) : selection_start]
    neighbor_after = content[selection_end : min(len(content), selection_end + 280)]
    replacement = ""
    provider = _provider_name()
    last_mfill_error: ProposalError | None = None
    for attempt in (1, 2):
        if lean:
            user_block = (
                f"Client: {rfp.client}\n"
                f"Section: {section.title}\n\n"
                f"User instruction:\n{user_message.strip()}\n\n"
                f"Text immediately before excerpt:\n\"\"\"{neighbor_before}\"\"\"\n\n"
                f"Selected excerpt (replace ONLY this span):\n\"\"\"{masked_excerpt}\"\"\"\n\n"
                f"Text immediately after excerpt:\n\"\"\"{neighbor_after}\"\"\"\n\n"
                "Return ONLY the revised excerpt. Do not rewrite surrounding text.\n"
            )
        else:
            user_block = (
                f"BRAND VOICE (mandatory):\n{voice_block}\n\n"
                f"Client: {rfp.client}\n"
                f"Sector: {rfp.sector}\n"
                f"RFP: {rfp.title}\n"
                f"Section: {section.title}\n"
                f"Register: {register}\n\n"
                f"User instruction:\n{user_message.strip()}\n\n"
                f"Selected excerpt (replace ONLY this span):\n\"\"\"{masked_excerpt}\"\"\"\n\n"
                f"Full section (context — do NOT rewrite outside the excerpt):\n"
                f"\"\"\"{masked_content[:8000]}\"\"\"\n\n"
                f"RFP excerpt:\n{rfp_context[:2000]}\n\n"
            )
            if evidence:
                user_block += f"Evidence corpus:\n{_format_evidence(evidence)}\n\n"
            if kb_block.strip():
                user_block += f"KB excerpts:\n{kb_block[:4000]}\n\n"
            if avoidance_block:
                user_block += f"{avoidance_block}\n\n"
            if should_apply_budget_playbook(section, ask_for_compliance):
                user_block += f"{budget_playbook_prompt_block(research=research)}\n\n"
        if attempt == 2 and (mfill_originals or content_mfill):
            user_block = (
                f"RETRY: Your previous output dropped protected «MFILL_N» tokens. "
                f"Copy every «MFILL_N» through unchanged.\n\n{user_block}"
            )

        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_block},
            ],
            max_tokens=2048 if not lean else 1200,
            temperature=0.25,
            node_name="chat_excerpt_edit",
            tier="light" if lean else "heavy",
        )
        replacement = str(raw.get("replacement") or raw.get("content") or "").strip()
        if not replacement:
            raise ProposalError(
                "Selection edit did not return replacement text. Try a more specific instruction.",
                status_code=422,
            )
        try:
            # Only excerpt placeholders must survive in the replacement span.
            replacement = _unmask_manual_fill_checked(
                replacement, mfill_originals, attempt=attempt
            )
            last_mfill_error = None
            break
        except ProposalError as exc:
            last_mfill_error = exc
            logger.warning(
                "Selection edit MANUAL FILL preserve failed attempt %d: %s",
                attempt,
                str(exc)[:200],
            )
            if attempt >= 2:
                raise ProposalError(
                    "Rewrite removed protected [MANUAL FILL] tag(s) twice. "
                    "Ask to fill those tags explicitly with a real value or KB fact, "
                    "or edit a span that does not include them.",
                    status_code=422,
                ) from exc
    if last_mfill_error and not replacement:
        raise last_mfill_error

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

    if not lean:
        replacement = enforce_narrative_voice(
            replacement,
            section_id=section.id,
            title=section.title,
            zo_mode=section.mode,
        )
    from app.services.proposal_manuscript import strip_evidence_citation_markers

    replacement = strip_evidence_citation_markers(replacement)
    # Pure splice — do not run strip/voice on the full section (that mutates prefix/suffix
    # and falsely trips the before/after guards).
    new_content = _splice_selection(
        content,
        start=selection_start,
        end=selection_end,
        replacement=replacement,
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

    # Protect MANUAL FILL tags in the prior draft from incidental rewrite.
    source_for_tags = prior_content or original_content
    masked_prior, mfill_originals = _mask_manual_fill_for_rewrite(source_for_tags)
    # Keep prior_for_agent length behavior but on masked text when tags exist.
    if mfill_originals:
        prior_for_agent, _ = prior_content_for_redraft(
            section.model_copy(update={"content": masked_prior})
        )
    redraft_system = SECTION_REDRAFT_PROMPT
    if mfill_originals:
        redraft_system = f"{SECTION_REDRAFT_PROMPT}\n\n{_MANUAL_FILL_PRESERVE_CONSTRAINT}"

    max_tokens = 8192 if section.word_target >= 1500 else 6144
    content = ""
    provider = _provider_name()
    raw: dict[str, Any] = {}

    for attempt in (1, 2):
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
        if attempt == 2 and mfill_originals:
            user_block = (
                "RETRY: Previous output dropped protected «MFILL_N» tokens. "
                "Copy every «MFILL_N» through unchanged.\n\n"
                + user_block
            )
        if bio_kb.strip():
            user_block += (
                f"\n\n=== 04_Bio approved file (use for Work History, education, accounts) ===\n"
                f"{bio_kb[:50_000]}\n"
            )
        if should_apply_budget_playbook(section, user_message):
            user_block += f"\n{budget_playbook_prompt_block(research=research)}\n"

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
                    {"role": "system", "content": redraft_system},
                    {"role": "user", "content": user_block},
                ],
                max_tokens=max_tokens,
                temperature=0.4,
                node_name="chat_full_redraft",
            )

        from app.services.proposal_langchain_agents import content_from_agent_payload

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
                    {"role": "system", "content": redraft_system},
                    {"role": "user", "content": user_block},
                ],
                max_tokens=max_tokens,
                temperature=0.35,
                node_name="chat_full_redraft",
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

        try:
            content = _unmask_manual_fill_checked(
                content, mfill_originals, attempt=attempt
            )
            break
        except ProposalError as exc:
            logger.warning(
                "Full redraft MANUAL FILL preserve failed attempt %d: %s",
                attempt,
                str(exc)[:200],
            )
            if attempt >= 2:
                raise ProposalError(
                    "Rewrite removed protected [MANUAL FILL] tag(s) twice. "
                    "Ask to fill those tags explicitly with a real value or KB fact, "
                    "or edit a span that does not include them.",
                    status_code=422,
                ) from exc

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
            "designer_note": (
                (raw.get("designerNote") or raw.get("designer_note"))
                if isinstance(raw, dict)
                else None
            ),
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
    masked_prior, mfill_originals = _mask_manual_fill_for_rewrite(prior)
    system_prompt = STATIC_SECTION_REDRAFT_PROMPT
    if mfill_originals:
        system_prompt = f"{STATIC_SECTION_REDRAFT_PROMPT}\n\n{_MANUAL_FILL_PRESERVE_CONSTRAINT}"

    content = ""
    provider = _provider_name()
    raw: dict[str, Any] = {}
    for attempt in (1, 2):
        user_content = (
            f"BRAND VOICE (mandatory — maintain throughout; do not genericize):\n{voice_block}\n\n"
            f"Section: {section.title}\n"
            f"Mode: {section.mode}\n"
            f"Client: {rfp.client}\n"
            f"Sector: {rfp.sector}\n"
            f"User request:\n{user_message}\n\n"
            f"Previous content (preserve zö voice while improving — fill gaps from KB):\n"
            f"{masked_prior[:9000]}\n\n"
            f"KB excerpts:\n{'---'.join(kb_parts)[:14000]}\n\n"
            f"RFP excerpt:\n{rfp_context[:5000]}"
        )
        if attempt == 2 and mfill_originals:
            user_content = (
                "RETRY: Previous output dropped protected «MFILL_N» tokens. "
                "Copy every «MFILL_N» through unchanged.\n\n"
                + user_content
            )
        if avoidance_block:
            user_content += f"\n\n{avoidance_block}"

        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=4096,
            temperature=0.28,
            node_name="chat_full_redraft",
        )
        content = enforce_narrative_voice(
            str(raw.get("content", "")).strip(),
            section_id=section.id,
            title=section.title,
            register="narrative",
        )
        try:
            content = _unmask_manual_fill_checked(
                content, mfill_originals, attempt=attempt
            )
            break
        except ProposalError as exc:
            logger.warning(
                "Static rewrite MANUAL FILL preserve failed attempt %d: %s",
                attempt,
                str(exc)[:200],
            )
            if attempt >= 2:
                raise ProposalError(
                    "Rewrite removed protected [MANUAL FILL] tag(s) twice. "
                    "Ask to fill those tags explicitly with a real value or KB fact, "
                    "or edit a span that does not include them.",
                    status_code=422,
                ) from exc

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


def _find_attestation_section(
    draft: ProposalDraft, preferred_id: str | None = None
) -> ProposalSection | None:
    """Prefer real E-Verify / disclosure tabs over bogus placeholder tabs."""
    from app.services.proposal_chat_structure import _is_bogus_structure_title

    if preferred_id:
        hit = _find_draft_section(draft, preferred_id)
        if hit and not _is_bogus_structure_title(hit.title):
            title = (hit.title or "").casefold()
            body = (hit.content or "")[:500].casefold()
            if any(
                k in title or k in body
                for k in ("e-verify", "affidavit", "disclosure", "conflict of interest")
            ):
                return hit

    ranked: list[tuple[int, ProposalSection]] = []
    for section in draft.sections:
        if _is_bogus_structure_title(section.title):
            continue
        title = (section.title or "").casefold()
        body = (section.content or "")[:600].casefold()
        score = 0
        if "e-verify" in title or "e-verify" in body:
            score += 50
        if "affidavit" in title:
            score += 30
        if "disclosure" in title or "conflict" in title:
            score += 20
        if score:
            ranked.append((score, section))
    if not ranked:
        return _find_draft_section(draft, preferred_id) if preferred_id else None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


async def _apply_attestation_inplace_fix(
    *,
    draft: ProposalDraft,
    section_id: str,
    user_message: str,
) -> tuple[ProposalDraft, ProposalSection, str]:
    """Gate E-Verify / disclosure in place; remove accidental placeholder tabs."""
    from app.services.evidence_trust.legal_attestation_gate import (
        gate_section_legal_attestations,
    )
    from app.services.proposal_chat_structure import _is_bogus_structure_title

    target = _find_attestation_section(draft, section_id) or _find_draft_section(
        draft, section_id
    )
    if target is None:
        raise ProposalError(f"Section {section_id} not found in draft.", status_code=404)

    # Drop bogus 'placeholder: HUMAN SIGN-OFF…' sidebar tabs created by bad chat plans.
    cleaned_sections = [
        s
        for s in draft.sections
        if not _is_bogus_structure_title(s.title) or s.id == target.id
    ]
    if target.id not in {s.id for s in cleaned_sections}:
        cleaned_sections.append(target)

    gated, report = gate_section_legal_attestations(target, force=True)
    body = (gated.content or "").strip()
    # If prior chat turned the body into shouty PLACEHOLDER spam, reset to a clean VERIFY form.
    shouty = body.count("PLACEHOLDER") >= 2 or body.count("HUMAN SIGN-OFF") >= 2
    if shouty or len(body) < 40:
        gated = gated.model_copy(
            update={
                "content": (
                    f"### {gated.title}\n\n"
                    "Contractor / offeror identification (legal name, FEIN, contacts) as "
                    "required by the RFP form.\n\n"
                    "[VERIFY: E-Verify enrollment — unconfirmed in KB — Sonja Anderson or "
                    "Ella Lindau / Operations must confirm before any sworn affidavit or "
                    "penalty-of-perjury attestation]\n\n"
                    "Do not assert active E-Verify participation until that confirmation "
                    "is recorded. Keep required signature/date blocks blank for human sign-off.\n"
                ),
                "status": "outline",
            }
        )
        report.logs.append("Reset shouty placeholder body → clean VERIFY affidavit form")

    sections = [
        gated if s.id == gated.id else s for s in cleaned_sections
    ]
    # Ensure gated section present after filter
    if gated.id not in {s.id for s in sections}:
        sections.append(gated)

    updated = draft.model_copy(
        update={
            "sections": sections,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    removed = len(draft.sections) - len(cleaned_sections)
    bits = [
        f"Updated **{gated.title}** in place — unconfirmed E-Verify / disclosure stays "
        "[VERIFY] for Sonja/Ella (no new sidebar tab)."
    ]
    if removed:
        bits.append(f"Removed {removed} bogus placeholder tab(s).")
    if report.logs:
        bits.append(report.logs[0])
    del user_message  # used for routing only
    return updated, gated, " ".join(bits)


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

    # Powerful chat ops: duplicate audit / fabrication purge (content → RFP → KB)
    from app.services.proposal_chat_ops import classify_chat_op, run_chat_ops

    chat_op = classify_chat_op(user_message)
    if chat_op != "none" and not selection_mode:
        before_ids = [(s.id, s.content or "") for s in draft.sections]
        updated_draft, ops_report = await run_chat_ops(
            kind=chat_op,
            draft=draft,
            rfp=rfp,
            rfp_context=rfp_context,
            research=research,
        )
        provider = _provider_name()
        if research is None:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
        after_ids = [(s.id, s.content or "") for s in updated_draft.sections]
        changed = before_ids != after_ids
        draft = updated_draft

        focus = _find_draft_section(draft, section_id) or (
            draft.sections[0] if draft.sections else None
        )
        if focus is None:
            raise ProposalError("Draft has no sections.", status_code=400)

        if changed and persist:
            draft = await _persist_section_improve_draft(
                draft,
                research,
                section_title=focus.title,
            )
            focus = _find_draft_section(draft, section_id) or focus

        return focus, draft, research, provider, ops_report.reply, changed

    # Case-study replace/improve without a named Our Work tab: ask — never rewrite
    # whatever happens to be open (e.g. Who We Are).
    focus_for_clarify = _find_draft_section(draft, section_id)
    if (
        not selection_mode
        and _message_needs_case_study_clarify(user_message)
        and not _is_our_work_section(focus_for_clarify)
        and not re.search(
            r"\b\d+\.\d+\b|"
            r"(?:section\s*)?\d+\.\d+\s*[—–-]|"
            r"\b(oregon\s+employment|umatilla|san\s+leandro)\b",
            user_message,
            re.I,
        )
    ):
        cases = [s for s in draft.sections if _is_our_work_section(s)]
        if len(cases) != 1:
            provider = _provider_name()
            if research is None:
                research = ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                )
            section = focus_for_clarify or (
                draft.sections[0] if draft.sections else None
            )
            if section is None:
                raise ProposalError("Draft has no sections.", status_code=400)
            return (
                section,
                draft,
                research,
                provider,
                _case_study_clarify_reply(draft, open_section=focus_for_clarify),
                False,
            )

    # Advisory / audit asks ("check all case studies… list which don't") must never
    # rewrite a focused tab. Answer in chat first — skip structure + improve.
    if not selection_mode and not _wants_section_edit(user_message):
        section = _find_draft_section(draft, section_id) or (
            draft.sections[0] if draft.sections else None
        )
        if section is None:
            raise ProposalError("Draft has no sections.", status_code=400)
        requirements_block = _rfp_section_requirements_block(research, section.id)
        manuscript_digest = _manuscript_digest(draft) if proposal_wide else ""
        if manuscript_digest:
            rfp_context = f"{rfp_context}\n\n{manuscript_digest}"
        if requirements_block:
            rfp_context = (
                f"{rfp_context}\n\n--- Mapped section requirements ---\n{requirements_block}"
            )
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
            from app.services.proposal_draft_snapshots import (
                push_before_structure_change_snapshot,
            )

            focus_before = _find_draft_section(draft, section_id)
            pre_title = (
                (focus_before.title if focus_before else None) or "proposal"
            )
            # Always checkpoint the live manuscript BEFORE destructive structure
            # so Saved versions can undo a bad rename/delete (e.g. staffing → stub).
            draft = push_before_structure_change_snapshot(
                draft, section_title=pre_title
            )
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

    # Explicit MANUAL FILL resolution — never invent; user text then KB only.
    if is_manual_fill_request(latest_user_ask):
        target_text = section.content or ""
        sel_start = selection_start
        sel_end = selection_end
        if (
            selection_mode
            and sel_start is not None
            and sel_end is not None
            and sel_end > sel_start
        ):
            target_text = (section.content or "")[sel_start:sel_end]

        if extract_manual_fill_tags(target_text):
            evidence_blob = ""
            if research and research.evidence_corpus:
                evidence_blob = _section_corpus_blob(
                    research.evidence_corpus, section_id
                )
            supplemental = _draft_supplemental_blob(draft)
            kb_blob = "\n\n".join(
                part for part in (evidence_blob, supplemental, rfp_context[:8000]) if part.strip()
            )
            filled, fill_log, remaining = fill_manual_fill_tags(
                target_text,
                user_message=latest_user_ask,
                kb_blob=kb_blob,
            )
            if fill_log:
                if selection_mode and sel_start is not None and sel_end is not None:
                    new_content = _splice_selection(
                        section.content or "",
                        start=sel_start,
                        end=sel_end,
                        replacement=filled,
                    )
                else:
                    new_content = filled
                # Factual substitution only — never brand-voice / tone the resolved value
                # or regenerate surrounding prose as part of MANUAL FILL resolution.
                updated_section = section.model_copy(
                    update={"content": new_content, "status": "generated"}
                )
                provider = "manual-fill"
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
                sources = ", ".join(
                    f"`{e['tag']}` ← {e['source']}" for e in fill_log[:6]
                )
                assistant_message = (
                    f"Resolved **{len(fill_log)}** MANUAL FILL tag(s) from "
                    f"explicit sources ({sources})."
                )
                if remaining:
                    assistant_message += (
                        " Still open (no user value or KB match): "
                        + ", ".join(f"`{t}`" for t in remaining[:6])
                        + "."
                    )
                return (
                    updated_section,
                    updated_draft,
                    research,
                    provider,
                    assistant_message,
                    True,
                )

            # Nothing filled — explain gap, do not invent, do not fall through to
            # a general rewrite that could silently resolve the tags.
            provider = _provider_name()
            if research is None:
                research = ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                )
            gap_list = ", ".join(
                f"`{t.text}`" for t in extract_manual_fill_tags(target_text)[:6]
            )
            reply = (
                "I found MANUAL FILL tag(s) but could not resolve them from your message "
                "or the knowledge base: "
                f"{gap_list}. "
                "Provide the value in chat (e.g. “fill [MANUAL FILL: Title] with Director”) "
                "or add the fact to Supermemory — I will not invent it."
            )
            return section, draft, research, provider, reply, False

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
    # Do NOT refresh full proposal KB (bios/company/case studies) on every chat turn —
    # that gather is for Sections 1–3 generation. Chat patches use targeted queries only.

    # LLM understands the ask → prefers surgical patch(es) over full-section rewrite.
    scope_plan: EditScopePlan | None = None
    planned_spans: list[tuple[int, int, EditScopePatch]] | None = None
    if not selection_mode:
        try:
            scope_plan = await _plan_edit_scope(
                section=section,
                rfp=rfp,
                user_message=latest_user_ask,
            )
            if scope_plan.mode == "patch" and scope_plan.patches:
                planned_spans = _locate_planned_patches(
                    section.content or "",
                    scope_plan.patches,
                )
                if planned_spans:
                    logger.info(
                        "Edit-scope plan → %d patch(es) for %s / %s ask=%r",
                        len(planned_spans),
                        rfp_id,
                        section_id,
                        (scope_plan.understood_ask or latest_user_ask)[:80],
                    )
                    if len(planned_spans) == 1:
                        selection_start, selection_end, only = planned_spans[0]
                        selection_text = (section.content or "")[
                            selection_start:selection_end
                        ]
                        selection_mode = True
                        user_message = only.editor_instruction
                else:
                    logger.info(
                        "Edit-scope plan asked patch but no anchors found in %s — "
                        "falling back to full rewrite",
                        section_id,
                    )
            elif scope_plan.mode == "full_rewrite":
                logger.info(
                    "Edit-scope plan → full_rewrite for %s / %s ask=%r",
                    rfp_id,
                    section_id,
                    (scope_plan.understood_ask or latest_user_ask)[:80],
                )
                if scope_plan.editor_instruction.strip():
                    user_message = scope_plan.editor_instruction
        except Exception:
            logger.exception(
                "Edit-scope planning failed for %s / %s — continuing with default path",
                rfp_id,
                section_id,
            )

    # Multi-patch planned edits: apply every located passage in one turn (end→start).
    if (
        planned_spans
        and len(planned_spans) > 1
        and not selection_mode
    ):
        kb_queries = list((scope_plan.kb_queries if scope_plan else None) or [])
        kb_block = ""
        contact_fact_blob = ""
        if kb_queries:
            kb_block, contact_fact_blob = await _fetch_kb_blob_for_selection(
                kb_queries,
                evidence_blob="",
                supplemental_blob="",
            )
        fact_blob = contact_fact_blob
        working_section = section
        provider = _provider_name()
        total_kb_fills = 0
        applied = 0
        # End→start so earlier char offsets stay valid after each splice.
        for start, end, patch in sorted(planned_spans, key=lambda t: t[0], reverse=True):
            content_now = working_section.content or ""
            if start < 0 or end > len(content_now) or start >= end:
                logger.warning(
                    "Skipping stale patch span %d-%d after prior edit in %s",
                    start,
                    end,
                    section_id,
                )
                continue
            sel_text = content_now[start:end]
            working_section, provider, kb_fills = await _improve_section_selection(
                section=working_section,
                rfp=rfp,
                rfp_context=rfp_context,
                user_message=patch.editor_instruction,
                selection_start=start,
                selection_end=end,
                selection_text=sel_text,
                brand_voice=brand_voice_dict,
                kb_zo_voice=kb_zo_voice,
                evidence=[],
                kb_block=kb_block,
                fact_blob=fact_blob,
                avoidance_block="",
                research=research,
                compliance_user_message=latest_user_ask,
                lean=True,
            )
            total_kb_fills += kb_fills
            applied += 1

        if applied == 0:
            logger.info(
                "Multi-patch plan produced no applied edits for %s — falling through",
                section_id,
            )
        else:
            if research is None:
                research = ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                )
            else:
                research = research.model_copy(update={"provider": provider})
            merged_sections = [
                working_section if s.id == section_id else s for s in draft.sections
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
            after_words = word_count(working_section.content or "")
            assistant_message = (
                f"Updated **{applied}** passage(s) in **{section.title}** "
                f"({before_words} → {after_words} words). "
                f"Scanned the full section for matching issues; surrounding text unchanged."
            )
            if total_kb_fills > 0:
                assistant_message = (
                    f"Filled **{total_kb_fills}** verified fact(s) and updated "
                    f"**{applied}** passage(s) in **{section.title}** "
                    f"({before_words} → {after_words} words)."
                )
            logger.info(
                "Multi-patch section edit complete for %s / %s: %d patches (%d → %d words)",
                rfp_id,
                section_id,
                applied,
                before_words,
                after_words,
            )
            return (
                working_section,
                updated_draft,
                research,
                provider,
                assistant_message,
                True,
            )

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
        planned_patch = bool(
            scope_plan
            and scope_plan.mode == "patch"
            and selection_start is not None
            and selection_end is not None
        )
        if planned_patch:
            # Reuse edit-scope plan — no second planner LLM, no shotgun KB.
            editor_instruction = scope_plan.editor_instruction or user_message
            kb_queries = list(scope_plan.kb_queries or [])
            lean_patch = True
        else:
            editor_instruction, kb_queries = await _plan_selection_edit(
                section=section,
                rfp=rfp,
                user_message=user_message,
                excerpt=excerpt,
                full_content=full_content,
                selection_start=selection_start,
                selection_end=selection_end,
            )
            lean_patch = False
        evidence_blob = ""
        avoidance_block = ""
        evidence: list[EvidenceItem] = []
        if research and not lean_patch:
            avoidance_block = format_avoidance_block(
                research.writing_avoidances,
                research.loss_lessons,
            )
            evidence = _evidence_for_section(section_id, research.evidence_corpus or [])
            if research.evidence_corpus:
                evidence_blob = _section_corpus_blob(research.evidence_corpus, section_id)

        logger.info(
            "Selection KB plan for %s / %s gaps=%r queries=%r lean=%s",
            rfp_id,
            section_id,
            gap_fields,
            kb_queries,
            lean_patch,
        )
        kb_block = ""
        contact_fact_blob = ""
        if kb_queries:
            supplemental = "" if lean_patch else _draft_supplemental_blob(draft)
            kb_block, contact_fact_blob = await _fetch_kb_blob_for_selection(
                kb_queries,
                evidence_blob=evidence_blob,
                supplemental_blob=supplemental,
            )
            if not lean_patch:
                kb_block, contact_fact_blob = await _merge_bio_kb_into_blobs(
                    section,
                    kb_block=kb_block,
                    fact_blob=contact_fact_blob,
                )
        fact_blob = "\n\n".join(
            part
            for part in (
                ("" if lean_patch else full_content),
                contact_fact_blob,
            )
            if part.strip()
        )

        excerpt = (section.content or "")[selection_start:selection_end]
        bio_wh_fills = 0
        if not lean_patch:
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
            new_content = _splice_selection(
                full_content,
                start=selection_start,
                end=selection_end,
                replacement=working_excerpt,
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
            lean=lean_patch,
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
    understood_ask = ""

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
        understood_ask, editor_instruction, planned = await _plan_section_improve(
            section=section,
            rfp=rfp,
            rfp_section=rfp_section,
            user_message=query_focus,
            prior_queries=[*prior_queries, *seeded],
        )
        user_message = editor_instruction
        # Prefer gap seeds first, then understood-ask queries, then fallbacks.
        merged_q: list[str] = []
        used_q = {q.strip().lower() for q in prior_queries}
        for q in [*seeded, *planned]:
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
        # RFP sections: live-search KB for this turn. An empty Phase 2 corpus is OK —
        # chat used to hard-fail here, which blocked cross-tab edits on finished drafts.
        if research is None:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                evidenceCorpus=[],
                rfpSections=[],
            )
        prior_corpus = list(research.evidence_corpus or [])
        if not prior_corpus:
            logger.info(
                "Section improve for %s / %s: empty evidence corpus — "
                "bootstrapping via live KB search this turn",
                rfp_id,
                section_id,
            )

        prior_queries = (research.section_queries or {}).get(section_id, [])
        rfp_section = _find_rfp_section(research, section_id)

        understood_ask, editor_instruction, queries = await _plan_section_improve(
            section=section,
            rfp=rfp,
            rfp_section=rfp_section,
            user_message=query_focus,
            prior_queries=prior_queries,
        )
        user_message = editor_instruction
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

        prior_corpus_len = len(prior_corpus)
        corpus = _merge_hits_into_corpus(prior_corpus, all_hits, section_id)
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

        new_queries = {
            **(research.section_queries or {}),
            section_id: [*prior_queries, *queries],
        }
        updated_rfp_sections: list[RfpSectionMap] = []
        for s in research.rfp_sections or []:
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
                "rfp_sections": updated_rfp_sections or list(research.rfp_sections or []),
                "provider": provider,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    # After LLM rewrite: scrub invented E-Verify / conflict claims, keep VERIFY tags.
    # Also strip internal evidence markers ([E1], **[E14]**, …) — never client-facing.
    from app.services.proposal_manuscript import strip_evidence_citation_markers
    from app.services.proposal_chat_structure import (
        renumber_dynamic_group_titles,
        sync_case_study_title_from_content,
    )

    updated_section = updated_section.model_copy(
        update={
            "content": strip_evidence_citation_markers(updated_section.content or ""),
            "kb_refs": [],
        }
    )
    # Case-study body swapped (e.g. Umatilla → San Leandro) must update the sidebar title.
    updated_section = sync_case_study_title_from_content(updated_section)
    try:
        from app.services.evidence_trust.legal_attestation_gate import (
            gate_section_legal_attestations,
        )

        gated_section, _gate_flags = gate_section_legal_attestations(updated_section)
        if (gated_section.content or "") != (updated_section.content or ""):
            updated_section = gated_section
            updated_section = updated_section.model_copy(
                update={
                    "content": strip_evidence_citation_markers(
                        updated_section.content or ""
                    )
                }
            )
            updated_section = sync_case_study_title_from_content(updated_section)
    except Exception:
        logger.exception("Legal attestation gate failed after section improve")

    merged_sections = [
        updated_section if s.id == section_id else s for s in draft.sections
    ]
    merged_sections = renumber_dynamic_group_titles(merged_sections)
    updated_section = next(
        (s for s in merged_sections if s.id == section_id), updated_section
    )

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
            section_title=updated_section.title,
        )

    word_count_result = word_count(updated_section.content)
    remaining_gaps = _gap_fields_from_text(updated_section.content or "")
    title_for_msg = updated_section.title or section.title
    if is_static:
        assistant_message = (
            f"Ran **{query_count}** gap-targeted KB queries (VERIFY fields + RFP asks), "
            f"re-applied zö brand voice, and rewrote **{title_for_msg}** "
            f"({word_count_result} words)."
        )
    else:
        assistant_message = (
            f"Ran {query_count} new Supermemory queries (different from prior searches), "
            f"added {evidence_added} evidence item(s) to the corpus, preserved brand voice, "
            f"and rewrote **{title_for_msg}** ({word_count_result} words)."
        )
    if title_for_msg != section.title:
        assistant_message += (
            f" Sidebar title updated from **{section.title}** → **{title_for_msg}**."
        )
    if understood_ask:
        assistant_message = f"**Understood:** {understood_ask}\n\n{assistant_message}"
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
