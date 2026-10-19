"""Shared zö brand voice formatting for all proposal LLM passes.

The governing text is the revision pinned to the proposal, or the Brand Voice
default when the proposal has no pin. Nothing in this module substitutes an
older revision.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Literal

from app.models.rfp import RfpRecord
from app.services import brand_voice_revisions as bvr
from app.services import proposal_knowledge_base_tools

logger = logging.getLogger(__name__)

Register = Literal["narrative", "procurement", "cover_letter"]


INTERESTING_PROPOSAL_ANSWER_BLOCK = """## INTERESTING PROPOSAL ANSWER (mandatory)

Interesting is NOT hype. Empty words stay banned (exciting, amazing, passionate, robust, seamless, leverage…).
Interesting means the evaluator learns something specific they did not already write in the RFP.

REQUIRED for scored narrative tabs (Executive Summary, Understanding, Approach, Methodology, Past Performance):
1. Open with a concrete moment, decision, deliverable, or named tactic — never by restating what the client asked for or already built.
2. Teach by showing: put the idea inside one verified case study, named method, or festival-ready example from KB.
3. Prefer a fresh, specific idea over a generic capability list ("we do websites / social / content").
4. Admit a true cost or tradeoff when it matters (scope edge, timeline pressure, handoff risk).
5. First sentence: one promise, in contractions and future tense ("we'll"). Say it the way you would across a desk. If the work has several outcomes, the first sentence carries one of them. Each other outcome gets its own next sentence.
6. Never pack three outcomes into one "so" clause. Never open in third person about the agency. The first sentence is first person and carries one promise.
7. ANTI-RFP-ECHO still holds: requirements are a private checklist; the body is only our answer.
"""

def _render_standards(rev: bvr.Revision) -> str:
    return (
        f"## zö Brand & Writing Standards ({rev.label}) · COMPULSORY for proposals\n"
        "Highest rev governs. Older files are dead on arrival. "
        "Scope: proposal writing only. Do not change UI fonts or layout.\n"
        "Follow company name, writing rules, voice, money, and checklist below.\n\n"
        f"{bvr.words_only(rev.body)}"
    )


@lru_cache(maxsize=16)
def _standards_for_revision(rev_id: str) -> str:
    """Revisions never change, so the rendered text is cached by id."""
    return _render_standards(bvr.get_revision(rev_id) or bvr.builtin())


def load_writing_standards(rev_id: str | None = None) -> str:
    """Writing-rules text for a revision. Default: the active one."""
    rev = bvr.get_revision(rev_id) or bvr.active_revision()
    return _standards_for_revision(rev.id)


def load_writing_standards_rev6() -> str:
    return load_writing_standards()


def voice_standards_for(rfp_id: str | None = None) -> tuple[str, str]:
    """(standards text, revision id) for a proposal: its pin, else the active default."""
    if not bvr.enabled():  # no Supabase: the repo file is the only revision, so there is no pin to look up
        return _standards_for_revision(bvr.BUILTIN_ID), bvr.BUILTIN_ID
    pinned = None
    if rfp_id:
        try:
            from app.services.proposal_repository import get_proposal_draft

            draft = get_proposal_draft(rfp_id)
            pinned = getattr(draft, "voice_rev_id", None) if draft else None
        except Exception as exc:  # noqa: BLE001 - a missing draft must not break prompt building
            logger.warning("voice pin lookup failed for %s: %s", rfp_id, str(exc)[:200])
            pinned = None
    try:
        rev = bvr.get_revision(pinned)
    except Exception as exc:  # noqa: BLE001 - a failed pin lookup falls back to the default revision
        logger.warning("voice revision %s lookup failed for %s: %s", pinned, rfp_id, str(exc)[:200])
        rev = None
    rev = rev or bvr.active_revision()
    return _standards_for_revision(rev.id), rev.id


def load_writing_standards_rev3() -> str:
    """Backward-compatible alias. Returns the active Brand Voice revision."""
    return load_writing_standards()


def get_writing_standards_rev3() -> str:
    return load_writing_standards()


def get_writing_standards_rev6() -> str:
    return load_writing_standards()


def __getattr__(name: str) -> Any:
    if name in ("ZO_WRITING_STANDARDS_REV3", "ZO_WRITING_STANDARDS_REV6", "ZO_WRITING_STANDARDS"):
        return load_writing_standards()
    raise AttributeError(f"module {__name__!r} has no attribute {name}")


NARRATIVE_REGISTER_BLOCK = """## NARRATIVE REGISTER (MANDATORY for this section)

