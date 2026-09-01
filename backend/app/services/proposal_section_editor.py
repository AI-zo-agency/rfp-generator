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
from app.services.proposal_draft_structure_stubs import (
    repair_prose_disguised_as_table_rows,
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
    strip_section_draft_stub_manual_fills,
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
    section_has_budget_verify_tags,
    section_is_budget_related,
    should_apply_budget_playbook,
    user_asked_reverse_engineered_total,
    user_asks_budget_explanation,
    user_asks_budget_rebuild,
    user_asks_budget_summary_reconcile,
    user_asks_global_cost_rebuild,
    user_asks_insert_budget_table,
    user_asks_section_budget_fill,
    user_points_at_open_section,
)
from app.services.proposal_budget_content import (
    budget_section_score,
    canonical_budget_summary_figures,
    fill_section_budget_verify_from_canonical,
    insert_budget_table_into_section,
    normalize_fixed_pricing_narrative,
    reconcile_draft_budget_summaries,
    render_budget_markdown,
    render_embedded_budget_table_markdown,
)

_MANUAL_FILL_PRESERVE_CONSTRAINT = (
    "CRITICAL HARD CONSTRAINT — PROTECTED MANUAL FILL PLACEHOLDERS:\n"
    "The text contains «MFILL_N» tokens. These stand for protected [MANUAL FILL …] "
    "tags that MUST appear in your output EXACTLY as «MFILL_N» (same index). "
    "Do not resolve, paraphrase, delete, invent content for, or replace them. "
    "Copy every «MFILL_N» token through unchanged.\n"
)


def _mask_manual_fill_for_rewrite(text: str) -> tuple[str, list[str]]:
    """Mask MANUAL FILL tags before an incidental LLM rewrite.

    Whole-section structure stubs (``Draft this RFP-required section — …``) are
    stripped, not protected — Improve/full redraft is supposed to replace them
    with real prose. Protecting them caused 422s when the model correctly drafted
    the section.
    """
    cleaned = strip_section_draft_stub_manual_fills(text or "")
    from app.services.proposal_manuscript_locks import strip_kpi_lock_manual_fills

    cleaned, _ = strip_kpi_lock_manual_fills(cleaned)
    if not extract_manual_fill_tags(cleaned):
        return cleaned, []
    return mask_manual_fill_tags(cleaned)


def _unmask_manual_fill_checked(output: str, originals: list[str], *, attempt: int) -> str:
    """Restore masked MANUAL FILL tags. Never 422 — splice dropped tags back in.

    Chat Improve used to fail twice with "removed protected MANUAL FILL tag(s)"
    whenever the model omitted «MFILL_N». Hand-off tags stay in the draft instead.
    """
    del attempt
    if not originals:
        return output or ""
    text = unmask_manual_fill_tags(output or "", originals)
    missing = missing_manual_fill_placeholders(output or "", originals)
    if missing:
        logger.warning(
            "Rewrite omitted %s MANUAL FILL tag(s) — restored at end of section",
            len(missing),
        )
        text = (text.rstrip() + "\n\n" + "\n".join(missing)).strip()
    return text

logger = logging.getLogger(__name__)

SECTION_CHAT_ADVISORY_PROMPT = """You are a zö agency proposal editor assistant — sharp, thorough, and honest.

FIRST understand the user's actual question (including typos and missing punctuation).
Answer THAT ask. Do not dump a full-proposal audit unless they asked for a review,
gaps, compliance, or whole-proposal check.

You receive the FULL proposal manuscript digest (every section) plus the currently open
tab for orientation. The manuscript digest is authoritative for whole-proposal questions.

Rules:
1. Answer from the RFP requirements and the FULL proposal manuscript — do not invent
   compliance facts.
2. Whole-proposal / gaps / missing / compliance / trade-secret / terms questions:
   scan EVERY section in the manuscript digest. Never say you only saw the open tab
   or that you need a "full-document pass" — you already have the manuscript.
3. If the user asks about another named section, use that section from the digest.
3a. CRITICAL — "section 11" / "section N" in chat means the PROPOSAL SIDEBAR tab
    (Sidebar N/total in the manuscript, or the AUTHORITATIVE TARGET TAB block).
    It does NOT mean an RFP document heading, evaluation criterion, or SOW clause
    that happens to be numbered the same. When an AUTHORITATIVE TARGET TAB is
    provided, answer ONLY about that tab's title + Open-tab draft. Never describe
    a different sidebar section.
4. If the user asks whether something meets the RFP, cite specific RFP asks and gaps.
5. When the user asks to check / evaluate / list which case studies (or sections) do
   NOT meet the RFP:
   - Review EVERY Our Work / case-study section in the manuscript digest (not only
     the open tab).
   - Return a clear pass/fail (or partial) list with the sidebar title and the unmet
     RFP expectations.
   - Do NOT rewrite any section in this turn.
6. You may disagree or push back when their request would weaken compliance or accuracy.
7. Do NOT rewrite the section in this turn — explain what you would change and why,
   or answer the question.
8. Format the chat reply as clean markdown the UI will render (not plain text):
   - Use **bold** for section titles, Status labels, and key RFP requirements
   - Use "- " bullet lists (one item per line) for audits / gaps / checklists
   - Short paragraphs; blank line between title, status, and list blocks
   - Do NOT wrap the entire reply in one **…**; do NOT escape asterisks as \\*
   - Prefer scannable structure over a dense wall of text
9. If they need an edit, explain the concrete fix in the reply. When the open tab
   has incorrect / invented / unverifiable content you can safely scrub (remove
   invented contacts, drop clients not on ClientList, soft-flag VERIFY) — set
   hasFix=true with an imperative applyInstruction for THAT tab so the UI can
   offer **Apply the fix**. Do not rewrite the draft in this turn.
   Multi-item recommendation lists still count as one single-section hasFix —
   put the full scrub checklist into applyInstruction. Never invent KB contacts
   in applyInstruction (say remove or [VERIFY: Sonja…]).
   When the only safe edit is inserting a [VERIFY: …] flag (e.g. buyer board
   roster zö cannot independently confirm) — still set hasFix=true with
   applyInstruction that inserts that exact VERIFY tag once and leaves verified
   contact fields / disclosure answers unchanged. "No structural rewrite" does
   NOT mean hasFix=false when a VERIFY flag should be planted.
9a. Highlighted excerpt + verify/confirm/check — OR the user says the excerpt is
    wrong/incorrect: they want a VERDICT on that text, not a rewrite. You MUST use
    the Verified KB facts block when present — never answer from memory alone.
    Answer in this shape:
    - State the verdict first: **Correct**, **Incorrect**, or **Cannot confirm**.
      **Cannot confirm** means KB has no matching capacity/pricing guide — NOT that
      the table is wrong. Still check internal arithmetic (do line items sum to the
      stated total?) and say so clearly.
    - Quote the KB fact you checked against and name its source doc
      (e.g. 01_companyfacts verified.docx or 04_Bio_FirstLast.pdf). Prefer
      verified.docx over older .md when both appear. If a Verified 04_Bio KB
      block is present, those people HAVE bios in the KB — never say there is
      no bio, and never recommend invented stand-ins (Drew Stone, Brittany
      Frazier, or anyone not in that 04_Bio block / companyfacts / org chart).
      If no KB excerpt covers it, say so plainly —
      never imply you verified something you only read back from the draft.
    - If it is wrong or needs a scrub, show what to change and set hasFix so they
      can apply it. Do not apply it in this turn.
    - **Cannot confirm** on capacity/hours/pricing: set hasFix=false unless you are
      asking to insert a specific [PRICING FLAG: …] — then hasFix=true with that
      flag in applyInstruction. Never use contact-scrub language for hour tables.
    - Contact fields (email, phone, website): use ONLY the value in Verified KB
      facts / CANONICAL CONTACT from 01_companyfacts. Never substitute a different
      @zo.agency address from memory (e.g. hello@ or info@ when companyfacts says
      connect@). Won/finalist proposals may repeat contact info but companyfacts
      wins for agency-wide Business Information.
9b. Awards & Recognition: NEVER say the KB has no awards inventory until PACKED KB /
    Verified KB facts blocks are present in this prompt. Plan Supermemory queries
    for 05_Awards and companyfacts when the user asks to add or populate an awards
    table. Populate rows ONLY from retrieved KB snippets (award name, issuer, year).
    Never invent rows. Never output "TBD — Needs your input" placeholder tables —
    use [MANUAL FILL: Sonja — confirm from 05_Awards] per missing cell after KB
    search. For "add awards table" asks on the open tab: set hasFix=true with an
    applyInstruction to query KB (05_Awards / companyfacts) and insert a populated
    markdown table after Scored Capability (or where the section structure requires).
10. Budget/pricing/fees: follow the pricing playbook when provided — refuse invented
    numbers and reverse-engineered totals; flag out-of-guide scope with
    [PRICING FLAG: … — Sonja review required].
11. For duplicates / fabrication / ClientList trust: prefer directing them to say
    **check duplicates**, **remove duplicates**, or **remove fabricated content** so
    the system can run the full content→RFP→KB pipeline. Also recognize these issue
    classes and set hasFix when the open tab has them:
    - **Past-proven capability fabrication**: tables/prose saying "we have implemented /
      integrated …" (enterprise permissions, municipal integrations, etc.) when case
      studies/bios do not evidence that past delivery → applyInstruction: rewrite to
      capability-we-can-deliver OR [VERIFY: substantiate from 03_CS / 04_Bio].
    - **Bio year / specialization inflation**: draft years higher than 04_Bio KB, or
      government/municipal/enterprise specialization not in the bio PDF → applyInstruction:
      REPLACE the invented sentences with 2–4 sentences copied from that person's 04_Bio
      (years, tools, markets the KB actually states). NEVER delete the paragraph and leave
      only a Role line. NEVER invent a replacement specialization. Also strip [E#] citation
      markers and drop empty headers with no body (e.g. Team Qualifications Summary).
    When the user pastes a multi-item audit covering capability + bio fabrications across
    tabs, tell them to say **remove fabricated content** or **fix these content issues**
    so the proposal-wide purge runs — still set hasFix for the open tab's safe scrub.

Return ONLY JSON:
{
  "reply": "<markdown chat message with **bold** and bullets as needed>",
  "hasFix": false,
  "applyInstruction": "",
  "summary": "",
  "sectionId": "",
  "sectionTitle": ""
}

When hasFix is true:
- applyInstruction must be an imperative single-section edit instruction the rewriter
  can follow (name the sidebar section; say exactly what to change / remove / VERIFY).
- summary is a short label for the UI button context (one line).
- sectionId / sectionTitle must identify the ONE sidebar tab to edit (use the
  AUTHORITATIVE TARGET TAB / open tab when bound; never invent ids).
- Never set hasFix for whole-proposal / every-section / multi-tab work.
- DO set hasFix for reference/fact audits on the open tab even when there are
  several recommendations — one applyInstruction covering the safe scrub.
When hasFix is false, leave applyInstruction/summary/sectionId empty (only when
the draft needs no change, e.g. **Correct** / fully accurate)."""

# Explicit mutate verbs — required before we rewrite a section.
from app.services.proposal_draft_llm import chat_json_with_repair
from app.services.proposal_chat_verbs import (
    ADD_VERBS,
    EDIT_VERBS,
    QUESTION_OPENERS,
    verb_alternation,
)


_IN_PLACE_CONTENT_ADD_RE = re.compile(
    r"(?is)\b(?:add|insert|include|put)\b.{0,50}\b("
    r"client\s+voice|testimonial|quote|kpi|metrics?|results?|"
    r"challenge|solution|approach|client\s+feedback|voice\s+of\s+the\s+client"
    r")\b"
)


def _should_skip_structure_planner(
    chat_intent: str,
    *,
    user_message: str,
    selection_mode: bool,
    apply_fix: bool,
    improve_section_pinned: bool,
) -> bool:
    """Content edits on the bound tab — not outline add/delete/rename.

    Outline planner runs only for ``structure`` intent or clear add-tab asks.
    Improve-pin / single_edit content asks must NOT hit the outline planner
    (that produced "couldn't plan the outline change" for 'make it concise').
    """
    from app.services.proposal_chat_structure import (
        is_add_section_intent,
        is_bio_resume_attachment_intent,
    )

    # Bio → designer PDF note must run the planner (stub in place), not a rewrite
    # and not a sidebar delete — even when Improve is pinned or the ask says "here".
    if is_bio_resume_attachment_intent(user_message):
        return False
    # Add/delete sidebar tabs always go through the structure planner — even when
    # Improve is pinned or the classifier guessed single_edit.
    if is_add_section_intent(user_message):
        return False
    # "Improve this section" is a content rewrite on the open tab — never outline
    # clarify, even if the classifier guessed structure.
    if user_points_at_open_section(user_message):
        return True
    if chat_intent == "structure":
        return False
    if selection_mode or apply_fix or improve_section_pinned:
        return True
    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill
    from app.services.proposal_verify_optional_scrub import (
        user_asks_scrub_optional_verify,
        user_asks_strip_inline_evidence_tags,
    )

    if (
        user_asks_kb_fetch_or_fill(user_message)
        or _selection_asks_to_fill_verify(user_message)
        or user_asks_scrub_optional_verify(user_message)
        or user_asks_strip_inline_evidence_tags(user_message)
        or _open_tab_verify_resolve_ask(user_message)
    ):
        return True
    # Content edits / advisory / multi-patch — never outline clarify.
    if chat_intent in {"single_edit", "multi_patch", "advisory"}:
        return True
    return False


def _chat_improve_skip_kb(gate: Any, user_message: str) -> bool:
    """True when this chat rewrite does not need a second understand/KB-plan LLM.

    Evidence gate already decided WRITE / MANUAL FILL / cleanup. Running
    `_plan_section_improve` (previously unnamed → quality model) then throwing
    the queries away was a wasted hop.
    """
    from app.services.proposal_evidence_gate import EvidenceDecision
    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

    if user_asks_kb_fetch_or_fill(user_message) or _selection_asks_to_fill_verify(
        user_message
    ):
        return False
    if gate is None:
        return False
    return bool(
        not gate.requires_retrieval
        or gate.action
        in {
            EvidenceDecision.WRITE_FROM_PLAN,
            EvidenceDecision.MANUAL_FILL,
            EvidenceDecision.DETERMINISTIC_CLEANUP,
            EvidenceDecision.VERIFY_FIELD,
        }
    )


def _advisory_needs_kb_lookup(user_message: str, excerpt: str) -> bool:
    """KB retrieval for advisory: fact-check / fetch / pinned-wrong — not 'what is this?'.

    Highlighting text used to trigger a query-planner LLM + two KB fetches on every
    question, including 'what does this mean?'.
    """
    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

    text = user_message or ""
    if user_asks_kb_fetch_or_fill(text):
        return True
    if _is_verification_only_ask(text) or _VERIFY_ASK_RE.search(text):
        return True
    pin = (excerpt or "").strip()
    if pin and _EXCERPT_CLAIMED_WRONG_RE.search(text):
        return True
    # Pinned excerpt + a correctness question (not a meaning/what-is ask).
    if pin and not _is_informational_only_ask(text):
        if _ADVISORY_INTENT_RE.search(text) or text.strip().endswith("?"):
            return True
    return False


# Built from the shared vocabulary so the three edit-verb lists in this codebase
# cannot drift apart again. See proposal_chat_verbs.EDIT_VERBS.
_EDIT_INTENT_RE = re.compile(
    r"\b("
    + verb_alternation(EDIT_VERBS)
    + r"|make\s+it|make\s+this|"
    + r"(?:" + verb_alternation(ADD_VERBS) + r")\b.{0,50}\b"
    + r"(?:section|tab|h2|paragraph|sentence|case\s*stud(?:y|ies)?|"
    + r"bios?|bullet|row|line)\b|"
    + r"more\s+\d*\s*(?:team\s*)?bios?"
    + r")",
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

# Interrogative openers that make a message a question about the draft rather than
# an instruction to change it. See proposal_chat_verbs.QUESTION_OPENERS.
_QUESTION_OPENER_RE = re.compile(
    r"^\s*(?:" + "|".join(o.replace(" ", r"\s+") for o in QUESTION_OPENERS) + r")\b",
    re.I,
)


# "check X and then fix it" — an advisory opener followed by an instruction is an
# edit. Uses the shared vocabulary; the old hardcoded seven verbs missed
# "review this and tighten it up".
_FOLLOW_WITH_MUTATE_RE = re.compile(
    r"\b(?:then|and)\s+(?:please\s+)?(?:" + verb_alternation(EDIT_VERBS) + r")\b",
    re.I,
)

# Verification asks. None of these are edit verbs, and with an excerpt pinned
# they are the most common thing a user actually wants: an answer about the
# highlighted text, not a rewrite of it.
_VERIFY_ASK_RE = re.compile(
    r"\b(?:verify|verifying|confirm|confirming|"
    r"double[\s-]?check|sanity[\s-]?check|cross[\s-]?check|cross[\s-]?verify|"
    r"fact[\s-]?check|"
    r"are\s+you\s+sure|is\s+that\s+(?:right|correct|accurate)|"
    r"fabricat\w*|fabri\w*act\w*|made[\s-]?up|"
    r"(?:everything|this|it)\s+(?:is\s+)?(?:true|truth|accurate|real)\b|"
    r"(?:any|is\s+there|are\s+there)\s+(?:fake|false|fabricat\w*|made[\s-]?up)"
    r")\b",
    re.I,
)

_EXCERPT_CLAIMED_WRONG_RE = re.compile(
    r"\b(?:wrong|incorrect|inaccurate|not\s+right|isn'?t\s+right|typo|mistake)\b",
    re.I,
)

_SECTION_MARK_IN_TITLE_RE = re.compile(
    r"^\s*\d+(?:\.\d+)*\s*[.:—–\-)]\s*",
    re.I,
)

_COMPANYFACTS_SOURCE_RE = re.compile(r"01_companyfacts", re.I)
_ZO_AGENCY_EMAIL_RE = re.compile(r"\b([a-z0-9._+-]+@zo\.agency)\b", re.I)


def _companyfacts_source_rank(source: str) -> int:
    """Lower rank = higher priority. verified.docx beats stale .md."""
    s = (source or "").casefold()
    if "01_companyfacts" not in s:
        return 2
    if "verified" in s:
        return 0
    return 1


def _extract_companyfacts_contact_pin(facts_block: str) -> str:
    """Pull canonical email/phone/website only from 01_companyfacts lines."""
    if not (facts_block or "").strip():
        return ""
    companyfacts_lines = [
        ln
        for ln in facts_block.splitlines()
        if _COMPANYFACTS_SOURCE_RE.search(ln)
    ]
    if not companyfacts_lines:
        return ""
    verified_lines = [
        ln for ln in companyfacts_lines if "verified" in ln.casefold()
    ]
    blob = "\n".join(verified_lines or companyfacts_lines)

    email = ""
    for match in _ZO_AGENCY_EMAIL_RE.finditer(blob):
        email = match.group(1).casefold()
        break

    phone = ""
    phone_match = re.search(
        r"(?:\*\*Main Phone:\*\*|Main Phone:)\s*(\(\d{3}\)\s*\d{3}-\d{4})",
        blob,
        re.I,
    )
    if phone_match:
        phone = phone_match.group(1).strip()
    if not phone:
        generic = re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", blob)
        if generic:
            phone = generic.group(0).strip()

    website = ""
    site_match = re.search(
        r"(?:\*\*Website:\*\*|Website:)\s*([a-z0-9][a-z0-9.-]+\.[a-z]{2,})",
        blob,
        re.I,
    )
    if site_match:
        website = site_match.group(1).strip().rstrip(".")

    if not any((email, phone, website)):
        return ""

    lines = [
        "CANONICAL CONTACT (01_companyfacts verified — cite exactly; "
        "do NOT substitute a different @zo.agency address):"
    ]
    if email:
        lines.append(f"- Email: {email}")
    if phone:
        lines.append(f"- Main phone: {phone}")
    if website:
        lines.append(f"- Website: {website}")
    return "\n".join(lines)


def _is_verification_only_ask(user_message: str) -> bool:
    """True when the user wants a fact-check verdict and no draft mutation.

    'cross verify … fabricated?' must stay advisory even if the classifier
    wrongly returns single_edit. Explicit remove/rewrite of fabricated content
    still goes through the edit / chat-ops path.
    """
    text = (user_message or "").strip()
    if not text:
        return False
    if not _VERIFY_ASK_RE.search(text):
        return False
    if _EDIT_INTENT_RE.search(text) and not _QUESTION_OPENER_RE.match(text):
        # "remove fabricated content" / "fix the fake numbers" are edits.
        return False
    return True


_INFORMATIONAL_ASK_RE = re.compile(
    r"(?is)^\s*(?:what|whats|what'?s|who|why|when|where|how|explain|summarize|"
    r"describe|tell\s+me)\b"
    r"|\bwhat\s+(?:is|are|'s|does|this|the|about)\b"
    r"|\b(?:this\s+)?section\s+about\b"
    r"|\babout\s*(?:this\s+)?(?:section|tab|part)?\s*\?*\s*$"
)


def _is_informational_only_ask(user_message: str) -> bool:
    """True for 'what is this section about?' — answer only, never rewrite."""
    text = (user_message or "").strip()
    if not text:
        return False
    if not _INFORMATIONAL_ASK_RE.search(text):
        return False
    if _EDIT_INTENT_RE.search(text) and not _QUESTION_OPENER_RE.match(text):
        return False
    return True


def _clean_section_title_for_kb(title: str) -> str:
    """Strip sidebar marks like '3.1 —' so KB search can match 03_CS_ filenames."""
    cleaned = _SECTION_MARK_IN_TITLE_RE.sub("", (title or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" —–-")
    return cleaned


def _verification_needles_from_content(title: str, content: str) -> list[str]:
    """Pull client / project names from the open case-study tab for KB search."""
    needles: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        text = re.sub(r"\s+", " ", (value or "").strip())
        if len(text) < 4:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        needles.append(text)

    clean_title = _clean_section_title_for_kb(title)
    if clean_title:
        _add(clean_title)
        # Drop trailing years so "… Campaign 2006" still matches undated chunks.
        _add(re.sub(r"\b(?:19|20)\d{2}\b", "", clean_title).strip(" —–-"))

    blob = f"{title}\n{(content or '')}"
    for match in re.finditer(
        r"\b(?:City of|County of|Town of)\s+[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){0,3}",
        blob,
    ):
        full = match.group(0)
        _add(full)
        parts = full.split()
        # Always keep the short client form ("City of Umatilla") so filenames
        # like 06_WON_CityofUmatilla_* match.
        if len(parts) >= 3:
            _add(" ".join(parts[:3]))
    for match in re.finditer(
        r"\bRock the Locks?(?:\s+(?:Music\s+)?Festival)?\b",
        blob,
        re.I,
    ):
        full = match.group(0)
        _add(full)
        _add("Rock the Locks")
        _add("Rock the Lock")
    # Quoted campaign / festival titles in ALL CAPS or Title Case headings.
    for match in re.finditer(
        r"(?m)^(?:#{1,3}\s*)?([A-Z][A-Za-z0-9'’&\-, ]{8,60}(?:Festival|Campaign|Initiative))\s*$",
        content or "",
    ):
        _add(match.group(1))
    return needles[:8]


def _build_verification_kb_queries(
    *,
    section: ProposalSection,
    user_message: str,
    excerpt: str,
    rfp_client: str = "",
    rfp_sector: str = "",
    rfp_title: str = "",
) -> list[str]:
    """KB queries for fact-check asks — never the section number or chat chrome."""
    seeds: list[str] = []
    needles = _verification_needles_from_content(
        section.title or "", section.content or ""
    )
    # Prefer short entity seeds first so "Rock the Lock" is not crowded out by
    # long title duplicates before the query cap.
    short_first = sorted(needles, key=len)
    for needle in short_first:
        seeds.append(needle)
    for needle in short_first:
        seeds.append(f"{needle} case study")
    excerpt_snip = re.sub(r"\s+", " ", (excerpt or "").strip())[:120]
    if excerpt_snip and len(excerpt_snip) >= 12:
        seeds.append(excerpt_snip)

    from app.services.proposal_capability_bio_grounding import named_people_in_section

    person_seeds: list[str] = []
    for person in named_people_in_section(
        section, user_message=f"{user_message or ''}\n{excerpt or ''}"
    )[:4]:
        person_seeds.append(person)
        person_seeds.append(f"04_Bio {person}")
    seeds = person_seeds + seeds

    # Keep short proper-noun spans from the user ask (e.g. "Umatilla"), drop verbs.
    ask = (user_message or "").strip()
    ask_clean = _VERIFY_ASK_RE.sub(" ", ask)
    ask_clean = re.sub(
        r"(?i)\b(?:for|section|can|you|just|if|everything|any|there|please|"
        r"values?|numbers?|claims?|facts?|about|this|the|a|an|or|and)\b",
        " ",
        ask_clean,
    )
    ask_clean = re.sub(r"\d+(?:\.\d+)*", " ", ask_clean)
    ask_clean = re.sub(r"[^\w\s'’\-]", " ", ask_clean)
    ask_clean = re.sub(r"\s+", " ", ask_clean).strip()
    if len(ask_clean) >= 4:
        seeds.append(ask_clean)

    queries: list[str] = []
    seen: set[str] = set()
    for seed in seeds:
        if not seed.strip():
            continue
        q = proposal_knowledge_base_tools.normalize_zo_kb_query(
            seed,
            rfp_client=rfp_client,
            rfp_sector=rfp_sector,
            rfp_title=rfp_title,
        ).strip()
        # Guard: never send sidebar marks into hybrid search.
        q = _SECTION_MARK_IN_TITLE_RE.sub("", q)
        q = re.sub(r"\s+", " ", q).strip()
        key = q.casefold()
        if len(q) < 8 or key in seen:
            continue
        if re.search(r"(?i)\b(?:verify|fabricat|truth|cross)\b", q):
            continue
        seen.add(key)
        queries.append(q[:220])
        if len(queries) >= 8:
            break
    return queries


async def _plan_verification_kb_queries(
    *,
    section: ProposalSection,
    user_message: str,
    excerpt: str,
    rfp_client: str = "",
    rfp_sector: str = "",
    rfp_title: str = "",
    research: ProposalResearchCache | None = None,
) -> list[str]:
    """Agent decides Supermemory queries for fact-check / pinned-excerpt asks."""
    heuristic = _build_verification_kb_queries(
        section=section,
        user_message=user_message,
        excerpt=excerpt,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_title=rfp_title,
    )
    from app.services import llm

    if not llm.is_configured():
        return heuristic

    from app.services.proposal_langchain_agents import AgentRole, plan_section_queries_agent

    task_parts = [
        "Fact-check ask: plan Supermemory queries to verify whether the draft "
        "excerpt or user concern matches zö agency verified facts.",
        "Prefer 01_companyfacts verified.docx for agency profile (legal name, "
        "email, phone, team size, certifications). Use 03_CS / 06_WON for "
        "case-study claims. For capacity/hours/budget tables use 00_Guide_Pricing. "
        "For awards / recognition / agency honors asks, plan queries for 05_Awards "
        "and companyfacts awards sections — YOU decide exact query wording. "
        "Return queries only — empty list if KB cannot help.",
        f"User message: {user_message.strip()}",
        f"Open tab: {section.title or ''}",
    ]
    if excerpt.strip():
        task_parts.append(f"Highlighted excerpt under review:\n{excerpt[:800]}")
    task = "\n\n".join(task_parts)

    try:
        planned = await plan_section_queries_agent(
            role=AgentRole.QUERY_PLANNER,
            rfp_client=rfp_client,
            rfp_sector=rfp_sector,
            rfp_title=rfp_title,
            section_title=section.title or "",
            requirements=_rfp_section_requirements_list(research, section.id),
            retrieval_focus=[excerpt[:400]] if excerpt.strip() else [],
            prior_queries=heuristic,
            user_message=task,
            current_content=(section.content or "")[:2000],
        )
    except Exception:
        logger.warning(
            "Verification query planner failed for %s", section.id, exc_info=True
        )
        return heuristic

    merged: list[str] = []
    seen: set[str] = set()
    for q in [*heuristic, *(planned or [])]:
        key = q.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(q)
        if len(merged) >= 8:
            break
    return merged


def _selection_ask_is_advisory(
    user_message: str,
    *,
    conversation_history: list[dict[str, str]] | None = None,
) -> bool:
    """True when a pinned-excerpt turn asks a question rather than ordering an edit.

    Pinning an excerpt says WHAT the user is talking about, not that they want it
    changed. Assuming otherwise made "can you verify if it is Z'Onion?" rewrite
    the line instead of answering it.

    The default here is the opposite of _wants_section_edit(): clicking
    "Revise content" is itself edit intent, so a pinned turn edits unless it
    reads as a question.
    """
    text = (user_message or "").strip()
    if not text:
        return False
    # "check this and then fix it" — advisory opener, edit instruction.
    if _FOLLOW_WITH_MUTATE_RE.search(text):
        return False
    # "yes" / "do it" after the assistant offered a change applies that change.
    if _SHORT_CONFIRM_RE.match(text) and conversation_history:
        return False
    # An explicit edit verb wins unless the sentence is built as a question:
    # "shorten this" edits, "is this short enough?" asks.
    if _EDIT_INTENT_RE.search(text) and not _QUESTION_OPENER_RE.match(text):
        return False
    return bool(
        _VERIFY_ASK_RE.search(text)
        or _ADVISORY_INTENT_RE.search(text)
        or _QUESTION_OPENER_RE.match(text)
        or text.endswith("?")
    )


def _user_asks_replace_section_for_other_rfp_need(text: str) -> bool:
    """Replace THIS tab's topic with a different RFP requirement (not polish same content)."""
    raw = text or ""
    if not re.search(
        r"(?is)\b(replace|swap|change|rewrite|redo)\b.{0,80}\b("
        r"section|this\s+(?:section|tab)|tab\b|it\b"
        r")\b",
        raw,
    ) and not re.search(r"(?is)\breplace\s+section\s+\d+\b", raw):
        return False
    return bool(
        re.search(
            r"(?is)\b("
            r"other|another|different|new\s+section|rfp\s+need|needs?\s+of\s+(?:the\s+)?rfp|"
            r"according\s+to\s+(?:the\s+)?rfp|scan\s+(?:the\s+)?rfp|"
            r"uncovered|missing\s+(?:rfp\s+)?(?:need|requirement)"
            r")\b",
            raw,
        )
    )


def _existing_sidebar_titles(draft: ProposalDraft, *, exclude_id: str) -> list[str]:
    return [
        (s.title or "").strip()
        for s in draft.sections
        if s.id != exclude_id and (s.title or "").strip()
    ]


async def _pick_and_draft_replacement_rfp_section(
    *,
    section: ProposalSection,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_context: str,
    research: ProposalResearchCache | None,
    user_message: str,
) -> tuple[str, str, str]:
    """Return (new_title, new_content, rationale) for a different RFP need."""
    import json

    other_titles = _existing_sidebar_titles(draft, exclude_id=section.id)
    mapped: list[dict[str, Any]] = []
    if research and research.rfp_sections:
        for rs in research.rfp_sections[:20]:
            mapped.append(
                {
                    "title": rs.title,
                    "requirements": (rs.requirements or [])[:8],
                    "uncovered": (rs.uncovered_requirements or [])[:6],
                }
            )

    prompt = (
        f"RFP: {rfp.title} — {rfp.client}\n\n"
        f"User ask:\n{user_message.strip()}\n\n"
        f"CURRENT sidebar tab to REPLACE (do NOT keep this topic):\n"
        f"Title: {section.title}\n"
        f"Opening excerpt:\n{(section.content or '')[:900]}\n\n"
        f"Other sidebar titles already present — do NOT duplicate these topics:\n"
        + "\n".join(f"- {t}" for t in other_titles[:40])
        + "\n\n"
        f"Mapped RFP sections / requirements:\n{json.dumps(mapped, indent=2)[:10_000]}\n\n"
        f"RFP excerpt:\n{rfp_context[:14_000]}\n\n"
        "Task: Replace the CURRENT tab with a DIFFERENT RFP submission/evaluation need.\n"
        "Rules:\n"
        "1. newTitle must match THIS RFP's language and must NOT be a near-duplicate of any "
        "other sidebar title (especially not another tourism/experience rehash of Section 3).\n"
        "2. Prefer an uncovered or weakly covered RFP ask (forms, compliance, approach detail, "
        "timeline, evaluation response, etc.) that the outline is missing.\n"
        "3. Write full section content in zö voice. Do not invent facts — use [VERIFY: …] "
        "when KB/RFP facts are missing. Keep it dense (target ~400–700 words), not padded.\n"
        "4. Do NOT polish or lightly edit the old tourism/experience narrative — change the topic.\n\n"
        "Return ONLY JSON:\n"
        '{"newTitle":"…","content":"full markdown body","rationale":"one sentence"}'
    )

    raw, _ = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You replace one proposal sidebar section with a different RFP need. "
                    "Never keep the old topic. Never invent company facts."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=16000,
        temperature=0.2,
        tier="heavy",
        node_name="chat_replace_section_rfp_need",
    )
    new_title = str((raw or {}).get("newTitle") or "").strip()
    content = str((raw or {}).get("content") or "").strip()
    rationale = str((raw or {}).get("rationale") or "").strip()
    if not new_title or not content:
        raise ProposalError(
            "Could not draft a replacement RFP section topic. Name the requirement to cover.",
            status_code=422,
        )
    # Guard: if model kept nearly the same title, force a clearer failure for retry messaging
    old_core = re.sub(r"[^a-z0-9]+", "", (section.title or "").casefold())
    new_core = re.sub(r"[^a-z0-9]+", "", new_title.casefold())
    if old_core and new_core and (
        old_core == new_core or old_core in new_core or new_core in old_core
    ):
        raise ProposalError(
            "Replacement kept the same topic. Say which RFP requirement to cover instead "
            f"(current title is still “{section.title}”).",
            status_code=422,
        )
    return new_title, content, rationale or "Replaced with a different RFP need."


def _user_asks_remove_percent_time_column(text: str) -> bool:
    """Drop the Percent-Time column from a staffing table (not a VERIFY scrub)."""
    return bool(
        re.search(
            r"(?is)\b(remove|drop|delete|omit)\b.{0,48}\bpercent[-\s]?time\b.{0,24}\bcolumn\b"
            r"|"
            r"\bpercent[-\s]?time\b.{0,24}\bcolumn\b.{0,24}\b(remove|drop|delete|omit)\b",
            text or "",
        )
    )


def _user_asks_percent_time_integrity(text: str) -> bool:
    """Flag/replace invented percent-time cells — no KB hunt required."""
    raw = text or ""
    if _user_asks_remove_percent_time_column(raw):
        return False
    if not re.search(r"(?i)\bpercent[-\s]?time\b|\b%\s*time\b|\bFTE\b", raw):
        return False
    return bool(
        re.search(
            r"(?is)\b("
            r"verify|cross[-\s]?check|unsourced|don'?t\s+invent|do\s+not\s+invent|"
            r"kb|knowledge[\s-]?base|replace|flag|scrub"
            r")\b",
            raw,
        )
    )


def _find_section_with_percent_time_table(
    draft: ProposalDraft,
) -> ProposalSection | None:
    """Prefer the section that actually has a Percent-Time table (not a bio tab)."""
    scored: list[tuple[int, ProposalSection]] = []
    for section in draft.sections:
        body = section.content or ""
        if not re.search(r"(?i)percent[-\s]?time", body):
            continue
        score = 0
        if re.search(r"(?im)^\s*\|[^\n]*percent[-\s]?time", body):
            score += 50
        if re.search(r"(?i)\[VERIFY:\s*percent\s*time\]", body):
            score += 20
        title = (section.title or "").casefold()
        if any(k in title for k in ("team", "staff", "personnel", "qualification")):
            score += 10
        if section.id.startswith("section-2-bio"):
            score -= 30
        scored.append((score, section))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _remove_markdown_table_column(content: str, *, header_pat: str) -> tuple[str, int]:
    """Remove one markdown table column whose header matches header_pat. Returns (text, rows touched)."""
    header_re = re.compile(header_pat, re.I)
    lines = (content or "").splitlines(keepends=False)
    out: list[str] = []
    col_idx: int | None = None
    touched = 0

    def _cells(line: str) -> list[str] | None:
        stripped = line.strip()
        if not stripped.startswith("|"):
            return None
        return [c.strip() for c in stripped.strip("|").split("|")]

    for line in lines:
        cells = _cells(line)
        if cells is None:
            col_idx = None
            out.append(line)
            continue
        if col_idx is None:
            for i, cell in enumerate(cells):
                if header_re.search(cell):
                    col_idx = i
                    break
            if col_idx is None:
                out.append(line)
                continue
        if col_idx is not None and len(cells) > col_idx:
            kept = cells[:col_idx] + cells[col_idx + 1 :]
            out.append("| " + " | ".join(kept) + " |")
            touched += 1
        else:
            out.append(line)

    text = "\n".join(out)
    # Drop leftover commitment sentences that only exist for the % column.
    text, _ = re.subn(
        r"(?im)^[^\n]*percent[-\s]?time\s+commitments?[^\n]*\n?",
        "",
        text,
    )
    return text, touched


_SHORT_CONFIRM_RE = re.compile(
    r"^(?:yes|ok|okay|please|go\s+ahead|do\s+it|yep|yup|sure|confirmed?|"
    r"yes\s+(?:apply|do\s+it|please|go))\s*[.!]?$",
    re.I,
)


def _wants_section_edit(user_message: str, *, conversation_history: list[dict[str, str]] | None = None) -> bool:
    """True only when the user clearly asks to mutate draft text.

    Default is advisory. Phrases like "check all case studies… list which don't"
    must NOT trigger a rewrite of the focused tab.

    Multi-section apply/fix asks are classified by the LLM in improve_section_with_chat
    (not by keyword lists here).
    """
    text = user_message.strip()
    if not text:
        return False

    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

    # Questions first. "what's missing from this section?" is advisory — it must
    # not be treated as a KB-fill rewrite just because it says "missing".
    if (
        _is_informational_only_ask(text) or _is_verification_only_ask(text)
    ) and not _FOLLOW_WITH_MUTATE_RE.search(text):
        if not user_asks_kb_fetch_or_fill(text):
            return False

    # KB fetch/fill always mutates the open tab — never answer-only advisory.
    if user_asks_kb_fetch_or_fill(text) or _open_tab_verify_resolve_ask(text):
        return True

    if _IN_PLACE_CONTENT_ADD_RE.search(text):
        return True

    # Short confirmations ("yes", "ok", "do it") after the assistant proposed an
    # edit are edit intent — not advisory.  Without this the system re-queries KB
    # from scratch instead of applying the already-proposed change.
    if _SHORT_CONFIRM_RE.match(text) and conversation_history:
        for turn in reversed(conversation_history[-6:]):
            if turn.get("role") == "assistant":
                assistant_text = (turn.get("content") or "").casefold()
                if any(
                    signal in assistant_text
                    for signal in (
                        "apply", "shall i", "want me to", "i can update",
                        "i'll update", "ready to apply", "go ahead",
                        "would you like me to", "i found",
                    )
                ):
                    return True
                break

    advisory = bool(_ADVISORY_INTENT_RE.search(text))
    mutate = bool(_EDIT_INTENT_RE.search(text))

    if advisory and not mutate:
        return False
    if advisory and mutate and not _FOLLOW_WITH_MUTATE_RE.search(text):
        return False

    # A question *about* the draft is not an instruction to change it, even when
    # it contains an edit verb: "does the budget cut affect this?", "which
    # sections need trimming?". Request forms ("can you shorten this?", "please
    # tighten this") are deliberately not question openers, so they still edit.
    if (
        mutate
        and _QUESTION_OPENER_RE.match(text)
        and not _FOLLOW_WITH_MUTATE_RE.search(text)
    ):
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


@dataclass(frozen=True)
class ChatRoute:
    """Whether a chat turn answers in chat or mutates the draft, and why."""

    advisory: bool
    reason: str


def _format_apply_fix_prior_context(
    conversation_history: list[dict[str, str]] | None,
) -> str:
    """Prior audit chat — already contains KB-backed facts; no re-fetch on Apply."""
    if not conversation_history:
        return ""
    parts: list[str] = []
    for turn in conversation_history[-10:]:
        role = str(turn.get("role") or "user").strip()
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        parts.append(f"{label}: {content[:3500]}")
    return "\n\n".join(parts)


async def _apply_suggested_fix_to_section(
    *,
    rfp_id: str,
    section: ProposalSection,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    instruction: str,
    conversation_history: list[dict[str, str]] | None,
    persist: bool,
) -> tuple[ProposalSection, ProposalDraft, ProposalResearchCache, str, str, bool]:
    """One-click Apply the fix — rewrite from prior audit context, no KB re-plan."""
    prior_content = section.content or ""
    from app.services.proposal_bio_stub import (
        MISPLACED_BIO_STUB_REWRITE_NOTE,
        prior_content_for_rewrite,
    )

    rewrite_body = prior_content_for_rewrite(section.id, prior_content)
    masked_prior, mfill_originals = _mask_manual_fill_for_rewrite(rewrite_body)
    prior_chat = _format_apply_fix_prior_context(conversation_history)

    user_content = (
        f"Section: {section.title}\n"
        f"Client: {rfp.client}\n\n"
        f"Apply instruction:\n{instruction.strip()}\n\n"
    )
    if not rewrite_body.strip() and prior_content.strip():
        user_content += f"{MISPLACED_BIO_STUB_REWRITE_NOTE}\n\n"
    if prior_chat:
        user_content += (
            "Prior chat (verified audit — use facts cited here; do not invent):\n"
            f"{prior_chat}\n\n"
        )

    from app.services.proposal_capability_bio_grounding import (
        pack_04_bio_kb_for_section,
        section_or_instruction_needs_bio_kb,
    )

    if section_or_instruction_needs_bio_kb(section, instruction):
        try:
            bio_kb = await pack_04_bio_kb_for_section(
                section, user_message=instruction
            )
        except Exception:
            logger.exception("04_Bio pack for apply-fix failed")
            bio_kb = ""
        if bio_kb.strip():
            user_content += (
                "04_Bio KB (authoritative — every restored sentence must come from here; "
                "never invent specialization or years):\n"
                f"{bio_kb[:18000]}\n\n"
            )

    from app.services.proposal_section_kb_evidence import (
        fetch_packed_section_kb_evidence,
    )

    try:
        packed, _packed_sources = await fetch_packed_section_kb_evidence(
            section_title=section.title or "",
            user_message=instruction,
            section_content=section.content or "",
        )
    except Exception:
        logger.exception("Packed KB retrieve for apply-fix failed")
        packed = ""
    if packed.strip():
        user_content += (
            "PACKED KB EVIDENCE (same Supermemory retrieval as KB QA — populate "
            "tables and facts ONLY from this block; never TBD placeholder rows):\n"
            f"{packed[:14000]}\n\n"
        )

    user_content += f"Current section content:\n{masked_prior[:12000]}"

    system_prompt = APPLY_FIX_REDRAFT_PROMPT
    if mfill_originals:
        system_prompt = f"{APPLY_FIX_REDRAFT_PROMPT}\n\n{_MANUAL_FILL_PRESERVE_CONSTRAINT}"

    raw, provider = await llm.chat_json(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_tokens=16000,
        temperature=0.2,
        node_name="chat_apply_suggested_fix",
    )
    content = enforce_narrative_voice(
        str(raw.get("content", "")).strip(),
        section_id=section.id,
        title=section.title,
        register="narrative",
    )
    content = _unmask_manual_fill_checked(content, mfill_originals, attempt=1)
    from app.services.proposal_manuscript import scrub_client_facing_section_artifacts

    content = scrub_client_facing_section_artifacts(content)

    working = section.model_copy(update={"content": content, "status": "generated"})
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )
    else:
        research = research.model_copy(update={"provider": provider})

    changed = (content or "") != prior_content
    merged = [working if s.id == section.id else s for s in draft.sections]
    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(
        update={"sections": merged, "updated_at": now, "provider": provider}
    )
    if changed and persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=section.title,
        )
        working = _find_draft_section(updated_draft, section.id) or working

    before_words = word_count(prior_content)
    after_words = word_count(content)
    if changed:
        assistant_message = (
            f"Applied the suggested fix to **{section.title}** "
            f"({before_words} → {after_words} words)."
        )
    else:
        assistant_message = (
            f"I could not apply the fix to **{section.title}** — "
            "the section content did not change. Try editing the instruction or "
            "select the exact line and ask again."
        )
    logger.info(
        "Apply suggested fix for %s / %s changed=%s (no KB re-plan)",
        rfp_id,
        section.id,
        changed,
    )
    return working, updated_draft, research, provider, assistant_message, changed


