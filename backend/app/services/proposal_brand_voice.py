"""Shared zö brand voice formatting for all proposal LLM passes.

Governing standard: branding/ZO_BRAND_AND_WRITING_STANDARDS_REV6.md
(rev 6 · August 2026). Compulsory for every proposal writing / rewrite /
improve / Review-Fix / Complete Scan / section-chat persist pass.
Older revs are dead on arrival.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.models.rfp import RfpRecord
from app.services import proposal_knowledge_base_tools

Register = Literal["narrative", "procurement", "cover_letter"]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STANDARDS_PATH = _REPO_ROOT / "branding" / "ZO_BRAND_AND_WRITING_STANDARDS_REV6.md"

# Fallback only if the canonical file is missing at runtime.
_STANDARDS_FALLBACK = """# zö Brand & Writing Standards
rev 6 · August 2026 · confidential
Scope: proposal writing only (not app UI).

## 1. Company name
Always: zö agency. Lowercase z. Umlaut always. Both words lowercase.
Never: Zo, ZO, ZÖ Agency, zo agency, Zö Agency.

## 2. Writing rules
Write plainly. Lead with the point. Short ordinary sentences. American English.
Never: em dashes; negation-contrast; performative openers; empty words
(nice, great, amazing, incredible, exciting, passionate, robust, seamless,
leverage, elevate, unlock, journey, solution, impactful).
No writing for effect. No process verbs. Be specific. Contract: we'll / I'll.
Before finish: read aloud and cut. Then stop.

## 3. Voice
Proposals are deliverables: rules straight. No exclamation points, no emoji, no filler willingness.
Shape: open with something real; teach by showing; admit a true cost; state the point flat and stop.
Section 2 hard rules always hold.
"""

INTERESTING_PROPOSAL_ANSWER_BLOCK = """## INTERESTING PROPOSAL ANSWER (mandatory — still Rev 6 deliverable)

Interesting is NOT hype. Empty words stay banned (exciting, amazing, passionate, robust, seamless, leverage…).
Interesting means the evaluator learns something specific they did not already write in the RFP.

REQUIRED for scored narrative tabs (Executive Summary, Understanding, Approach, Methodology, Past Performance):
1. Open with a concrete moment, decision, deliverable, or named tactic — never by restating what the client asked for or already built.
2. Teach by showing: put the idea inside one verified case study, named method, or festival-ready example from KB.
3. Prefer a fresh, specific idea over a generic capability list ("we do websites / social / content").
4. Admit a true cost or tradeoff when it matters (scope edge, timeline pressure, handoff risk).
5. State our commitment in contractions and future tense ("we'll…") — flat — and stop.
6. ANTI-RFP-ECHO still holds: requirements are a private checklist; the body is only our answer.
"""

# Compact hard rules for section-chat / selection / apply-fix rewrites.
CHAT_REV6_VOICE_HARD_RULES = """## REV 6 VOICE (compulsory on every chat edit — branding/ZO_BRAND_AND_WRITING_STANDARDS_REV6.md)

Hard bans (cut or rewrite before returning):
- Em dashes (use a comma, period, or new sentence).
- Negation-contrast: rather than, instead of, "X, not Y", not just / not only / more than just / beyond just.
- Significance-closes ("That's the kind of…", "which is what makes…").
- Hedging announcements ("worth noting", "worth naming", "keep in mind").
- Empty words: robust, seamless, leverage, unlock, impactful, exciting, passionate, amazing.
- Writing for effect: hooks, punchlines, rhetorical questions, crafted repetition.

