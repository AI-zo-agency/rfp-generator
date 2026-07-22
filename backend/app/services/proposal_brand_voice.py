"""Shared zö brand voice formatting for all proposal LLM passes.

Governing standard: branding/ZO_BRAND_AND_WRITING_STANDARDS_REV3.md
(rev 3 · July 2026). Compulsory for every proposal writing / rewrite / improve pass.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.models.rfp import RfpRecord
from app.services import proposal_knowledge_base_tools

Register = Literal["narrative", "procurement"]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STANDARDS_PATH = _REPO_ROOT / "branding" / "ZO_BRAND_AND_WRITING_STANDARDS_REV3.md"

# Fallback only if the canonical file is missing at runtime.
_STANDARDS_FALLBACK = """# zö Brand & Writing Standards
rev 3 · July 2026 · confidential
Scope: proposal writing only (not app UI).

## 1. Company name
Always: zö agency. Lowercase z. Umlaut always. Both words lowercase.
Never: Zo, ZO, ZÖ Agency, zo agency, Zö Agency.

## 2. Writing rules
Write plainly. Lead with the point. Short ordinary sentences.
Never: em dashes; "not X, it's Y"; performative openers; empty words
(nice, great, amazing, incredible, exciting, passionate, robust, seamless,
leverage, elevate, unlock, journey, solution, impactful).
No writing for effect. No process verbs. Be specific.
Before finish: read aloud and cut.

## 3. Voice
Proposals are deliverables: rules straight. No exclamation points, no emoji, no filler willingness.
Section 2 hard rules always hold.
"""


@lru_cache(maxsize=1)
def load_writing_standards_rev3() -> str:
    """Rev 3 writing standards for proposal copy (company name, writing rules, voice)."""
    try:
        text = _STANDARDS_PATH.read_text(encoding="utf-8").strip()
        if text:
            return (
                "## zö Brand & Writing Standards (rev 3 · July 2026) · COMPULSORY for proposals\n"
                "Scope: proposal writing only. Do not change UI fonts or layout.\n"
                "Follow company name, writing rules, and voice below.\n\n"
                f"{text}"
            )
    except OSError:
        pass
    return (
        "## zö Brand & Writing Standards (rev 3 · July 2026) · COMPULSORY for proposals\n\n"
        f"{_STANDARDS_FALLBACK}"
    )


# Public alias — always returns the full loaded standards file.
def __getattr__(name: str) -> Any:
    if name == "ZO_WRITING_STANDARDS_REV3":
        return load_writing_standards_rev3()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_writing_standards_rev3() -> str:
    return load_writing_standards_rev3()


NARRATIVE_REGISTER_BLOCK = """## NARRATIVE REGISTER (MANDATORY for this section)

Write as zö agency speaking directly to the client. Not as a legal brief describing a vendor.

REQUIRED:
- Follow rev 3 writing standards above (company name, writing rules, voice). Proposal copy only.
- First person: "we", "our", "us". zö is the author who did/will do the work.
- Active voice and concrete verbs: "We built...", "We led...", "Our team designed..."
- Outcome-focused: what changed for the client, not abstract capability claims.
- Specific to THIS client's challenge. Tie proof points directly to their goals.
- Plain, direct sentences. Lead with the point.
- Deliverable register: no exclamation points, no emoji, no filler willingness.

FORBIDDEN in narrative sections (never use):
- Em dashes; "not X, it's Y"; empty hype words (amazing, seamless, leverage, unlock, journey, solution, impactful, exciting, passionate, robust).
- Process verbs and abstract verb+noun pairs (drive alignment, unlock value, socialize the plan, craft the scope).
- "The Vendor", "The Offeror", "The Proposer", "The Respondent", "The Contractor".
- Third-person agency distance: "The agency delivers...", "zö agency brings...", "The firm has experience...".
- Passive procurement boilerplate: "services will be provided", "experience includes...".
- Compliance-form tone in scored narrative sections.
- Writing for effect (hooks, punchlines, rhetorical questions, crafted repetition).