def _improve_outcome(
    section: ProposalSection,
    draft: ProposalDraft,
    research: ProposalResearchCache,
    provider: str,
    message: str,
    changed: bool,
    suggested_fix: Any = None,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache,
    str,
    str,
    bool,
    Any,
]:
    """Uniform 7-tuple from improve_proposal_section (suggested_fix optional)."""
    return section, draft, research, provider, message, changed, suggested_fix


async def _finish_chat_structure_plan(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    structure_plan: Any,
    section_id: str,
    rfp: RfpRecord,
    rfp_context: str,
    research: ProposalResearchCache | None,
    persist: bool,
    allow_clarify: bool = True,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache,
    str,
    str,
    bool,
    Any,
] | None:
    """Apply add/delete/clarify structure plans. Returns None when action=edit.

    When ``allow_clarify`` is False (content edit / Improve pin), clarify is ignored
    so the caller can fall through to a normal section rewrite — never surface
    "couldn't plan the outline change" for make-it-concise style asks.
    """
    from app.services.proposal_chat_structure import (
        StructurePlan,
        apply_chat_structure_plan,
    )
    from app.services.proposal_draft_snapshots import push_before_structure_change_snapshot

    if not isinstance(structure_plan, StructurePlan):
        return None

    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )

    if structure_plan.action == "clarify":
        if not allow_clarify:
            logger.info(
                "Ignoring structure clarify for content-edit ask section_id=%s",
                section_id,
            )
            return None
        question = (structure_plan.clarify_question or "").strip() or (
            "Should I edit the current section, add new sidebar sections, or delete something?"
        )
        focus = _find_draft_section(draft, section_id) or draft.sections[0]
        return _improve_outcome(focus, draft, research, provider, question, False)

    if structure_plan.action not in {"add_sections", "delete_sections", "stub_bio"}:
        return None

    focus_before = _find_draft_section(draft, section_id)
    pre_title = (focus_before.title if focus_before else None) or "proposal"
    draft = push_before_structure_change_snapshot(draft, section_title=pre_title)
    updated_draft, focus, assistant_message = await apply_chat_structure_plan(
        draft=draft,
        plan=structure_plan,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector or "",
        rfp_context=rfp_context or "",
    )
    if persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=focus.title,
        )
    return _improve_outcome(
        focus, updated_draft, research, provider, assistant_message, True
    )


def decide_chat_route(
    *,
    chat_intent: str,
    user_message: str,
    selection_mode: bool,
    conversation_history: list[dict[str, str]] | None = None,
    improve_pinned: bool = False,
) -> ChatRoute:
    """Decide advisory vs edit for one chat turn.

    Lifted verbatim out of improve_proposal_section so the decision can be tested
    directly instead of only through a 1,700-line dispatcher. Order matters and is
    load-bearing:

    * a pinned excerpt edits unless the turn reads as a question about it —
      highlighting text scopes the ask, it does not authorise a rewrite;
    * Improve full section binds the open tab the same way: questions are
      answered, change requests rewrite THAT tab, never "which section?";
    * a structure ask (add/delete a section) always mutates, overriding even a
      classifier that said "advisory";
    * the LLM classifier decides next, because it reads the whole manuscript;
    * only when the classifier abstains ("none", including when it could not run)
      does the keyword gate decide, and its default is advisory.
    """
    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

    # KB fetch/fill on any tab overrides selection mode, classifier, and verify-only
    # — except a pure question that only said "missing" / "this section".
    kb_fill = _selection_asks_to_fill_verify(
        user_message
    ) or user_asks_kb_fetch_or_fill(user_message)
    question_only = (
        (
            _is_informational_only_ask(user_message)
            or _is_verification_only_ask(user_message)
        )
        and not _FOLLOW_WITH_MUTATE_RE.search(user_message)
        and not user_asks_kb_fetch_or_fill(user_message)
    )
    if kb_fill and not question_only:
        return ChatRoute(advisory=False, reason="kb_fetch_or_verify_fill")

    if selection_mode:
        if chat_intent == "advisory":
            return ChatRoute(advisory=True, reason="selection_classifier_advisory")
        if _is_verification_only_ask(user_message):
            return ChatRoute(advisory=True, reason="selection_verify_ask")
        if _is_informational_only_ask(user_message):
            return ChatRoute(advisory=True, reason="selection_informational_ask")
        if chat_intent in {"single_edit", "multi_patch"}:
            return ChatRoute(advisory=False, reason=f"selection_classifier_{chat_intent}")
        if _selection_ask_is_advisory(
            user_message, conversation_history=conversation_history
        ):
            return ChatRoute(advisory=True, reason="selection_question")
        return ChatRoute(advisory=False, reason="selection_edit")

    if improve_pinned:
        from app.services.proposal_chat_structure import is_add_section_intent

        # New sidebar tab still goes through the outline planner.
        if is_add_section_intent(user_message):
            return ChatRoute(advisory=False, reason="structure_ask")
        # Questions about THIS tab: answer, do not rewrite, do not ask which section.
        if _is_verification_only_ask(user_message):
            return ChatRoute(advisory=True, reason="improve_pin_verify_ask")
        if _is_informational_only_ask(user_message):
            return ChatRoute(advisory=True, reason="improve_pin_informational_ask")
        if _selection_ask_is_advisory(
            user_message, conversation_history=conversation_history
        ):
            return ChatRoute(advisory=True, reason="improve_pin_question")
        # Change request / default "Improve this section for the RFP." → rewrite this tab.
        return ChatRoute(advisory=False, reason="improve_pin_edit")

    from app.services.proposal_chat_structure import is_add_section_intent

    if chat_intent == "structure" or is_add_section_intent(user_message):
        return ChatRoute(
            advisory=False,
            reason="structure_ask" if is_add_section_intent(user_message) else "classifier_structure",
        )
    # Fact-check / fabricated-values asks must never rewrite — even when the
    # classifier guesses single_edit because the open tab looks like a target.
    if _is_verification_only_ask(user_message):
        return ChatRoute(advisory=True, reason="verify_ask")
    if _is_informational_only_ask(user_message):
        return ChatRoute(advisory=True, reason="informational_ask")
    # "Improve this section" is a bound-tab rewrite, not a whole-proposal Q&A.
    if user_points_at_open_section(user_message) and _wants_section_edit(
        user_message, conversation_history=conversation_history
    ):
        return ChatRoute(advisory=False, reason="open_tab_edit")
    if chat_intent == "advisory":
        return ChatRoute(advisory=True, reason="classifier_advisory")
    if chat_intent in {"single_edit", "multi_patch"}:
        return ChatRoute(advisory=False, reason=f"classifier_{chat_intent}")
    if _wants_section_edit(user_message, conversation_history=conversation_history):
        return ChatRoute(advisory=False, reason="keyword_edit")
    return ChatRoute(advisory=True, reason="keyword_default_advisory")


def _compose_chat_user_message(
    user_message: str,
    conversation_history: list[dict[str, str]] | None,
) -> str:
    if not conversation_history:
        return user_message
    lines = [
        "Prior conversation (MUST remember — address the latest message using this context):"
    ]
    for turn in conversation_history[-8:]:
        role = turn.get("role", "user")
        label = "User" if role == "user" else "Assistant"
        content = (turn.get("content") or "").strip()
        if content:
            lines.append(f"{label}: {content[:800]}")
    lines.append(f"\nLatest user message:\n{user_message.strip()}")
    return "\n".join(lines)


_KNOWN_REFERENCE_CONTACTS: list[tuple[str, str]] = [
    (
        r"oregon\s+employment",
        "Sytel G. Oelke, Sytel.G.Oelke@employ.oregon.gov, (503) 341-5661",
    ),
    (
        r"carbondale",
        "Steven Mitchell, Economic Development Director, "
        "steven.mitchell@carbondaleil.gov, (618) 457-3286",
    ),
]

_MARICOPA_VERIFY = (
    "[VERIFY: client-side reference contact required — confirm with Sonja or Ella "
    "before submission; not currently on file]"
)

_USER_CONTACT_BLOCK_RE = re.compile(
    r"(?is)"
    r"(oregon\s+employment\s+department|city\s+of\s+carbondale|maricopa\s+county)"
    r"\s*[:\-–—]\s*"
    r"([^\n]{10,220})"
)


def _message_targets_non_references_section(user_message: str) -> bool:
    """True when the latest ask clearly names another section (not References)."""
    text = user_message or ""
    if re.search(
        r"(?i)\b(?:umatilla|rock\s+the\s+locks|case\s+stud(?:y|ies)|"
        r"cover\s+letter|section\s*11|§\s*11|§\s*13)\b",
        text,
    ):
        return True
    # Explicit section mark that is not 21 / references.
    marks = re.findall(r"(?:§|sec(?:tion)?\.?)\s*(\d+)\b", text, re.I)
    if marks and all(m != "21" for m in marks):
        if not re.search(r"(?i)\breferences?\b", text):
            return True
    return False


def _user_asks_reference_integrity_fix(
    user_message: str,
    *,
    apply_fix: bool = False,
) -> bool:
    """Broader reference scrub triggers — duplicates, Exhibit K, Apply fix from review."""
    text = user_message or ""
    if _message_targets_non_references_section(text):
        return False
    if re.search(
        r"(?i)revert|restore|leave\s+(?:as-?is|alone)|do\s+not\s+add\s+a?\s*verify|"
        r"available\s+on\s+request[\"']?\s*\.?\s*do\s+not",
        text,
    ):
        return False
    if apply_fix and re.search(
        r"(?i)\b(?:reference|exhibit\s+[a-k]\b|duplicate|inconsistency)",
        text,
    ):
        return True
    if re.search(
        r"(?i)"
        r"duplicate.{0,60}reference|reference.{0,60}duplicate|"
        r"same\s+reference\s+twice|listed\s+the\s+same\s+reference|"
        r"exhibit\s+k|agency\s+director|sonja\s+anderson|connect@zo\.agency|"
        r"fix.{0,40}reference|reference.{0,40}integrity|"
        r"remove\s+duplicate|scrub\s+reference|fill\s+in\s+content",
        text,
    ):
        return True
    return _user_asks_reference_contact_fix(text)


def _user_asks_reference_contact_fix(user_message: str) -> bool:
    text = user_message or ""
    if _message_targets_non_references_section(text):
        return False
    # Revert / keep "available on request" is NOT a contact-fill ask.
    if re.search(
        r"(?i)revert|restore|leave\s+(?:as-?is|alone)|do\s+not\s+add\s+a?\s*verify|"
        r"available\s+on\s+request[\"']?\s*\.?\s*do\s+not",
        text,
    ):
        return False
    if not re.search(
        r"(?i)\breferences?\b|upon\s+request|pre-?cleared|§\s*21|"
        r"fix\s+(?:§\s*)?21\b|section\s*21\b",
        text,
    ):
        return False
    return bool(
        re.search(
            r"(?i)upon\s+request|pre-?cleared|verified\s+contact|"
            r"fix\s+(?:§\s*)?21\b|section\s*21\b|"
            r"replace.{0,40}upon\s+request|sytel|carbondale|maricopa|"
            r"do\s+not\s+invent|client-side\s+reference|"
            r"drop\s+it\s+into\s+the\s+table",
            text,
        )
    )


def _user_asks_references_sentence_revert(user_message: str) -> bool:
    return bool(
        re.search(
            r"(?i)revert.{0,80}(?:additional\s+references|available\s+on\s+request)|"
            r"additional\s+references.{0,80}available\s+on\s+request|"
            r"do\s+not\s+add\s+a?\s*verify.{0,40}(?:there|that\s+line)",
            user_message or "",
        )
    )


_BEND_ON_REQUEST_SENTENCE = (
    "Additional references, including the City of Bend Water Conservation, "
    "are available on request."
)


def _try_references_bend_sentence_revert(
    *,
    section: ProposalSection,
    user_message: str,
) -> tuple[ProposalSection, str] | None:
    if "reference" not in (section.title or "").casefold():
        return None
    if not _user_asks_references_sentence_revert(user_message):
        return None
    text = section.content or ""
    # Replace VERIFY form of the Bend additional-references line.
    updated = re.sub(
        r"(?is)Additional\s+references,\s+including\s+the\s+City\s+of\s+Bend"
        r"\s+Water\s+Conservation,\s+are\s+"
        r"(?:\[VERIFY:[^\]]+\]|available\s+(?:upon|on)\s+request)\.?",
        _BEND_ON_REQUEST_SENTENCE,
        text,
        count=1,
    )
    if updated == text:
        # Broader: any VERIFY after Bend Water Conservation clause.
        updated = re.sub(
            r"(?is)(Additional\s+references,\s+including\s+the\s+City\s+of\s+Bend"
            r"\s+Water\s+Conservation,\s+are\s+)\[VERIFY:[^\]]+\]\.?",
            r"\1available on request.",
            text,
            count=1,
        )
    if updated.strip() == text.strip():
        return None
    working = section.model_copy(update={"content": updated, "status": "generated"})
    return (
        working,
        f"Reverted the Bend additional-references sentence in **{section.title}** "
        "to exactly: available on request (no VERIFY tag).",
    )


def _extract_contacts_from_ask(user_message: str) -> dict[str, str]:
    """Parse org → contact line from the user message (never invent)."""
    found: dict[str, str] = {}
    blob = user_message or ""
    for match in _USER_CONTACT_BLOCK_RE.finditer(blob):
        org = match.group(1).casefold()
        detail = match.group(2).strip()
        if "verify" in detail.casefold() and "maricopa" in org:
            found["maricopa"] = _MARICOPA_VERIFY
            continue
        if "@" in detail or re.search(r"\(\d{3}\)", detail) or "VERIFY" in detail:
            if "oregon" in org:
                found["oregon"] = detail
            elif "carbondale" in org:
                found["carbondale"] = detail
            elif "maricopa" in org:
                found["maricopa"] = detail
    for pattern, contact in _KNOWN_REFERENCE_CONTACTS:
        key = "oregon" if "oregon" in pattern else "carbondale"
        if key in found:
            continue
        if re.search(pattern, blob, re.I) and (
            re.search(r"(?i)sytel|steven\s+mitchell|verified|kb|knowledge", blob)
            or re.search(r"(?i)drop\s+it\s+into|replace.{0,40}upon\s+request", blob)
        ):
            found[key] = contact
    if "maricopa" not in found and re.search(r"(?i)maricopa", blob):
        if re.search(
            r"(?i)\[?\s*VERIFY|not\s+currently\s+on\s+file|do\s+not\s+invent",
            blob,
        ):
            found["maricopa"] = _MARICOPA_VERIFY
    return found


def _apply_reference_contacts_to_content(
    content: str, contacts: dict[str, str]
) -> tuple[str, list[str]]:
    from app.services.proposal_integrity_guards import scrub_reference_withholding

    text, logs = scrub_reference_withholding(content or "")
    org_patterns = {
        "oregon": r"(?is)([^\n]*oregon\s+employment[^\n]*)",
        "carbondale": r"(?is)([^\n]*carbondale[^\n]*)",
        "maricopa": r"(?is)([^\n]*maricopa[^\n]*)",
    }
    for key, pattern in org_patterns.items():
        contact = contacts.get(key)
        if not contact:
            continue

        def _repl(m: re.Match[str], contact_line: str = contact) -> str:
            line = m.group(1)
            line2 = re.sub(
                r"(?i)(?:reference\s+)?contact\s+details?\s+(?:are\s+)?available\s+upon\s+request|"
                r"available\s+upon\s+request|"
                r"\[VERIFY:[^\]]+\]",
                contact_line,
                line,
            )
            if line2 == line and contact_line.casefold() not in line.casefold():
                line2 = f"{line.rstrip()} — {contact_line}"
            return line2

        new_text, n = re.subn(pattern, _repl, text, count=1)
        if n:
            text = new_text
            logs.append(
                f"Filled {key} reference contact from verified/user-provided facts"
            )
        elif key == "maricopa" and contact.startswith("[VERIFY"):
            if "maricopa" in text.casefold() and contact not in text:
                text = re.sub(
                    r"(?is)(maricopa\s+county[^\n]*)",
                    r"\1 — " + contact,
                    text,
                    count=1,
                )
                logs.append("Tagged Maricopa with VERIFY (no invent)")
    return text, logs


def _try_deterministic_references_fix(
    *,
    section: ProposalSection,
    user_message: str,
    conversation_history: list[dict[str, str]] | None,
    apply_fix: bool = False,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalSection, str] | None:
    """Apply References integrity fixes without LLM — no fabrications.

    Runs on explicit contact-fill asks, Apply-fix from review, or duplicate/
    agency-contact defects on a references tab.
    """
    del conversation_history  # Intentionally unused — latest message only.
    if _message_targets_non_references_section(user_message):
        return None

    title_cf = (section.title or "").casefold()
    is_ref_section = "reference" in title_cf or "reference" in (section.id or "").casefold()
    if not is_ref_section:
        return None

    revert = _try_references_bend_sentence_revert(
        section=section, user_message=user_message
    )
    if revert is not None:
        return revert

    from app.services.proposal_integrity_guards import (
        apply_reference_content_scrubs,
        apply_reference_post_fill_scrubs,
        reference_section_has_scrubbable_defects,
    )

    locks = research.manuscript_locks if research else None
    primary = (locks.primary_contact_name if locks else "") or ""
    body = section.content or ""
    wants_integrity = _user_asks_reference_integrity_fix(
        user_message, apply_fix=apply_fix
    )
    wants_contacts = _user_asks_reference_contact_fix(user_message)
    has_defects = reference_section_has_scrubbable_defects(body)
    run_scrub = wants_integrity or (
        apply_fix and is_ref_section and has_defects
    )

    if not run_scrub and not wants_contacts:
        return None

    scrubbed = body
    scrub_logs: list[str] = []

    if wants_contacts:
        from app.services.proposal_integrity_guards import scrub_reference_withholding

        contacts = _extract_contacts_from_ask(user_message)
        withheld, ref_logs = scrub_reference_withholding(scrubbed)
        scrub_logs.extend(ref_logs)
        scrubbed, contact_logs = _apply_reference_contacts_to_content(
            withheld, contacts
        )
        scrub_logs.extend(contact_logs)
        scrubbed, post_logs = apply_reference_post_fill_scrubs(
            scrubbed, primary_contact_name=primary
        )
        scrub_logs.extend(post_logs)
    elif run_scrub:
        scrubbed, scrub_logs = apply_reference_content_scrubs(
            scrubbed, primary_contact_name=primary
        )

    if scrubbed.strip() == body.strip():
        return None

    working = section.model_copy(update={"content": scrubbed, "status": "generated"})
    parts = [
        f"Updated **{section.title}** only — references integrity fix "
        "(no other sections touched)."
    ]
    for line in scrub_logs:
        parts.append(f"- {line}")
    if wants_contacts:
        contacts = _extract_contacts_from_ask(user_message)
        if "oregon" in contacts:
            parts.append(f"- Oregon Employment Department → {contacts['oregon']}")
        if "carbondale" in contacts:
            parts.append(f"- City of Carbondale → {contacts['carbondale']}")
        if "maricopa" in contacts:
            parts.append(f"- Maricopa County → {contacts['maricopa']}")
        parts.append(
            "No fabricated contacts. Pre-cleared / agreed-to-respond claims removed."
        )
    elif has_defects:
        parts.append(
            "Removed duplicate or agency-contact placeholder rows. "
            "Use verified ClientList contacts only — or leave MANUAL FILL for Sonja."
        )
    return working, "\n".join(parts)


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
            f"{rfp.sector} {section.title}"
        )[:240]
        key = q.lower()
        if key not in used:
            seeded.append(q)
            used.add(key)
    return seeded


def _rfp_section_requirements_list(
    research: ProposalResearchCache | None,
    section_id: str,
) -> list[str]:
    if not research or not research.rfp_sections:
        return []
    for sec in research.rfp_sections:
        if sec.id == section_id:
            return [r for r in (sec.requirements or []) if str(r).strip()]
    return []


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


# rfp_context is built by concatenation: up to 50k chars of raw RFP body
# (RFP_PROMPT_MAX_CHARS), then HARD FACTS from load_rfp_for_proposal(), then the
# mapped section requirements, manuscript digest and pricing guide appended by
# improve_proposal_section(). Slicing that blob by position hands the excerpt
# rewriter the RFP letterhead and drops every block assembled for it, so each
# block gets its own budget instead of competing on offset.
HARD_FACTS_MARKER = "## HARD FACTS (from full RFP text"
REQUIREMENTS_MARKER = "--- Mapped section requirements ---"
MANUSCRIPT_MARKER = "FULL PROPOSAL MANUSCRIPT (every section"
PRICING_MARKER = "=== 00_Guide_Pricing (Supermemory) ==="

_CONTEXT_BLOCK_BUDGETS: tuple[tuple[str, int], ...] = (
    (HARD_FACTS_MARKER, 3_000),
    (REQUIREMENTS_MARKER, 3_000),
    (MANUSCRIPT_MARKER, 6_000),
    (PRICING_MARKER, 4_000),
)
# What the KB query planner needs: what the RFP demands of this section, not the
# prose already written elsewhere.
_PLANNER_MARKERS = (HARD_FACTS_MARKER, REQUIREMENTS_MARKER)
RFP_BODY_CONTEXT_CHARS = 2_000


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}\n…[truncated]"


def _budget_rfp_context(
    rfp_context: str,
    *,
    body_chars: int = RFP_BODY_CONTEXT_CHARS,
    keep: tuple[str, ...] | None = None,
) -> str:
    """Rebuild rfp_context by block priority rather than by character offset.

    `keep` selects which appended blocks to emit; boundaries are always computed
    from the full marker set so a skipped block never bleeds into its neighbour.
    """
    if not rfp_context.strip():
        return ""

    found = sorted(
        (at, marker, budget)
        for marker, budget in _CONTEXT_BLOCK_BUDGETS
        if (at := rfp_context.find(marker)) >= 0
    )

    body_end = found[0][0] if found else len(rfp_context)
    parts = [_clip(rfp_context[:body_end], body_chars)]
    for index, (start, marker, budget) in enumerate(found):
        if keep is not None and marker not in keep:
            continue
        end = found[index + 1][0] if index + 1 < len(found) else len(rfp_context)
        parts.append(_clip(rfp_context[start:end], budget))

    return "\n\n".join(part for part in parts if part)


def _manuscript_digest(
    draft: ProposalDraft,
    *,
    max_chars: int = 36_000,
    titles_only: bool = False,
) -> str:
    """Compact full-proposal context for chat (TOC + section snippets).

    Each heading includes Sidebar N/total so "section 11" maps to the proposal
    outline — not an RFP document section with the same number.
    """
    total = len(draft.sections)
    lines: list[str] = [
        "FULL PROPOSAL MANUSCRIPT (every section — use this for whole-proposal answers).\n"
        "Sidebar N/total is the proposal outline index the user means by 'section N'."
    ]
    used = 0
    for index, section in enumerate(draft.sections):
        title = section.title or section.id
        heading = f"Sidebar {index + 1}/{total} — {title}"
        body = (section.content or "").strip()
        if titles_only or not body:
            block = f"\n### {heading}\n" + (
                "(empty)\n" if not body and not titles_only else ""
            )
        else:
            cap = 2_400 if total <= 12 else 1_400
            snippet = body[:cap] + ("…" if len(body) > cap else "")
            block = f"\n### {heading}\n{snippet}\n"
        if used + len(block) > max_chars:
            remaining = total - index
            lines.append(
                f"\n…({remaining} additional sections omitted — ask to focus a named "
                "section if needed)"
            )
            break
        lines.append(block)
        used += len(block)
    return "".join(lines)


def _sidebar_position(draft: ProposalDraft, section_id: str) -> int | None:
    for index, section in enumerate(draft.sections):
        if section.id == section_id:
            return index + 1
    return None