Do: state what the thing is; first person we/our in narrative tabs; contractions + future tense (we'll); American English; company name always "zö agency".
Exempt only the registered tagline: "We are more than your agency. We are your strongest advocate."
"""


@lru_cache(maxsize=1)
def load_writing_standards() -> str:
    """Rev 6 writing standards — compulsory for every proposal copy pass."""
    try:
        text = _STANDARDS_PATH.read_text(encoding="utf-8").strip()
        if text:
            return (
                "## zö Brand & Writing Standards (rev 6 · August 2026) · COMPULSORY for proposals\n"
                "Highest rev governs. Older files are dead on arrival. "
                "Scope: proposal writing only. Do not change UI fonts or layout.\n"
                "Follow company name, writing rules, voice, money, and checklist below.\n\n"
                f"{text}"
            )
    except OSError:
        pass
    return (
        "## zö Brand & Writing Standards (rev 6 · August 2026) · COMPULSORY for proposals\n\n"
        f"{_STANDARDS_FALLBACK}"
    )


def load_writing_standards_rev6() -> str:
    return load_writing_standards()


def load_writing_standards_rev3() -> str:
    """Backward-compatible alias — always returns governing rev 6."""
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
- Follow rev 6 writing standards above (company name, writing rules, voice). Proposal copy only.
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
- RIGHT: "We'll run festival-week conversion checks on ticket and vendor flows, then keep Instagram and Facebook fed year-round with volunteer stories — the same handoff pattern we used at Rock the Locks."

Note: RFP instructions may say "Vendor". That is form language. Narrative sections must still use first-person zö voice."""

COVER_LETTER_REGISTER_BLOCK = """## COVER LETTER / TRANSMITTAL REGISTER (Rev 6 signed passage)

Governing standard: branding/ZO_BRAND_AND_WRITING_STANDARDS_REV6.md — signed passages
inside a proposal follow the correspondence standard; the rest of the proposal stays
deliverable register.

CONTENT / STRUCTURE (highest priority after Rev 6 voice bans):
- Follow THIS RFP's cover-letter / letter-of-transmittal format and required statements
  (addressee, required attestations, contact/signature block, page limit).
- Do not invent a Zo template letter that ignores the RFP's own package instructions.
- When 06_WON / past won-proposal cover letters or letters of transmittal appear in
  evidence: use them as FORM / STRUCTURE / VOICE models only (salutation → intent →
  short offer summary → close / signature shape). NEVER copy their client names,
  project claims, dates, dollar figures, or prior RFP facts — rewrite for THIS buyer.

VOICE (Rev 6 correspondence for signed passages):
- First person from the authorized signer (I / we). Address the recipient by name when known.
- Warm, contracted, human — still no em dashes, no negation-contrast, no empty hype.
- NEVER open with "On behalf of zö agency" or other third-person agency boilerplate.
- Name "zö agency" when identifying the offeror, certifications, or contact block — as the
  firm, not as a corporate narrator speaking "on behalf of" itself.

If a wet-ink / signed PDF is required: write the letter body + [DESIGNER NOTE] to attach
the signed file. Never invent signature dates or claim the PDF is already attached."""

PROCUREMENT_REGISTER_BLOCK = """## PROCUREMENT REGISTER (this section)

Use formal third-person language appropriate for attachments, legal forms, certifications, pricing tables, or compliance schedules.
"The Vendor" / "Offeror" language is acceptable when matching RFP form or attachment language.
Still follow Writing Standards rev 6 for company name (zö agency) and writing rules. No em dashes, no empty hype words, no process-verb jargon. American English."""


def classify_section_register(
    *,
    section_id: str = "",
    title: str = "",
    zo_mode: str = "write",
) -> Register:
    """Narrative / cover-letter correspondence / procurement register for a section."""
    sid = section_id.lower()
    t = title.lower()

    # Signed passages (Rev 6) — before generic narrative / Zo 1–3 defaults.
    if any(
        k in t
        for k in (
            "cover letter",
            "letter of transmittal",
            "transmittal letter",
            "letter of offer",
        )
    ) or "cover-letter" in sid or "transmittal" in sid:
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
) -> str:
    """Full dual-layer voice block (full rev 6 + interesting answer + core zö + RFP adaptation).

    ``compact=True`` (Review / fact-check lean path): hard Rev 6 rules + short zö
    core only — full standards file is enforced by deterministic scrub, not by
    stuffing ~10k tokens into every section rewrite.
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

    if compact:
        lines = [
            CHAT_REV6_VOICE_HARD_RULES,
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
        load_writing_standards(),
        "",
        CHAT_REV6_VOICE_HARD_RULES,
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
                "- Never open by paraphrasing the RFP. Prefer specific ideas over generic capability lists.",
            ]
        )
    else:
        lines.append(
            "Use procurement/form register for this attachment. "
            "Third-person Vendor language is OK here. "
            "Still: rev 6 company name and writing rules."
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
    "Follow branding/ZO_BRAND_AND_WRITING_STANDARDS_REV6.md for proposal copy (company name, writing rules, voice). Not for UI.",
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