Write as zö agency speaking directly to the client. Not as a legal brief describing a vendor.

REQUIRED:
- Follow the writing standards above. That text is the revision pinned to this proposal, or the Brand Voice default. Proposal copy only.
- First person: "we", "our", "us". zö is the author who did/will do the work.
- Contractions + future tense: "we'll build", "we'll ship" — not bare present process tone.
- Active voice and concrete verbs: "We built...", "We led...", "Our team designed..."
- Outcome-focused: what changed for the client, not abstract capability claims.
- Specific to THIS client's challenge. Tie proof points directly to their goals.
- Plain, direct sentences. Lead with the point.
- Deliverable register: no exclamation points, no emoji, no filler willingness.
- Interesting: open with something real, teach by showing, admit a true cost, stop flat.

FORBIDDEN in narrative sections (never use):
- Em dashes; negation-contrast ("not X, it's Y", not just, more than just); empty hype words (amazing, seamless, leverage, unlock, journey, solution, impactful, exciting, passionate, robust).
- Process verbs and abstract verb+noun pairs (drive alignment, unlock value, socialize the plan, craft the scope).
- "The Vendor", "The Offeror", "The Proposer", "The Respondent", "The Contractor".
- Third-person agency distance: "The agency delivers...", "zö agency brings...", "The firm has experience...".
- Passive procurement boilerplate: "services will be provided", "experience includes...".
- Compliance-form tone in scored narrative sections.
- Writing for effect (hooks, punchlines, rhetorical questions, crafted repetition).
- Opening by paraphrasing the RFP / what the client already asked for.

EXAMPLE · same fact, wrong vs right:
- WRONG: "Gilroy is not asking us to build something new. You built a website and social channels."
- RIGHT: "We'll run festival-week conversion checks on ticket and vendor flows, then keep Instagram and Facebook fed year-round with volunteer stories. That is the same handoff pattern we used at Rock the Locks."

Note: RFP instructions may say "Vendor". That is form language. Narrative sections must still use first-person zö voice."""

COVER_LETTER_REGISTER_BLOCK = """## COVER LETTER / TRANSMITTAL REGISTER (signed passage)

Signed passages follow the correspondence rules in the writing standards above
(the revision pinned to this proposal, or the Brand Voice default). The rest of
the proposal stays deliverable register.

CONTENT / STRUCTURE (highest priority after those writing standards):
- Follow THIS RFP's cover-letter / letter-of-transmittal format and required statements
  (addressee, required attestations, contact/signature block, page limit).
- Do not invent a Zo template letter that ignores the RFP's own package instructions.
- When 06_WON / past won-proposal cover letters or letters of transmittal appear in
  evidence: use them as FORM / STRUCTURE / VOICE models only (salutation → intent →
  short offer summary → close / signature shape). NEVER copy their client names,
  project claims, dates, dollar figures, or prior RFP facts — rewrite for THIS buyer.

VOICE (correspondence for signed passages):
- First person from the authorized signer (I / we). Address the recipient by name when known.
- Warm, contracted, human — still no em dashes, no negation-contrast, no empty hype.
- NEVER open with "On behalf of zö agency" or other third-person agency boilerplate.
- Name "zö agency" when identifying the offeror, certifications, or contact block — as the
  firm, not as a corporate narrator speaking "on behalf of" itself.

If a wet-ink / signed PDF is required: write the letter body + [DESIGNER NOTE] to attach
the signed file. Never invent signature dates or claim the PDF is already attached."""

PROCUREMENT_REGISTER_BLOCK = """## PROCUREMENT REGISTER (this section)