def _message_names_sidebar_section_number(user_message: str) -> bool:
    return bool(
        re.search(r"\b(?:section|sec|§)\s*\d+\b", user_message or "", re.I)
    )


def _advisory_target_binding(
    draft: ProposalDraft,
    section: ProposalSection,
) -> str:
    """Pin the model to the resolved proposal tab for 'what is section N about?'."""
    pos = _sidebar_position(draft, section.id)
    total = len(draft.sections)
    pos_label = f"{pos} of {total}" if pos else f"(id {section.id})"
    return (
        "AUTHORITATIVE TARGET TAB (answer ONLY about this — ignore other RFP/SOW "
        "numbers that reuse the same integer):\n"
        f"- Proposal sidebar position: {pos_label}\n"
        f"- Title: {section.title}\n"
        f"- id: {section.id}\n"
        "If the user said 'section N', they mean this sidebar tab. Do NOT describe "
        "Understanding / tourism-context / evaluation-criterion sections unless "
        "THIS tab's title is that topic.\n"
        "The user is already on this tab. Do not ask which section to work on. "
        "Answer questions about this tab, or describe the edit for this tab only.\n"
    )


def _message_needs_case_study_clarify(user_message: str) -> bool:
    """True when the ask is about case studies but no specific sidebar title is required yet.

    Outline add/create/delete asks skip this — structure planner handles them.
    VERIFY-fill asks skip too — pasted [VERIFY: … case study …] is about filling
    the open tab, not picking an Our Work piece.
    """
    text = user_message or ""
    # "[VERIFY: … San Francisco Travel case study …] fill from KB" must stay on
    # the open tab — the word "case study" inside a VERIFY tag is not a portfolio ask.
    if _selection_asks_to_fill_verify(text):
        return False
    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

    if user_asks_kb_fetch_or_fill(text):
        return False
    if user_points_at_open_section(text):
        return False
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


def _open_section_owns_case_study_ask(
    user_message: str,
    open_section: ProposalSection | None,
) -> bool:
    """True when the open tab already holds the VERIFY / client the user is talking about."""
    if open_section is None:
        return False
    content = (open_section.content or "").casefold()
    title = (open_section.title or "").casefold()
    if not content and not title:
        return False
    text = user_message or ""
    # Pasted VERIFY tag body — if it appears on the open tab, stay here.
    for match in VERIFY_TAG_RE.finditer(text):
        snippet = (match.group(0) or "")[:180].casefold()
        core = re.sub(r"\s+", " ", snippet)
        if len(core) > 40 and core[:80] in re.sub(r"\s+", " ", content):
            return True
    # Client / project names in the ask that already appear in the open draft.
    for name in re.findall(
        r"\b("
        r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,4}"
        r")\b",
        text,
    ):
        key = name.casefold()
        if key in {
            "request",
            "sonja",
            "knowledge",
            "base",
            "verify",
            "specifically",
            "without",
            "measurable",
            "tourism",
            "outcomes",
            "reference",
            "requirements",
        }:
            continue
        if key in content or key in title:
            return True
    return False


def _is_outline_structure_ask(user_message: str) -> bool:
    """Add/create/delete sidebar sections — proposal-wide, not open-tab rewrite."""
    from app.services.proposal_chat_structure import (
        is_add_section_intent,
        is_bio_resume_attachment_intent,
    )

    text = (user_message or "").strip()
    if not text:
        return False
    # Keep the bio tab; this is an in-place designer-note stub, not a delete.
    if is_bio_resume_attachment_intent(text):
        return False
    if is_add_section_intent(text):
        return True
    if re.search(
        r"\b(?:delete|remove)\b.{0,40}\b(?:section|tab|bio|case\s*stud)",
        text,
        re.I,
    ):
        return True
    if re.search(r"\binstead\s+of\b.{0,80}\b(?:add|use|put)\b", text, re.I):
        return True
    if re.search(r"\bmore\s+\d*\s*(?:team\s*)?bios?\b", text, re.I):
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


def _section_title_core(title: str) -> str:
    return re.sub(
        r"^\s*(?:section\s*)?\d+(?:\.\d+)*\s*[—\-–:.]?\s*",
        "",
        (title or "").strip(),
        flags=re.I,
    ).strip()


def _normalize_title_phrase(text: str) -> str:
    s = (text or "").casefold().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_TITLE_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "section",
        "part",
        "our",
        "of",
        "to",
        "a",
        "an",
    }
)


def _message_mentions_section_title(message: str, title: str) -> bool:
    lower = _normalize_title_phrase(message)
    if not lower or not (title or "").strip():
        return False
    full = _normalize_title_phrase(title)
    if len(full) >= 4 and full in lower:
        return True
    core = _normalize_title_phrase(_section_title_core(title))
    if len(core) >= 4 and core in lower:
        return True
    tokens = [
        t
        for t in core.split()
        if len(t) >= 4 and t not in _TITLE_STOPWORDS
    ]
    if not tokens:
        return False
    if len(tokens) == 1 and len(tokens[0]) >= 8 and tokens[0] in lower:
        return True
    # Long titles must not require every token — clarify replies often paste the head.
    head = " ".join(tokens[: min(4, len(tokens))])
    if len(head) >= 12 and head in lower:
        return True
    hits = [t for t in tokens if t in lower]
    if len(hits) >= 2 and all(t in lower for t in tokens):
        return True
    if len(hits) >= 3:
        return True
    if len(hits) >= 2 and len(hits) >= (len(tokens) + 1) // 2:
        return True
    return False


def _is_our_work_section(section: ProposalSection | None) -> bool:
    if section is None:
        return False
    return (
        section.id.startswith("section-3-work-")
        and section.id != "section-3-work-placeholder"
    )


def _message_explicitly_targets_remote_section(
    text: str,
    remote: ProposalSection,
    default: ProposalSection | None,
) -> bool:
    """True when the user clearly wants `remote`, not the API-bound open tab."""
    if default is None or remote.id == default.id:
        return True
    message = (text or "").strip()
    if not message:
        return False

    title = remote.title or ""
    if _message_mentions_section_title(message, title):
        return True

    mark = re.match(r"^\s*(\d+)\s*[.:—–\-\)]", title, re.I)
    if mark:
        n = mark.group(1)
        if re.search(rf"(?:§|sec(?:tion)?\.?)\s*{re.escape(n)}\b", message, re.I):
            return True
        if re.search(
            rf"\b(?:fix|edit|rewrite|update|patch|improve)\s+(?:§\s*)?{re.escape(n)}\b",
            message,
            re.I,
        ):
            return True

    dotted = re.match(r"^\s*(\d+\.\d+)", title)
    if dotted:
        num = re.escape(dotted.group(1))
        if re.search(
            rf"\b(?:rewrite|replace|edit|fix|update|revise|patch|improve|delete|remove)\b"
            rf"[^.]{{0,100}}\b{num}\b",
            message,
            re.I,
        ):
            return True
        if re.search(rf"\bsection\s+{num}\b", message, re.I):
            return True

    name = ""
    if "—" in title:
        name = title.split("—", 1)[-1].strip()
    elif "–" in title:
        name = title.split("–", 1)[-1].strip()
    name = re.sub(r"^\d+\.\d+\s*[—\-–:]\s*", "", name).strip()
    if len(name) >= 4:
        first = name.split()[0]
        if re.search(
            rf"\b(?:rewrite|replace|edit|fix|update|revise|patch|improve|delete|remove|"
            rf"swap\s+out|instead\s+of)\s+(?:the\s+)?(?:{re.escape(name)}|{re.escape(first)})",
            message,
            re.I,
        ):
            return True
        if re.search(
            rf"\b{re.escape(name)}\s+(?:bio|resume|case\s*study)\b",
            message,
            re.I,
        ):
            return True

    core = _section_title_core(title).casefold()
    if _is_our_work_section(remote):
        if re.search(r"\b(?:our\s+work|case\s+study\s+tab)\b", message, re.I):
            return True
        lower = message.casefold()
        for needle in (
            "umatilla",
            "rock the locks",
            "carbondale",
            "maricopa",
            "deschutes",
        ):
            if needle not in core and needle not in name.casefold():
                continue
            if needle not in lower:
                continue
            if re.search(r"\b(?:needs|need)\s+(?:a\s+)?rewrite\b", message, re.I):
                return True
            if re.search(
                r"\b(?:misrepresent|addressed|hasn't been addressed|flagged)\b",
                message,
                re.I,
            ):
                return True
            if re.search(r"\bcase\s+stud", message, re.I) and not re.search(
                r"\b(?:in\s+this|this)\s+case\s+stud", message, re.I
            ):
                return True
            needle_pat = needle.replace(" ", r"\s+")
            if re.search(
                rf"\b(?:rewrite|replace|edit|fix|update|revise|patch|improve|delete|remove)"
                rf"\s+(?:the\s+)?(?:{needle_pat}|case\s+study)\b",
                message,
                re.I,
            ):
                return True

    if remote.id.startswith("section-2-bio-") and re.search(
        r"\b(?:bio|bios|resume|team\s*bios?)\b", message, re.I
    ):
        if re.search(r"\b(?:team\s+bio|bio\s+tab|resume)\b", message, re.I):
            return True

    return False


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
    title_hits = [
        s for s in ranked if _message_mentions_section_title(text, s.title or "")
    ]
    if len(title_hits) == 1:
        return title_hits[0]
    if len(title_hits) > 1:
        # Prefer unique "References"/"Pricing" topic among hits.
        for topic in ("references", "reference", "pricing", "budget", "insurance"):
            if not re.search(rf"\b{topic}\b", text, re.I):
                continue
            topic_hits = [
                s
                for s in title_hits
                if topic in (s.title or "").casefold()
            ]
            if len(topic_hits) == 1:
                return topic_hits[0]
        title_hits.sort(
            key=lambda s: len(_section_title_core(s.title or "")),
            reverse=True,
        )
        top = title_hits[0]
        top_len = len(_section_title_core(top.title or ""))
        tied = [
            s
            for s in title_hits
            if len(_section_title_core(s.title or "")) == top_len
        ]
        return tied[0] if len(tied) == 1 else title_hits[0]

    # §21 / "Fix 21" → title starting with 21. (sidebar mark, not ordinal)
    mark = re.search(
        r"(?:§|sec(?:tion)?\.?)\s*(\d+)\b(?!\s*\.\d)"
        r"|\b(?:fix|edit|rewrite|update|patch)\s+(?:§\s*)?(\d+)\b",
        text,
        re.I,
    )
    if mark:
        n = mark.group(1) or mark.group(2)
        mark_hits = [
            s
            for s in draft.sections
            if re.match(rf"^\s*{re.escape(n)}\s*[.:—–\-\)]", s.title or "", re.I)
            or re.match(rf"^\s*{re.escape(n)}\s+", s.title or "", re.I)
        ]
        if len(mark_hits) == 1:
            return mark_hits[0]
        if len(mark_hits) > 1:
            for s in mark_hits:
                if any(
                    tok in lower
                    for tok in re.findall(r"[a-z]{4,}", (s.title or "").casefold())
                ):
                    return s
            return mark_hits[0]

    # Client / project name in title (Umatilla) — only when explicitly targeted.
    client_needles = re.findall(
        r"\b(?:umatilla|rock\s+the\s+locks|carbondale|maricopa|deschutes|"
        r"city\s+of\s+[a-z]+(?:\s+[a-z]+){0,2})\b",
        lower,
    )
    if client_needles:
        client_hits: list[ProposalSection] = []
        for section in draft.sections:
            blob = (section.title or "").casefold()
            if any(n.replace("  ", " ") in blob for n in client_needles):
                client_hits.append(section)
        if len(client_hits) == 1 and _message_explicitly_targets_remote_section(
            text, client_hits[0], default
        ):
            return client_hits[0]

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
    if len(named_hits) == 1 and _message_explicitly_targets_remote_section(
        text, named_hits[0], default
    ):
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
                if _message_explicitly_targets_remote_section(text, section, default):
                    return section
                break

    # UI "Section 15 of 18" → 1-based index in sidebar order (not dotted 3.1 titles).
    ordinal = re.search(r"\bsection\s+(\d+)\b(?:\s+of\s+\d+)?(?!\s*\.\d)", lower)
    if ordinal:
        n = int(ordinal.group(1))
        if 1 <= n <= len(draft.sections):
            return draft.sections[n - 1]

    # Unique topic tab — only when intentionally targeted (not "before the References fix").
    for topic in (
        "references",
        "reference",
        "pricing",
        "budget",
        "insurance",
        "subcontractor",
    ):
        if not _message_targets_unique_topic(text, topic):
            continue
        topic_hits = [
            s for s in draft.sections if topic in (s.title or "").casefold()
        ]
        if len(topic_hits) == 1:
            return topic_hits[0]

    if re.search(r"\b(bio|bios|resume|resumes|team\s*bios?|team\s*member)\b", text, re.I):
        from app.services.proposal_capability_bio_grounding import (
            is_personnel_bio_section,
        )

        if user_points_at_open_section(text) or (
            default and is_personnel_bio_section(default)
        ):
            return default
        bios = [
            s
            for s in draft.sections
            if s.id.startswith("section-2-bio-") and s.id != "section-2-bio-placeholder"
        ]
        if bios:
            if default and any(b.id == default.id for b in bios):
                return default
            return bios[-1]

    # Only remap to Cost Proposal for *global* rebuild asks — not "here fill budget
    # part" on a case study that itself has a budget VERIFY table.
    if user_asks_global_cost_rebuild(text) and not user_points_at_open_section(text):
        budget_secs = [s for s in draft.sections if section_is_budget_related(s)]
        if budget_secs:
            budget_secs.sort(
                key=lambda s: budget_section_score(s.title or ""),
                reverse=True,
            )
            best = budget_secs[0]
            if not default or not section_is_budget_related(default):
                return best

    # Cross-tab: user quotes or paraphrases a claim that lives in another section.
    content_hit = _resolve_section_by_content_needle(draft, text, default_section_id)
    if content_hit is not None and (
        default is None
        or content_hit.id == default.id
        or _message_explicitly_targets_remote_section(text, content_hit, default)
    ):
        return content_hit

    return default


def _remap_chat_section_if_explicit(
    draft: ProposalDraft,
    user_message: str,
    section_id: str,
) -> str:
    """Keep API-bound open tab unless the message explicitly names another section."""
    default = _find_draft_section(draft, section_id)
    resolved = _resolve_section_from_message(draft, user_message, section_id)
    if resolved is None or resolved.id == section_id:
        return section_id
    if _message_explicitly_targets_remote_section(user_message, resolved, default):
        logger.info(
            "Chat target section remapped %s → %s (%s) ask=%r",
            section_id,
            resolved.id,
            resolved.title,
            user_message[:80],
        )
        return resolved.id
    return section_id


def _history_may_override_open_tab(user_message: str) -> bool:
    """Short follow-ups inherit prior chat context — not a new section target."""
    text = (user_message or "").strip()
    if not text:
        return False
    if len(text) > 120 and not re.match(
        r"(?is)^(?:apply|do\s+it|yes|ok|please|go\s+ahead)\b", text
    ):
        return False
    if re.search(
        r"(?i)(?:§|sec(?:tion)?\.?)\s*\d+\b|\b(?:umatilla|rock\s+the\s+locks|case\s+stud|references?)\b",
        text,
    ):
        return False
    return True


def _message_targets_unique_topic(text: str, topic: str) -> bool:
    """True when topic is the ask's target — not an incidental mention."""
    if not re.search(rf"\b{re.escape(topic)}\b", text or "", re.I):
        return False
    # "implement budget table here" must stay on the open tab — never steal to Cost.
    if topic in ("budget", "pricing", "cost") and user_points_at_open_section(text):
        return False
    if re.search(
        r"(?i)\b(?:umatilla|rock\s+the\s+locks|case\s+stud(?:y|ies)|cover\s+letter)\b",
        text,
    ):
        topic_primary = re.search(
            rf"(?i)(?:fix|edit|rewrite|update|patch|fill|improve)\s+(?:the\s+)?{re.escape(topic)}\b|"
            rf"\b{re.escape(topic)}\s+(?:section|tab|contacts?|integrity)\b|"
            rf"(?:§|sec(?:tion)?\.?)\s*\d+[^\n]{{0,40}}\b{re.escape(topic)}\b",
            text,
        )
        if not topic_primary:
            return False
    if topic in ("references", "reference"):
        return bool(
            re.search(
                rf"(?i)(?:fix|edit|rewrite|update|patch|fill|improve|scrub)\s+"
                rf"(?:the\s+|§\s*\d+\s+)?{re.escape(topic)}\b|"
                rf"\b{re.escape(topic)}\s+(?:section|tab|contacts?|integrity|only)\b|"
                rf"(?:§|sec(?:tion)?\.?)\s*\d+[^\n]{{0,60}}\b{re.escape(topic)}\b|"
                r"\b(?:upon\s+request|pre-?cleared)\b",
                text,
            )
        )
    return True


def _message_has_explicit_section_target(user_message: str) -> bool:
    """Latest message already names a section / client / case study — don't use history."""
    text = user_message or ""
    if re.search(
        r"(?i)(?:§|sec(?:tion)?\.?)\s*\d+\b|"
        r"\b(?:umatilla|rock\s+the\s+locks|carbondale|maricopa|"
        r"oregon\s+employment|cover\s+letter|case\s+stud)\b|"
        r"\breferences?\b",
        text,
    ):
        return True
    return False


def _resolve_section_from_conversation_history(
    draft: ProposalDraft,
    conversation_history: list[dict[str, str]] | None,
    default_section_id: str,
    *,
    latest_user_message: str = "",
) -> ProposalSection | None:
    """Short follow-ups inherit the section named earlier — never override a new target."""
    if _message_has_explicit_section_target(latest_user_message):
        return None
    # Only for short follow-ups ("apply these", "do it", "yes").
    latest = (latest_user_message or "").strip()
    if len(latest) > 120 and not re.search(
        r"(?i)^(?:apply|do\s+it|yes|ok|please|go\s+ahead)\b", latest
    ):
        return None
    if not conversation_history:
        return None
    for turn in reversed(conversation_history[-12:]):
        content = (turn.get("content") or "").strip()
        if len(content) < 8:
            continue
        hit = _resolve_section_from_message(draft, content, default_section_id)
        if hit is None:
            continue
        if _message_mentions_section_title(content, hit.title or "") or re.search(
            r"(?:§|sec(?:tion)?\.?)\s*\d+", content, re.I
        ) or re.search(r"\breferences?\b", content, re.I):
            return hit
    return None


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

NEVER choose full_rewrite when the user asks to ADD / CREATE a new sidebar section or tab
(alongside existing content). Those are structural adds handled elsewhere — not rewrites
of the open tab.

When the user reports MISSING subsections (e.g. I.2 Active Client List entirely
missing, jumps I.1 → I.3), empty headers, empty table cells, or a numbered list of
structural defects: mode MUST be full_rewrite. A surgical patch cannot insert a
heading that is not in the current content.

When the user asks to ADD a table, subsection, or block (e.g. "add awards table as well"):
mode MUST be full_rewrite — a patch cannot insert a new table outside the anchor excerpt.

When the user asks to REPLACE this section with a DIFFERENT / OTHER RFP need (scan RFP,
another requirement, not the same topic): mode=full_rewrite. The instruction MUST change
the section TOPIC and TITLE to a different uncovered RFP ask — do NOT polish or lightly
edit the existing narrative while keeping the same subject (e.g. do not keep rewriting
tourism/destination experience if they asked for a different RFP need).

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
   scrubbing fabricated past-proven capability claims or inflated bio years, instruct the
   rewriter to use can-deliver / VERIFY language and match 04_Bio year numbers exactly —
   never add government/municipal specialization the bio KB does not state. If the ask is
   only remove / make qualitative, return "kbQueries": [].
5. For full_rewrite, return "patches": [] and put the rewrite instruction in understoodAsk.
6. When the user asks to add a designer note: editorInstruction MUST require a standalone
   paragraph exactly as [DESIGNER NOTE: …] after a blank line — NEVER **Designer Note:**,
   HTML/div, or "styled as …". The note body must be a layout/production handoff
   (callout box, columns, attach signed form/PDF, visual separation) — never meta
   commentary like "This section establishes…" or "critical for budget control".
7. If the user asks to REMOVE or ADD a named person, bio, row, sentence, or fact:
   mode MUST be patch. Anchor the paragraph, list item, or table row that contains
   that name (or the team/staff block for an add). NEVER full_rewrite the section.
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
                    f"Current section content:\n{content[:6000]}"
                ),
            },
        ],
        max_tokens=2400,
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


VERIFICATION_FACT_MIN_SIMILARITY = 0.55
VERIFICATION_FACT_LIMIT = 12


async def _verification_facts_block(
    queries: list[str],
    *,
    prefer_needles: list[str] | None = None,
) -> str:
    """Crisp KB memories with provenance, for "is this right?" questions.

    _fetch_kb_blob_for_selection expands hits into whole documents, which buries a
    one-line verified fact under OCR'd logo captions and drops the filename. A
    verification answer needs the opposite: the exact memory plus the doc it came
    from, so the reply can cite 01_companyfacts instead of hedging.

    Case-study PDF hits often leave `memory` empty and put text in `chunk` /
    `content` — those must count. When the ask names a client/project, prefer
    hits that mention those needles so Maricopa/Lake Oswego noise does not drown
    a real Umatilla 03_CS_ / 06_WON_ match.
    """
    needles = [
        n.casefold().strip()
        for n in (prefer_needles or [])
        if n and len(n.strip()) >= 4
    ]

    def _fact_text(hit: dict) -> str:
        return str(
            hit.get("memory") or hit.get("chunk") or hit.get("content") or ""
        ).strip()

    def _source(hit: dict) -> str:
        return str((hit.get("metadata") or {}).get("fileName") or "").strip()

    def _matches_needle(hit: dict) -> bool:
        if not needles:
            return True
        blob = f"{_source(hit)}\n{_fact_text(hit)}"
        # Filenames use CityofUmatilla; needles use "City of Umatilla".
        compact_blob = re.sub(r"[^a-z0-9]+", "", blob.casefold())
        for n in needles:
            if n in blob.casefold():
                return True
            compact_n = re.sub(r"[^a-z0-9]+", "", n)
            if len(compact_n) >= 6 and compact_n in compact_blob:
                return True
        return False

    matched: list[str] = []
    other: list[str] = []
    companyfacts_verified: list[str] = []
    companyfacts_other: list[str] = []
    seen: set[str] = set()

    def _append_line(line: str, hit: dict) -> None:
        source = _source(hit)
        if _COMPANYFACTS_SOURCE_RE.search(source):
            bucket = (
                companyfacts_verified
                if _companyfacts_source_rank(source) == 0
                else companyfacts_other
            )
            bucket.append(line)
        elif _matches_needle(hit):
            matched.append(line)
        else:
            other.append(line)

    for query in queries:
        try:
            hits = await supermemory.search_hybrid(
                query=query,
                limit=SEARCH_LIMIT,
                include_full_docs=False,
                filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
            )
        except Exception:
            logger.warning("Verification KB search failed for %r", query[:80], exc_info=True)
            continue
        for hit in hits or []:
            if float(hit.get("similarity") or 0) < VERIFICATION_FACT_MIN_SIMILARITY:
                continue
            fact = _fact_text(hit)
            if not fact or fact in seen:
                continue
            seen.add(fact)
            source = _source(hit)
            fact_line = re.sub(r"\s+", " ", fact)[:400]
            line = f"- {fact_line}" + (f"  [source: {source}]" if source else "")
            _append_line(line, hit)
        total = (
            len(companyfacts_verified)
            + len(companyfacts_other)
            + len(matched)
        )
        if total >= VERIFICATION_FACT_LIMIT:
            break

    ordered = companyfacts_verified + companyfacts_other + matched + other
    if ordered:
        return "\n".join(ordered[:VERIFICATION_FACT_LIMIT])
    return ""


