"""Shared zö brand voice formatting for all proposal LLM passes."""

from __future__ import annotations

from typing import Any, Literal

from app.models.rfp import RfpRecord
from app.services import proposal_knowledge_base_tools

Register = Literal["narrative", "procurement"]

NARRATIVE_REGISTER_BLOCK = """## NARRATIVE REGISTER (MANDATORY for this section)

Write as zö agency speaking directly to the client — NOT as a legal brief describing a vendor.

REQUIRED:
- **Zö Tone & Style**: Write with modern agency energy, bold creativity, and high-impact language. Keep it fresh, human, and engaging — NEVER sound boring, dry, academic, or like generic corporate boilerplate.
- First person: "we", "our", "us" — zö is the author who did/will do the work.
- Active voice and concrete verbs: "We built...", "We led...", "Our team designed..."
- Outcome-focused: what changed for the client, not abstract capability claims.
- Specific to THIS client's challenge — tie proof points directly to their goals.

FORBIDDEN in narrative sections (never use):
- Dry, boring, or generic corporate filler phrasing.
- "The Vendor", "The Offeror", "The Proposer", "The Respondent", "The Contractor".
- Third-person agency distance: "The agency delivers...", "zö agency brings...", "The firm has experience...".
- Passive procurement boilerplate: "services will be provided", "experience includes...".
- Compliance-form tone in scored narrative sections.

EXAMPLE — same fact, wrong vs right:
- WRONG & BORING: "The Vendor's environmental and sustainability subject matter experience includes the City of Bend WaterWise conservation campaign, which advanced measurable behavior change in residential water use."
- RIGHT & EXCITING (ZÖ VOICE): "We built the WaterWise campaign for the City of Bend's conservation office from the ground up — brand, toolkits, messaging — and designed it specifically to change how residents think about water use, not just inform them."

Note: RFP instructions may say "Vendor" — that is form language. Narrative sections must still use first-person zö voice."""

PROCUREMENT_REGISTER_BLOCK = """## PROCUREMENT REGISTER (this section)

Use formal third-person language appropriate for attachments, legal forms, certifications, pricing tables, or compliance schedules.
"The Vendor" / "Offeror" language is acceptable when matching RFP form or attachment language."""


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
    """Full dual-layer voice block (core zö + RFP adaptation) for LLM prompts."""
    bv = brand_voice or {}
    zo_core = (
        bv.get("zoCoreVoice")
        or bv.get("zo_core_voice")
        or (
            "zö agency writes as a confident, human-centered marketing partner — "
            "direct, warm, first-person (we/our), and grounded in verified facts."
        )
    )

    kb_voice = kb_zo_voice or bv.get("kbZoVoice") or bv.get("kb_zo_voice") or ""
    lines = [
        format_register_block(register),
        "",
        "## zö core brand voice (MANDATORY — preserve in every rewrite)",
        zo_core,
    ]
    if kb_voice.strip():
        lines.append(kb_voice.strip()[:10000]) # Keep more voice content if available


    lines.extend(
        [
            "",
            "## This RFP voice adaptation (MANDATORY — do not genericize)",
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
                "- Every sentence should sound like zö telling the client what we did and will do.",
                "- If you wrote 'The Vendor' or third-person agency references, rewrite in first person before returning.",
            ]
        )
    else:
        lines.append(
            "Use procurement/form register for this attachment — third-person Vendor language is OK here."
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
    "Write narrative sections in first person: we, our, us — never 'The Vendor' or third-person agency distance.",
    "Lead with client outcomes and specific proof, not abstract capability claims.",
    "Use active voice and concrete verbs.",
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
                "zö agency writes as a confident, human-centered marketing partner — "
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