This is a form, price sheet, certification, or compliance schedule. The prose around it is still zö speaking: first person, contractions, future tense (we'll).

Third person stays only where the buyer's form requires the legal name:
- a form label or signature line ("Submitted by zö agency")
- a certification the firm signs ("zö agency certifies", "acknowledges", "represents", "agrees")

Price and offer sentences name us as we/we'll. "zö agency proposes", "zö agency bills", "zö agency gives", and "zö agency carries" are third person and get rewritten.
Still follow the writing standards: company name zö agency, no em dashes, no empty hype words, no negation-contrast, no process-verb jargon. American English."""


def classify_section_register(
    *,
    section_id: str = "",
    title: str = "",
    zo_mode: str = "write",
    submission_instrument: str | None = None,
    plan_register: str | None = None,
) -> Register:
    """Narrative / cover-letter correspondence / procurement register for a section.

    Prefer Phase 2 meaning stamps (submissionInstrument=letter, plan register=
    cover_letter). Title tokens are fallback only.
    """
    from app.services.proposal_draft_structure_stubs import is_cover_letter_section

    sid = section_id.lower()
    t = title.lower()

    # Signed passages (Rev 6) — instrument/register beat title synonyms.
    if is_cover_letter_section(
        title=title,
        section_id=section_id,
        submission_instrument=submission_instrument,
        voice_register=plan_register,
    ):
        return "cover_letter"

    if sid.startswith(
        ("section-1", "section-2", "section-3", "section-4", "section-5")
    ):
        return "narrative"

    procurement_signals = (
        "attachment d",
        "attachment f",
        "attachment g",
        "attachment ",
        "exhibit ",
        "schedule ",
        "form ",
        "certification",
        "certify",
        "affidavit",
        "notary",
        "w-9",
        "w9",
        "insurance certificate",
        "pricing table",
        "cost proposal",
        "price proposal",
        "signed statement",
        "compliance checklist",
        "vendor registration",
        "tax registration",
        "dbe",
        "mbe",
        "wbe",
        "lobbying",
        "debarment",
    )
    if any(sig in t for sig in procurement_signals):
        return "procurement"

    narrative_signals = (
        "executive summary",
        "company overview",
        "team overview",
        "case stud",
        "our work",
        "project approach",
        "scope of work",
        "methodology",
        "understanding of",
        "understanding the",
        "qualifications",
        "technical approach",
        "work plan",
        "narrative",
        "introductory",
    )
    if any(sig in t for sig in narrative_signals):
        return "narrative"

    if zo_mode in ("pull", "select"):
        return "narrative"

    return "narrative"


def format_register_block(register: Register) -> str:
    if register == "cover_letter":
        return COVER_LETTER_REGISTER_BLOCK
    if register == "procurement":
        return PROCUREMENT_REGISTER_BLOCK
    return NARRATIVE_REGISTER_BLOCK


def format_brand_voice_block(
    brand_voice: dict[str, Any] | None,
    *,
    kb_zo_voice: str = "",
    rfp_client: str = "",
    register: Register = "narrative",
    compact: bool = False,
    rfp_id: str | None = None,
) -> str:
    """Voice block for one section.

    The standards text is the proposal pin, or the Brand Voice default. Compact
    keeps that same text and drops the interesting-answer addendum.
    """
    bv = brand_voice or {}
    zo_core = (
        bv.get("zoCoreVoice")
        or bv.get("zo_core_voice")
        or (
            "zö agency writes as a confident, human-centered marketing partner: "
            "direct, warm, first-person (we/our), and grounded in verified facts."
        )
    )
    zo_core = zo_core.replace("—", ",").replace("–", "-")

    standards = voice_standards_for(rfp_id)[0]
    if compact:
        lines = [
            standards,
            "",
            format_register_block(register),
            "",
            "## zö core brand voice (MANDATORY)",
            zo_core[:1200],
        ]
        if rfp_client:
            lines.append(f"Write for evaluator expectations at: {rfp_client}")
        return "\n".join(lines)

    kb_voice = kb_zo_voice or bv.get("kbZoVoice") or bv.get("kb_zo_voice") or ""
    lines = [
        standards,
        "",
        format_register_block(register),
        "",
        INTERESTING_PROPOSAL_ANSWER_BLOCK,
        "",
        "## zö core brand voice (MANDATORY · preserve in every rewrite)",
        zo_core,
    ]
    if kb_voice.strip():
        lines.append(kb_voice.strip()[:10000])

    lines.extend(
        [
            "",
            "## This RFP voice adaptation (MANDATORY · do not genericize)",
            f"Tone: {bv.get('tone') or 'professional'}",
            f"Formality: {bv.get('formality') or 'semi-formal'}",
            (
                "Client expectations: "
                f"{bv.get('clientExpectations') or bv.get('client_expectations') or '(client-focused, outcome-led)'}"
            ),
        ]
    )

    adaptation = bv.get("rfpAdaptationNotes") or bv.get("rfp_adaptation_notes")
    if adaptation:
        lines.append(f"Adaptation notes: {adaptation}")

    guidelines = bv.get("voiceGuidelines") or bv.get("voice_guidelines") or []
    if guidelines:
        lines.append("Writing guidelines for this proposal:")
        lines.extend(f"- {g}" for g in guidelines)

    terms = bv.get("keyTerms") or bv.get("key_terms") or []
    if terms:
        lines.append(
            "Use RFP terminology where it names deliverables or scored concepts "
            "(substance only — never paste or paraphrase RFP instruction sentences): "
            f"{', '.join(str(t) for t in terms)}"
        )

    if rfp_client:
        lines.append(f"Write for evaluator expectations at: {rfp_client}")

    if register == "narrative":
        lines.extend(
            [
                "",
                "NARRATIVE VOICE CHECK:",
                "- Company name is always 'zö agency' (lowercase z, umlaut).",
                "- No em dashes. No negation-contrast. No empty hype words. No process verbs.",
                "- American English. Contractions + future tense (we'll / I'll).",
                "- Deliverable register: no !, no emoji, no filler willingness.",
                "- Every sentence should sound like zö telling the client what we did and will do.",
                "- If you wrote 'The Vendor' or third-person agency references, rewrite in first person before returning.",
                "- If a sentence sounds crafted for effect, flatten it.",
                "- Shape: concrete open, teach by showing, admit a true cost, state the point flat and stop.",
                "- First sentence is one promise, in first person. Do not stack outcomes after \"so\".",
                "- Do not open in third person about the agency.",
                "- Never open by paraphrasing the RFP. Prefer specific ideas over generic capability lists.",
            ]
        )
    else:
        lines.append(
            "Prose in this section is first person (we/we'll). "
            "Third person only for a form label, a signature line, or a certification the firm signs. "
            "Still: company name zö agency and the writing rules."
        )

    return "\n".join(lines)


async def fetch_zo_voice_excerpt(
    *,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
) -> str:
    """Fetch voice samples only — never the full bios/company/case-study gather."""
    bundles = await proposal_knowledge_base_tools.gather_proposal_kb_for_sections(
        rfp_title=rfp_title,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_location=rfp_location,
        rfp_context=rfp_context,
        buckets=("zo_voice",),
    )
    text, _sources = bundles.get("zo_voice", ("", []))
    return text[:8000]


_DEFAULT_NARRATIVE_GUIDELINES = [
    "Follow the writing standards in this prompt (the revision pinned to this proposal, or the Brand Voice default). Not for UI.",
    "Always write the company name as 'zö agency' (lowercase z, umlaut).",
    "Never use em dashes. Prefer commas, periods, or new sentences.",
    "Never use negation-contrast (not X it's Y / not just / more than just). Say what the thing is.",
    "Write narrative sections in first person: we, our, us. Never 'The Vendor' or third-person agency distance.",
    "Use contractions and future tense: we'll, I'll.",
    "Lead with the point. Be specific. No empty words (amazing, seamless, leverage, unlock, journey, solution, impactful, exciting).",
    "No process verbs or abstract verb+noun pairs (drive alignment, unlock value, socialize the plan).",
    "No writing for effect: no hooks, punchlines, rhetorical questions, or crafted repetition.",
    "Proposal deliverable register: no exclamation points, no emoji, no filler willingness.",
    "Interesting answer: concrete open + case-study show + true cost + flat stop. Never generic capability lists. Never RFP paraphrase.",
    "Use active voice and concrete human actions.",
]


async def resolve_voice_context(
    *,
    rfp: RfpRecord,
    rfp_context: str,
    brand_voice: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    """Return brand_voice dict + KB zo voice excerpt for prompts."""
    bv = dict(brand_voice or {})
    kb_zo_voice = ""

    needs_kb = not (bv.get("zoCoreVoice") or bv.get("zo_core_voice"))
    if needs_kb or not bv.get("voiceGuidelines"):
        kb_zo_voice = await fetch_zo_voice_excerpt(
            rfp_title=rfp.title,
            rfp_client=rfp.client,
            rfp_sector=rfp.sector,
            rfp_location=rfp.location,
            rfp_context=rfp_context,
        )

    if needs_kb and kb_zo_voice.strip():
        if not bv.get("zoCoreVoice") and not bv.get("zo_core_voice"):
            bv["zoCoreVoice"] = (
                "zö agency writes as a confident, human-centered marketing partner: "
                "direct, warm, first-person (we/our), and grounded in verified facts."
            )

    if not bv.get("tone"):
        bv["tone"] = "professional"
    if not bv.get("formality"):
        bv["formality"] = "semi-formal"

    existing = list(bv.get("voiceGuidelines") or bv.get("voice_guidelines") or [])
    merged_guidelines = list(
        dict.fromkeys([*_DEFAULT_NARRATIVE_GUIDELINES, *existing])
    )
    bv["voiceGuidelines"] = merged_guidelines

    return bv, kb_zo_voice


_VERB = re.compile(r"\b(can|will|is|are|has|have|does|do)\b", re.I)


def first_narrative_sentence(text: str) -> str:
    """First prose sentence, skipping headings and table rows."""
    for para in (text or "").split("\n\n"):
        line = para.strip()
        if not line or line.startswith("#") or line.startswith("|"):
            continue
        flat = " ".join(line.split())
        match = re.match(r".+?[.!?](?:\s|$)", flat)
        return (match.group(0) if match else flat).strip()
    return ""


def narrative_opening_problem(text: str) -> str | None:
    """Flag a first sentence that stacks three verb-clauses after ``so``.

    Shape only: ``We'll … so [clause], [clause], and [clause]``, each clause
    with its own verb. A noun list (``forms, minutes, and notices``) has no
    verbs and is left alone. One outcome after ``so`` is left alone.
    """
    sentence = first_narrative_sentence(text)
    if not sentence or not re.match(r"^(We['’]ll|We will)\b", sentence, re.I):
        return None
    match = re.search(r"\bso\b\s+(.+)$", sentence, re.I)
    if not match:
        return None
    parts = [
        part.strip(" .")
        for part in re.split(r",\s+(?:and\s+)?", match.group(1))
        if part.strip(" .")
    ]
    if sum(1 for part in parts if _VERB.search(part)) >= 3:
        return "piled-so-opening"
    return None


def brand_voice_is_usable(brand_voice: Any) -> bool:
    """True when Phase 3 has a real per-RFP voice profile, not the empty default.

    Strict-RFP generation skips Sections 1–3, which is the only step that used
    to synthesize this profile. An empty ``ProposalBrandVoice`` has formality
    set and nothing else. Drafting then falls back to one generic sentence.
    """
    if brand_voice is None:
        return False
    if isinstance(brand_voice, dict):
        tone = str(brand_voice.get("tone") or "")
        core = str(brand_voice.get("zoCoreVoice") or brand_voice.get("zo_core_voice") or "")
        kb = str(brand_voice.get("kbZoVoice") or brand_voice.get("kb_zo_voice") or "")
    else:
        tone = str(getattr(brand_voice, "tone", "") or "")
        core = str(getattr(brand_voice, "zo_core_voice", "") or "")
        kb = str(getattr(brand_voice, "kb_zo_voice", "") or "")
    return bool(tone.strip() and (core.strip() or kb.strip()))


async def ensure_drafting_brand_voice(
    brand_voice: Any,
    *,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
) -> Any:
    """Synthesize zö core voice + this RFP's adaptation when the cache has none."""
    from app.models.proposal import ProposalBrandVoice

    if brand_voice_is_usable(brand_voice):
        if isinstance(brand_voice, ProposalBrandVoice):
            return brand_voice
        return ProposalBrandVoice.model_validate(brand_voice)

    zo = await fetch_zo_voice_excerpt(
        rfp_title=rfp_title,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_location=rfp_location,
        rfp_context=rfp_context,
    )
    from app.services.proposal_sections_graph import _synthesize_proposal_voice

    raw = await _synthesize_proposal_voice(
        {
            "rfp_client": rfp_client,
            "rfp_sector": rfp_sector,
            "rfp_title": rfp_title,
            "rfp_context": rfp_context,
            "kb_zo_voice": zo,
        }
    )
    voice = ProposalBrandVoice.model_validate((raw or {}).get("brand_voice") or {})
    if not (voice.kb_zo_voice or "").strip() and zo.strip():
        voice = voice.model_copy(update={"kb_zo_voice": zo})
    return voice