async def _verification_04_bio_kb_block(
    section: ProposalSection,
    *,
    user_message: str = "",
    excerpt: str = "",
) -> str:
    """Pack 04_Bio into QA/verify prompts. Never write it into the manuscript.

    Generate keeps designer-note stubs. Chat verify / Complete Scan still need
    the real PDF so they cannot claim a named teammate has no bio.
    """
    from app.services.proposal_capability_bio_grounding import pack_04_bio_kb_for_section

    packed = await pack_04_bio_kb_for_section(
        section, user_message=f"{user_message or ''}\n{excerpt or ''}"
    )
    if not packed.strip():
        return ""
    return (
        "\n\nVerified 04_Bio KB (authoritative for named staff — these PDFs ARE "
        "in the knowledge base. Never say there is no bio for anyone listed here. "
        "Cite the 04_Bio filename. Do NOT paste this resume into the manuscript; "
        "Section 2 tabs stay designer-note stubs. Never recommend fabricated "
        "stand-ins such as Drew Stone or Brittany Frazier.)\n"
        f"{packed}\n"
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
    draft: ProposalDraft | None = None,
) -> tuple[str, Any]:
    """Return (reply_markdown, suggested_fix_or_none)."""
    from app.services.proposal_suggested_fix import (
        resolve_advisory_suggested_fix,
    )

    excerpt = (selection_text or "").strip()
    excerpt_block = f"\n\nHighlighted excerpt:\n\"{excerpt[:2000]}\"\n" if excerpt else ""

    # "section N about?" must not be answered from RFP clause numbering — pin the
    # resolved sidebar tab and keep RFP/manuscript noise short.
    sidebar_number_ask = _message_names_sidebar_section_number(user_message)
    target_binding = ""
    if draft is not None:
        target_binding = _advisory_target_binding(draft, section)
        if sidebar_number_ask or _is_informational_only_ask(user_message):
            manuscript_digest = _manuscript_digest(
                draft, max_chars=8_000, titles_only=True
            )

    # "Can you verify this?" needs the knowledge base. Without retrieval the model
    # can only restate the line it was asked to check, which reads as confirmation
    # while proving nothing.
    kb_block = ""
    from app.services.proposal_section_kb_evidence import (
        fetch_packed_section_kb_evidence,
        user_asks_kb_fetch_or_fill,
    )

    if user_asks_kb_fetch_or_fill(user_message or ""):
        packed, packed_sources = await fetch_packed_section_kb_evidence(
            section_title=section.title or "",
            user_message=user_message or "",
            requirements=_rfp_section_requirements_list(research, section.id),
            section_content=section.content or "",
        )
        bio_block = await _verification_04_bio_kb_block(
            section, user_message=user_message or "", excerpt=excerpt
        )
        if packed:
            kb_block = (
                "\n\n"
                + packed
                + "\n\nAdvisory rule: the PACKED KB EVIDENCE above was retrieved live. "
                "Cite strategy/KPIs found there. Never say a client/case study "
                "does not exist in the KB when this block contains it. "
                "Use [VERIFY] only for fields still missing after this search.\n"
            )
        else:
            kb_block = (
                "\n\nKB status: searched the knowledge base for this fetch ask but "
                "found no matching case-study snippets. Say what is still missing — "
                "do NOT claim you searched if this block is empty.\n"
            )
        kb_block = bio_block + kb_block
    elif _advisory_needs_kb_lookup(user_message or "", excerpt):
        queries = await _plan_verification_kb_queries(
            section=section,
            user_message=user_message or "",
            excerpt=excerpt,
            rfp_client=rfp.client,
            rfp_sector=rfp.sector or "",
            rfp_title=rfp.title or "",
            research=research,
        )
        prefer = _verification_needles_from_content(
            section.title or "", section.content or ""
        )
        reachable = True
        facts = ""
        try:
            facts = await _verification_facts_block(
                queries,
                prefer_needles=prefer,
            )
        except Exception:
            reachable = False
            logger.warning("Advisory KB lookup failed for %s", section.id, exc_info=True)

        supporting = ""
        if reachable:
            try:
                supporting, _contacts = await _fetch_kb_blob_for_selection(queries)
            except Exception:
                logger.warning("Advisory KB blob failed for %s", section.id, exc_info=True)

        bio_block = ""
        if reachable:
            try:
                bio_block = await _verification_04_bio_kb_block(
                    section, user_message=user_message or "", excerpt=excerpt
                )
            except Exception:
                logger.warning(
                    "Advisory 04_Bio pack failed for %s", section.id, exc_info=True
                )

        if not reachable:
            # Never let an outage be reported as "the KB has no such document".
            kb_block = (
                "\n\nKB status: the knowledge base could not be reached for this "
                "question. Say the check could not be run — do NOT state that the "
                "fact is missing from the knowledge base.\n"
            )
        elif facts:
            contact_pin = _extract_companyfacts_contact_pin(facts)
            kb_block = (
                "\n\nVerified KB facts (zö agency source of truth — check the "
                "draft against these and cite the [source: …] you used):\n"
            )
            if contact_pin:
                kb_block += f"{contact_pin}\n\n"
            kb_block += f"{facts}\n"
            if supporting.strip():
                kb_block += f"\nSupporting KB excerpts:\n{supporting[:4000]}\n"
            kb_block = bio_block + kb_block
        elif bio_block.strip():
            kb_block = bio_block
            if supporting.strip():
                kb_block += f"\nSupporting KB excerpts:\n{supporting[:4000]}\n"
        else:
            kb_block = (
                "\n\nKB status: searched the knowledge base with queries "
                f"{queries!r} and found no matching verified fact for this "
                "question. Only say the project is missing if these entity-"
                "focused searches truly returned nothing — do not blame the "
                "section number.\n"
            )

    if not _is_informational_only_ask(user_message or ""):
        if "PACKED KB EVIDENCE" not in kb_block:
            try:
                packed, _packed_sources = await fetch_packed_section_kb_evidence(
                    section_title=section.title or "",
                    user_message=user_message or "",
                    requirements=_rfp_section_requirements_list(research, section.id),
                    section_content=section.content or "",
                )
            except Exception:
                logger.warning(
                    "Advisory packed KB retrieve failed for %s",
                    section.id,
                    exc_info=True,
                )
                packed = ""
            if packed:
                kb_block += (
                    "\n\n"
                    + packed
                    + "\n\nAdvisory rule: PACKED KB EVIDENCE was retrieved live from "
                    "Supermemory using the user's ask (same retrieval path as KB QA). "
                    "Never say the KB lacks awards/clients/facts when this block contains "
                    "them. Populate tables from these snippets only — never TBD placeholder "
                    "rows. For add-table asks on the open tab: set hasFix=true with an "
                    "applyInstruction to insert a markdown table from PACKED KB EVIDENCE.\n"
                )

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

    # Numbered-section asks: put the target draft FIRST and shrink RFP context so
    # an RFP "Section 11" criterion cannot override sidebar section 11.
    rfp_budget = (
        1_500
        if sidebar_number_ask or _is_informational_only_ask(user_message)
        else 8_000
    )
    open_tab_block = (
        f"Open-tab draft (THIS is what 'section N' refers to when a target is bound):\n"
        f"{(section.content or '')[:6000]}"
    )
    if sidebar_number_ask or target_binding:
        prompt = (
            f"RFP: {rfp.title} — {rfp.client}\n\n"
            f"{target_binding}\n"
            f"{open_tab_block}"
            f"{excerpt_block}\n\n"
            f"User message:\n{user_message.strip()}\n\n"
            f"Proposal outline (titles only):\n{manuscript_digest}\n\n"
            f"RFP context (secondary — do not remap section numbers from here):\n"
            f"{rfp_context[:rfp_budget]}\n\n"
            f"{requirements_block}\n"
            f"{guide_block}"
            f"{kb_block}"
            f"{history_block}"
        )
    else:
        prompt = (
            f"RFP: {rfp.title} — {rfp.client}\n\n"
            f"RFP context (rescan):\n{rfp_context[:rfp_budget]}\n\n"
            f"{requirements_block}\n\n"
            f"{manuscript_digest}\n\n"
            f"{guide_block}"
            f"{target_binding}"
            f"Currently open tab (orientation only — NOT the full proposal):\n"
            f"{section.title}\n\n"
            f"{open_tab_block}"
            f"{excerpt_block}"
            f"{kb_block}"
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
    max_tokens = 16000
    # Advisory replies are long markdown wrapped in {"reply": "..."} and the model
    # intermittently emits JSON this strict parser rejects, which surfaced to the
    # user as a 502 on roughly half of all questions. Repair once before failing —
    # same helper Phase 3 drafting already uses.
    raw, _ = await chat_json_with_repair(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.25 if user_asks_budget_explanation(user_message) else 0.35,
        node_name="section_chat_advisory",
    )
    reply = str(raw.get("reply", "")).strip()
    if not reply:
        reply = (
            "I reviewed the RFP context for this section — ask me to change specific text when you are ready."
        )
    suggested = resolve_advisory_suggested_fix(
        raw,
        fallback_section_id=section.id,
        section_title=section.title or "",
        draft=draft,
    )
    return reply, suggested

REFINE_QUERIES_PROMPT = """Plan 5-6 NEW Supermemory search queries to improve ONE proposal section.

The KB is ONLY about zö agency — never about the RFP buyer. Buyer requirements are not KB queries.

FIRST: restate what the user is asking for (one line). THEN map that ask to RFP themes and [VERIFY]
gaps. ONLY THEN invent search queries that chase zö facts those themes need.

Prior queries failed or returned insufficient evidence. User feedback describes what is wrong or missing.

Rules:
- Queries must follow from the understood ask + RFP themes — not random keyword mash.
- NEVER put the RFP client/buyer name as the search subject.
- Queries must be MORE SPECIFIC and DIFFERENT from all prior queries (never repeat or lightly rephrase).
- Use document-type hints where relevant: 01 companyfacts, 02 master template, 03_CS case studies, 04 bio, certifications, org chart, references.
- Target the exact gaps: firm legal name, Bend address, phone/email contacts, employee count, philosophy, sector-matched experience, org structure, case studies, fees, etc.
- Include "zö agency" + field name + doc hint in each query. Add sector/theme for similarity — not buyer name.
- If [VERIFY: ...] fields are listed, dedicate at least one query per missing zö field.
- Legal attestations (E-Verify, conflicts): search companyfacts only; do not plan queries that assume enrollment is proven.

Return ONLY JSON: {"queries": ["detailed query 1", "detailed query 2", "detailed query 3", "detailed query 4", "detailed query 5"]}"""

SECTION_IMPROVE_PLAN_PROMPT = """You are the first step of a proposal section improve — BEFORE any KB search or rewrite.

Read the user message, the section draft, and the RFP requirements for THIS section.
Understand the ask correctly. Do not jump to rewriting.

Return ONLY JSON:
{
  "understoodAsk": "One sentence: what the user wants done",
  "outlineAction": "edit_open_section" | "add_sidebar_section" | "delete_sidebar_section",
  "editorInstruction": "Clear instruction for the rewriter — address the ask, cover listed RFP needs, fill VERIFY from KB only when evidenced, keep unconfirmed legal attestations as [VERIFY: …]",
  "kbQueries": ["3-6 targeted Supermemory queries derived from the understood ask + RFP needs + VERIFY gaps"],
  "rfpNeedsAddressed": ["short phrases of RFP requirements this edit must cover"]
}

Rules:
- outlineAction=add_sidebar_section when the user wants a NEW sidebar tab/section (any phrasing:
  "add section new name X", "create a tab titled Y", "add another bio", etc.) — NEVER edit_open_section.
- outlineAction=delete_sidebar_section when they want a sidebar tab removed.
- outlineAction=edit_open_section only when they want THIS/open/named existing tab rewritten.
- understoodAsk must reflect the user's actual request (not a generic 'improve section').
- kbQueries must chase specific facts those needs require (zö agency + field + doc hint like 01 companyfacts / 03_CS_).
- For examples / case studies / references / campaign results: include at least one query that seeks
  real KB results/KPIs for clients or projects named in the draft (use those names — never the RFP buyer).
- For awards / recognition / agency honors asks: kbQueries MUST target 05_Awards and
  companyfacts — the query planner decides wording; never skip retrieval.
- Never invent E-Verify enrollment as a searchable 'confirmed' fact — search companyfacts; leave enrollment VERIFY unless facts prove it.
- If the user only wants VERIFY tags filled, say so in editorInstruction and keep surrounding prose intact.
- When the user lists multiple issues, defects, or requirements: editorInstruction MUST enumerate
  each item and require the rewriter to fix ALL of them — partial fixes are unacceptable.
- editorInstruction must say: cite KPIs/results present in KB evidence; use [VERIFY] or
  [MANUAL FILL: Sonja — …] only for fields still missing after retrieval — never invent
  team members, awards, carriers, metrics, or compliance statuses.
- DEFAULT STYLE: Unless the user asks for more detail, instruct the editor to write concisely — cover every RFP requirement but in the fewest tight, proof-led sentences. No filler, no restating the RFP back to the evaluator."""

SECTION_REDRAFT_PROMPT = """Rewrite ONE zö agency proposal section based on user feedback and evidence.

Rules:
1. Directly address the user's edit request.
2. Use ONLY facts from the evidence corpus. If PACKED KB / 04_Bio is provided, every
   sentence you add must be supportable from that text — never invent years, sector
   specialization, clients, or metrics. Do NOT put citation markers like [E1], [E12, E13],
   or **References:** [E…] lists in the prose — write clean client-facing sentences with
   proper **bold** markdown for labels and amounts.
2a. Team bios / Experience of Personnel: never delete a person's supporting paragraph
    to "fix" fabrication. Rewrite it from 04_Bio only. Never leave a named person with
    only a Role line when KB facts exist. Drop empty headers with no body.
3. Never dump [PRICING FLAG: …] into Compliance / narrative sections — those are internal only.
4. Improve substantially on the previous draft — never return the same placeholder or [VERIFY] block if evidence now supports the content.
5. Use [VERIFY: ...] only for requirements still missing from evidence — never for
   facts/KPIs/results that appear in the evidence or PACKED KB EVIDENCE block.
5a. When evidence lists strategy or results for a named client/project, write those
    facts into the prose. Do not replace them with a Sonja [VERIFY] asking for details
    the evidence already provides.
6. Follow the REGISTER block: narrative sections use first person we/our — NEVER "The Vendor", "The Offeror", or third-person agency distance.
   CONSISTENCY: Once a paragraph starts with "We", do NOT switch to "zö agency" mid-paragraph.
   Use "zö agency" only on first mention or in headings; everywhere else use "we/our/us".
7. PRESERVE the full BRAND VOICE block — zö core voice + RFP adaptation. User edits must NOT flatten tone into generic consultant/corporate prose.
8. Keep rhythm, confidence, warmth, and client-centered framing from the previous draft unless the user explicitly requests a tone change.
9. Apply WRITING AVOIDANCES from lost bids when provided — do not repeat past loss patterns.
10. Write submission-ready prose in zö's voice.
11. LENGTH: The Word target is a CEILING, not a goal. Aim for 60-75% of it. Be concise and tight —
    cover every RFP requirement and scoring criterion but use the fewest words that convey
    the point. Never pad with filler, restated RFP criteria, generic boilerplate, or
    multi-page essays. One strong sentence beats three weak ones. Let designer notes and
    visual layouts carry density instead of prose.
12. FORMAT: Prefer short paragraphs, markdown bullet lists, and markdown tables for phases,
    process steps, cadence, comparisons, and roles — whenever that improves evaluator scanability.
    Dense, scannable layouts score better than walls of text.
13. DESIGNER NOTES: Insert ONLY as a standalone paragraph
    `[DESIGNER NOTE: concrete layout handoff]` after a blank line — never **Designer Note:**,
    HTML/div, or "styled as". Content is a production handoff (callout box title + placement,
    table columns, attach signed PDF, visual separation) so a designer can build without
    guessing. Do NOT write meta commentary ("This section establishes…", "critical for…").
    Also set designerNote in the JSON for the section-level layout hint.
14. Methodology / planning / approach / work-plan sections: use phased bullets or a compact
    phase table; keep each phase to a few tight lines.
15. THOROUGHNESS: If the user lists multiple issues, defects, or requirements, address EVERY
    item — do not fix one bullet and ignore the rest. Confirm each listed issue is resolved
    or marked [VERIFY] / [MANUAL FILL] with a specific handoff.
16. NO FABRICATION: Never invent team members, clients, awards, insurance carriers, rates,
    metrics, or "Compliant" statuses. Use KB evidence only; otherwise [VERIFY] or
    [MANUAL FILL: Sonja — …]. Never invent «MFILL_N» tokens.

Return ONLY JSON:
{
  "content": "full section prose",
  "kbRefs": [],
  "designerNote": "concrete layout hint or null"
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
   - Escape line breaks inside the string as \\n (required for markdown tables).
   - If the user asked to REMOVE THIS HIGHLIGHT (this excerpt / this part / cut this out):
     prefer {"replacement": ""}. If empty deletion would leave a broken join, return a
     MINIMAL join fix — do not invent claims.
   - If they named a person, row, or fact to remove INSIDE the excerpt: delete only that
     person/row/sentence. Keep every other sentence, heading, and table row verbatim.
     Never blank the whole excerpt because they said "remove".
   - If they asked to ADD a person, sentence, or row: insert it and preserve the rest.
9. Budget/pricing excerpts: NEVER change agency revenue or commission lines to $0 — use commission rate × pass-through or canonical fee from section context; if unknown use [VERIFY: Sonja confirm commission rate and annual media estimate].
10. Do NOT reverse-engineer dollar amounts to hit a user-requested total — each line must trace to the Pricing Guide; suggest tier/scope changes instead (option C).
11. One-time setup/development lines must not be multiplied by 12 unless the excerpt is explicitly a monthly recurring service from the guide.
12. Reference excerpts: include name, title, phone, and email — never "contact on request" or deferral language.
13. PSA/compliance excerpts: add specific acknowledgment language when user asks — cover insurance, living wage, MacBride, Title VI, Chapter 63, audit rights as applicable.
14. Do NOT shorten or summarize the excerpt unless the user asked to remove, delete, cut, shorten, make concise, trim, or condense it. When they do ask to shorten: cover ALL key points and requirements but in fewer, tighter words — never drop substance.
15. When the user asks to fill gaps, placeholders, or [VERIFY] tags: ONLY replace those tags with KB facts — do not rewrite or summarize the surrounding prose.
16. Designer notes: insert ONLY a standalone paragraph `[DESIGNER NOTE: concrete layout handoff]`
    (blank line before/after). Never **Designer Note:**, HTML/div, or "styled as". Body =
    layout/production only (callout box, columns, attach form/PDF) — never meta commentary
    ("This section establishes…").
17. NEVER invent «MFILL_N» tokens. They appear only when already in the excerpt (protected
    handoff placeholders). For empty table cells use [MANUAL FILL: Sonja — confirm from KB]
    or [VERIFY: specific field]. If the user asks to ADD a new table or subsection that is
    not in the excerpt, do NOT cram a partial table into the span — return the excerpt
    unchanged."""

SELECTION_KB_PLAN_PROMPT = """You plan a surgical edit to ONE highlighted excerpt inside a zö agency proposal section.

Read the user's instruction and the selected excerpt. Understand what they want changed.

Return ONLY JSON:
{
  "editorInstruction": "One clear instruction for the editor. If they want gaps/VERIFY tags filled, say to replace only those tags from KB and preserve every other sentence verbatim.",
  "kbQueries": ["2-5 targeted Supermemory queries for missing zö facts — NEVER the RFP buyer name; use fields + doc hints like 04 bio, 01 companyfacts, 03_CS"],
  "preserveFullExcerpt": true
}

Rules:
- preserveFullExcerpt must be true when the selection is long or the user wants gaps/placeholders filled — the editor must NOT shorten or summarize.
- kbQueries must target zö facts missing in the excerpt, not the RFP buyer and not the user's chat message verbatim.
- If the instruction only removes, strips, or makes wording qualitative (no new facts needed), return "kbQueries": []."""

APPLY_FIX_REDRAFT_PROMPT = """Apply ONE suggested fix to a zö agency proposal section.

The user's apply instruction is authoritative. Obey it. Do NOT invent a different task.

The user clicked Apply the fix after an advisory audit. PRIOR CHAT holds the
KB-backed verdict — use cited facts exactly. If a 04_Bio / companyfacts block is
provided, that block is authoritative. Do NOT invent or substitute values.
If PACKED KB EVIDENCE is provided for awards, populate the table ONLY from that
block — never TBD placeholder rows.

Rules:
- Implement the apply instruction only; preserve all unrelated prose and structure.
- Keep markdown tables/lists/headings unless the instruction requires layout change.
- For table cell fixes: same columns — change only the wrong value(s).
- Never add deferred "upon request" language or new unverified contacts.
- Bios / Experience of Personnel: if a named person's specialization was invented,
  REPLACE it with 2–4 sentences from that person's 04_Bio KB. Never leave only a
  Role line when 04_Bio is provided. Never add government/municipal/enterprise
  claims the KB does not state. Years must match the KB number exactly. If 04_Bio
  is missing for that person, keep Role + [VERIFY: restore bio from 04_Bio] —
  do not invent years or specializations.
- Never insert citation markers like [E3] or [E3, E4]. Strip any that are present.
- Drop empty headers with no body (e.g. **Team Qualifications Summary** with nothing under it).

These rules govern how you write; they are never content. Never write sentences about
submission requirements, pass/fail status, what cannot be submitted, or what must be
verified or confirmed with anyone — apply the rule silently instead of narrating it.
When something is missing or needs a human, emit exactly one tag —
[MANUAL FILL: Sonja — <what is needed>] or [VERIFY: <field> — <reason>] — and nothing
else. Never explain the tag, never preface it, never restate the rule that produced it.

Return ONLY JSON: {"content": "<full updated section markdown>"}"""

STATIC_SECTION_REDRAFT_PROMPT = """Improve ONE static zö proposal section (company overview, team bios, or case studies).

Use ONLY the knowledge-base excerpts provided.
Address the user's feedback. Do not invent clients, metrics, addresses, phones, or emails.
Every sentence you add must be supportable from the KB excerpts — if a fact is not there,
omit it or use [VERIFY: specific field], never guess.

When fixing an invented bio specialization: REPLACE with 2–4 sentences from that person's
04_Bio. Never leave only a Role line. Never insert [E#] markers. Drop empty headers.

DESIGNER NOTES: Insert ONLY standalone `[DESIGNER NOTE: …]` paragraphs (blank line before/after) —
never **Designer Note:** or HTML. Content = layout/production handoff (callout, columns, attach PDF),
never meta commentary ("This section establishes…"). Be specific so a designer can build without guessing.
Also set designerNote in JSON for section-level layout hints.

When rewriting an Our Work / case study to a DIFFERENT client or project from the KB:
- Open the markdown with an H2 for the NEW case study (e.g. `## City of San Leandro: Brand Assessment`).
- Do not keep the old client's name in the leading heading.

When the user asks to ADD a team bio alongside existing bios in the same tab (legacy path only):
- Keep every existing bio verbatim and append the new bio — never replace or delete other people.

NARRATIVE REGISTER: first person we/our — never "The Vendor" or third-person procurement language.
PRESERVE the full BRAND VOICE block — zö core voice + RFP adaptation are mandatory.
- Keep warm, confident, proof-led rhythm — not generic consultant prose.
- Prefer concrete facts from KB over vague claims.
- Fill [VERIFY: ...] tags when KB has the fact; otherwise keep a precise [VERIFY: ...] tag.
- Do not flatten the previous draft's voice unless the user explicitly asked for a tone change.
Apply WRITING AVOIDANCES when provided.

THOROUGHNESS: If the user lists multiple issues or requirements, fix EVERY item — not one
bullet and leave the rest. NO FABRICATION: never invent «MFILL_N» tokens; use [VERIFY] or
[MANUAL FILL: Sonja — …] for gaps.

These rules govern how you write; they are never content. Never write sentences about
submission requirements, pass/fail status, what cannot be submitted, or what must be
verified or confirmed with anyone — apply the rule silently instead of narrating it.
When something is missing or needs a human, emit exactly one tag —
[MANUAL FILL: Sonja — <what is needed>] or [VERIFY: <field> — <reason>] — and nothing
else. Never explain the tag, never preface it, never restate the rule that produced it.

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
    from app.services.proposal_manual_flags import verify_tag_row_label

    seen: set[str] = set()
    fields: list[str] = []
    for match in VERIFY_TAG_RE.finditer(text):
        # Bare [VERIFY] has no field description of its own — use the table
        # row's label ("Phone" for `| Phone | [VERIFY] |`) so it still shows
        # up as a named gap instead of silently disappearing from the recap.
        field = (match.group(1).strip() or verify_tag_row_label(text, match.start())).strip()
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


def _selection_asks_to_remove(user_message: str) -> bool:
    """True when a highlighted span may shrink or disappear (remove/shorten/cut).

    Empty replacement is allowed. The model is told to DELETE the whole span only
    when `_selection_asks_to_delete_entire_span` is also true.
    """
    return bool(
        re.search(
            r"\b("
            r"remove|delete|drop|cut(\s+out)?|erase|omit|excise|"
            r"get\s+rid\s+of|take\s+(?:this|it|that)\s+out|"
            r"strip\s+(?:this|it|that|out)|"
            r"don'?t\s+(?:need|want)\s+(?:this|that)|"
            r"this\s+(?:part\s+)?only|"
            r"shorten|shorter|make\s+(?:it|this)\s+short|concise|condense|"
            r"trim|brief(?:er)?|tighten|summarize|summarise|compress"
            r")\b",
            user_message or "",
            re.I,
        )
    )


@dataclass(frozen=True)
class LocalChatEdit:
    """Localized add/remove inside a section — not a full rewrite."""

    kind: str  # delete_span | remove_named | add_named
    target: str


_LOCAL_DEMONSTRATIVE_RE = re.compile(
    r"^(?:this|that|it|these|those|"
    r"the\s+(?:excerpt|span|selection|highlight|highlighted\s+text|"
    r"part|text|paragraph|sentence|block|piece))s?\b",
    re.I,
)
# Per line — last matching instruction wins when the user pasted an excerpt first.
_LOCAL_REMOVE_LEAD_RE = re.compile(
    r"(?im)^\s*(?:please\s+)?(?:"
    r"remove|delete|drop|omit|erase|excise|cut\s+out|take\s+out|get\s+rid\s+of"
    r")\s+(?P<body>.+)$"
)
_LOCAL_ADD_LEAD_RE = re.compile(
    r"(?im)^\s*(?:please\s+)?(?:add|insert|include|put)\s+(?P<body>.+)$"
)
_NAMED_PERSON_SKIP_WORDS = frozenset(
    {
        "section",
        "tab",
        "indicators",
        "indicator",
        "appendix",
        "attachment",
        "form",
        "letter",
        "proposal",
        "draft",
        "excerpt",
        "highlight",
        "paragraph",
        "sentence",
    }
)


def _clean_local_edit_body(body: str) -> str:
    text = (body or "").strip().strip("\"'")
    text = re.sub(
        r"(?is)\s+(?:from|in|to)\s+(?:this|the|that)?\s*"
        r"(?:section|tab|excerpt|highlight|proposal|draft|team|staff|bios?|list|table).*$",
        "",
        text,
    )
    text = re.sub(r"(?is)\s+(?:please|thanks|thank you)[.!]*$", "", text)
    text = re.sub(
        r"(?i)^(the|a|an|all|any)\s+(?:mentions?\s+of\s+|references?\s+to\s+)?",
        "",
        text,
    )
    return text.strip(" .,")


def _understand_local_edit(user_message: str) -> LocalChatEdit | None:
    """Parse add/remove of a named person or of this highlight — meaning, not keywords."""
    text = (user_message or "").strip()
    if not text:
        return None
    from app.services.proposal_chat_structure import is_add_section_intent

    if is_add_section_intent(text):
        return None
    last: tuple[int, str, str] | None = None
    for match in _LOCAL_REMOVE_LEAD_RE.finditer(text):
        last = (match.start(), "remove", match.group("body"))
    for match in _LOCAL_ADD_LEAD_RE.finditer(text):
        if last is None or match.start() >= last[0]:
            last = (match.start(), "add", match.group("body"))
    if last is None:
        return None
    _, kind, raw_body = last
    body = _clean_local_edit_body(raw_body)
    if kind == "remove":
        if not body or _LOCAL_DEMONSTRATIVE_RE.match(body):
            return LocalChatEdit("delete_span", "")
        if re.match(
            r"(?i)^(?:this|that)\s+(?:person|people|bio|row|bullet|name|one)\b",
            body,
        ):
            return LocalChatEdit("delete_span", "")
        return LocalChatEdit("remove_named", body[:120])
    if not body or _LOCAL_DEMONSTRATIVE_RE.match(body):
        return None
    return LocalChatEdit("add_named", body[:120])


def _selection_asks_to_delete_entire_span(
    user_message: str,
    *,
    excerpt: str = "",
    full_content: str = "",
    selection_start: int | None = None,
    selection_end: int | None = None,
) -> bool:
    """True only when the highlighted span itself should be deleted.

    'remove Drew Stone' from a staffing table is a patch, not a wipe of the
    highlight. Never delete a near-full-section highlight.
    """
    local = _understand_local_edit(user_message)
    if local is None or local.kind != "delete_span":
        return False
    if (
        full_content
        and selection_start is not None
        and selection_end is not None
        and _selection_covers_most_of_section(
            full_content, selection_start, selection_end
        )
    ):
        return False
    excerpt_words = word_count(excerpt)
    if excerpt_words >= 80:
        return False
    return True


def _expand_match_to_block(content: str, start: int, end: int) -> tuple[int, int]:
    """Grow a name match to its markdown block (row, list item, or paragraph)."""
    body = content or ""
    if start < 0 or end > len(body) or start >= end:
        return start, end
    line_start = body.rfind("\n", 0, start) + 1
    line_end = body.find("\n", end)
    if line_end < 0:
        line_end = len(body)
    line = body[line_start:line_end]
    stripped = line.lstrip()
    if stripped.startswith("|") or re.match(r"^(?:[-*]|\d+[.)])\s+", stripped):
        return line_start, line_end
    if stripped.startswith("#"):
        nxt = re.search(r"(?m)^#{1,6}\s+", body[line_end:])
        block_end = len(body) if nxt is None else line_end + nxt.start()
        if block_end - line_start <= 1200 and (
            len(body) < 40 or (block_end - line_start) < int(len(body) * 0.75)
        ):
            return line_start, block_end
    para_start = body.rfind("\n\n", 0, start)
    block_start = 0 if para_start < 0 else para_start + 2
    para_end = body.find("\n\n", end)
    block_end = len(body) if para_end < 0 else para_end
    if block_end - block_start > 1200 or (
        len(body) >= 40 and (block_end - block_start) >= int(len(body) * 0.75)
    ):
        return line_start, line_end
    return block_start, block_end


def _expand_match_to_sentence(
    content: str,
    start: int,
    end: int,
    *,
    include_pronoun: bool = False,
) -> tuple[int, int]:
    """Grow a name match to its table row, list item, or prose sentence — never just the name."""
    body = content or ""
    if start < 0 or end > len(body) or start >= end:
        return start, end
    line_start = body.rfind("\n", 0, start) + 1
    line_end = body.find("\n", end)
    if line_end < 0:
        line_end = len(body)
    line = body[line_start:line_end]
    stripped = line.lstrip()
    if stripped.startswith("|") or re.match(r"^(?:[-*]|\d+[.)])\s+", stripped):
        return line_start, line_end
    if stripped.startswith("#"):
        return line_start, line_end

    sent_start = 0
    i = start - 1
    while i >= 0:
        ch = body[i]
        if ch in ".!?" and (i + 1 >= len(body) or body[i + 1] in " \t\n\"')"):
            sent_start = i + 1
            while sent_start < start and body[sent_start] in " \t\n":
                sent_start += 1
            break
        if ch == "\n" and (i == 0 or body[i - 1] == "\n"):
            sent_start = i + 1
            break
        i -= 1

    sent_end = len(body)
    j = end
    while j < len(body):
        ch = body[j]
        if ch in ".!?":
            sent_end = j + 1
            break
        if ch == "\n" and (j + 1 >= len(body) or body[j + 1] == "\n"):
            sent_end = j
            break
        j += 1
    if include_pronoun:
        sent_end = _extend_span_with_following_pronoun(body, sent_end)
    return sent_start, sent_end


_FOLLOW_PRONOUN_RE = re.compile(
    r"\A[ \t]*(?:\n{1,2}(?!#{1,6}\s))?(?:His|Her|Him|She|He)\b",
    re.I,
)


def _extend_span_with_following_pronoun(body: str, sent_end: int) -> int:
    """Include the next sentence when it is 'He/She owns…' after the named person."""
    if sent_end < 0 or sent_end >= len(body):
        return sent_end
    match = _FOLLOW_PRONOUN_RE.match(body[sent_end:])
    if not match:
        return sent_end
    j = sent_end + match.end()
    end = len(body)
    while j < len(body):
        ch = body[j]
        if ch in ".!?":
            return j + 1
        if ch == "\n" and (j + 1 >= len(body) or body[j + 1] == "\n"):
            return j
        j += 1
    return end


_GIVEN_NAME_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "this",
        "that",
        "our",
        "their",
        "and",
        "or",
        "for",
        "from",
    }
)


def _named_target_given_name(target: str) -> str | None:
    if not _target_looks_like_named_person(target):
        return None
    words = [w for w in re.split(r"\s+", (target or "").strip()) if w]
    first = re.sub(r"[^A-Za-z'-]", "", words[0] if words else "")
    if len(first) < 2 or first.casefold() in _GIVEN_NAME_STOP:
        return None
    return first


def _named_target_candidates(target: str) -> list[str]:
    needle = (target or "").strip()
    if len(needle) < 2:
        return []
    candidates: list[str] = [needle]
    # Split "Ron Comer and Letitia Hopper" — never "Performance and Outcome Indicators".
    if not _target_looks_like_named_person(needle):
        return candidates
    for part in re.split(r"\s+and\s+|,\s*", needle, flags=re.I):
        part = part.strip()
        if len(part) >= 2 and part.casefold() not in {c.casefold() for c in candidates}:
            candidates.append(part)
    words = [w for w in re.split(r"\s+", needle) if w]
    if len(words) >= 2:
        two = f"{words[0]} {words[1]}"
        if two.casefold() not in {c.casefold() for c in candidates}:
            candidates.append(two)
    return candidates


def _target_looks_like_named_person(target: str) -> bool:
    """True for 'Ron Comer' / 'Letitia Hopper' — not a sidebar title or 'this excerpt'."""
    words = [w for w in re.split(r"\s+", (target or "").strip()) if w]
    if not (2 <= len(words) <= 4):
        return False
    if any(re.sub(r"[^a-z]", "", w.casefold()) in _NAMED_PERSON_SKIP_WORDS for w in words):
        return False
    return sum(1 for w in words if re.search(r"[A-Za-z]", w)) >= 2


def _iter_named_matches(
    content: str,
    target: str,
    *,
    skip_given_name: bool = False,
) -> list[tuple[int, int]]:
    body = content or ""
    found: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for cand in _named_target_candidates(target):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(cand)}(?![A-Za-z0-9])"
        for match in re.finditer(pattern, body, re.I):
            key = (match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            found.append(key)
    given = None if skip_given_name else _named_target_given_name(target)
    if given:
        for match in re.finditer(rf"\b{re.escape(given)}(?:['’]s)?\b", body, re.I):
            key = (match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            found.append(key)
    found.sort(key=lambda pair: (pair[0], -(pair[1] - pair[0])))
    merged: list[tuple[int, int]] = []
    for start, end in found:
        if merged and start < merged[-1][1]:
            continue
        merged.append((start, end))
    return merged


def _normalize_heading_title(title: str) -> str:
    text = (title or "").strip()
    text = re.sub(r"^\d+(?:\.\d+)*\s*[—\-–:]\s*", "", text)
    return re.sub(r"\s+", " ", text).casefold()


def _heading_block_spans(content: str, target: str) -> list[tuple[int, int]]:
    """Subsection from a matching markdown heading through the next same-or-higher heading."""
    body = content or ""
    needle = _normalize_heading_title(target)
    if len(needle) < 4:
        return []
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"(?m)^(#{1,6})\s+(.+)$", body):
        title_cf = _normalize_heading_title(match.group(2))
        if not title_cf:
            continue
        if needle != title_cf and not (
            len(needle) >= 8 and (needle in title_cf or title_cf in needle)
        ):
            continue
        level = len(match.group(1))
        start = match.start()
        rest = body[match.end() :]
        nxt = re.search(rf"(?m)^#{{1,{level}}}\s+", rest)
        end = len(body) if nxt is None else match.end() + nxt.start()
        if start < end:
            spans.append((start, end))
    return spans


_LINE_START_PRONOUN_RE = re.compile(r"(?m)^(?:His|Her|Him|She|He)\b", re.I)


def _spans_for_named_target(content: str, target: str) -> list[tuple[int, int]]:
    """Every sentence / row / list item / heading block that names this target."""
    body = content or ""
    person = _target_looks_like_named_person(target)
    heading_spans = _heading_block_spans(body, target)
    include_pronoun = person and not heading_spans
    spans: list[tuple[int, int]] = list(heading_spans)
    for match_start, match_end in _iter_named_matches(
        body, target, skip_given_name=bool(heading_spans)
    ):
        start, end = _expand_match_to_sentence(
            body,
            match_start,
            match_end,
            include_pronoun=include_pronoun,
        )
        if start < end:
            spans.append((start, end))
    if include_pronoun:
        for match in _LINE_START_PRONOUN_RE.finditer(body):
            start, end = _expand_match_to_sentence(
                body, match.start(), match.end(), include_pronoun=False
            )
            if start < end:
                spans.append((start, end))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _span_for_named_target(content: str, target: str) -> tuple[int, int] | None:
    """Locate the sentence / list item / table row that names this person or item."""
    spans = _spans_for_named_target(content, target)
    return spans[0] if spans else None


def _strip_named_mentions(
    content: str,
    target: str,
    restrict: tuple[int, int] | None = None,
) -> tuple[str, int]:
    """Delete every sentence/row naming ``target``. Returns (new_content, mention_count)."""
    body = content or ""
    if restrict is not None:
        start, end = restrict
        if start < 0 or end > len(body) or start >= end:
            return body, 0
        inner, n = _strip_named_mentions(body[start:end], target, restrict=None)
        if n <= 0:
            return body, 0
        return body[:start] + inner + body[end:], n
    spans = _spans_for_named_target(body, target)
    if not spans:
        return body, 0
    out = body
    for start, end in reversed(spans):
        out = _splice_selection(out, start=start, end=end, replacement="")
        out = _heal_selection_join_deterministic(out, splice_at=start)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out, len(spans)


def _span_for_staff_block(content: str) -> tuple[int, int] | None:
    """Team/staff heading block — insert point when adding a named person."""
    body = content or ""
    match = re.search(
        r"(?im)^#{1,6}\s+[^\n]*(team|staff|personnel|bios?|people)\b[^\n]*",
        body,
    )
    if not match:
        return None
    start = match.start()
    nxt = re.search(r"(?m)^#{1,6}\s+", body[match.end() :])
    end = len(body) if nxt is None else match.end() + nxt.start()
    if end <= start:
        return None
    if len(body) >= 40 and (end - start) >= int(len(body) * 0.75):
        return None
    return start, end


def _locate_selection_span(content: str, selection_text: str | None) -> tuple[int, int] | None:
    """Recover char offsets when the client sent excerpt text but lost start/end."""
    needle = (selection_text or "").strip()
    if len(needle) < 3:
        return None
    body = content or ""
    idx = body.find(needle)
    if idx < 0:
        idx = body.lower().find(needle.lower())
    if idx < 0:
        # Preview/table selections often omit pipes — match distinctive endpoints.
        tokens = [
            t
            for t in re.split(r"\s+", needle)
            if len(t.strip()) >= 4
        ]
        if len(tokens) >= 2:
            first, last = tokens[0], tokens[-1]
            start = body.find(first)
            if start >= 0:
                end_at = body.find(last, start)
                if end_at >= start:
                    return start, end_at + len(last)
        return None
    return idx, idx + len(needle)


def _selection_asks_to_fill_verify(user_message: str) -> bool:
    """True when the user wants [VERIFY] / gap tags filled (not a rewrite/truncate)."""
    from app.services.proposal_chat_structure import _is_in_place_kb_or_verify_edit
    from app.services.proposal_verify_optional_scrub import (
        user_asks_scrub_optional_verify,
    )

    raw = user_message or ""
    # "Remove VERIFY tags" is a scrub/reframe, not a KB fill.
    if user_asks_scrub_optional_verify(raw):
        return False
    if _is_in_place_kb_or_verify_edit(raw):
        # In-place detector also matches scrub-adjacent phrases; re-check scrub.
        if user_asks_scrub_optional_verify(raw):
            return False
        return True
    return bool(
        re.search(
            r"(?i)"
            r"(?:fill|resolve|complete|replace).{0,80}\[?\s*VERIFY|"
            r"\[VERIFY|"
            r"missing\s+verify|"
            r"fill\s+(?:the\s+)?verify\s+tags?",
            raw,
        )
    )


def _open_tab_kb_fetch_ask(user_message: str) -> bool:
    """KB fetch/fill on the open tab — one packed retrieve + section rewrite."""
    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

    return user_asks_kb_fetch_or_fill(user_message) or _selection_asks_to_fill_verify(
        user_message
    )


def _open_tab_verify_resolve_ask(user_message: str) -> bool:
    """Fill, scrub, or strip [VERIFY] tags on the open tab — never structure clarify."""
    from app.services.proposal_verify_optional_scrub import (
        user_asks_scrub_optional_verify,
        user_asks_strip_inline_evidence_tags,
    )

    if not (user_message or "").strip():
        return False
    if user_asks_scrub_optional_verify(user_message):
        return True
    if user_asks_strip_inline_evidence_tags(user_message):
        return True
    if _open_tab_kb_fetch_ask(user_message):
        return True
    return bool(
        re.search(
            r"(?i)\bverify\b.{0,60}\b(?:fill|remove|strip|scrub|fetch)\b",
            user_message,
        )
        or re.search(
            r"(?i)\b(?:fill|remove|strip|scrub|fetch)\b.{0,60}\bverify\b",
            user_message,
        )
    )


async def _try_open_section_verify_fill_or_remove(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp_context: str,
    raw_user_message: str,
    persist: bool,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache,
    str,
    str,
    bool,
    Any,
] | None:
    """Fill [VERIFY] from packed KB when possible; strip/scrub leftovers when asked."""
    from app.services.proposal_section_kb_evidence import (
        fetch_packed_section_kb_evidence,
        user_asks_kb_fetch_or_fill,
    )
    from app.services.proposal_verify_optional_scrub import (
        count_verify_tags,
        scrub_optional_verify_tags,
        strip_inline_evidence_tags,
        user_asks_scrub_optional_verify,
        user_asks_strip_inline_evidence_tags,
    )

    if not _open_tab_verify_resolve_ask(raw_user_message):
        return None
    if not VERIFY_TAG_RE.search(section.content or ""):
        return None

    wants_remove = bool(
        user_asks_scrub_optional_verify(raw_user_message)
        or user_asks_strip_inline_evidence_tags(raw_user_message)
        or re.search(r"(?i)\b(?:remove|strip|delete|scrub|drop|remvoe)\b", raw_user_message)
    )
    wants_fill = bool(
        user_asks_kb_fetch_or_fill(raw_user_message)
        or re.search(r"(?i)\b(?:fill|fetch|populate|resolve|complete)\b", raw_user_message)
    )
    if not wants_fill and not wants_remove:
        return None

    rfp_section = _find_rfp_section(research, section_id)
    content = section.content or ""
    total_fills = 0

    if wants_fill:
        packed, _ = await fetch_packed_section_kb_evidence(
            section_title=section.title or "",
            user_message=raw_user_message,
            requirements=_rfp_section_requirements_list(research, section_id),
            section_content=content,
        )
        supplemental = _draft_supplemental_blob(draft)
        blob = "\n\n".join(p for p in (packed, supplemental) if p and p.strip())
        if blob.strip():
            content, total_fills = _replace_verify_tags_from_blob(content, blob)

    removed = 0
    if wants_remove and count_verify_tags(content) > 0:
        if user_asks_strip_inline_evidence_tags(raw_user_message) or re.search(
            r"(?i)\b(?:or\s+)?remov\w*\s+them\b|\bremove\s+verify\b",
            raw_user_message,
        ):
            content, removed = strip_inline_evidence_tags(content)
        else:
            scrub = await scrub_optional_verify_tags(
                content,
                section_title=section.title or "",
                rfp_text=rfp_context or "",
            )
            if scrub.changed:
                content = scrub.content
                removed = scrub.removed

    if content == (section.content or ""):
        return None

    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )
    else:
        research = research.model_copy(update={"provider": provider})

    working = section.model_copy(update={"content": content, "status": "generated"})
    merged = [working if s.id == section_id else s for s in draft.sections]
    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(
        update={"sections": merged, "updated_at": now, "provider": provider}
    )
    if persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=section.title,
        )

    parts: list[str] = []
    if total_fills > 0:
        parts.append(f"filled **{total_fills}** tag(s) from the knowledge base")
    if removed > 0:
        parts.append(f"removed **{removed}** leftover `[VERIFY]` tag(s)")
    summary = " and ".join(parts) if parts else "updated verify placeholders"
    assistant_message = f"Updated **{section.title}**: {summary}."
    remaining = count_verify_tags(content)
    if remaining:
        assistant_message += f" **{remaining}** tag(s) still open (no KB match or RFP-required)."
    logger.info(
        "verify_fill_or_remove rfp_id=%s section_id=%s fills=%d removed=%d remaining=%d",
        rfp_id,
        section_id,
        total_fills,
        removed,
        remaining,
    )
    return _improve_outcome(
        working, updated_draft, research, provider, assistant_message, True, None
    )


def _strip_verify_tags_for_compare(text: str) -> str:
    cleaned = VERIFY_TAG_RE.sub(" ", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _selection_covers_most_of_section(content: str, start: int, end: int) -> bool:
    if not content:
        return False
    return (end - start) / max(len(content), 1) >= _NEAR_FULL_SELECTION_RATIO


def _selection_replacement_regressed(
    excerpt: str,
    replacement: str,
    *,
    allow_remove: bool = False,
    allow_verify_fill: bool = False,
) -> bool:
    if allow_remove:
        return False
    # Filling [VERIFY] tags often shortens the span (tag → short fact). Compare the
    # surrounding prose with tags removed so we don't false-reject real fills.
    if allow_verify_fill and VERIFY_TAG_RE.search(excerpt or ""):
        base_ex = _strip_verify_tags_for_compare(excerpt)
        base_rep = _strip_verify_tags_for_compare(replacement)
        if not base_ex:
            return False
        # Replacement kept most of the non-VERIFY prose, or grew it.
        if word_count(base_rep) >= int(word_count(base_ex) * 0.85):
            return False
        # Still reject if the model returned only the filled value and dropped the passage.
        if word_count(replacement) < max(8, int(word_count(excerpt) * 0.35)):
            return True
        return False
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
    rfp_context: str = "",
) -> tuple[str, list[str]]:
    """LLM understands user intent and plans KB queries + editor instruction."""
    near_full = _selection_covers_most_of_section(full_content, selection_start, selection_end)
    # Without the RFP's own demands the planner writes queries against the
    # excerpt alone, so retrieval misses the facts the section is scored on.
    requirements = _budget_rfp_context(rfp_context, body_chars=0, keep=_PLANNER_MARKERS)
    user_content = (
        f"Client: {rfp.client}\n"
        f"Section: {section.title}\n"
        f"User instruction:\n{user_message.strip()}\n\n"
        f"Selected excerpt ({word_count(excerpt)} words, "
        f"{'near-full section' if near_full else 'partial'}):\n"
        f"\"\"\"{excerpt[:6000]}\"\"\"\n\n"
        f"Full section length: {word_count(full_content)} words\n"
        f"VERIFY tags in excerpt: {_gap_fields_from_text(excerpt) or '(none)'}"
    )
    if requirements:
        user_content += f"\n\nWhat the RFP requires here:\n{requirements}"
    raw, _ = await llm.chat_json(
        [
            {"role": "system", "content": SELECTION_KB_PLAN_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=16000,
        temperature=0.2,
        node_name="chat_selection_kb_plan",
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
            f"zö agency {section.title} {rfp.sector} {gap_hint[0] if gap_hint else user_message}"[
                :240
            ],
        ]
    else:
        queries = [str(q).strip()[:240] for q in queries_raw if str(q).strip()][:5]
    queries = [
        proposal_knowledge_base_tools.normalize_zo_kb_query(
            q, rfp_client=rfp.client, rfp_sector=rfp.sector, rfp_title=rfp.title
        )
        for q in queries
    ]
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
) -> tuple[str, str, list[str], str]:
    """LLM understands the user ask + RFP needs first, then plans KB queries.

    Returns (understood_ask, editor_instruction, kb_queries, outline_action).
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
        max_tokens=800,
        temperature=0.2,
        tier="light",
        node_name="section_improve_plan",
    )
    understood = str(raw.get("understoodAsk") or "").strip()
    outline_action = str(
        raw.get("outlineAction") or raw.get("outline_action") or "edit_open_section"
    ).strip()
    if outline_action not in {
        "edit_open_section",
        "add_sidebar_section",
        "delete_sidebar_section",
    }:
        outline_action = "edit_open_section"
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
    if outline_action == "edit_open_section" and _understood_ask_implies_sidebar_add(
        understood
    ):
        outline_action = "add_sidebar_section"
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
        "Section improve understood ask for %s: %r outline=%s → %d KB queries",
        section.id,
        understood[:160],
        outline_action,
        len(queries),
    )
    return understood, editor_instruction, queries, outline_action