EXAMPLE · same fact, wrong vs right:
- WRONG: "The Vendor's sustainability experience includes the City of Bend WaterWise campaign, which unlocked impactful behavior change."
- RIGHT: "We built WaterWise for the City of Bend conservation office: brand, toolkits, and messaging. We designed it to change how residents use water."

Note: RFP instructions may say "Vendor". That is form language. Narrative sections must still use first-person zö voice."""

PROCUREMENT_REGISTER_BLOCK = """## PROCUREMENT REGISTER (this section)

Use formal third-person language appropriate for attachments, legal forms, certifications, pricing tables, or compliance schedules.
"The Vendor" / "Offeror" language is acceptable when matching RFP form or attachment language.
Still follow Writing Standards rev 3 for company name (zö agency) and writing rules. No em dashes, no empty hype words, no process-verb jargon."""

def classify_section_register(
    *,
    section_id: str = "",
    title: str = "",
    zo_mode: str = "write",
) -> Register:
    """Narrative (we/our) vs procurement (Vendor/form) register for a section."""
    sid = section_id.lower()
    t = title.lower()

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
        "cover letter",
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
        "letter of transmittal",
        "transmittal letter",
    )
    if any(sig in t for sig in narrative_signals):
        return "narrative"

    if zo_mode in ("pull", "select"):
        return "narrative"

    return "narrative"


def format_register_block(register: Register) -> str:
    return (
        NARRATIVE_REGISTER_BLOCK
        if register == "narrative"
        else PROCUREMENT_REGISTER_BLOCK
    )


def format_brand_voice_block(
    brand_voice: dict[str, Any] | None,
    *,
    kb_zo_voice: str = "",
    rfp_client: str = "",
    register: Register = "narrative",
) -> str:
    """Full dual-layer voice block (full rev 3 + core zö + RFP adaptation)."""
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

    kb_voice = kb_zo_voice or bv.get("kbZoVoice") or bv.get("kb_zo_voice") or ""
    lines = [
        load_writing_standards_rev3(),
        "",
        format_register_block(register),
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
            "Mirror RFP terminology in substance (not register): "
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
                "- No em dashes. No empty hype words. No process verbs.",
                "- Deliverable register: no !, no emoji, no filler willingness.",
                "- Every sentence should sound like zö telling the client what we did and will do.",
                "- If you wrote 'The Vendor' or third-person agency references, rewrite in first person before returning.",
                "- If a sentence sounds crafted for effect, flatten it.",
                "- Shape for longer pieces: concrete open, teach by showing, admit a true cost, state the point flat and stop.",
            ]
        )
    else:
        lines.append(
            "Use procurement/form register for this attachment. "
            "Third-person Vendor language is OK here. "
            "Still: rev 3 company name and writing rules."
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
    bundles = await proposal_knowledge_base_tools.gather_proposal_kb_for_sections(
        rfp_title=rfp_title,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_location=rfp_location,
        rfp_context=rfp_context,
    )
    text, _sources = bundles["zo_voice"]
    return text[:8000]


_DEFAULT_NARRATIVE_GUIDELINES = [
    "Follow branding/ZO_BRAND_AND_WRITING_STANDARDS_REV3.md for proposal copy (company name, writing rules, voice). Not for UI.",
    "Always write the company name as 'zö agency' (lowercase z, umlaut).",
    "Never use em dashes. Prefer commas, periods, or new sentences.",
    "Write narrative sections in first person: we, our, us. Never 'The Vendor' or third-person agency distance.",
    "Lead with the point. Be specific. No empty words (amazing, seamless, leverage, unlock, journey, solution, impactful).",
    "No process verbs or abstract verb+noun pairs (drive alignment, unlock value, socialize the plan).",
    "No writing for effect: no hooks, punchlines, rhetorical questions, or crafted repetition.",
    "Proposal deliverable register: no exclamation points, no emoji, no filler willingness.",
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