def _understood_ask_implies_sidebar_add(understood: str) -> bool:
    """True when the improve planner restated the ask as a new sidebar tab."""
    u = (understood or "").casefold()
    if not u:
        return False
    add_markers = (
        "add a new sidebar",
        "add new sidebar",
        "new sidebar section",
        "add a new section",
        "add new section",
        "create a new section",
        "create a new sidebar",
        "create new section",
        "new section titled",
        "new section named",
        "new section called",
    )
    return any(m in u for m in add_markers)


async def _redirect_sidebar_add_to_structure(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    section_id: str,
    raw_user_message: str,
    rfp: RfpRecord,
    rfp_context: str,
    research: ProposalResearchCache | None,
    persist: bool,
    outline_hint: str = "add_sidebar_section",
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache,
    str,
    str,
    bool,
    Any,
] | None:
    """Run structure planner for add-tab asks; never fall through to open-tab rewrite."""
    from app.services.proposal_chat_structure import plan_chat_structure_action

    structure_plan = await plan_chat_structure_action(
        draft=draft,
        user_message=raw_user_message,
        focus_section_id=section_id,
        rfp_title=rfp.title,
        rfp_client=rfp.client,
        rfp_context=rfp_context,
        chat_intent="structure",
        outline_hint=outline_hint,
    )
    outcome = await _finish_chat_structure_plan(
        rfp_id=rfp_id,
        draft=draft,
        structure_plan=structure_plan,
        section_id=section_id,
        rfp=rfp,
        rfp_context=rfp_context,
        research=research,
        persist=persist,
    )
    if outcome is not None:
        return outcome
    focus = _find_draft_section(draft, section_id) or draft.sections[0]
    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )
    return _improve_outcome(
        focus,
        draft,
        research,
        provider,
        (
            "I understood you want a **new sidebar section**, not a rewrite of "
            f"**{focus.title}**. The outline planner did not return a safe add plan — "
            "please try again or use **Add** at the bottom of the section list."
        ),
        False,
    )


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
            hybrid_res, chunk_res = await asyncio.gather(
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
                return_exceptions=True,
            )
            hybrid: list[dict[str, Any]] = []
            chunks: list[dict[str, Any]] = []
            if isinstance(hybrid_res, BaseException):
                logger.warning(
                    "KB hybrid search failed for chat patch query %r: %s",
                    query[:80],
                    hybrid_res,
                )
            else:
                hybrid = list(hybrid_res or [])
            if isinstance(chunk_res, BaseException):
                logger.warning(
                    "KB chunk search failed for chat patch query %r: %s",
                    query[:80],
                    chunk_res,
                )
            else:
                chunks = list(chunk_res or [])
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
        try:
            chunk_fact_text = await supermemory.fetch_hits_fact_text(
                chunk_hits,
                max_hits=12,
                max_chars=32_000,
            )
        except supermemory.SupermemoryError as exc:
            logger.warning("KB fetch_hits_fact_text failed: %s", exc)

    hybrid_text = ""
    if hybrid_hits:
        hybrid_text = supermemory.format_search_hits(hybrid_hits, max_chars=12_000)

    if chunk_fact_text.strip():
        llm_parts.append(chunk_fact_text)
    elif hybrid_text.strip():
        llm_parts.append(hybrid_text)

    if not hybrid_hits and not chunk_hits:
        for query in queries:
            try:
                text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
                    query,
                    limit=8,
                    max_chars=8_000,
                )
            except supermemory.SupermemoryError as exc:
                logger.warning(
                    "KB fallback search failed for %r: %s", query[:80], exc
                )
                continue
            if text.strip():
                llm_parts.append(text[:8000])

    fact_parts = [part for part in (supplemental_blob, chunk_fact_text) if part.strip()]
    return "\n\n".join(llm_parts), "\n\n".join(fact_parts)


async def _bio_kb_context_for_section(
    section: ProposalSection,
    *,
    user_message: str = "",
) -> str:
    """Authoritative 04_Bio PDF text for team bios and Experience of Personnel tabs."""
    from app.services.proposal_bio_stub import is_bio_pdf_designer_note
    from app.services.proposal_capability_bio_grounding import (
        is_personnel_bio_section,
        pack_04_bio_kb_for_section,
    )

    wants_full_bio = bool(
        re.search(
            r"\b(resume|full bio|inline bio|work history|key accounts)\b",
            user_message or "",
            re.I,
        )
    )
    if (
        (section.id or "").startswith("section-2-bio")
        and is_bio_pdf_designer_note(section.content or "")
        and not wants_full_bio
    ):
        return ""

    # Chat *edits* must not dump 04_Bio into a designer-note stub. QA/verify
    # packs bios separately via _verification_04_bio_kb_block.
    if is_personnel_bio_section(section):
        packed = await pack_04_bio_kb_for_section(
            section, user_message=user_message
        )
        if packed.strip():
            return packed
    if not (section.id or "").startswith("section-2-bio"):
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


async def _case_study_kb_context_for_section(section: ProposalSection) -> str:
    """Authoritative 03_CS source document for a Section 3 case-study tab.

    Mirrors _bio_kb_context_for_section. Without it, a chat redraft of a case
    study relies on a broad semantic query, which returns the right document
    buried among other clients' proposals — measured at 4% of a 120k-char payload
    for City of Umatilla — and the writer emitted "no source material available in
    knowledge base" for a case study that is in the knowledge base.
    """
    if not section.id.startswith("section-3-work-"):
        return ""
    from app.services.proposal_knowledge_base_tools import (
        find_case_study_document_for_section,
    )

    doc = await find_case_study_document_for_section(section.id)
    if not doc:
        return ""
    fetch_key = supermemory.document_fetch_key(doc)
    if not fetch_key:
        return ""
    try:
        body = await supermemory.get_document_content(custom_id=fetch_key)
    except supermemory.SupermemoryError as exc:
        logger.warning("Case-study source fetch failed for %s: %s", section.id, exc)
        return ""
    return body or ""


async def _merge_bio_kb_into_blobs(
    section: ProposalSection,
    *,
    kb_block: str,
    fact_blob: str,
    user_message: str = "",
) -> tuple[str, str]:
    bio_text = await _bio_kb_context_for_section(
        section, user_message=user_message
    )
    if bio_text:
        header = f"=== 04_Bio approved file ({section.title}) ===\n{bio_text[:80_000]}"
        merged_kb = f"{kb_block}\n\n{header}".strip() if kb_block.strip() else header
        merged_fact = (
            f"{fact_blob}\n\n{bio_text}".strip() if fact_blob.strip() else bio_text
        )
        return merged_kb, merged_fact

    case_text = await _case_study_kb_context_for_section(section)
    if case_text:
        header = (
            f"=== 03_CS source case study ({section.title}) — write from THIS ===\n"
            f"{case_text[:80_000]}"
        )
        merged_kb = f"{header}\n\n{kb_block}".strip() if kb_block.strip() else header
        merged_fact = (
            f"{case_text}\n\n{fact_blob}".strip() if fact_blob.strip() else case_text
        )
        logger.info(
            "chat redraft using named 03_CS source for %s (%d chars)",
            section.id,
            len(case_text),
        )
        return merged_kb, merged_fact

    return kb_block, fact_blob


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


def _trim_replacement_boundary_overlap(
    replacement: str,
    *,
    prefix: str,
    suffix: str,
    min_overlap: int = 20,
) -> str:
    """Trim text the model re-emitted beyond the selected span, so a splice cannot
    duplicate the surrounding sentence.

    Observed defect: a user selects a few words of a table cell and asks to "fill
    this gap"; the model returns the WHOLE completed sentence as the replacement.
    Spliced as ``prefix + replacement + suffix`` that repeats the part of the
    sentence that already lives in ``suffix`` (and/or ``prefix``), producing
    ``…environment.e will select…environment.`` back-to-back.

    Deterministic fix: if the replacement's tail equals the head of ``suffix``
    (or its head equals the tail of ``prefix``) for at least ``min_overlap``
    chars, drop that overlap from the replacement. Case-insensitive; the length
    floor keeps ordinary coincidental word matches from being trimmed.
    """
    r = replacement or ""
    if not r:
        return r
    suf = suffix or ""
    if suf:
        max_k = min(len(r), len(suf))
        for k in range(max_k, min_overlap - 1, -1):
            if r[-k:].casefold() == suf[:k].casefold():
                r = r[:-k]
                break
    pre = prefix or ""
    if r and pre:
        max_k = min(len(r), len(pre))
        for k in range(max_k, min_overlap - 1, -1):
            if r[:k].casefold() == pre[-k:].casefold():
                r = r[k:]
                break
    return r


def _splice_selection(
    content: str,
    *,
    start: int,
    end: int,
    replacement: str,
) -> str:
    return content[:start] + replacement + content[end:]


def _selection_join_looks_broken(before: str, after: str) -> bool:
    """True when a delete/edit left ungrammatical join (e.g. paragraph starts mid-phrase)."""
    a = after.lstrip(" \t")
    if not a:
        return False
    b = before.rstrip(" \t")
    at_block_start = (
        not b
        or b.endswith("\n")
        or b.endswith("#")
        or re.search(r"[.:!?]\s*$", b) is not None
    )
    if at_block_start and a[0].islower():
        return True
    return False


def _heal_selection_join_deterministic(content: str, *, splice_at: int) -> str:
    """Capitalize / tidy whitespace at the splice without rewriting the section."""
    if splice_at < 0 or splice_at > len(content):
        return content
    before = content[:splice_at]
    after = content[splice_at:]
    before_r = before.rstrip(" \t")
    after_l = after.lstrip(" \t")
    if before_r and after_l and before_r[-1:].isalnum() and after_l[:1].isalnum():
        joiner = " "
    else:
        joiner = ""
    merged = before_r + joiner + after_l
    # Capitalize first letter of a new paragraph / after heading / section start.
    if merged and merged[0].islower():
        merged = merged[0].upper() + merged[1:]
    else:
        m = re.search(r"(\n\n+)([a-z])", merged)
        if m:
            i = m.start(2)
            merged = merged[:i] + merged[i].upper() + merged[i + 1 :]
        else:
            # After a markdown heading line
            m2 = re.search(r"(^|\n)(#{1,6}[^\n]*\n+)([a-z])", merged)
            if m2:
                i = m2.start(3)
                merged = merged[:i] + merged[i].upper() + merged[i + 1 :]
    return re.sub(r"\n{3,}", "\n\n", merged)


async def _heal_selection_join_llm(
    *,
    section_title: str,
    content: str,
    deleted_excerpt: str,
    user_message: str,
) -> str | None:
    """Minimal grammar heal after a delete — change as little as possible."""
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "A user deleted a highlighted span from a proposal section. "
                        "The remaining text may be ungrammatical at the join "
                        "(e.g. a sentence starts mid-phrase with a lowercase word).\n"
                        "Fix ONLY grammar/capitalization/whitespace near the join so the "
                        "section reads correctly. Do NOT add new claims, sections, or "
                        "facts. Do NOT rewrite unrelated paragraphs.\n"
                        "Return JSON: {\"content\": \"full healed section text\"}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Section: {section_title}\n"
                        f"User ask: {user_message.strip()}\n"
                        f"Deleted span:\n\"\"\"{deleted_excerpt[:1500]}\"\"\"\n\n"
                        f"Section after delete:\n\"\"\"{content[:12000]}\"\"\"\n"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.1,
            tier="light",
            node_name="chat_excerpt_remove_heal",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Selection remove heal LLM failed: %s", exc)
        return None
    if not isinstance(raw, dict):
        return None
    healed = str(raw.get("content") or "").strip()
    return healed or None


def _remove_heal_is_safe(*, spliced: str, healed: str) -> bool:
    """Reject LLM heals that rewrite or balloon the section after a delete."""
    if not healed.strip():
        return False
    if len(healed) > int(len(spliced) * 1.25) + 80:
        return False
    if len(spliced) > 100 and len(healed) < int(len(spliced) * 0.5):
        return False
    return True


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
    allow_remove = _selection_asks_to_remove(user_message) or _selection_asks_to_remove(
        ask_for_compliance
    )
    delete_entire_span = _selection_asks_to_delete_entire_span(
        user_message,
        excerpt=excerpt,
        full_content=content,
        selection_start=selection_start,
        selection_end=selection_end,
    ) or _selection_asks_to_delete_entire_span(
        ask_for_compliance,
        excerpt=excerpt,
        full_content=content,
        selection_start=selection_start,
        selection_end=selection_end,
    )
    local_edit = _understand_local_edit(ask_for_compliance) or _understand_local_edit(
        user_message
    )
    allow_verify_fill = (
        _selection_asks_to_fill_verify(user_message)
        or _selection_asks_to_fill_verify(ask_for_compliance)
        or bool(VERIFY_TAG_RE.search(excerpt or ""))
    )
    # Prefer deterministic KB fill for VERIFY tags before any LLM rewrite — avoids
    # lean multi-patch truncating the span to just the filled value.
    if allow_verify_fill and blob_for_facts.strip() and VERIFY_TAG_RE.search(excerpt):
        filled_excerpt, pre_kb_fills = _replace_verify_tags_from_blob(
            excerpt, blob_for_facts
        )
        if pre_kb_fills > 0 and not _selection_replacement_regressed(
            excerpt,
            filled_excerpt,
            allow_remove=False,
            allow_verify_fill=True,
        ):
            from app.services.proposal_manuscript import strip_evidence_citation_markers

            filled_excerpt = strip_evidence_citation_markers(filled_excerpt)
            new_content = _splice_selection(
                content,
                start=selection_start,
                end=selection_end,
                replacement=filled_excerpt,
            )
            updated = section.model_copy(
                update={"content": new_content, "status": "generated"}
            )
            return updated, _provider_name(), pre_kb_fills

    neighbor_before = content[max(0, selection_start - 280) : selection_start]
    neighbor_after = content[selection_end : min(len(content), selection_end + 280)]
    replacement = ""
    provider = _provider_name()
    last_mfill_error: ProposalError | None = None
    raw: dict | None = None
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
                f"RFP context:\n{_budget_rfp_context(rfp_context)}\n\n"
            )
            if evidence:
                user_block += f"Evidence corpus:\n{_format_evidence(evidence)}\n\n"
            if kb_block.strip():
                user_block += f"KB excerpts:\n{kb_block[:4000]}\n\n"
            if avoidance_block:
                user_block += f"{avoidance_block}\n\n"
            if should_apply_budget_playbook(section, ask_for_compliance):
                user_block += f"{budget_playbook_prompt_block(research=research)}\n\n"
        if delete_entire_span:
            user_block += (
                "\nThe user wants this selected span REMOVED. "
                'Prefer {"replacement": ""}. '
                "If that would leave ungrammatical text at the join (e.g. a paragraph "
                "starting mid-phrase with a lowercase word), return a MINIMAL "
                "grammatically correct replacement for the span only — fix capitalization/"
                "join words, do not add new claims.\n"
            )
        elif local_edit and local_edit.kind == "remove_named":
            user_block += (
                f"\nThe user asked to REMOVE {local_edit.target!r} from this excerpt. "
                "Delete only that person / row / sentence. Keep every other heading, "
                "sentence, and table row verbatim. Do NOT blank the whole excerpt.\n"
            )
        elif local_edit and local_edit.kind == "add_named":
            user_block += (
                f"\nThe user asked to ADD {local_edit.target!r} in this excerpt. "
                "Insert it and preserve all existing sentences and rows.\n"
            )
        elif allow_verify_fill and VERIFY_TAG_RE.search(masked_excerpt):
            user_block += (
                "\nThe user wants [VERIFY] tags filled. Return the FULL selected excerpt "
                "with ONLY those tags replaced by KB facts (or keep precise [VERIFY] if "
                "unknown). Preserve every other sentence/heading/list item verbatim — "
                "do NOT return only the filled value.\n"
            )
        if attempt == 2 and (mfill_originals or content_mfill):
            user_block = (
                f"RETRY: Your previous output dropped protected «MFILL_N» tokens. "
                f"Copy every «MFILL_N» through unchanged.\n\n{user_block}"
            )

        table_excerpt = (masked_excerpt or "").count("|") >= 4
        # Sonnet 5 (tier="heavy") can spend a large, variable share of any
        # budget on invisible adaptive-thinking tokens before the first
        # visible content token — a tight cap risks empty/truncated output.
        # Haiku 4.5 (tier="light", the lean path) doesn't have that behavior,
        # so its smaller, table-aware budget is left as-is.
        sel_max_tokens = (4096 if table_excerpt else 1200) if lean else 16000
        try:
            raw, provider = await llm.chat_json(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_block},
                ],
                max_tokens=sel_max_tokens,
                temperature=0.25,
                node_name="chat_excerpt_edit",
                tier="light" if lean else "heavy",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Selection edit LLM failed attempt %d: %s",
                attempt,
                str(exc)[:240],
            )
            if attempt < 2:
                continue
            raise
        raw = raw if isinstance(raw, dict) else {}
        # Empty string is valid when deleting the span — do not .strip() away intent
        # before checking the key; treat missing key as empty only for remove asks.
        if "replacement" in raw:
            replacement = str(raw.get("replacement") or "")
        elif "content" in raw:
            replacement = str(raw.get("content") or "")
        else:
            replacement = ""
        # Normalize: keep internal whitespace, but treat whitespace-only as empty delete.
        if not replacement.strip() and (
            delete_entire_span
            or (
                local_edit is not None
                and local_edit.kind == "remove_named"
                and word_count(excerpt) <= 220
            )
        ):
            replacement = ""
        elif not replacement.strip():
            raise ProposalError(
                "Selection edit did not return replacement text. "
                "Try a more specific instruction.",
                status_code=422,
            )
        elif not allow_remove:
            replacement = replacement.strip()
        try:
            # Only excerpt placeholders must survive in the replacement span.
            if replacement and mfill_originals:
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
                if replacement and mfill_originals:
                    replacement = _unmask_manual_fill_checked(
                        replacement, mfill_originals, attempt=attempt
                    )
                last_mfill_error = None
                break
    if last_mfill_error and not replacement and not allow_remove:
        raise last_mfill_error

    refusal = refuse_noncompliant_budget_edit(
        ask_for_compliance, replacement, prior_text=excerpt
    )
    if refusal:
        raise ProposalError(refusal, status_code=422)

    kb_fills = 0
    if blob_for_facts.strip() and VERIFY_TAG_RE.search(replacement):
        replacement, kb_fills = _replace_verify_tags_from_blob(replacement, blob_for_facts)

    if _selection_replacement_regressed(
        excerpt,
        replacement,
        allow_remove=allow_remove,
        allow_verify_fill=allow_verify_fill,
    ):
        # Last chance: if LLM truncated a VERIFY fill, try deterministic fill on excerpt.
        if allow_verify_fill and blob_for_facts.strip() and VERIFY_TAG_RE.search(excerpt):
            filled_excerpt, fill_n = _replace_verify_tags_from_blob(
                excerpt, blob_for_facts
            )
            if fill_n > 0 and not _selection_replacement_regressed(
                excerpt,
                filled_excerpt,
                allow_remove=False,
                allow_verify_fill=True,
            ):
                replacement = filled_excerpt
                kb_fills = max(kb_fills, fill_n)
            else:
                raise ProposalError(
                    "Selection edit would remove too much content — rejected to protect the section. "
                    "Try selecting only the passage with [VERIFY] tags, or ask to fill a specific gap.",
                    status_code=422,
                )
        else:
            raise ProposalError(
                "Selection edit would remove too much content — rejected to protect the section. "
                "Try selecting only the passage with [VERIFY] tags, or ask to fill a specific gap.",
                status_code=422,
            )
    if replacement.strip() == excerpt.strip() and kb_fills == 0 and not allow_remove:
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
    if allow_remove and replacement.strip() == excerpt.strip():
        # Model failed to delete — force empty replacement for explicit remove asks.
        replacement = ""

    if not lean:
        replacement = enforce_narrative_voice(
            replacement,
            section_id=section.id,
            title=section.title,
            zo_mode=section.mode,
        )
    from app.services.proposal_manuscript import strip_evidence_citation_markers

    replacement = strip_evidence_citation_markers(replacement)
    if "«MFILL_" in replacement and "«MFILL_" not in (excerpt or ""):
        from app.services.proposal_manual_flags import scrub_orphan_mfill_placeholders

        replacement, _ = scrub_orphan_mfill_placeholders(replacement)
    # Guard: if the model over-ran the selected span and re-emitted surrounding
    # text, trim the overlap so the splice cannot duplicate the sentence.
    if replacement.strip():
        replacement = _trim_replacement_boundary_overlap(
            replacement,
            prefix=content[:selection_start],
            suffix=content[selection_end:],
        )
    # Pure splice — do not run strip/voice on the full section (that mutates prefix/suffix
    # and falsely trips the before/after guards).
    new_content = _splice_selection(
        content,
        start=selection_start,
        end=selection_end,
        replacement=replacement,
    )

    join_healed = False
    prefix = content[:selection_start]
    suffix = content[selection_end:]
    if allow_remove and not replacement.strip():
        broken = _selection_join_looks_broken(prefix, suffix)
        new_content = _heal_selection_join_deterministic(
            new_content, splice_at=selection_start
        )
        join_healed = True
        if broken:
            llm_healed = await _heal_selection_join_llm(
                section_title=section.title,
                content=new_content,
                deleted_excerpt=excerpt,
                user_message=user_message,
            )
            if llm_healed and _remove_heal_is_safe(
                spliced=new_content, healed=llm_healed
            ):
                new_content = llm_healed

    if not join_healed:
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
        role=AgentRole.QUERY_PLANNER,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector,
        rfp_title=rfp.title,
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
        tier="light",
        node_name="query_planner",
    )
    queries = raw.get("queries", [])
    if not isinstance(queries, list):
        return []
    used = {q.strip().lower() for q in prior_queries}
    cleaned: list[str] = []
    for query in queries:
        text = proposal_knowledge_base_tools.normalize_zo_kb_query(
            str(query).strip(),
            rfp_client=rfp.client,
            rfp_sector=rfp.sector,
            rfp_title=rfp.title,
        )
        if text and text.lower() not in used:
            cleaned.append(text[:240])
            used.add(text.lower())
    return cleaned[:6]


async def _redraft_rfp_section(
    *,
    draft: ProposalDraft,
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
    compliance_user_message: str | None = None,
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
    from app.services.proposal_bio_stub import (
        MISPLACED_BIO_STUB_REWRITE_NOTE,
        prior_content_for_rewrite,
    )

    if original_content and not prior_for_agent.strip():
        rewrite_note = f"\n\nIMPORTANT: {MISPLACED_BIO_STUB_REWRITE_NOTE}\n"
    bio_kb = await _bio_kb_context_for_section(
        section, user_message=user_message or compliance_user_message or ""
    )
    if full_rewrite:
        rewrite_note = (
            rewrite_note
            + "\n\nIMPORTANT: Prior draft is below the word target or not marked generated. "
            "Write the COMPLETE section for every listed requirement from evidence and KB tools. "
            "Do not return stubs, error text, or unchanged placeholder content.\n"
        )

    # Protect MANUAL FILL tags in the prior draft from incidental rewrite.
    source_for_tags = prior_for_agent or prior_content_for_rewrite(
        section.id, prior_content or original_content
    )
    from app.services.proposal_manuscript_locks import (
        kpi_weave_instruction,
        strip_kpi_lock_manual_fills,
    )

    source_for_tags, _ = strip_kpi_lock_manual_fills(source_for_tags)
    kpi_block = kpi_weave_instruction(
        research.manuscript_locks if research else None
    )
    masked_prior, mfill_originals = _mask_manual_fill_for_rewrite(source_for_tags)
    # Keep prior_for_agent length behavior but on masked text when tags exist.
    if mfill_originals:
        prior_for_agent, _ = prior_content_for_redraft(
            section.model_copy(update={"content": masked_prior})
        )
    redraft_system = SECTION_REDRAFT_PROMPT
    if mfill_originals:
        redraft_system = f"{SECTION_REDRAFT_PROMPT}\n\n{_MANUAL_FILL_PRESERVE_CONSTRAINT}"

    max_tokens = 16000
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
            f"Word target: {section.word_target} MAX — aim for {int(section.word_target * 0.6)}-{int(section.word_target * 0.75)} words. "
            "Every sentence must earn its place; cut filler, redundancy, and RFP echo. "
            "Go above 75% ONLY if substance demands it.\n"
            "FORMAT: Prefer short paragraphs, markdown bullets, and compact markdown tables for "
            "phases/process/cadence. Add designerNote / [DESIGNER NOTE: …] when a "
            "table/timeline/swimlane/infographic would help evaluators scan faster.\n"
            f"Requirements:\n"
            + "\n".join(f"- {r}" for r in requirements)
            + rewrite_note
            + (
                f"\n\n=== USER INSTRUCTION (verbatim — obey this; YOU decide tools/queries) ===\n"
                f"{(compliance_user_message or user_message).strip()}\n"
            )
            + (
                f"\nPlanner notes (secondary — do not override the user instruction):\n"
                f"{user_message.strip()}\n"
                if compliance_user_message
                and user_message.strip()
                and user_message.strip() != compliance_user_message.strip()
                else ""
            )
            + f"\nPrevious draft:\n{prior_for_agent[:3000] if prior_for_agent else '(none — write from scratch)'}\n\n"
            f"RFP excerpt:\n{rfp_context[:4000]}\n\n"
            f"Evidence corpus:\n{_format_evidence(evidence)}\n\n"
            + (f"{avoidance_block}\n\n" if avoidance_block else "")
            + (f"zö Sections 1–3 reference:\n{zo_context[:3000]}\n" if zo_context else "")
        )
        from app.services.proposal_drafting_prompts import (
            MODULAR_APPROACH_BLOCK,
            is_modular_approach_section,
        )

        if is_modular_approach_section(section.title or ""):
            user_block = f"{MODULAR_APPROACH_BLOCK}\n\n{user_block}"
        # Cross-section anti-duplication: tell LLM what other sections already cover
        from app.services.proposal_section_dedup import (
            format_anti_duplication_rules,
            format_prior_sections_block,
        )

        prior_secs = [
            s for s in draft.sections
            if s.id != section.id and (s.content or "").strip()
        ]
        dedup_rules = format_anti_duplication_rules()
        prior_block = format_prior_sections_block(prior_secs, exclude_ids={section.id})
        user_block = f"{dedup_rules}\n\n{prior_block}\n\n{user_block}" if prior_block else f"{dedup_rules}\n\n{user_block}"
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
        if kpi_block.strip():
            user_block += f"\n\n{kpi_block.strip()}\n"
        if should_apply_budget_playbook(section, user_message):
            from app.services.proposal_budget_playbook import build_budget_repair_context

            try:
                budget_ctx = await build_budget_repair_context(
                    rfp=rfp,
                    rfp_text=rfp_context,
                    research=research,
                    user_message=user_message,
                )
                user_block += f"\n{budget_ctx}\n"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Budget revise context skipped: %s", exc)
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
                rfp_sector=rfp.sector,
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

        refusal = refuse_noncompliant_budget_edit(
            (compliance_user_message or user_message),
            content,
            prior_text=original_content,
        )
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
                content = _unmask_manual_fill_checked(
                    content, mfill_originals, attempt=attempt
                )
                break

    if bio_kb.strip():
        content, _ = _apply_bio_work_history_kb_fill(section, content, bio_kb)
        content = enforce_narrative_voice(
            content,
            section_id=section.id,
            title=section.title,
            zo_mode=section.mode,
        )
    # KB references removed - not included in proposals

    content = repair_prose_disguised_as_table_rows(content)

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
            rfp_client=rfp.client,
            rfp_sector=rfp.sector,
        )
        if text.strip():
            kb_parts.append(text[:4500])
        sources.extend(refs)

    if not kb_parts:
        text, refs = await proposal_knowledge_base_tools.search_knowledge_base(
            f"zö agency {section.title} firm address phone email philosophy {rfp.sector}",
            limit=10,
            rfp_client=rfp.client,
            rfp_sector=rfp.sector,
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
    from app.services.proposal_bio_stub import (
        MISPLACED_BIO_STUB_REWRITE_NOTE,
        prior_content_for_rewrite,
    )

    rewrite_body = prior_content_for_rewrite(section.id, prior)
    masked_prior, mfill_originals = _mask_manual_fill_for_rewrite(rewrite_body)
    system_prompt = STATIC_SECTION_REDRAFT_PROMPT
    if mfill_originals:
        system_prompt = f"{STATIC_SECTION_REDRAFT_PROMPT}\n\n{_MANUAL_FILL_PRESERVE_CONSTRAINT}"

    content = ""
    provider = _provider_name()
    raw: dict[str, Any] = {}
    for attempt in (1, 2):
        misplaced = (
            f"{MISPLACED_BIO_STUB_REWRITE_NOTE}\n\n"
            if prior.strip() and not rewrite_body.strip()
            else ""
        )
        user_content = (
            f"BRAND VOICE (mandatory — maintain throughout; do not genericize):\n{voice_block}\n\n"
            f"Section: {section.title}\n"
            f"Mode: {section.mode}\n"
            f"Client: {rfp.client}\n"
            f"Sector: {rfp.sector}\n"
            f"{misplaced}"
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
            max_tokens=16000,
            temperature=0.28,
            node_name="chat_full_redraft",
        )
        content = enforce_narrative_voice(
            str(raw.get("content", "")).strip(),
            section_id=section.id,
            title=section.title,
            register="narrative",
        )
        content = _unmask_manual_fill_checked(
            content, mfill_originals, attempt=attempt
        )
        break

    # Prefer deterministic KB fill for remaining VERIFY tags after rewrite
    bio_kb = await _bio_kb_context_for_section(
        section, user_message=user_message
    )
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
    from app.services.proposal_cross_reference_resolver import (
        resolve_tags_from_manuscript,
    )
    from app.services.proposal_pointer_page_integrity import (
        apply_pointer_page_integrity_to_draft,
    )
    from app.services.proposal_zero_fabrication import (
        apply_zero_fabrication_guards_before_persist,
    )

    # Resolve VERIFY/MANUAL FILL answered elsewhere in THIS manuscript first.
    try:
        updated_draft, xref_logs = await resolve_tags_from_manuscript(updated_draft)
        if xref_logs:
            logger.info(
                "cross-reference resolve (chat-persist): %s",
                "; ".join(xref_logs[:8]),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cross-reference resolve skipped on chat-persist: %s", exc)

    # Remap wrong Section-N cross-refs + execute/strip EDITOR NOTES before ZF.
    try:
        updated_draft, ptr_logs = apply_pointer_page_integrity_to_draft(updated_draft)
        if ptr_logs:
            logger.info(
                "pointer-page integrity (chat-persist): %s",
                "; ".join(ptr_logs[:8]),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("pointer-page integrity skipped on chat-persist: %s", exc)

    guarded, _report = await apply_zero_fabrication_guards_before_persist(
        updated_draft,
        research=research,
        budget=research.budget if research else None,
        label="chat-persist",
    )
    to_save = push_after_section_edit_snapshot(
        guarded,
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


async def _try_budget_summary_reconcile(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    persist: bool,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache | None,
    str,
    str,
    bool,
] | None:
    """Sync Professional fees / travel / total labels from the canonical ledger.

    On budget tabs: always compare labeled figures to the fee table — no
    user-message keyword gate. When labels already match, return None so the
    pricing agent can draft. Never Stage 3.5 (line items stay as-is).
    """
    ask = (user_message or "").strip()
    # Full rebuild owns the tab — don't steal it into label-only sync.
    if user_asks_budget_rebuild(ask) or user_asks_global_cost_rebuild(ask):
        return None
    if not section_is_budget_related(section):
        # Non-budget tabs: keep the existing explicit Year-1 / pass-through path.
        if not user_asks_budget_summary_reconcile(ask):
            return None

    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )
    canonical = research.budget if research else None
    if canonical is None or not (canonical.line_items or []):
        # Budget tab with no ledger yet: fall through to the pricing agent /
        # canonical refresh unless this is an explicit Year-1 summary ask.
        if section_is_budget_related(section) and not user_asks_budget_summary_reconcile(
            ask
        ):
            return None
        return (
            section,
            draft,
            research,
            provider,
            (
                "I can reconcile the investment summary paragraphs to distinct "
                "agency / pass-through / total figures, but there is no canonical "
                "fee table yet. Restore a prior Cost draft (or rebuild Cost of Base "
                "Proposal once), then resend this summary-only fix."
            ),
            False,
        )

    updated_draft, n = reconcile_draft_budget_summaries(draft, canonical)
    focus = _find_draft_section(updated_draft, section_id) or (
        _find_draft_section(updated_draft, section.id) or section
    )
    if n <= 0:
        # Labels already match — let the agent handle the ask.
        return None

    if persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=focus.title,
        )
        focus = _find_draft_section(updated_draft, section_id) or focus

    from app.services.proposal_budget_content import canonical_budget_summary_figures

    figs = canonical_budget_summary_figures(canonical)
    reply = (
        f"**Patches applied:** Synced investment summary labels from the fee ledger "
        f"({n} label fix(es)) — professional/agency fees ${figs['agency_fee']:,.2f}, "
        f"media pass-through ${figs['passthrough']:,.2f}, direct travel "
        f"${figs['direct']:,.2f}, total client invoicing ${figs['total']:,.2f}. "
        "Phase/line items were left unchanged."
    )
    return focus, updated_draft, research, provider, reply, True


def _budget_section_chat_would_freeform_edit(
    *,
    chat_intent: str,
    user_message: str,
    conversation_history: list[dict[str, str]] | None,
) -> bool:
    if chat_intent in {"single_edit", "multi_patch"}:
        return True
    if chat_intent in {"advisory", "structure"}:
        return False
    return _wants_section_edit(user_message, conversation_history=conversation_history)


async def _apply_budget_section_canonical_refresh(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    rfp_text: str,
    persist: bool,
    reply_hint: str = "",
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache | None,
    str,
    str,
    bool,
]:
    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )
    canonical = research.budget if research else None
    if canonical is None or not (canonical.line_items or []):
        return (
            section,
            draft,
            research,
            provider,
            (
                f"**{section.title}** — I can't refresh fees yet because there is no "
                "canonical Stage 3.5 budget. Run **Budget build** or ask to rebuild "
                "Cost Proposal from the pricing guide first."
            ),
            False,
        )

    from app.services.proposal_budget_validation import reconcile_proposal_budget

    budget = reconcile_proposal_budget(canonical, rfp_context=rfp_text)
    budget = normalize_fixed_pricing_narrative(budget, rfp_text=rfp_text)
    now = datetime.now(timezone.utc).isoformat()
    research = research.model_copy(update={"budget": budget, "updatedAt": now})
    content = render_budget_markdown(budget, rfp_text=rfp_text)
    working = section.model_copy(update={"content": content, "status": "generated"})
    merged = [working if s.id == section_id else s for s in draft.sections]
    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(
        update={"sections": merged, "updated_at": now, "provider": provider}
    )
    if persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=section.title,
        )
        working = _find_draft_section(updated_draft, section_id) or working

    figs = canonical_budget_summary_figures(budget)
    n_items = len(budget.line_items or [])
    cap = budget.rfp_budget_cap or budget.rfp_media_or_program_envelope
    reply = (
        f"**{section.title}** — refreshed from the canonical Stage 3.5 fee ledger "
        f"(${figs['total']:,.2f} total; {n_items} line item(s)). "
        "Cost Proposal is rendered from verified phase fees — chat does not invent "
        "hourly rates or rewrite the fee table. For a full pricing rebuild, ask to "
        "**rebuild Cost Proposal from the pricing guide**."
    )
    if cap is not None and float(cap) > 0:
        total = float(figs.get("total") or 0)
        if total <= float(cap) + 0.01:
            reply += (
                f" Bid total is at or under the RFP available-funds cap "
                f"(${float(cap):,.2f})."
            )
        else:
            reply += (
                f" WARNING: bid total still exceeds the RFP cap "
                f"(${float(cap):,.2f}) — rebuild Cost Proposal."
            )
    if reply_hint.strip():
        reply = f"{reply}\n\n{reply_hint.strip()}"
    return working, updated_draft, research, provider, reply, True


def _budget_manual_fill_tags(
    section: ProposalSection,
    research: ProposalResearchCache | None,
) -> list:
    from app.services.proposal_manual_flags import extract_manual_fill_tags

    seen: set[str] = set()
    tags = []
    for blob in (section.content or "",):
        for tag in extract_manual_fill_tags(blob):
            if tag.text not in seen:
                seen.add(tag.text)
                tags.append(tag)
    budget = research.budget if research else None
    if budget is not None:
        for item in budget.line_items or []:
            for field in (item.description or "", item.notes or ""):
                for tag in extract_manual_fill_tags(field):
                    if tag.text not in seen:
                        seen.add(tag.text)
                        tags.append(tag)
    return tags


async def _try_budget_manual_fill_handoff(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    rfp_context: str,
    rfp_text: str,
    persist: bool,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache | None,
    str,
    str,
    bool,
] | None:
    """Resolve or honestly surface confirm-before-submit MANUAL FILL rows on Cost tabs."""
    from app.services.proposal_manual_flags import (
        is_manual_fill_request,
        user_asks_submit_handoff_fill,
    )

    ask = (user_message or "").strip()
    if not section_is_budget_related(section):
        return None
    if not (is_manual_fill_request(ask) or user_asks_submit_handoff_fill(ask)):
        return None

    tags = _budget_manual_fill_tags(section, research)
    if not tags:
        return None

    mfill = await _try_manual_fill_resolution(
        rfp_id=rfp_id,
        section=section,
        section_id=section_id,
        draft=draft,
        research=research,
        user_message=ask,
        rfp_context=rfp_context,
        persist=False,
    )
    if mfill is not None:
        working, updated_draft, research, provider, reply, changed = mfill
        if changed:
            if persist:
                updated_draft = await _persist_section_improve_draft(
                    updated_draft,
                    research,
                    section_title=section.title,
                )
                working = _find_draft_section(updated_draft, section_id) or working
            return working, updated_draft, research, provider, reply, True

    # Could not invent values — re-render fee table with handoff tags intact.
    refresh = await _apply_budget_section_canonical_refresh(
        rfp_id=rfp_id,
        section=section,
        section_id=section_id,
        draft=draft,
        research=research,
        user_message=ask,
        rfp_text=rfp_text,
        persist=persist,
        reply_hint="",
    )
    working, updated_draft, research, provider, _, changed = refresh
    open_tags = [t.text for t in _budget_manual_fill_tags(working, research)]
    reply = (
        f"**{section.title}** — {len(open_tags)} confirm-before-submit item(s) still "
        "need Sonja / KB before submission (not invented):\n"
        + "\n".join(f"- {tag}" for tag in open_tags[:8])
    )
    if len(open_tags) > 8:
        reply += f"\n- …and {len(open_tags) - 8} more"
    reply += (
        "\n\nProvide the value in chat or add the fact to Supermemory — "
        "I will not invent media base or commission dollars."
    )
    return working, updated_draft, research, provider, reply, changed


async def _try_budget_section_canonical_refresh(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    chat_intent: str,
    conversation_history: list[dict[str, str]] | None,
    rfp_text: str,
    persist: bool,
    selection_mode: bool,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache | None,
    str,
    str,
    bool,
] | None:
    """Re-render Cost / Budget tabs from Stage 3.5 — never freeform LLM fee invention."""
    if selection_mode or not section_is_budget_related(section):
        return None
    ask = (user_message or "").strip()
    if user_asks_budget_rebuild(ask) or user_asks_global_cost_rebuild(ask):
        return None
    if user_asks_budget_summary_reconcile(ask):
        return None
    if user_asked_reverse_engineered_total(ask):
        return None
    from app.services.proposal_manual_flags import (
        extract_manual_fill_tags,
        is_manual_fill_request,
        user_asks_submit_handoff_fill,
    )

    if is_manual_fill_request(ask) or user_asks_submit_handoff_fill(ask):
        return None
    if extract_manual_fill_tags(section.content or ""):
        return None
    if not _budget_section_chat_would_freeform_edit(
        chat_intent=chat_intent,
        user_message=ask,
        conversation_history=conversation_history,
    ):
        return None

    hint = ""
    if ask:
        hint = (
            f"*(Your ask: “{ask[:160]}” — applied as a canonical fee-table refresh, "
            "not a free rewrite.)*"
        )
    return await _apply_budget_section_canonical_refresh(
        rfp_id=rfp_id,
        section=section,
        section_id=section_id,
        draft=draft,
        research=research,
        user_message=ask,
        rfp_text=rfp_text,
        persist=persist,
        reply_hint=hint,
    )


async def _try_forms_attachments_integrity_repair(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    persist: bool,
    apply_fix: bool = False,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache | None,
    str,
    str,
    bool,
] | None:
    """Audit + repair all forms-table status claims against the manuscript in one pass."""
    from app.services.proposal_forms_attachments_integrity import (
        audit_and_repair_forms_attachments,
        format_forms_integrity_reply,
        section_is_forms_attachments,
    )

    if not section_is_forms_attachments(section):
        return None
    if not _wants_section_edit(user_message or ""):
        return None

    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )

    result = await audit_and_repair_forms_attachments(
        section.content or "",
        draft=draft,
        research=research,
    )

    if not result.findings and not result.changed:
        if apply_fix:
            # Nothing to repair — let the Apply-the-fix rewrite run instead of
            # answering an audit the user never asked for.
            return None
        return (
            section,
            draft,
            research,
            provider,
            format_forms_integrity_reply(result, section_title=section.title or ""),
            False,
        )

    working = section.model_copy(
        update={"content": result.content, "status": "generated"}
    )
    merged = [working if s.id == section_id else s for s in draft.sections]
    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(
        update={"sections": merged, "updated_at": now, "provider": provider}
    )
    if persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=section.title,
        )
        working = _find_draft_section(updated_draft, section_id) or working

    reply = format_forms_integrity_reply(result, section_title=section.title or "")
    return working, updated_draft, research, provider, reply, result.changed


async def _try_section_cert_claim_scrub(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    persist: bool,
    apply_fix: bool = False,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache | None,
    str,
    str,
    bool,
] | None:
    """Deterministic cert scrub when user asks to remove false/unverified certifications."""
    from app.services.proposal_cert_claim_scrub import (
        scrub_section_cert_claims,
        user_asks_cert_claim_scrub,
    )

    ask = (user_message or "").strip()
    if not user_asks_cert_claim_scrub(ask):
        return None

    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )

    scrubbed, logs = scrub_section_cert_claims(section)
    if not logs and (scrubbed.content or "") == (section.content or ""):
        if apply_fix:
            # Nothing fabricated to remove — Apply the fix must fall through to
            # the rewrite, not report a cert review and drop the instruction.
            return None
        return (
            section,
            draft,
            research,
            provider,
            (
                f"**{section.title}** — reviewed certification claims against the verified "
                "agency list (WBENC and WOSB only). No fabricated or unverified badges remain."
            ),
            False,
        )

    working = scrubbed.model_copy(update={"status": "generated"})
    merged = [working if s.id == section_id else s for s in draft.sections]
    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(
        update={"sections": merged, "updated_at": now, "provider": provider}
    )
    if persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=section.title,
        )
        working = _find_draft_section(updated_draft, section_id) or working

    reply = (
        f"**{section.title}** — removed fabricated / unverified certification claims "
        f"({len(logs)} change(s)). Kept only verified agency credentials: WBENC/WOSB. "
        + "; ".join(logs[:4])
        + ("…" if len(logs) > 4 else "")
    )
    return working, updated_draft, research, provider, reply, True


async def _try_section_budget_table_insert(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    persist: bool,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache | None,
    str,
    str,
    bool,
] | None:
    """Insert/replace a fee table in the open section from the canonical budget.

    Never runs Stage 3.5 and never rewrites surrounding prose.
    """
    ask = (user_message or "").strip()
    if not user_asks_insert_budget_table(ask):
        return None
    if section_is_budget_related(section):
        from app.services.go_no_go_service import combine_rfp_text
        from app.services.proposal_common import load_rfp_for_proposal

        _, content_info, rfp_ctx = load_rfp_for_proposal(rfp_id)
        rfp_body = combine_rfp_text(content_info.description, content_info.pdf_text)
        return await _apply_budget_section_canonical_refresh(
            rfp_id=rfp_id,
            section=section,
            section_id=section_id,
            draft=draft,
            research=research,
            user_message=ask,
            rfp_text=rfp_body or rfp_ctx or "",
            persist=persist,
            reply_hint=(
                "Inserted/refreshed the fee table from the canonical Stage 3.5 budget."
            ),
        )

    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )
    canonical = research.budget if research else None
    if canonical is None or not (canonical.line_items or []):
        # Fall back to copying an existing Cost Proposal section body table if present.
        cost_sec = next(
            (s for s in draft.sections if section_is_budget_related(s)),
            None,
        )
        if cost_sec and (cost_sec.content or "").strip():
            table_md = cost_sec.content or ""
        else:
            return (
                section,
                draft,
                research,
                provider,
                (
                    f"**{section.title}** — I can add a budget table here, but there is "
                    "no canonical Cost Proposal / Stage 3.5 budget yet. Open **Cost of "
                    "Base Proposal**, rebuild it from 00_Guide_Pricing first, then ask "
                    "again to **add the budget table here**. Surrounding prose was not "
                    "changed."
                ),
                False,
            )
    else:
        table_md = render_embedded_budget_table_markdown(canonical)

    before = section.content or ""
    # Always scrub E-markers / pricing flags even when refreshing the table.
    from app.services.proposal_manuscript import scrub_client_facing_section_artifacts

    before_scrubbed = scrub_client_facing_section_artifacts(before)
    updated, action = insert_budget_table_into_section(before_scrubbed, table_md)
    updated = scrub_client_facing_section_artifacts(updated)
    if updated.strip() == before.strip():
        # Scrub-only success (removed E markers / flags, no table change needed).
        if before_scrubbed.strip() != before.strip():
            working = section.model_copy(
                update={"content": before_scrubbed, "status": "generated"}
            )
            merged = [working if s.id == section_id else s for s in draft.sections]
            now = datetime.now(timezone.utc).isoformat()
            updated_draft = draft.model_copy(
                update={
                    "sections": merged,
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
            return (
                working,
                updated_draft,
                research,
                provider,
                (
                    f"Removed evidence markers (`[E…]`) and internal pricing flags from "
                    f"**{section.title}**. No other prose rewritten."
                ),
                True,
            )
        return (
            section,
            draft,
            research,
            provider,
            (
                f"Could not {action} a budget table in **{section.title}** — "
                "surrounding text left unchanged."
            ),
            False,
        )

    working = section.model_copy(update={"content": updated, "status": "generated"})
    merged = [working if s.id == section_id else s for s in draft.sections]
    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(
        update={"sections": merged, "updated_at": now, "provider": provider}
    )
    if persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=section.title,
        )
    n_lines = len(canonical.line_items) if canonical and canonical.line_items else "existing"
    total = None
    if canonical is not None:
        from app.services.proposal_budget_content import _canonical_client_total

        total = _canonical_client_total(canonical)
    total_bit = f", total **{_usd_fmt(total)}**" if total else ""
    reply = (
        f"{'Replaced' if action == 'replaced' else 'Inserted'} a clean budget / fee table "
        f"in **{section.title}** from the canonical Cost Proposal "
        f"({n_lines} line items{total_bit}). "
        "Removed `[E…]` evidence markers and `[PRICING FLAG]` notes. "
        "Bold labels + phase table only — surrounding compliance prose preserved "
        "(no Stage 3.5 rebuild)."
    )
    return working, updated_draft, research, provider, reply, True


def _usd_fmt(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value - round(value)) < 0.01:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


async def _try_manual_fill_resolution(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    rfp_context: str,
    persist: bool,
    selection_mode: bool = False,
    selection_start: int | None = None,
    selection_end: int | None = None,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache | None,
    str,
    str,
    bool,
] | None:
    """Resolve explicit MANUAL FILL asks from user text then KB — never invent.

    Returns None when this path does not apply. Must run before LLM intent
    classification so fill requests never hit rewrite/classify paths (T2.4).
    """
    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

    latest_user_ask = (user_message or "").strip()
    wants_gap_fill = is_manual_fill_request(latest_user_ask) or (
        selection_mode and user_asks_kb_fetch_or_fill(latest_user_ask)
    )
    if not wants_gap_fill:
        return None

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

    from app.services.proposal_manuscript import (
        convert_bare_confirmation_lines,
        convert_inline_confirmation_phrases,
        convert_instruction_blocks,
    )

    target_text = convert_inline_confirmation_phrases(
        convert_bare_confirmation_lines(convert_instruction_blocks(target_text))
    )

    if not extract_manual_fill_tags(target_text):
        return None

    tags = extract_manual_fill_tags(target_text)
    from app.services.proposal_manuscript_locks import is_kpi_lock_manual_fill

    if tags and all(is_kpi_lock_manual_fill(t.text) for t in tags):
        # KPI-lock tags need prose weaving on Improve — not KB scalar fill.
        return None

    logger.info(
        "manual_fill_resolution start rfp_id=%s section_id=%s selection=%s",
        rfp_id,
        section_id,
        selection_mode,
    )

    evidence_blob = ""
    if research and research.evidence_corpus:
        evidence_blob = _section_corpus_blob(research.evidence_corpus, section_id)
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
        sources = ", ".join(f"`{e['tag']}` ← {e['source']}" for e in fill_log[:6])
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
        logger.info(
            "manual_fill_resolution filled count=%s remaining=%s rfp_id=%s",
            len(fill_log),
            len(remaining),
            rfp_id,
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
    logger.info(
        "manual_fill_resolution unresolved tags=%s rfp_id=%s",
        len(extract_manual_fill_tags(target_text)),
        rfp_id,
    )
    return section, draft, research, provider, reply, False


async def _try_section_budget_verify_fill(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    persist: bool,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache | None,
    str,
    str,
    bool,
] | None:
    """Fill budget/investment [VERIFY] tags in the open section from canonical budget.

    Returns None when this path does not apply. Used before advisory/structure routing
    so "here fill Investment part!" does not become a clarify-about-sidebar-tabs reply.
    """
    ask = (user_message or "").strip()
    if not user_asks_section_budget_fill(ask):
        return None
    if section_is_budget_related(section):
        return None
    if not section_has_budget_verify_tags(section.content or ""):
        return None

    from app.core.step_debug_logger import step_trace

    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )
    canonical = research.budget if research else None
    if canonical is None or not (canonical.line_items or []):
        step_trace(
            "section_budget_verify_fill_no_canonical",
            rfp_id=rfp_id,
            section_id=section.id,
            section_title=section.title,
            has_canonical=bool(canonical is not None),
            canonical_line_items=len(getattr(canonical, "line_items", []) or [])
            if canonical is not None
            else 0,
        )
        return (
            section,
            draft,
            research,
            provider,
            (
                f"**{section.title}** still has budget `[VERIFY]` tags, but there is no "
                "canonical Stage 3.5 budget to pull figures from yet. Open **Cost of "
                "Base Proposal** (or ask to rebuild that Cost section from the Pricing "
                "Guide) first — then ask again to fill this section's budget part."
            ),
            False,
        )
    filled, n_fills = fill_section_budget_verify_from_canonical(
        section.content or "",
        canonical,
    )
    step_trace(
        "section_budget_verify_fill_result",
        rfp_id=rfp_id,
        section_id=section.id,
        section_title=section.title,
        n_fills=n_fills,
        canonical_line_items=len(getattr(canonical, "line_items", []) or []),
    )
    if n_fills <= 0:
        return (
            section,
            draft,
            research,
            provider,
            (
                f"I could not map the remaining budget `[VERIFY]` tags in "
                f"**{section.title}** to the canonical fee table. Say which phase "
                "rows to fill, or paste the dollar amounts."
            ),
            False,
        )
    working = section.model_copy(update={"content": filled, "status": "generated"})
    merged = [working if s.id == section_id else s for s in draft.sections]
    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(
        update={"sections": merged, "updated_at": now, "provider": provider}
    )
    if persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=section.title,
        )
    remaining = _gap_fields_from_text(filled)
    remaining_budget = [
        g
        for g in remaining
        if any(
            k in g.casefold()
            for k in ("budget", "investment", "fee", "total", "cost")
        )
    ]
    reply = (
        f"Filled **{n_fills}** budget figure(s) in **{section.title}** from the "
        "canonical Cost Proposal / Stage 3.5 budget. Other sections unchanged."
    )
    if remaining_budget:
        reply += (
            " Still open: "
            + ", ".join(f"`{g}`" for g in remaining_budget[:6])
            + "."
        )
    return working, updated_draft, research, provider, reply, True


async def _try_offer_form_of2_fill(
    *,
    rfp_id: str,
    section: ProposalSection,
    section_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    persist: bool,
) -> tuple[
    ProposalSection,
    ProposalDraft,
    ProposalResearchCache | None,
    str,
    str,
    bool,
] | None:
    """Deterministically fill OF-2 from the canonical budget.

    Root-cause fix: OF-2 is budget-related, so it bypasses the generic
    section-budget VERIFY path. Without a deterministic renderer it falls through
    to freeform LLM rewrite, which can leak nearby phone numbers into cost cells.
    """
    ask = (user_message or "").strip()
    if not user_asks_section_budget_fill(ask):
        return None
    if "offer form of-2" not in (section.title or "").casefold():
        return None

    from app.core.step_debug_logger import step_trace
    from app.services.proposal_budget_content import render_offer_form_of2_from_canonical

    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp_id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )
    canonical = research.budget if research else None
    if canonical is None or not (canonical.line_items or []):
        step_trace(
            "offer_form_of2_fill_no_canonical",
            rfp_id=rfp_id,
            section_id=section.id,
            section_title=section.title,
        )
        return None

    filled, changed = render_offer_form_of2_from_canonical(section.content or "", canonical)
    step_trace(
        "offer_form_of2_fill_result",
        rfp_id=rfp_id,
        section_id=section.id,
        section_title=section.title,
        changed=changed,
        canonical_line_items=len(getattr(canonical, "line_items", []) or []),
        total=getattr(canonical, "lump_sum_total", None)
        or getattr(canonical, "agency_revenue_estimate", None),
    )
    if not changed:
        return None

    working = section.model_copy(update={"content": filled, "status": "generated"})
    merged = [working if s.id == section_id else s for s in draft.sections]
    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(
        update={"sections": merged, "updated_at": now, "provider": provider}
    )
    if persist:
        updated_draft = await _persist_section_improve_draft(
            updated_draft,
            research,
            section_title=section.title,
        )
    reply = (
        f"Filled **{section.title}** deterministically from the canonical Stage 3.5 "
        "budget, including subtotal / GET / total fields."
    )
    return working, updated_draft, research, provider, reply, True



async def _try_redraft_failed_section(
    *,
    rfp_id: str,
    section: ProposalSection,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    rfp: RfpRecord,
):
    """Rebuild a section that holds no usable draft, straight from chat.

    A dead section holds only a short [VERIFY: ...] stub, so the normal chat edit
    paths have nothing to improve — they would rewrite the stub rather than
    produce the section. Recovering previously meant waiting for the whole
    pipeline to be idle and pressing Continue Proposal, which is not possible
    while a later phase (e.g. budget) is still running.

    Detection is on the section's STATE, not on the user's wording: if the section
    holds no draft, any ask to write/fix/redraft it means "draft this properly".
    Returns None when this path does not apply.

    State is classified by proposal_section_health rather than compared against
    SECTION_DRAFT_FAILURE_PLACEHOLDER. Stored drafts contain punctuation variants
    of that sentinel, and exact equality silently skipped them — the section then
    fell through to the advisory router and chat answered "I cannot improve this
    section" instead of rebuilding it.
    """
    from app.services.proposal_section_health import (
        SectionHealth,
        classify_section_health,
        is_failed_draft_stub,
    )

    # Only a section that drafting ran on and left a stub in. An empty section is
    # not hijacked here — the normal edit path writes it. is_failed_draft_stub is
    # the single definition of that set, so adding a stub kind (PLACEHOLDER_ONLY)
    # does not need a second edit here.
    if not is_failed_draft_stub(section.content):
        return None
    health = classify_section_health(section.content)

    from app.services.proposal_self_edit_loop import (
        _redraft_section_via_phase3_isolated,
    )

    logger.info(
        "chat redrafting dead section %s for %s (health=%s)",
        section.id,
        rfp_id,
        health.value,
    )
    next_draft, next_research, changed, detail = await _redraft_section_via_phase3_isolated(
        rfp_id=rfp_id,
        section_id=section.id,
        rewrite_brief=(user_message or "").strip()[:600],
        rfp=rfp,
        draft=draft,
        research=research,
    )
    if not changed:
        # Distinguish the two failure modes. A provider outage is retryable; a
        # corpus gap is not, and telling the user to "try again shortly" for a
        # corpus gap just sends them round the same loop.
        if health is SectionHealth.NO_EVIDENCE:
            message = (
                f"I re-ran drafting for \u201c{section.title}\u201d but the knowledge "
                f"base still has no evidence to write it from ({detail}).\n\n"
                "This section needs source material before it can be drafted — "
                "add the relevant document to the knowledge base, or tell me the "
                "specifics here and I'll write it from what you give me. "
                "I won't invent content for it."
            )
        else:
            message = (
                f"Could not rebuild \u201c{section.title}\u201d yet ({detail}). "
                "This usually means the model provider is rate-limited or out of "
                "credit — try again shortly."
            )
        return (section, draft, research, "none", message, False)

    rebuilt = next((s for s in next_draft.sections if s.id == section.id), section)
    return (
        rebuilt,
        next_draft,
        next_research,
        "phase3",
        f"Rebuilt \u201c{section.title}\u201d from Phase 3 drafting.",
        True,
    )


def _is_static_company_section(section: ProposalSection) -> bool:
    sid = section.id or ""
    return (
        sid in STATIC_SECTION_IDS
        or section.source == "template"
        or sid.startswith("section-1-")
        or sid.startswith("section-2-")
        or sid.startswith("section-3-")
    )


async def _seed_empty_static_section(
    *,
    section: ProposalSection,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    rfp_context: str,
    user_message: str,
    persist: bool,
) -> tuple[ProposalSection, ProposalDraft, ProposalResearchCache, str, str, bool]:
    """Write one empty static tab without Phase-3/repair recursion."""
    from datetime import datetime, timezone

    from app.services.agency_facts import (
        default_business_information_markdown,
        enforce_agency_tenure,
    )

    provider = _provider_name()
    if research is None:
        research = ProposalResearchCache(
            rfpId=rfp.id,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            provider=provider,
        )

    brief = (user_message or "").strip()[:600] or "Draft this section for the RFP."
    updated = section

    # Canonical 1.3 — never invent; seed companyfacts table immediately.
    if section.id == "section-1-business-info":
        content = enforce_agency_tenure(default_business_information_markdown())
        updated = section.model_copy(
            update={"content": content, "status": "generated"}
        )
        detail = "seeded Business Information from companyfacts"
    else:
        brand_voice = None
        if research and research.brand_voice is not None:
            try:
                brand_voice = research.brand_voice.model_dump(by_alias=True)
            except Exception:  # noqa: BLE001
                brand_voice = None
        updated, provider = await _improve_static_section(
            section=section,
            rfp=rfp,
            rfp_context=rfp_context,
            queries=[
                f"zö agency {section.title} companyfacts business information",
                f"zö agency {section.title} verified facts",
            ],
            user_message=(
                f"{brief}\n\n"
                "This section is EMPTY. Write full submission-ready content now "
                "from KB / companyfacts. Do not leave blank. Do not invent staff, "
                "rates, insurance carriers, or certifications."
            ),
            brand_voice=brand_voice,
            kb_zo_voice="",
        )
        detail = "drafted via static section improve"
        if not (updated.content or "").strip():
            return (
                section,
                draft,
                research,
                provider,
                (
                    f"Could not draft \u201c{section.title}\u201d yet "
                    f"({detail}: empty model response)."
                ),
                False,
            )

    merged = [
        updated if s.id == section.id else s for s in draft.sections
    ]
    next_draft = draft.model_copy(
        update={
            "sections": merged,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
        }
    )
    research = research.model_copy(update={"provider": provider})
    if persist:
        next_draft = await _persist_section_improve_draft(
            next_draft,
            research,
            section_title=section.title,
        )
        updated = next(
            (s for s in next_draft.sections if s.id == section.id),
            updated,
        )
    return (
        updated,
        next_draft,
        research,
        provider,
        f"Drafted \u201c{section.title}\u201d ({detail}).",
        True,
    )


async def _try_draft_empty_section(
    *,
    rfp_id: str,
    section: ProposalSection,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    user_message: str,
    rfp: RfpRecord,
    rfp_context: str,
    improve_section_pinned: bool,
    apply_fix: bool,
    persist: bool = True,
    selection_mode: bool = False,
):
    """Draft a never-populated tab when the user explicitly asked to improve it.

    Writes THAT tab only — never kicks off Sections 1–3 / full proposal, and
    never re-enters improve_proposal_section (that caused an infinite loop).
    """
    from app.services.proposal_section_health import (
        SectionHealth,
        classify_section_health,
    )

    if selection_mode:
        return None

    if classify_section_health(section.content) is not SectionHealth.EMPTY:
        return None
    if not (improve_section_pinned or apply_fix or _wants_section_edit(user_message)):
        return None

    logger.info(
        "chat drafting empty section %s for %s (improve_pin=%s static=%s)",
        section.id,
        rfp_id,
        improve_section_pinned,
        _is_static_company_section(section),
    )

    # Static 1–3 tabs: direct write. The Phase-3 isolated repair path used to
    # call improve_proposal_section again on empty content → infinite loop.
    if _is_static_company_section(section):
        return await _seed_empty_static_section(
            section=section,
            draft=draft,
            research=research,
            rfp=rfp,
            rfp_context=rfp_context,
            user_message=user_message,
            persist=persist,
        )

    from app.services.proposal_self_edit_loop import (
        _redraft_section_via_phase3_isolated,
    )

    brief = (user_message or "").strip()[:600] or "Draft this section for the RFP."
    next_draft, next_research, changed, detail = await _redraft_section_via_phase3_isolated(
        rfp_id=rfp_id,
        section_id=section.id,
        rewrite_brief=brief,
        rfp=rfp,
        draft=draft,
        research=research,
    )
    if not changed:
        message = (
            f"Could not draft \u201c{section.title}\u201d yet ({detail}). "
            "Try again in a moment, or add the missing KB source and retry."
        )
        return (section, draft, research, "none", message, False)

    rebuilt = next((s for s in next_draft.sections if s.id == section.id), section)
    return (
        rebuilt,
        next_draft,
        next_research,
        "phase3",
        f"Drafted \u201c{section.title}\u201d for this RFP.",
        True,
    )


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
    apply_fix: bool = False,
    improve_section_pinned: bool = False,
) -> tuple[ProposalSection, ProposalDraft, ProposalResearchCache, str, str, bool, Any]:
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
    if not selection_mode:
        focus_body = next(
            (s.content or "" for s in draft.sections if s.id == section_id),
            "",
        )
        recovered = _locate_selection_span(focus_body, selection_text)
        if recovered is not None:
            selection_start, selection_end = recovered
            selection_mode = True
            if not (selection_text or "").strip():
                selection_text = focus_body[selection_start:selection_end]
            logger.info(
                "Recovered selection span for %s / %s: chars %d-%d",
                rfp_id,
                section_id,
                selection_start,
                selection_end,
            )
    # Non-selection chat always sees the whole proposal — never only the open tab.
    if not selection_mode:
        proposal_wide = True

    # `user_message` gets prompt scaffolding prepended below (the evidence-gate
    # stanza), so from that point on it is no longer what the user typed. Intent
    # classification must read this copy instead: routing on the augmented string
    # matched the stanza's own edit verbs and turned every question into an edit.
    raw_user_message = user_message.strip()

    from app.services.proposal_chat_improve_pin import user_asks_thorough_section_repair

    if improve_section_pinned:
        selection_mode = False
        selection_start = None
        selection_end = None
        selection_text = None

    if selection_mode and user_asks_thorough_section_repair(raw_user_message):
        logger.info(
            "Thorough / structural ask — full section edit (not selection splice) for %s / %s",
            rfp_id,
            section_id,
        )
        selection_mode = False
        selection_start = None
        selection_end = None
        selection_text = None

    from app.services.proposal_chat_ops import chat_ask_is_proposal_wide

    # Improve pin / "this section" binds the open tab. Don't dump the whole
    # manuscript into advisory — that made the model ask "which section?".
    if (
        (improve_section_pinned or user_points_at_open_section(raw_user_message))
        and not chat_ask_is_proposal_wide(raw_user_message)
        and not selection_mode
    ):
        proposal_wide = False

    research = await aget_research_cache(rfp_id)

    # Shared Evidence Gate: decide KB vs write (same policy as drafting / repair).
    from app.services.proposal_evidence_gate import (
        EvidenceDecision,
        decide_evidence_action,
        evidence_policy_prompt_stanza,
    )

    gate = None
    try:
        target = next((s for s in draft.sections if s.id == section_id), None)
        gate = decide_evidence_action(
            section_id=section_id,
            section_title=target.title if target else "",
            user_ask=user_message,
        )
        logger.info(
            "section_chat_evidence_gate rfp_id=%s section_id=%s decision=%s reason=%s",
            rfp_id,
            section_id,
            gate.action.value,
            gate.reason,
        )
        user_message = (
            evidence_policy_prompt_stanza(gate, section_id=section_id)
            + "\n\n"
            + user_message
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("section_chat_evidence_gate failed rfp_id=%s: %s", rfp_id, exc)

    # Structural merge: keep one tab's body under another title, delete orphan duplicate.
    from app.services.proposal_section_merge import (
        apply_section_merge,
        format_merge_reply,
        plan_section_merge,
    )

    merge_plan = plan_section_merge(
        draft,
        raw_user_message,
        open_section_id=section_id,
    )
    # Apply the fix is a single-section rewrite by construction — a keyword
    # merge plan must never reinterpret the instruction it carries.
    if merge_plan is not None and not selection_mode and not apply_fix:
        provider = _provider_name()
        if research is None:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
        updated_draft, focus, merge_logs = apply_section_merge(draft, merge_plan)
        from app.services.proposal_forms_attachments_integrity import (
            audit_and_repair_forms_attachments,
            section_is_forms_attachments,
        )

        if section_is_forms_attachments(focus):
            forms_fix = await audit_and_repair_forms_attachments(
                focus.content or "",
                draft=updated_draft,
                research=research,
            )
            if forms_fix.changed:
                focus = focus.model_copy(update={"content": forms_fix.content})
                updated_draft = updated_draft.model_copy(
                    update={
                        "sections": [
                            focus if s.id == focus.id else s
                            for s in updated_draft.sections
                        ]
                    }
                )
                merge_logs.extend(forms_fix.fix_logs)
        if persist:
            updated_draft = await _persist_section_improve_draft(
                updated_draft,
                research,
                section_title=focus.title,
            )
            focus = _find_draft_section(updated_draft, focus.id) or focus
        reply = format_merge_reply(merge_logs, focus_title=focus.title or "")
        return _improve_outcome(focus, updated_draft, research, provider, reply, True)

    # Powerful chat ops: duplicate audit / fabrication purge (content → RFP → KB)
    from app.services.proposal_chat_ops import classify_chat_op, run_chat_ops
    from app.services.proposal_align_rfp_outline import (
        align_draft_from_chat,
        message_asks_align_rfp_outline,
    )

    # Whole-packet RFP order/format → Align (not Improve redraft of the open tab).
    # Wins over pin/selection: rearrange asks must never rewrite Who We Are.
    if not apply_fix and message_asks_align_rfp_outline(raw_user_message):
        early = _find_draft_section(draft, section_id) or (
            draft.sections[0] if draft.sections else None
        )
        if early is None:
            raise ProposalError("Draft has no sections.", status_code=400)
        focus, updated, research, reply, changed = await align_draft_from_chat(
            rfp_id=rfp_id,
            draft=draft,
            research=research,
            section=early,
        )
        return _improve_outcome(
            focus, updated, research, _provider_name(), reply, changed
        )

    chat_op = "none" if apply_fix else classify_chat_op(raw_user_message)
    if chat_op != "none" and not selection_mode:
        before_ids = [(s.id, s.content or "") for s in draft.sections]
        updated_draft, ops_report = await run_chat_ops(
            kind=chat_op,
            draft=draft,
            rfp=rfp,
            rfp_context=rfp_context,
            research=research,
            user_message=raw_user_message,
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

        return _improve_outcome(focus, draft, research, provider, ops_report.reply, changed)

    # A section whose Phase 3 draft failed holds only the failure placeholder.
    # Rebuild it before any edit path, which would otherwise "improve" the
    # placeholder text instead of writing the section.
    failed_section = _find_draft_section(draft, section_id) or (
        draft.sections[0] if draft.sections else None
    )
    if failed_section is not None:
        redraft = await _try_redraft_failed_section(
            rfp_id=rfp_id,
            section=failed_section,
            draft=draft,
            research=research,
            user_message=user_message,
            rfp=rfp,
        )
        if redraft is not None:
            return (*redraft, None)
        empty_draft = await _try_draft_empty_section(
            rfp_id=rfp_id,
            section=failed_section,
            draft=draft,
            research=research,
            user_message=raw_user_message,
            rfp=rfp,
            rfp_context=rfp_context,
            improve_section_pinned=improve_section_pinned,
            apply_fix=apply_fix,
            persist=persist,
            selection_mode=selection_mode,
        )
        if empty_draft is not None:
            return (*empty_draft, None)

    # Explicit MANUAL FILL before LLM intent classify — never invent; skip rewrite.
    mfill_section = _find_draft_section(draft, section_id) or (
        draft.sections[0] if draft.sections else None
    )
    if mfill_section is not None:
        # Intent gate must use the raw ask — the evidence stanza literally
        # contains "[MANUAL FILL]" and would hijack every chat turn.
        mfill_result = await _try_manual_fill_resolution(
            rfp_id=rfp_id,
            section=mfill_section,
            section_id=mfill_section.id,
            draft=draft,
            research=research,
            user_message=raw_user_message,
            rfp_context=rfp_context,
            persist=persist,
            selection_mode=selection_mode,
            selection_start=selection_start,
            selection_end=selection_end,
        )
        if mfill_result is not None:
            return (*mfill_result, None)

    # LLM understands the ask: multi-section apply/fix vs advisory vs single edit.
    # No RFP-specific or keyword regex — classification is model-driven.
    chat_intent = "none"
    # The classifier only runs for non-selection chat, but a pinned question now
    # reaches the advisory branch too — which reads this.
    intent_degraded = False
    if not selection_mode:
        early_focus = _find_draft_section(draft, section_id) or (
            draft.sections[0] if draft.sections else None
        )
        if early_focus is None:
            raise ProposalError("Draft has no sections.", status_code=400)
        # Summary-only budget reconcile MUST run before multi_patch / Stage 3.5
        # so "recalculate agency vs pass-through vs total from the fee table"
        # cannot become a full pricing-agent rebuild.
        summary_reconcile = await _try_budget_summary_reconcile(
            rfp_id=rfp_id,
            section=early_focus,
            section_id=section_id,
            draft=draft,
            research=research,
            user_message=raw_user_message,
            persist=persist,
        )
        if summary_reconcile is not None:
            return (*summary_reconcile, None)

        from app.services.proposal_chat_manuscript_fix import (
            classify_chat_edit_intent,
            run_manuscript_wide_fixes,
        )

        # Always classify / resolve on the raw ask. The evidence-gate stanza
        # prepended onto `user_message` contains edit verbs ("replace", "remove")
        # and section-id noise that flip intent and target the wrong tab.
        if apply_fix:
            # One-click Apply the fix: always mutate THIS section only.
            chat_intent = "single_edit"
            intent_info = {
                "intent": "single_edit",
                "primarySectionId": section_id,
                "degraded": False,
            }
            intent_degraded = False
            scope_reason = "apply_fix"
        elif improve_section_pinned:
            intent_info = await classify_chat_edit_intent(
                user_message=raw_user_message,
                draft=draft,
                focus_section_id=section_id,
                rfp_title=rfp.title,
                rfp_client=rfp.client,
                conversation_history=conversation_history,
            )
            chat_intent = str(intent_info.get("intent") or "none")
            from app.services.proposal_chat_ops import coerce_chat_intent_for_scope
            from app.services.proposal_chat_structure import is_add_section_intent

            chat_intent, scope_reason = coerce_chat_intent_for_scope(
                chat_intent, raw_user_message
            )
            # Improve pin binds THIS tab only. Mentions of §21/§22 as move
            # destinations must never become multi_patch / remaps. Only a clear
            # add-tab ask leaves the pin.
            if chat_intent == "structure" or is_add_section_intent(raw_user_message):
                if chat_intent != "structure":
                    chat_intent = "structure"
                intent_degraded = bool(intent_info.get("degraded"))
                scope_reason = "add_section_overrides_improve_pin"
            else:
                intent_degraded = bool(intent_info.get("degraded"))
                if chat_intent == "multi_patch":
                    chat_intent = "single_edit"
                    scope_reason = "improve_pin_blocks_multi_patch"
                else:
                    scope_reason = "improve_section_pinned"
                intent_info["primarySectionId"] = section_id
                intent_info["intent"] = chat_intent
        else:
            intent_info = await classify_chat_edit_intent(
                user_message=raw_user_message,
                draft=draft,
                focus_section_id=section_id,
                rfp_title=rfp.title,
                rfp_client=rfp.client,
                conversation_history=conversation_history,
            )
            chat_intent = str(intent_info.get("intent") or "none")
            from app.services.proposal_chat_ops import coerce_chat_intent_for_scope

            chat_intent, scope_reason = coerce_chat_intent_for_scope(
                chat_intent, raw_user_message
            )
            if scope_reason != "unchanged":
                logger.info(
                    "chat intent coerced to %s (%s) ask=%r",
                    chat_intent,
                    scope_reason,
                    raw_user_message[:80],
                )
            # True when the classifier could not run at all (provider outage), as
            # opposed to deciding "none". Routing then falls back to the keyword gate,
            # which defaults to advisory — so tell the user rather than letting an
            # outage look like the assistant declining to edit.
            intent_degraded = bool(intent_info.get("degraded"))

        from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

        if user_asks_kb_fetch_or_fill(raw_user_message) or _selection_asks_to_fill_verify(
            raw_user_message
        ):
            chat_intent = "single_edit"
            intent_info.setdefault("primarySectionId", section_id)
            logger.info(
                "chat intent forced to single_edit (kb_fetch_or_verify) section_id=%s",
                section_id,
            )

        from app.services.proposal_chat_structure import (
            is_add_section_intent,
            is_bio_resume_attachment_intent,
        )

        if (
            user_points_at_open_section(raw_user_message)
            and _wants_section_edit(raw_user_message)
            and not _selection_ask_is_advisory(
                raw_user_message, conversation_history=conversation_history
            )
            and not is_add_section_intent(raw_user_message)
            and not is_bio_resume_attachment_intent(raw_user_message)
        ):
            chat_intent = "single_edit"
            intent_info["intent"] = "single_edit"
            intent_info.setdefault("primarySectionId", section_id)
            logger.info(
                "chat intent forced to single_edit (this section) section_id=%s",
                section_id,
            )

        if apply_fix:
            logger.info(
                "chat intent forced to single_edit (apply_fix) section_id=%s",
                section_id,
            )

        # For multi_patch, do NOT remap to one named section — the plan spans many.
        # For single_edit / default, named titles + LLM primary beat the open tab.
        # Exception: "… here / this section" stays on the open tab (budget table insert).
        stay_on_open = user_points_at_open_section(raw_user_message)
        if chat_intent != "multi_patch" and not stay_on_open and not apply_fix and not improve_section_pinned:
            primary = intent_info.get("primarySectionId")
            default_sec = _find_draft_section(draft, section_id)
            if isinstance(primary, str) and primary.strip():
                hit = _find_draft_section(draft, primary.strip())
                if hit is not None and _message_explicitly_targets_remote_section(
                    raw_user_message, hit, default_sec
                ):
                    section_id = hit.id
            section_id = _remap_chat_section_if_explicit(
                draft, raw_user_message, section_id
            )
            from_hist = _resolve_section_from_conversation_history(
                draft,
                conversation_history,
                section_id,
                latest_user_message=raw_user_message,
            )
            if from_hist is not None and from_hist.id != section_id:
                hist_default = _find_draft_section(draft, section_id)
                if _message_explicitly_targets_remote_section(
                    raw_user_message, from_hist, hist_default
                ) or _history_may_override_open_tab(raw_user_message):
                    logger.info(
                        "Chat target from history %s → %s (%s)",
                        section_id,
                        from_hist.id,
                        from_hist.title,
                    )
                    section_id = from_hist.id

        # Section-local budget VERIFY fill — before advisory/structure (avoids
        # "Which Investment section?" clarify when the open tab already has the table).
        if not selection_mode and chat_intent != "multi_patch":
            early_section = _find_draft_section(draft, section_id)
            if early_section is not None:
                table_insert = await _try_section_budget_table_insert(
                    rfp_id=rfp_id,
                    section=early_section,
                    section_id=section_id,
                    draft=draft,
                    research=research,
                    user_message=raw_user_message,
                    persist=persist,
                )
                if table_insert is not None:
                    return (*table_insert, None)
                # Deterministic References contact fill from the user's message / KB facts.
                ref_fix = _try_deterministic_references_fix(
                    section=early_section,
                    user_message=raw_user_message,
                    conversation_history=conversation_history,
                    apply_fix=apply_fix,
                    research=research,
                )
                if ref_fix is not None:
                    provider = _provider_name()
                    if research is None:
                        research = ProposalResearchCache(
                            rfpId=rfp_id,
                            updatedAt=datetime.now(timezone.utc).isoformat(),
                            provider=provider,
                        )
                    working, reply = ref_fix
                    merged = [
                        working if s.id == working.id else s for s in draft.sections
                    ]
                    now = datetime.now(timezone.utc).isoformat()
                    updated_draft = draft.model_copy(
                        update={
                            "sections": merged,
                            "updated_at": now,
                            "provider": provider,
                        }
                    )
                    if persist:
                        updated_draft = await _persist_section_improve_draft(
                            updated_draft,
                            research,
                            section_title=working.title,
                        )
                    return (
                        working,
                        updated_draft,
                        research,
                        provider,
                        reply,
                        True,
                        None,
                    )
                budget_fill = await _try_section_budget_verify_fill(
                    rfp_id=rfp_id,
                    section=early_section,
                    section_id=section_id,
                    draft=draft,
                    research=research,
                    user_message=raw_user_message,
                    persist=persist,
                )
                if budget_fill is not None:
                    return (*budget_fill, None)
                offer_form_fill = await _try_offer_form_of2_fill(
                    rfp_id=rfp_id,
                    section=early_section,
                    section_id=section_id,
                    draft=draft,
                    research=research,
                    user_message=raw_user_message,
                    persist=persist,
                )
                if offer_form_fill is not None:
                    return (*offer_form_fill, None)
                forms_integrity = await _try_forms_attachments_integrity_repair(
                    rfp_id=rfp_id,
                    section=early_section,
                    section_id=section_id,
                    draft=draft,
                    research=research,
                    user_message=raw_user_message,
                    persist=persist,
                    apply_fix=apply_fix,
                )
                if forms_integrity is not None:
                    return (*forms_integrity, None)
                cert_scrub = await _try_section_cert_claim_scrub(
                    rfp_id=rfp_id,
                    section=early_section,
                    section_id=section_id,
                    draft=draft,
                    research=research,
                    user_message=raw_user_message,
                    persist=persist,
                    apply_fix=apply_fix,
                )
                if cert_scrub is not None:
                    return (*cert_scrub, None)

        if chat_intent == "multi_patch":
            from app.services.proposal_chat_content_repair import (
                user_asks_content_risk_repair,
                run_content_risk_repair,
            )

            fix_reply = ""
            updated = draft
            fixed = False
            if user_asks_content_risk_repair(raw_user_message):
                repaired = await run_content_risk_repair(
                    draft=updated,
                    rfp=rfp,
                    rfp_context=rfp_context,
                    research=research,
                    user_message=raw_user_message,
                )
                updated = repaired.draft
                fix_reply = repaired.reply
                fixed = bool(repaired.sections_changed or repaired.logs)

            ms_updated, research, ms_reply, ms_fixed = await run_manuscript_wide_fixes(
                rfp_id=rfp_id,
                draft=updated,
                rfp=rfp,
                rfp_context=rfp_context,
                research=research,
                user_message=raw_user_message,
                conversation_history=conversation_history,
            )
            updated = ms_updated
            fixed = fixed or ms_fixed
            if ms_reply:
                fix_reply = (
                    f"{fix_reply}\n\n{ms_reply}".strip() if fix_reply else ms_reply
                )
            provider = _provider_name()
            if research is None:
                research = ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                )
            if fixed and persist:
                updated = await _persist_section_improve_draft(
                    updated,
                    research,
                    section_title="manuscript-wide fix",
                )
            focus = _find_draft_section(updated, section_id) or (
                updated.sections[0] if updated.sections else None
            )
            if focus is None:
                raise ProposalError("Draft has no sections.", status_code=400)
            # Always return here — never fall through to a single-tab rewrite.
            return (
                focus,
                updated,
                research,
                provider,
                fix_reply
                or (
                    "I treated that as a proposal-wide apply-fixes request.",
                None,
                ),
                fixed,
            )
        # single_edit / default continue on remapped section_id
    # Case-study replace/improve without a named Our Work tab: ask — never rewrite
    # whatever happens to be open (e.g. Who We Are). Skip for outline add/create/delete.
    # Also skip when the user is filling a VERIFY on the open tab (VERIFY text often
    # contains the words "case study" and used to falsely trigger this clarify).
    focus_for_clarify = _find_draft_section(draft, section_id)
    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

    if (
        not selection_mode
        and not improve_section_pinned
        and chat_intent not in {"multi_patch", "single_edit"}
        and chat_intent != "structure"
        and _message_needs_case_study_clarify(raw_user_message)
        and not _is_our_work_section(focus_for_clarify)
        and not _selection_asks_to_fill_verify(raw_user_message)
        and not user_asks_kb_fetch_or_fill(raw_user_message)
        and not _wants_section_edit(raw_user_message)
        and not _open_section_owns_case_study_ask(raw_user_message, focus_for_clarify)
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
                None,
            )

    # LLM advisory wins. For single_edit/multi_patch, do not short-circuit on
    # keyword advisory regex — the model already decided the user wants edits.
    if apply_fix:
        route = ChatRoute(advisory=False, reason="apply_fix")
    else:
        route = decide_chat_route(
            chat_intent=chat_intent,
            user_message=raw_user_message,
            selection_mode=bool(selection_mode),
            conversation_history=conversation_history,
            improve_pinned=bool(improve_section_pinned),
        )
    logger.info("chat route=%s reason=%s", "advisory" if route.advisory else "edit", route.reason)
    if route.advisory:
        section = _find_draft_section(draft, section_id) or (
            draft.sections[0] if draft.sections else None
        )
        if section is None:
            raise ProposalError("Draft has no sections.", status_code=400)
        requirements_block = _rfp_section_requirements_block(research, section.id)
        # Always build a fresh manuscript digest for advisory — do not bury it
        # inside a truncated RFP excerpt (that made the model "only see" Who We Are).
        # Numbered "section N about?" asks get a titles-only TOC inside the reply
        # helper so RFP clause numbers cannot steal the answer.
        manuscript_digest = _manuscript_digest(draft) if proposal_wide else ""
        reply, suggested_fix = await _section_chat_advisory_reply(
            section=section,
            rfp=rfp,
            rfp_context=rfp_context,
            user_message=raw_user_message,
            conversation_history=conversation_history,
            selection_text=selection_text,
            requirements_block=requirements_block,
            manuscript_digest=manuscript_digest,
            research=research,
            draft=draft,
        )
        if intent_degraded:
            # The classifier could not run, so this answer may be advisory only
            # because routing fell back to the keyword gate. Say so — otherwise a
            # provider outage is indistinguishable from a considered refusal.
            reply = (
                "_Note: the intent classifier is unavailable right now "
                "(model provider error), so I answered instead of editing. "
                "If you wanted a change made, resend the request in a moment._\n\n"
            ) + reply
        provider = _provider_name()
        if research is None:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
        return _improve_outcome(
            section, draft, research, provider, reply, False, suggested_fix
        )

    # When not pinned to a Revise-content excerpt, resolve structural asks
    # (add/delete sections) before rewriting the focused tab.
    # KB fetch/fill on the open tab is never a structure change — skip the planner
    # so "fetch San Francisco Travel case study here" cannot become Our Work clarify.
    if not selection_mode and not apply_fix:
        from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill
        from app.services.proposal_verify_optional_scrub import (
            user_asks_scrub_optional_verify,
            user_asks_strip_inline_evidence_tags,
        )

        focus_for_structure = _find_draft_section(draft, section_id)
        if focus_for_structure is not None:
            verify_resolve = await _try_open_section_verify_fill_or_remove(
                rfp_id=rfp_id,
                section=focus_for_structure,
                section_id=section_id,
                draft=draft,
                research=research,
                rfp_context=rfp_context,
                raw_user_message=raw_user_message,
                persist=persist,
            )
            if verify_resolve is not None:
                return verify_resolve

        skip_structure_plan = _should_skip_structure_planner(
            chat_intent,
            user_message=raw_user_message,
            selection_mode=selection_mode,
            apply_fix=apply_fix,
            improve_section_pinned=improve_section_pinned,
        )
        if not skip_structure_plan:
            from app.services.proposal_chat_structure import (
                is_add_section_intent,
                plan_chat_structure_action,
            )

            structure_plan = await plan_chat_structure_action(
                draft=draft,
                user_message=raw_user_message,
                focus_section_id=section_id,
                rfp_title=rfp.title,
                rfp_client=rfp.client,
                rfp_context=rfp_context,
                chat_intent=chat_intent,
            )
            allow_clarify = (
                chat_intent == "structure" or is_add_section_intent(raw_user_message)
            )
            structure_outcome = await _finish_chat_structure_plan(
                rfp_id=rfp_id,
                draft=draft,
                structure_plan=structure_plan,
                section_id=section_id,
                rfp=rfp,
                rfp_context=rfp_context,
                research=research,
                persist=persist,
                allow_clarify=allow_clarify,
            )
            if structure_outcome is not None:
                return structure_outcome

            if is_add_section_intent(raw_user_message) and structure_plan.action == "edit":
                logger.warning(
                    "Structure planner returned edit for add-section ask — forcing add retry"
                )
                forced = await _redirect_sidebar_add_to_structure(
                    rfp_id=rfp_id,
                    draft=draft,
                    section_id=section_id,
                    raw_user_message=raw_user_message,
                    rfp=rfp,
                    rfp_context=rfp_context,
                    research=research,
                    persist=persist,
                )
                if forced is not None:
                    return forced

            if structure_plan.edit_section_id:
                section_id = structure_plan.edit_section_id

            stay_on_open = user_points_at_open_section(raw_user_message)
            if not stay_on_open and not apply_fix:
                section_id = _remap_chat_section_if_explicit(
                    draft, raw_user_message, section_id
                )
                from_hist = _resolve_section_from_conversation_history(
                    draft,
                    conversation_history,
                    section_id,
                    latest_user_message=raw_user_message,
                )
                if from_hist is not None and from_hist.id != section_id:
                    hist_default = _find_draft_section(draft, section_id)
                    if _message_explicitly_targets_remote_section(
                        raw_user_message, from_hist, hist_default
                    ) or _history_may_override_open_tab(raw_user_message):
                        section_id = from_hist.id

    section = _find_draft_section(draft, section_id)
    if not section:
        raise ProposalError(f"Section {section_id} not found in draft.", status_code=404)
    before_section = section.model_copy()

    if should_apply_budget_playbook(section, raw_user_message):
        from app.services.proposal_budget_content import collapse_duplicate_cost_proposal_tabs
        from app.services.proposal_fulfill_rfp_budget_kpi import (
            restore_unresolved_budget_token_tabs,
        )

        collapsed, cost_logs = collapse_duplicate_cost_proposal_tabs(list(draft.sections))
        if cost_logs:
            draft = draft.model_copy(
                update={
                    "sections": collapsed,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        slot_logs: list[str] = []
        if research and research.budget:
            draft, slot_logs = restore_unresolved_budget_token_tabs(
                draft, research.budget, rfp_text=rfp_context
            )
        still = _find_draft_section(draft, section_id)
        merged_away = still is None
        if still is None:
            from app.services.proposal_budget_content import find_budget_section_index

            idx = find_budget_section_index(draft.sections)
            still = draft.sections[idx] if idx is not None else draft.sections[0]
        open_changed = merged_away or (
            (still.id == before_section.id)
            and (still.content or "") != (before_section.content or "")
        )
        if (cost_logs or slot_logs) and persist and research is not None:
            await _persist_section_improve_draft(
                draft, research, section_title=still.title
            )
        if open_changed:
            detail = "; ".join([*cost_logs, *slot_logs][:4]) or (
                "Synced Cost/Pricing from the fee ledger."
            )
            provider = _provider_name()
            return _improve_outcome(
                still,
                draft,
                research
                or ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                ),
                provider,
                f"Updated **{still.title}** from the existing budget "
                f"(no second Cost Proposal). {detail}",
                True,
            )
        section = still

    if apply_fix:
        working, updated_draft, research, provider, reply, changed = (
            await _apply_suggested_fix_to_section(
                rfp_id=rfp_id,
                section=section,
                draft=draft,
                research=research,
                rfp=rfp,
                instruction=raw_user_message,
                conversation_history=conversation_history,
                persist=persist,
            )
        )
        return _improve_outcome(
            working, updated_draft, research, provider, reply, changed, None
        )

    # Principle: the user's verbatim instruction reaches Claude. Do NOT short-circuit
    # with Designer-compact or deterministic compress — the User Revise agent decides
    # (tools + rewrite). Designer-compact only when the user explicitly asks for it.
    if not selection_mode and not apply_fix:
        from app.services.proposal_manuscript_compact import (
            should_run_designer_compact_for_chat,
            user_requests_designer_compact,
        )
        from app.services.proposal_self_edit_loop import designer_compact_single_section

        if should_run_designer_compact_for_chat(
            user_message=raw_user_message,
            improve_section_pinned=improve_section_pinned,
            section=section,
        ):
            changed, detail, compact_draft, compact_research = (
                await designer_compact_single_section(
                    rfp_id,
                    section_id,
                    rfp=rfp,
                    force=user_requests_designer_compact(raw_user_message),
                )
            )
            if changed and compact_draft:
                updated_section = next(
                    (s for s in compact_draft.sections if s.id == section_id),
                    section,
                )
                if compact_research is not None:
                    research = compact_research
                if persist:
                    await _persist_section_improve_draft(
                        compact_draft,
                        research,
                        section_title=section.title,
                    )
                bw = word_count(before_section.content or "")
                aw = word_count(updated_section.content or "")
                provider = _provider_name()
                assistant_message = (
                    f"Designer-compact rewrite for **{section.title}** "
                    f"({bw} → {aw} words). All RFP asks kept — dense tables/bullets "
                    f"for layout. ({detail})"
                )
                return _improve_outcome(
                    updated_section,
                    compact_draft,
                    research or ProposalResearchCache(
                        rfpId=rfp_id,
                        updatedAt=datetime.now(timezone.utc).isoformat(),
                        provider=provider,
                    ),
                    provider,
                    assistant_message,
                    True,
                    None,
                )

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

    if chat_intent not in {"single_edit", "multi_patch"} and not _wants_section_edit(
        raw_user_message, conversation_history=conversation_history
    ):
        reply, suggested_fix = await _section_chat_advisory_reply(
            section=section,
            rfp=rfp,
            rfp_context=rfp_context,
            user_message=raw_user_message,
            conversation_history=conversation_history,
            selection_text=selection_text,
            requirements_block=requirements_block,
            manuscript_digest=manuscript_digest,
            research=research,
            draft=draft,
        )
        provider = _provider_name()
        if research is None:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
        return _improve_outcome(
            section, draft, research, provider, reply, False, suggested_fix
        )

    # Downstream intent probes (percent-time column, add-section, budget playbook)
    # read this — it must stay the user's own words, not the evidence stanza.
    latest_user_ask = raw_user_message

    # "implement budget table here" → insert/replace table in THIS section only.
    if not selection_mode:
        table_insert = await _try_section_budget_table_insert(
            rfp_id=rfp_id,
            section=section,
            section_id=section_id,
            draft=draft,
            research=research,
            user_message=latest_user_ask,
            persist=persist,
        )
        if table_insert is not None:
            return (*table_insert, None)

    # "here fill budget part" on a non-Cost tab → fill THIS section's budget VERIFY
    # tags from the canonical budget. Do NOT run Stage 3.5 / rewrite Cost Proposal.
    if not selection_mode:
        budget_fill = await _try_section_budget_verify_fill(
            rfp_id=rfp_id,
            section=section,
            section_id=section_id,
            draft=draft,
            research=research,
            user_message=latest_user_ask,
            persist=persist,
        )
        if budget_fill is not None:
            return (*budget_fill, None)
        offer_form_fill = await _try_offer_form_of2_fill(
            rfp_id=rfp_id,
            section=section,
            section_id=section_id,
            draft=draft,
            research=research,
            user_message=latest_user_ask,
            persist=persist,
        )
        if offer_form_fill is not None:
            return (*offer_form_fill, None)

    if not selection_mode:
        forms_integrity = await _try_forms_attachments_integrity_repair(
            rfp_id=rfp_id,
            section=section,
            section_id=section_id,
            draft=draft,
            research=research,
            user_message=latest_user_ask,
            persist=persist,
        )
        if forms_integrity is not None:
            return (*forms_integrity, None)

    if not selection_mode:
        cert_scrub = await _try_section_cert_claim_scrub(
            rfp_id=rfp_id,
            section=section,
            section_id=section_id,
            draft=draft,
            research=research,
            user_message=latest_user_ask,
            persist=persist,
        )
        if cert_scrub is not None:
            return (*cert_scrub, None)

    if not selection_mode:
        from app.services.go_no_go_service import combine_rfp_text

        budget_handoff = await _try_budget_manual_fill_handoff(
            rfp_id=rfp_id,
            section=section,
            section_id=section_id,
            draft=draft,
            research=research,
            user_message=latest_user_ask,
            rfp_context=rfp_context or "",
            rfp_text=combine_rfp_text(
                "" if isinstance(_content, str) else getattr(_content, "description", "") or "",
                _content if isinstance(_content, str) else getattr(_content, "pdf_text", "") or "",
            ),
            persist=persist,
        )
        if budget_handoff is not None:
            return (*budget_handoff, None)

    if not selection_mode:
        canon_refresh = await _try_budget_section_canonical_refresh(
            rfp_id=rfp_id,
            section=section,
            section_id=section_id,
            draft=draft,
            research=research,
            user_message=latest_user_ask,
            chat_intent=chat_intent,
            conversation_history=conversation_history,
            rfp_text=rfp_context or "",
            persist=persist,
            selection_mode=selection_mode,
        )
        if canon_refresh is not None:
            return (*canon_refresh, None)

    # Budget/fee edits on the Cost Proposal tab: Stage 3.5 agent.
    # Never steal a case-study "fill budget part" / "implement table here" ask.
    # Never let a generic "single_edit" / Improve pin alone rebuild pricing.
    # Never steal a summary-prose reconcile into a fee-table rebuild.
    if (
        not selection_mode
        and not user_asked_reverse_engineered_total(latest_user_ask)
        and not user_asks_budget_summary_reconcile(latest_user_ask)
        and not user_asks_insert_budget_table(latest_user_ask)
        and (
            (
                section_is_budget_related(section)
                and user_asks_budget_rebuild(latest_user_ask)
            )
            or user_asks_global_cost_rebuild(latest_user_ask)
        )
        and not (
            user_asks_section_budget_fill(latest_user_ask)
            and not section_is_budget_related(section)
        )
    ):
        from app.services.proposal_generator import run_phase3_5_budget

        try:
            draft, research, budget = await run_phase3_5_budget(rfp_id)
            provider = _provider_name()
            focus = _find_draft_section(draft, section_id) or (
                draft.sections[0] if draft.sections else section
            )
            budget_focus = next(
                (s for s in draft.sections if section_is_budget_related(s)),
                None,
            )
            if budget_focus is not None and (
                section_is_budget_related(section)
                or user_asks_global_cost_rebuild(latest_user_ask)
            ):
                focus = budget_focus
            reply = (
                f"Rebuilt the budget with the Stage 3.5 pricing agent "
                f"(tier={budget.pricing_tier or '?'}, "
                f"{len(budget.line_items or [])} line items, "
                f"total={budget.agency_revenue_estimate}). "
                "Phase fees are regenerated from 00_Guide_Pricing against the "
                "approach narrative — review totals and any Sonja flags before submission."
            )
            if persist:
                draft = await _persist_section_improve_draft(
                    draft,
                    research,
                    section_title=focus.title,
                )
                focus = _find_draft_section(draft, focus.id) or focus
            return _improve_outcome(focus, draft, research, provider, reply, True)
        except Exception as exc:
            logger.warning(
                "Stage 3.5 budget agent failed during chat for %s — falling back: %s",
                rfp_id,
                exc,
            )

    # Explicit MANUAL FILL resolution — never invent; user text then KB only.
    # Prefer early path above; this is a safety net after section remapping.
    mfill_late = await _try_manual_fill_resolution(
        rfp_id=rfp_id,
        section=section,
        section_id=section_id,
        draft=draft,
        research=research,
        user_message=latest_user_ask,
        rfp_context=rfp_context,
        persist=persist,
        selection_mode=selection_mode,
        selection_start=selection_start,
        selection_end=selection_end,
    )
    if mfill_late is not None:
        return (*mfill_late, None)

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

    # Replace THIS tab with a DIFFERENT RFP need (title + content) — not a polish
    # of the same tourism/experience narrative.
    if (
        not selection_mode
        and _user_asks_replace_section_for_other_rfp_need(latest_user_ask)
    ):
        provider = _provider_name()
        if research is None:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
        try:
            new_title, new_content, rationale = await _pick_and_draft_replacement_rfp_section(
                section=section,
                draft=draft,
                rfp=rfp,
                rfp_context=rfp_context,
                research=research,
                user_message=latest_user_ask,
            )
        except ProposalError as exc:
            return (
                section,
                draft,
                research,
                provider,
                str(exc),
                False,
                None,
            )
        except LlmError as exc:
            logger.warning("Replace-section RFP-need LLM failed: %s", exc)
            return (
                section,
                draft,
                research,
                provider,
                "I could not re-scan a replacement RFP topic right now. Name the "
                "exact RFP requirement to put in this tab.",
                False,
                None,
            )

        working = section.model_copy(
            update={
                "title": new_title,
                "content": new_content,
                "status": "generated",
            }
        )
        merged = [working if s.id == section_id else s for s in draft.sections]
        now = datetime.now(timezone.utc).isoformat()
        updated_draft = draft.model_copy(
            update={"sections": merged, "updated_at": now, "provider": provider}
        )
        if persist:
            updated_draft = await _persist_section_improve_draft(
                updated_draft,
                research,
                section_title=new_title,
            )
        old_title = section.title
        return (
            working,
            updated_draft,
            research,
            provider,
            (
                f"Replaced **{old_title}** → **{new_title}** "
                f"({word_count(new_content)} words). {rationale} "
                "Same sidebar slot — different RFP need (not a polish of the old topic).",
            None,
            ),
            True,
        )

    # Strip [VERIFY]/[FLAG] evidence markers the user explicitly asked to delete.
    # Runs before the optional-VERIFY scrub: trust-audit tags ("gated evidence",
    # ClientList claim mismatches) are not "optional RFP" tags and used to stick.
    if not selection_mode and not apply_fix:
        from app.services.proposal_verify_optional_scrub import (
            count_inline_evidence_tags,
            strip_inline_evidence_tags,
            user_asks_strip_inline_evidence_tags,
        )

        if user_asks_strip_inline_evidence_tags(latest_user_ask):
            provider = _provider_name()
            if research is None:
                research = ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                )
            whole = bool(
                re.search(
                    r"(?is)\b(?:every|all)\s+sections?\b|"
                    r"\b(?:whole|entire)\s+(?:proposal|draft|manuscript)\b|"
                    r"\bacross\s+(?:the\s+)?(?:proposal|draft)\b",
                    latest_user_ask,
                )
            )
            targets = list(draft.sections) if whole else [section]
            changed_titles: list[str] = []
            new_sections = list(draft.sections)
            total_removed = 0
            focus = section
            for target in targets:
                before = count_inline_evidence_tags(target.content or "")
                if before <= 0:
                    continue
                cleaned, n = strip_inline_evidence_tags(target.content or "")
                total_removed += n
                working = target.model_copy(
                    update={"content": cleaned, "status": "generated"}
                )
                new_sections = [
                    working if s.id == target.id else s for s in new_sections
                ]
                changed_titles.append(target.title or target.id)
                if target.id == section.id:
                    focus = working
            if total_removed <= 0:
                scope = "the proposal" if whole else f"**{section.title}**"
                return (
                    section,
                    draft,
                    research,
                    provider,
                    f"{scope} has no `[VERIFY]` / `[FLAG]` tags to remove.",
                    False,
                    None,
                )
            now = datetime.now(timezone.utc).isoformat()
            updated_draft = draft.model_copy(
                update={
                    "sections": new_sections,
                    "updated_at": now,
                    "provider": provider,
                }
            )
            if persist:
                updated_draft = await _persist_section_improve_draft(
                    updated_draft,
                    research,
                    section_title=focus.title,
                )
            scope = (
                f"{len(changed_titles)} section(s)"
                if whole
                else f"**{focus.title}**"
            )
            return (
                focus,
                updated_draft,
                research,
                provider,
                (
                    f"Removed **{total_removed}** `[VERIFY]` / `[FLAG]` tag(s) from "
                    f"{scope}. Draft text otherwise unchanged.",
                None,
                ),
                True,
            )

    # Remove optional [VERIFY] tags (RFP-aware reframe) — before fill-VERIFY / percent-time.
    if not selection_mode:
        from app.services.proposal_verify_optional_scrub import (
            count_verify_tags,
            scrub_optional_verify_tags,
            user_asks_scrub_optional_verify,
        )

        if user_asks_scrub_optional_verify(latest_user_ask):
            provider = _provider_name()
            if research is None:
                research = ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                )
            if count_verify_tags(section.content or "") <= 0:
                return (
                    section,
                    draft,
                    research,
                    provider,
                    (
                        f"**{section.title}** has no `[VERIFY]` tags to remove. "
                        "Open the section with placeholders and ask again.",
                    None,
                    ),
                    False,
                )
            scrub = await scrub_optional_verify_tags(
                section.content or "",
                section_title=section.title,
                rfp_text=rfp_context or "",
            )
            if not scrub.changed:
                return (
                    section,
                    draft,
                    research,
                    provider,
                    (
                        f"I reviewed **{section.title}** against the RFP. "
                        f"{scrub.note or 'No optional [VERIFY] tags could be removed safely.'} "
                        f"Remaining required tags: {scrub.tags_after}.",
                    None,
                    ),
                    False,
                )
            working = section.model_copy(
                update={"content": scrub.content, "status": "generated"}
            )
            merged = [working if s.id == section.id else s for s in draft.sections]
            now = datetime.now(timezone.utc).isoformat()
            updated_draft = draft.model_copy(
                update={"sections": merged, "updated_at": now, "provider": provider}
            )
            if persist:
                updated_draft = await _persist_section_improve_draft(
                    updated_draft,
                    research,
                    section_title=section.title,
                )
            return (
                working,
                updated_draft,
                research,
                provider,
                (
                    f"Reframed **{section.title}** using the RFP: removed "
                    f"**{scrub.removed}** optional `[VERIFY]` tag(s); kept "
                    f"**{scrub.tags_after}** required. {scrub.note}",
                None,
                ),
                True,
            )

    # Percent-time column remove OR integrity scrub — before voice/KB gathers.
    # "remove Percent-Time column" must NOT be treated as another VERIFY scrub,
    # and must target the section that has the table (not whatever bio tab is open).
    if not selection_mode and _user_asks_remove_percent_time_column(latest_user_ask):
        target = _find_section_with_percent_time_table(draft) or section
        cleaned, n_rows = _remove_markdown_table_column(
            target.content or "",
            header_pat=r"percent[-\s]?time|%\s*time|allocation\s*%",
        )
        provider = _provider_name()
        if research is None:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
        if n_rows <= 0:
            return (
                target,
                draft,
                research,
                provider,
                (
                    f"I could not find a **Percent-Time** column to remove in "
                    f"**{target.title}**. Open the Agency team qualifications tab "
                    "and ask again.",
                None,
                ),
                False,
            )
        working = target.model_copy(
            update={"content": cleaned, "status": "generated"}
        )
        merged = [working if s.id == target.id else s for s in draft.sections]
        now = datetime.now(timezone.utc).isoformat()
        updated_draft = draft.model_copy(
            update={"sections": merged, "updated_at": now, "provider": provider}
        )
        if persist:
            updated_draft = await _persist_section_improve_draft(
                updated_draft,
                research,
                section_title=target.title,
            )
        return (
            working,
            updated_draft,
            research,
            provider,
            (
                f"Removed the **Percent-Time** column from **{target.title}** "
                f"({n_rows} table row(s)). Role / Name / experience kept.",
            None,
            ),
            True,
        )

    if not selection_mode and _user_asks_percent_time_integrity(latest_user_ask):
        from app.services.evidence_trust.legal_attestation_gate import (
            scrub_invented_percent_time,
        )

        target = _find_section_with_percent_time_table(draft) or section
        scrubbed, n_flags = scrub_invented_percent_time(target.content or "")
        provider = _provider_name()
        if research is None:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
        if n_flags <= 0:
            return (
                target,
                draft,
                research,
                provider,
                (
                    f"**{target.title}** has no invented percent-time / FTE % figures "
                    "left to flag (already VERIFY or no Percent-Time column). "
                    "Say **remove Percent-Time column only** if you want the column gone.",
                None,
                ),
                False,
            )
        working = target.model_copy(
            update={"content": scrubbed, "status": "generated"}
        )
        merged = [working if s.id == target.id else s for s in draft.sections]
        now = datetime.now(timezone.utc).isoformat()
        updated_draft = draft.model_copy(
            update={"sections": merged, "updated_at": now, "provider": provider}
        )
        if persist:
            updated_draft = await _persist_section_improve_draft(
                updated_draft,
                research,
                section_title=target.title,
            )
        return (
            working,
            updated_draft,
            research,
            provider,
            (
                f"Replaced **{n_flags}** unsourced percent-time figure(s) in "
                f"**{target.title}** with `[VERIFY: percent time]`. "
                "KB does not store pursuit-specific FTE % — confirm with Ella/Sonja "
                "or say **remove Percent-Time column only** if the RFP does not require it.",
            None,
            ),
            True,
        )

    # KB fetch/fill: skip 20k zo_voice gather — packed case-study retrieve is enough.
    if _open_tab_kb_fetch_ask(latest_user_ask):
        brand_voice_dict = (
            research.brand_voice.model_dump(by_alias=True)
            if research and research.brand_voice
            else {"tone": "professional", "formality": "semi-formal"}
        )
        kb_zo_voice = ""
        logger.info(
            "kb_fetch_fast_path: skipping zo_voice gather for %s / %s",
            rfp_id,
            section_id,
        )
    else:
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
    from app.services.proposal_chat_structure import is_add_section_intent

    local_edit = _understand_local_edit(latest_user_ask)
    section_body = section.content or ""
    if (
        local_edit
        and local_edit.kind == "remove_named"
        and local_edit.target
        and len(local_edit.target.strip()) >= 2
    ):
        restrict: tuple[int, int] | None = None
        stay_on_tab = user_points_at_open_section(latest_user_ask) or improve_section_pinned
        if (
            not stay_on_tab
            and selection_mode
            and selection_start is not None
            and selection_end is not None
            and selection_end > selection_start
            and not _selection_covers_most_of_section(
                section_body, selection_start, selection_end
            )
        ):
            restrict = (selection_start, selection_end)
        stripped, n_removed = _strip_named_mentions(
            section_body, local_edit.target, restrict=restrict
        )
        if n_removed > 0 and stripped != section_body:
            provider = _provider_name()
            if research is None:
                research = ProposalResearchCache(
                    rfpId=rfp_id,
                    updatedAt=datetime.now(timezone.utc).isoformat(),
                    provider=provider,
                )
            working = section.model_copy(
                update={"content": stripped, "status": "generated"}
            )
            merged = [working if s.id == section_id else s for s in draft.sections]
            now = datetime.now(timezone.utc).isoformat()
            updated_draft = draft.model_copy(
                update={"sections": merged, "updated_at": now, "provider": provider}
            )
            if persist:
                updated_draft = await _persist_section_improve_draft(
                    updated_draft,
                    research,
                    section_title=working.title,
                )
                working = next(
                    (s for s in updated_draft.sections if s.id == section_id),
                    working,
                )
            logger.info(
                "Mechanical named-remove %r → %d mention(s) in %s / %s",
                local_edit.target[:80],
                n_removed,
                rfp_id,
                section_id,
            )
            mention = "mention" if n_removed == 1 else "mentions"
            return _improve_outcome(
                working,
                updated_draft,
                research,
                provider,
                (
                    f"Removed **{local_edit.target}** from **{working.title}** "
                    f"({n_removed} {mention}; {word_count(working.content or '')} words). "
                    "Other sentences and tables in this tab are unchanged."
                ),
                True,
            )

    if local_edit and local_edit.kind in {"remove_named", "add_named"} and local_edit.target:
        search_body = section_body
        search_offset = 0
        if (
            selection_mode
            and selection_start is not None
            and selection_end is not None
            and selection_end > selection_start
        ):
            search_body = section_body[selection_start:selection_end]
            search_offset = selection_start
        span = _span_for_named_target(search_body, local_edit.target)
        if span is None and local_edit.kind == "add_named":
            span = _span_for_staff_block(search_body)
        if span is not None:
            selection_start = search_offset + span[0]
            selection_end = search_offset + span[1]
            selection_text = section_body[selection_start:selection_end]
            selection_mode = True
            scope_plan = EditScopePlan(
                understood_ask=latest_user_ask.strip(),
                mode="patch",
                patches=[
                    EditScopePatch(
                        anchor_excerpt=selection_text,
                        editor_instruction=latest_user_ask.strip(),
                    )
                ],
                kb_queries=[],
            )
            logger.info(
                "Localized %s %r → chars %d-%d in %s / %s",
                local_edit.kind,
                local_edit.target[:80],
                selection_start,
                selection_end,
                rfp_id,
                section_id,
            )

    # Required form slot: copy Active Client List from a sibling tab into missing I.2
    # before edit-scope planning (selection patches cannot insert a missing heading).
    # Draft-wide — same helper Complete Scan / Generate use — so every incomplete
    # eval form gets the slot, not only the open tab.
    if not selection_mode and (
        improve_section_pinned
        or re.search(r"(?i)active\s+client\s+list|jumps?\s+i\.?\s*1", latest_user_ask)
    ):
        from app.services.proposal_chat_improve_pin import (
            fill_all_active_client_lists_from_siblings,
            improve_pin_needs_full_rewrite,
        )

        draft, form_logs = fill_all_active_client_lists_from_siblings(draft)
        if form_logs:
            section = _find_draft_section(draft, section_id) or section
            logger.info(
                "Form-slot Active Client List fill for %s: %s",
                rfp_id,
                "; ".join(form_logs[:4]),
            )
            # Keep going through full rewrite so the rest of the issue list is fixed.
            if improve_section_pinned and improve_pin_needs_full_rewrite(
                latest_user_ask, section.content or ""
            ):
                pass  # fall through to full rewrite with I.2 already present

    if (
        not selection_mode
        and not is_add_section_intent(latest_user_ask)
        and not _open_tab_kb_fetch_ask(latest_user_ask)
    ):
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
                    from app.services.proposal_chat_improve_pin import (
                        improve_pin_needs_full_rewrite,
                        should_collapse_edit_scope_to_selection,
                    )

                    if improve_section_pinned and improve_pin_needs_full_rewrite(
                        latest_user_ask, section.content or ""
                    ):
                        # Missing I.2 / multi-issue lists cannot be selection-spliced.
                        planned_spans = None
                        scope_plan = EditScopePlan(
                            understood_ask=scope_plan.understood_ask
                            or latest_user_ask.strip(),
                            mode="full_rewrite",
                            patches=[],
                            kb_queries=list(scope_plan.kb_queries or []),
                        )
                        logger.info(
                            "Improve pin forced full_rewrite (structural/multi-issue) "
                            "for %s / %s",
                            rfp_id,
                            section_id,
                        )
                    elif (
                        len(planned_spans) == 1
                        and should_collapse_edit_scope_to_selection(
                            improve_section_pinned=improve_section_pinned,
                            user_message=latest_user_ask,
                            section_content=section.content or "",
                            planned_span_count=1,
                        )
                    ):
                        selection_start, selection_end, only = planned_spans[0]
                        selection_text = (section.content or "")[
                            selection_start:selection_end
                        ]
                        selection_mode = True
                        user_message = only.editor_instruction
                    elif len(planned_spans) == 1:
                        # Improve pin + non-collapsible single patch → full rewrite.
                        planned_spans = None
                        scope_plan = EditScopePlan(
                            understood_ask=scope_plan.understood_ask
                            or latest_user_ask.strip(),
                            mode="full_rewrite",
                            patches=[],
                            kb_queries=list(scope_plan.kb_queries or []),
                        )
                else:
                    logger.info(
                        "Edit-scope plan asked patch but no anchors found in %s — "
                        "falling back to named-target locate or full rewrite",
                        section_id,
                    )
                    if local_edit and local_edit.target:
                        fallback = _span_for_named_target(
                            section.content or "", local_edit.target
                        )
                        if fallback is None and local_edit.kind == "add_named":
                            fallback = _span_for_staff_block(section.content or "")
                        if fallback is not None:
                            selection_start, selection_end = fallback
                            selection_text = (section.content or "")[
                                selection_start:selection_end
                            ]
                            selection_mode = True
                            scope_plan = EditScopePlan(
                                understood_ask=scope_plan.understood_ask
                                or latest_user_ask.strip(),
                                mode="patch",
                                patches=[
                                    EditScopePatch(
                                        anchor_excerpt=selection_text,
                                        editor_instruction=latest_user_ask.strip(),
                                    )
                                ],
                                kb_queries=list(scope_plan.kb_queries or []),
                            )
                            logger.info(
                                "Recovered localized patch for %s chars %d-%d",
                                local_edit.target[:60],
                                selection_start,
                                selection_end,
                            )
            elif scope_plan.mode == "full_rewrite":
                coerced = False
                if local_edit and local_edit.kind in {"remove_named", "add_named"}:
                    fallback = _span_for_named_target(
                        section.content or "", local_edit.target
                    )
                    if fallback is None and local_edit.kind == "add_named":
                        fallback = _span_for_staff_block(section.content or "")
                    if fallback is not None:
                        selection_start, selection_end = fallback
                        selection_text = (section.content or "")[
                            selection_start:selection_end
                        ]
                        selection_mode = True
                        scope_plan = EditScopePlan(
                            understood_ask=scope_plan.understood_ask
                            or latest_user_ask.strip(),
                            mode="patch",
                            patches=[
                                EditScopePatch(
                                    anchor_excerpt=selection_text,
                                    editor_instruction=latest_user_ask.strip(),
                                )
                            ],
                            kb_queries=list(scope_plan.kb_queries or []),
                        )
                        coerced = True
                        logger.info(
                            "Coerced full_rewrite → patch for %s %r in %s",
                            local_edit.kind,
                            local_edit.target[:60],
                            section_id,
                        )
                if not coerced:
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
        # VERIFY-fill asks: prefer whole-section deterministic fill first. Lean
        # multi-patch LLM edits often return only the filled value and get 422'd.
        # KB fetch asks must fall through to packed section rewrite when tags stay open.
        from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

        if (
            _selection_asks_to_fill_verify(latest_user_ask)
            and not user_asks_kb_fetch_or_fill(latest_user_ask)
            and VERIFY_TAG_RE.search(section.content or "")
        ):
            supplemental = _draft_supplemental_blob(draft)
            blob = "\n\n".join(
                p for p in (fact_blob, kb_block, supplemental) if p and p.strip()
            )
            filled_content, total_kb_fills = _replace_verify_tags_from_blob(
                section.content or "", blob
            )
            remaining = _gap_fields_from_text(filled_content)
            if total_kb_fills > 0 or remaining:
                working_section = (
                    section.model_copy(
                        update={"content": filled_content, "status": "generated"}
                    )
                    if total_kb_fills > 0
                    else section
                )
                provider = _provider_name()
                if research is None:
                    research = ProposalResearchCache(
                        rfpId=rfp_id,
                        updatedAt=datetime.now(timezone.utc).isoformat(),
                        provider=provider,
                    )
                else:
                    research = research.model_copy(update={"provider": provider})
                changed = total_kb_fills > 0
                if changed:
                    merged_sections = [
                        working_section if s.id == section_id else s
                        for s in draft.sections
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
                else:
                    updated_draft = draft
                if total_kb_fills > 0:
                    assistant_message = (
                        f"Filled **{total_kb_fills}** [VERIFY] tag(s) in **{section.title}** "
                        f"from the knowledge base."
                    )
                else:
                    assistant_message = (
                        f"I looked up KB facts for **{section.title}** but could not safely "
                        "fill the remaining [VERIFY] tags (locked legal/attestation items or "
                        "missing facts)."
                    )
                if remaining:
                    assistant_message += (
                        " Still open: "
                        + ", ".join(f"`{g}`" for g in remaining[:8])
                        + "."
                    )
                return (
                    working_section,
                    updated_draft,
                    research,
                    provider,
                    assistant_message,
                    changed,
                    None,
                )

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
                None,
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
                rfp_context=rfp_context,
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
                    user_message=user_message,
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
            return _improve_outcome(updated_section, updated_draft, research, provider, assistant_message, True)

        from app.services.proposal_manual_flags import (
            extract_manual_fill_tags,
            fill_manual_fill_tags,
        )
        from app.services.proposal_manuscript import (
            convert_bare_confirmation_lines,
            convert_inline_confirmation_phrases,
        )
        from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

        if user_asks_kb_fetch_or_fill(latest_user_ask) or _selection_asks_to_fill_verify(
            latest_user_ask
        ):
            mfill_excerpt = convert_inline_confirmation_phrases(
                convert_bare_confirmation_lines(working_excerpt)
            )
            if extract_manual_fill_tags(mfill_excerpt):
                filled_mf, mfill_log, mfill_remaining = fill_manual_fill_tags(
                    mfill_excerpt,
                    user_message=latest_user_ask,
                    kb_blob=fact_blob,
                )
                if filled_mf != working_excerpt or mfill_log:
                    new_content = _splice_selection(
                        full_content,
                        start=selection_start,
                        end=selection_end,
                        replacement=filled_mf,
                    )
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
                    if mfill_log:
                        assistant_message = (
                            f"Resolved **{len(mfill_log)}** MANUAL FILL tag(s) in the "
                            f"selected excerpt of **{section.title}** from KB / your message."
                        )
                    else:
                        assistant_message = (
                            f"I checked KB facts for the selected table in **{section.title}**. "
                            "Addendum numbers/dates are not in the knowledge base — "
                            "confirm them from the Bonfire portal before submit."
                        )
                    if mfill_remaining:
                        assistant_message += (
                            " Still open: "
                            + ", ".join(f"`{t}`" for t in mfill_remaining[:6])
                            + "."
                        )
                    return _improve_outcome(
                        updated_section,
                        updated_draft,
                        research,
                        provider,
                        assistant_message,
                        True,
                    )

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
            compliance_user_message=latest_user_ask,
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
        # Report the change at the SELECTION level, not the whole section — a
        # section-level "692 → 725 words" reads as if the whole tab was rewritten
        # when only the highlighted span changed. Compute the excerpt delta from
        # the section length change (the splice preserves everything else).
        before_excerpt = (before_section.content or "")[selection_start:selection_end]
        len_delta = len(updated_section.content or "") - len(before_section.content or "")
        after_excerpt = (updated_section.content or "")[
            selection_start : selection_end + len_delta
        ]
        excerpt_before_words = word_count(before_excerpt)
        excerpt_after_words = word_count(after_excerpt)
        assistant_message = (
            f"Revised only your selected excerpt in **{section.title}** "
            f"({excerpt_before_words} → {excerpt_after_words} words in that span). "
            f"The rest of the section is unchanged."
        )
        if kb_fills > 0:
            assistant_message = (
                f"Filled **{kb_fills}** verified fact(s) in your selected excerpt in "
                f"**{section.title}** ({excerpt_before_words} → {excerpt_after_words} "
                f"words in that span). The rest of the section is unchanged."
            )
        logger.info(
            "Section selection edit complete for %s / %s (%d → %d words)",
            rfp_id,
            section_id,
            before_words,
            after_words,
        )
        return _improve_outcome(updated_section, updated_draft, research, provider, assistant_message, True)

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
        skip_kb = _chat_improve_skip_kb(gate, raw_user_message)
        if skip_kb:
            understood_ask = raw_user_message.strip()
            editor_instruction = raw_user_message.strip()
            planned = []
            outline_action = "edit_open_section"
            logger.info(
                "static section_improve planner skipped (no retrieval) %s / %s",
                rfp_id,
                section_id,
            )
        else:
            understood_ask, editor_instruction, planned, outline_action = await _plan_section_improve(
                section=section,
                rfp=rfp,
                rfp_section=rfp_section,
                user_message=query_focus,
                prior_queries=[*prior_queries, *seeded],
            )
        if outline_action in {"add_sidebar_section", "delete_sidebar_section"}:
            logger.info(
                "outlineAction=%s — redirecting to structure planner for %s",
                outline_action,
                section_id,
            )
            forced = await _redirect_sidebar_add_to_structure(
                rfp_id=rfp_id,
                draft=draft,
                section_id=section_id,
                raw_user_message=raw_user_message,
                rfp=rfp,
                rfp_context=rfp_context,
                research=research,
                persist=persist,
                outline_hint=outline_action,
            )
            if forced is not None:
                return forced
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
        if skip_kb:
            queries = []
        elif not queries:
            queries = [
                f"zö agency 01 companyfacts firm legal name address Bend Oregon {rfp.sector}"[:220],
                f"zö agency contact phone email Sonja 02 master template {section.title}"[:220],
                f"zö agency tourism DMO destination marketing experience {rfp.sector}"[:220],
                f"zö agency company philosophy employees organizational structure {section.title}"[:220],
            ]
        if not skip_kb:
            from app.services.proposal_section_kb_evidence import (
                fetch_packed_section_kb_evidence,
                inject_packed_evidence_into_instruction,
            )

            packed_block, _packed_sources = await fetch_packed_section_kb_evidence(
                section_title=section.title or "",
                user_message=raw_user_message,
                requirements=list(rfp_section.requirements or []) if rfp_section else [],
                section_content=section.content or "",
            )
            if packed_block:
                user_message = inject_packed_evidence_into_instruction(
                    user_message, packed_block
                )
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

        if _open_tab_kb_fetch_ask(raw_user_message):
            from app.services.proposal_capability_bio_grounding import (
                is_personnel_bio_section,
                pack_04_bio_kb_for_section,
            )

            understood_ask = raw_user_message.strip()
            if is_personnel_bio_section(section):
                user_message = (
                    f"{understood_ask}\n\n"
                    "Claude: YOU decide queries. Ground named team members to 04_Bio only. "
                    "REPLACE invented specialization / year claims with 2–4 sentences "
                    "from that person's packed 04_Bio. Keep Role lines. "
                    "Never leave only a Role line when 04_Bio has facts. "
                    "Never invent government/municipal/enterprise specialization. "
                    "Strip [E#] markers. Drop empty headers with no body."
                )
            else:
                user_message = (
                    f"{understood_ask}\n\n"
                    "Claude: YOU decide tools and queries. Obey the user instruction above. "
                    "Prefer the smallest edit that fully satisfies it. "
                    "Use PACKED KB / tools only for missing zö facts — do not invent."
                )
            queries = []
            logger.info(
                "kb_fetch_fast_path: skipping section_improve planner for %s / %s",
                rfp_id,
                section_id,
            )
            if is_personnel_bio_section(section):
                try:
                    bio_pack = await pack_04_bio_kb_for_section(
                        section, user_message=raw_user_message
                    )
                except Exception:
                    logger.exception("04_Bio pack for kb_fetch personnel failed")
                    bio_pack = ""
                if bio_pack.strip():
                    user_message = (
                        f"{user_message}\n\n"
                        "=== 04_Bio approved files (authoritative — write ONLY from this) ===\n"
                        f"{bio_pack[:20_000]}\n"
                    )
                    logger.info(
                        "kb_fetch_fast_path: injected 04_Bio pack (%d chars) for %s",
                        len(bio_pack),
                        section_id,
                    )
        elif _chat_improve_skip_kb(gate, raw_user_message):
            understood_ask = raw_user_message.strip()
            queries = []
            user_message = raw_user_message.strip()
            logger.info(
                "section_improve_planner_skipped (no retrieval) rfp_id=%s section_id=%s",
                rfp_id,
                section_id,
            )
        elif scope_plan is not None:
            understood_ask = (
                scope_plan.understood_ask or raw_user_message
            ).strip()
            queries = list(scope_plan.kb_queries or [])
            user_message = raw_user_message.strip()
            logger.info(
                "section_improve_planner_skipped (reuse edit-scope) rfp_id=%s "
                "section_id=%s queries=%d",
                rfp_id,
                section_id,
                len(queries),
            )
        else:
            understood_ask, editor_instruction, queries, outline_action = await _plan_section_improve(
                section=section,
                rfp=rfp,
                rfp_section=rfp_section,
                user_message=query_focus,
                prior_queries=prior_queries,
            )
            if outline_action in {"add_sidebar_section", "delete_sidebar_section"}:
                logger.info(
                    "outlineAction=%s — redirecting to structure planner for %s",
                    outline_action,
                    section_id,
                )
                forced = await _redirect_sidebar_add_to_structure(
                    rfp_id=rfp_id,
                    draft=draft,
                    section_id=section_id,
                    raw_user_message=raw_user_message,
                    rfp=rfp,
                    rfp_context=rfp_context,
                    research=research,
                    persist=persist,
                    outline_hint=outline_action,
                )
                if forced is not None:
                    return forced
            # Verbatim user ask reaches Claude — planner must not replace it.
            if (
                editor_instruction.strip()
                and editor_instruction.strip() != raw_user_message.strip()
            ):
                user_message = (
                    f"{raw_user_message.strip()}\n\n"
                    f"Planner notes (secondary — do not override the user instruction):\n"
                    f"{editor_instruction.strip()}"
                )
            else:
                user_message = raw_user_message.strip()
        skip_kb = _chat_improve_skip_kb(gate, raw_user_message)
        if skip_kb:
            queries = []
            logger.info(
                "section_improve_kb_skipped_by_gate rfp_id=%s section_id=%s decision=%s",
                rfp_id,
                section_id,
                gate.action.value if gate else "",
            )
        elif not queries:
            title = section.title
            queries = [
                f"zö agency firm history organizational chart employee count {rfp.sector} {title}"[:240],
                f"zö agency company philosophy capabilities statement {rfp.sector} {title}"[:240],
                f"zö agency 02 master template certifications WBENC WOSB {title}"[:240],
            ]

        # kb_qa_loop-quality pack for evidence-heavy tabs (case studies, examples,
        # references, campaigns…). Uses section title + RFP needs + names already
        # in the draft — no vertical hardcodes.
        if not skip_kb:
            from app.services.proposal_capability_bio_grounding import (
                is_personnel_bio_section,
            )
            from app.services.proposal_section_kb_evidence import (
                fetch_packed_section_kb_evidence,
                inject_packed_evidence_into_instruction,
            )

            # Personnel resume/bio fetch already has authoritative 04_Bio in
            # user_message — do not bury it under pricing-guide packed RAG.
            if not (
                _open_tab_kb_fetch_ask(raw_user_message)
                and is_personnel_bio_section(section)
                and "04_Bio approved files" in (user_message or "")
            ):
                packed_block, _packed_sources = await fetch_packed_section_kb_evidence(
                    section_title=section.title or "",
                    user_message=raw_user_message,
                    requirements=list(rfp_section.requirements or [])
                    if rfp_section
                    else [],
                    section_content=section.content or "",
                )
                if packed_block:
                    user_message = inject_packed_evidence_into_instruction(
                        user_message, packed_block
                    )

        query_count = len(queries)

        if _open_tab_kb_fetch_ask(raw_user_message):
            corpus = prior_corpus
            evidence_added = 0
            section_evidence = _evidence_for_section(section_id, corpus)
            logger.info(
                "kb_fetch_fast_path: skipping extra section queries for %s / %s",
                rfp_id,
                section_id,
            )
        else:
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
            draft=draft,
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
            compliance_user_message=raw_user_message,
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
    # Also strip evidence markers ([E12, E13, …]) and [PRICING FLAG] — never client-facing.
    from app.services.proposal_manuscript import scrub_client_facing_section_artifacts
    from app.services.proposal_chat_structure import (
        renumber_dynamic_group_titles,
        sync_case_study_cross_references,
        sync_case_study_title_from_content,
    )

    title_before_sync = updated_section.title or section.title or ""
    updated_section = updated_section.model_copy(
        update={
            "content": scrub_client_facing_section_artifacts(
                updated_section.content or ""
            ),
            "kb_refs": [],
        }
    )
    if re.search(
        r"certif|forms.*attach|supplier\s+diversity",
        f"{updated_section.title or ''}\n{updated_section.content or ''}",
        re.I,
    ):
        from app.services.proposal_cert_claim_scrub import scrub_section_cert_claims

        cert_scrubbed, cert_logs = scrub_section_cert_claims(updated_section)
        if cert_logs:
            updated_section = cert_scrubbed.model_copy(
                update={
                    "content": scrub_client_facing_section_artifacts(
                        cert_scrubbed.content or ""
                    )
                }
            )
            logger.info(
                "post_llm_cert_scrub section_id=%s logs=%s",
                section_id,
                cert_logs,
            )
    from app.services.proposal_forms_attachments_integrity import (
        audit_and_repair_forms_attachments,
        section_is_forms_attachments,
    )

    if section_is_forms_attachments(updated_section):
        forms_result = await audit_and_repair_forms_attachments(
            updated_section.content or "",
            draft=draft,
            research=research,
        )
        if forms_result.changed:
            updated_section = updated_section.model_copy(
                update={
                    "content": scrub_client_facing_section_artifacts(
                        forms_result.content
                    )
                }
            )
            logger.info(
                "post_llm_forms_integrity section_id=%s fixes=%s",
                section_id,
                forms_result.fix_logs,
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
                    "content": scrub_client_facing_section_artifacts(
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
    merged_sections, _xref_n = sync_case_study_cross_references(
        merged_sections,
        changed=updated_section,
        old_title=title_before_sync,
    )
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
    return _improve_outcome(updated_section, updated_draft, research, provider, assistant_message, True)
