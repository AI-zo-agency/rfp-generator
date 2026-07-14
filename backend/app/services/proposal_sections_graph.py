"""LangGraph pipeline for static proposal Sections 1–3 (KB pull/select + dual-layer voice)."""

from __future__ import annotations

import contextvars
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models.proposal import ProposalBrandVoice, ProposalSection
from app.services import llm, proposal_knowledge_base_tools
from app.services.llm import LlmError
from app.services.proposal_brand_voice import format_brand_voice_block, format_register_block
from app.services.proposal_voice_enforcement import enforce_narrative_voice
from app.services.proposal_langchain import _provider_name

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
    except Exception as exc:  # noqa: BLE001
        logger.debug("_emit_partial failed (non-fatal): %s", exc)


def _sanitize_content(text: str) -> str:
    """Normalize Unicode and strip non-ASCII/non-Latin garbage characters that
    sometimes bleed in from KB PDFs (e.g. Gujarati or other Indic scripts
    mixed into English names like 'V\\u0ac3\\u0ab5ek Patel').

    Strategy:
    1. NFKC normalize (handles ligatures, compat chars, etc.)
    2. For each character: if it is a basic Latin / common punctuation char, keep it.
       Otherwise try NFKD decomposition to get the base ASCII char.
       If still non-ASCII, drop it entirely.
    """
    if not text:
        return text
    # NFKC first — normalizes composed forms
    text = unicodedata.normalize("NFKC", text)
    result: list[str] = []
    for ch in text:
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
    # Collapse any double spaces created by dropped chars
    clean = re.sub(r" {2,}", " ", clean)
    return clean


def _apply_verified_corrections(text: str, rfp_client: str = "") -> str:
    """Deterministic post-processing: fix known spelling/template errors in all generated content.

    These corrections are programmatic (no LLM) and 100% safe to apply universally.
    """
    if not text:
        return text

    # 1. Legal name — must have apostrophe
    text = re.sub(r"\bZ[- ]?[Oo]nion\b", "Z'Onion", text)
    text = text.replace("ZOnion Creative", "Z'Onion Creative")

    # 2. Vivek Patel name — never "Vince Patel"
    text = re.sub(r"\bVince\s+Patel\b", "Vivek Patel", text, flags=re.I)

    # 3. Miguel Pérez / Miguel Perez title correction
    text = re.sub(
        r"(Miguel\s+P[eé]rez\b.*?)\bproduction\s+assistant\b",
        r"\1production designer",
        text,
        flags=re.I | re.DOTALL,
    )

    # 4. Insurance placeholder — "City of Bend" must be replaced with the actual RFP client
    if rfp_client and rfp_client.strip():
        text = text.replace("City of Bend", rfp_client)

    # 5. Strip Benedictine University hallucinated percentage metrics
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
    kb_bios: str
    kb_bio_sources: list[str]
    kb_case_studies: str
    kb_case_sources: list[str]
    sections: list[dict[str, Any]]
    provider: str
    error: str | None


def _proposal_voice_block(state: SectionsGraphState) -> str:
    return format_brand_voice_block(
        state.get("brand_voice"),
        kb_zo_voice=state.get("kb_zo_voice") or "",
        rfp_client=state.get("rfp_client") or "",
        register="narrative",
    )


def _narrative_section_preamble(state: SectionsGraphState) -> str:
    return (
        "You write zö agency NARRATIVE proposal content.\n"
        f"{format_register_block('narrative')}\n\n"
        "Facts (clients, certs, team, case studies) must come ONLY from knowledge-base excerpts.\n"
        "Voice must follow BOTH zö core brand voice AND the RFP-specific adaptation block.\n"
        f"Client: {state['rfp_client']} | Sector: {state['rfp_sector']}\n"
    )


async def _fetch_knowledge_base(state: SectionsGraphState) -> dict[str, Any]:
    bundles = await proposal_knowledge_base_tools.gather_proposal_kb_for_sections(
        rfp_title=state["rfp_title"],
        rfp_client=state["rfp_client"],
        rfp_sector=state["rfp_sector"],
        rfp_location=state.get("rfp_location"),
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
        "kb_bios": _sanitize_content(bios_text),
        "kb_bio_sources": bio_sources,
        "kb_case_studies": _sanitize_content(cases_text),
        "kb_case_sources": case_sources,
    }


async def _synthesize_proposal_voice(state: SectionsGraphState) -> dict[str, Any]:
    """Merge zö KB brand voice with RFP-specific tone adaptation."""
    zo_kb = (state.get("kb_zo_voice") or "")[:8000]
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


async def _build_section_1(state: SectionsGraphState) -> dict[str, Any]:
    """Emit each Section 1 subsection as its own highlighted section card."""
    voice = _proposal_voice_block(state)
    existing = state.get("sections") or []

    subsections = [
        (
            "section-1-who-we-are",
            "1.1 — Who We Are",
            (
                "Write the 'Who We Are' section for zö agency. Structure it into two distinct parts: '## Who We Are' and '## Our Promise'.\n"
                "Do NOT mention any certifications (WOSB, WBENC), business address, FEIN, or insurance details here (they are already covered in the subsections below).\n\n"
                "Tone & Content Guidelines:\n"
                "- Write in a highly expressive, bold, and passionate voice. Use bold formatting on key impact phrases.\n"
                "- We are 'more than just an agency' – we are their strongest advocate and happily become an extension of their team (as if we are their marketing department).\n"
                "- Explain that the name 'zö' is our term for family, kindred, clan, community, and a force of collaboration in action.\n"
                "- State that we believe in becoming true partners with our clients, particularly the client for this RFP (incorporate their name dynamically), creating authentic campaigns that reflect their unique spirit.\n"
                "- We approach each project with fresh energy, an open mind, and value timelessness. We combine deep sector/public expertise with energetic approaches.\n"
                "- Under the heading '## Our Promise', write our firm commitment: excellence is a guarantee, not a goal. We meet/beat deadlines and budgets, provide complete transparency, direct access to the team (no surprise bills or hidden agendas), and will ambassador their brand identity with the dedication of a true partner."
            ),
            "pull",
            0.04,
            600,
        ),
        (
            "section-1-org-structure",
            "1.2 — Organizational Structure",
            (
                "Write the 'Organizational Structure' for zö agency.\n"
                "You MUST list every zö agency team member currently working there — fetch ALL names and titles from the knowledge base.\n"
                "Do NOT skip anyone. Include leadership, account directors, project managers, creatives, media buyers, strategists, coordinators, and any other staff.\n"
                "For each department, write 1-2 sentences describing what that department does.\n"
                "List every person with their exact name and title under the correct department.\n"
                "Pull strictly from KB — do not invent any names."
            ),
            "pull",
            0.04,
            600,
        ),
        (
            "section-1-business-info",
            "1.3 — Business Information",
            (
                "Write the 'Business Information' subsection for zö agency.\n"
                "Include: legal business name, business type/structure (LLC/corp/etc.), FEIN or EIN if available, \n"
                "year founded, total number of employees, DUNS/SAM/CAGE codes if in KB, \n"
                "office address(es), phone number, email, and website URL.\n"
                "Pull ALL exact facts from the knowledge base. Do not leave any field blank if data exists."
            ),
            "pull",
            0.03,
            400,
        ),
        (
            "section-1-certifications",
            "1.4 — Certifications",
            (
                "Write the 'Certifications' section for zö agency. Make it highly impressive and clean.\n"
                "Format each certification as a bold, prominent list item with details. For example:\n"
                "- **[Certification Name]** (e.g. Women-Owned Small Business - WOSB)\n"
                "  - **Certifying Agency:** [Name of certifying body, e.g. US Small Business Administration or WBENC]\n"
                "  - **Certification Number:** [Number if in KB]\n"
                "  - **Status:** [Active / Certified / Expiration Date]\n"
                "  - **Impact:** [1 sentence explaining how this benefits our partnership, e.g. helping meet supplier diversity goals]\n"
                "List EVERY certification held. Pull strictly from the knowledge base, highlighting key ones like WBE, WOSB, and WBE/WBENC certifications."
            ),
            "pull",
            0.03,
            400,
        ),
        (
            "section-1-insurance",
            "1.5 — Insurance Information",
            (
                "Write the 'Insurance Information' subsection for zö agency.\n"
                "List every insurance policy held: general liability, professional liability / E&O, \n"
                "commercial auto, workers' compensation, umbrella/excess, cyber liability, or any others.\n"
                "For each policy: coverage type, carrier name, policy number (if in KB), coverage limits (per occurrence and aggregate).\n"
                "Pull strictly from the knowledge base."
            ),
            "pull",
            0.03,
            400,
        ),
    ]

    new_sections: list[dict[str, Any]] = []
    kb_sources = state.get("kb_company_sources") or []

    for sec_id, sec_title, instruction, mode, ratio, word_tgt in subsections:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        f"{_section_system_preamble(state)}\n"
                        f"You are writing subsection: '{sec_title}'.\n"
                        f"Task: {instruction}\n"
                        "Write in first-person zö voice (we/our/us). Never use 'The Vendor' or third-person.\n"
                        "Be thorough and detailed. Include every fact you find in the KB.\n"
                        'Return JSON: {"content": "full detailed content", "kbRefs": ["source1", ...]}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Voice:\n{voice}\n\n"
                        f"Company Knowledge Base:\n{state.get('kb_company', '')[:500000]}\n\n"
                        f"Team Bios KB (for org structure names):\n{state.get('kb_bios', '')[:200000]}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.3,
        )
        content = _sanitize_content(raw.get("content", "").strip())
        section = _section_payload(
            section_id=sec_id,
            title=sec_title,
            mode=mode,
            word_target=word_tgt,
            page_limit=state.get("page_limit"),
            page_ratio=ratio,
            designer_note_default=f"Section 1 subsection: {sec_title}. Pull from master template layout.",
            raw={"content": content, "kbRefs": raw.get("kbRefs") or []},
            kb_sources=kb_sources,
        )
        new_sections.append(section)
        await _emit_partial(state, [*existing, *new_sections])

    return {"sections": [*existing, *new_sections]}


async def _build_section_2(state: SectionsGraphState) -> dict[str, Any]:
    """Emit one section card per selected key lead team member matching the RFP requirements (5-6 total)."""
    voice = _proposal_voice_block(state)
    existing = state.get("sections") or []

    # Identify 5-6 key lead team members whose roles best match this RFP solicitation.
    # Sonja Anderson and Rachael Rice are always compulsory. Select 3-4 others who will lead the project scope.
    selection, _ = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "Analyze the RFP context carefully to determine the key roles and leadership needed for this solicitation.\n"
                    "Then look at the team bios in the knowledge base and select EXACTLY 5-6 total team members who will LEAD this project.\n\n"
                    "STRICT RULES:\n"
                    "- You MUST include 'Sonja Anderson' (Agency Director) and 'Rachael Rice' (Project Director/Account Manager) as compulsory first two.\n"
                    "- Select 3-4 other team members from the KB whose specific roles (e.g. Creative Director, Media Buyer, Digital Strategist) match the RFP scope.\n"
                    "- Do NOT select the same person twice under different name spellings.\n"
                    "- ONLY select people whose bios exist in the knowledge base. Do NOT invent members.\n"
                    "- Each selected member should have a DISTINCT role from the others \u2014 no duplicating the same function.\n"
                    'Return JSON: {"members": ["Sonja Anderson", "Rachael Rice", "Name 3", "Name 4", "Name 5"]}'
                )
            },
            {
                "role": "user",
                "content": (
                    f"RFP context:\n{state['rfp_context'][:15000]}\n\n"
                    f"Team bios KB:\n{state.get('kb_bios', '')[:500000]}"
                )
            }
        ]
    )
    raw_members = selection.get("members", ["Sonja Anderson", "Rachael Rice"])
    # Enforce Sonja and Rachael presence
    if not any("Sonja" in m for m in raw_members):
        raw_members.insert(0, "Sonja Anderson")
    if not any("Rachael" in m or "Rachel" in m for m in raw_members):
        raw_members.insert(1, "Rachael Rice")

    # Deduplicate: normalize names to first-name + last-name lower to prevent same person twice
    seen_normalized: set[str] = set()
    members: list[str] = []
    for m in raw_members:
        key = " ".join(m.strip().lower().split())  # normalize spaces
        # Also deduplicate by last name to catch e.g. "Rachel Rice" vs "Rachael Rice"
        last_name = key.split()[-1] if key.split() else key
        if last_name not in seen_normalized:
            seen_normalized.add(last_name)
            members.append(m.strip())
    members = members[:6]  # Cap at 6 key leads


    new_sections: list[dict[str, Any]] = []
    kb_sources = state.get("kb_bio_sources") or []

    for i, member in enumerate(members, 1):
        safe_id = member.lower().replace(" ", "-").replace("'", "")
        sec_id = f"section-2-bio-{safe_id}"
        sec_title = f"2.{i} — {member}"

        # Step 1: Extract RAW VERBATIM facts from KB for this specific member
        extracted, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        f"Your ONLY job is to extract EVERY fact about '{member}' from the knowledge base below.\n"
                        "DO NOT invent, infer, or fill in any missing data. If something is not explicitly in the KB, omit it.\n"
                        "Extract EXACTLY as written in the KB:\n"
                        "- Their full title/role at zo agency\n"
                        "- Their bio/overview paragraph verbatim or paraphrased closely\n"
                        "- EVERY expertise area with its EXACT year count as listed in the KB\n"
                        "- Every work history entry: exact company name, exact title, exact dates\n"
                        "- Every license or certification\n"
                        "- Every key account/client mentioned\n"
                        "- Education/credentials\n"
                        "Return JSON:\n"
                        '{"title": "...", "overview": "...", '
                        '"expertise": [{"area": "...", "years": "..."}], '
                        '"work_history": [{"company": "...", "title": "...", "dates": "..."}], '
                        '"licenses": ["..."], '
                        '"key_accounts": ["..."], '
                        '"education": ["..."]}'
                    ),
                },
                {
                    "role": "user",
                    "content": f"Team Bios Knowledge Base:\n{state.get('kb_bios', '')[:500000]}",
                },
            ],
            max_tokens=2048,
            temperature=0.0,  # Zero temp for exact extraction
        )

        # Step 2: Format extracted facts into a structured resume
        title = extracted.get("title", "Team Member")
        overview = extracted.get("overview", "")
        expertise = extracted.get("expertise", [])
        work_history = extracted.get("work_history", [])
        licenses = extracted.get("licenses", [])
        key_accounts = extracted.get("key_accounts", [])
        education = extracted.get("education", [])

        # Build structured markdown from extracted data
        content_parts = [f"### {member} — {title}\n"]

        if overview:
            content_parts.append(f"**Overview**\n{overview}\n")
        else:
            content_parts.append("**Overview**\n[VERIFY: Overview/Bio Paragraph]\n")

        content_parts.append("**Years of Experience**\n")
        content_parts.append("| Area of Expertise | Years |")
        content_parts.append("| --- | --- |")
        if expertise:
            for exp in expertise:
                area = exp.get("area", "")
                years = exp.get("years", "")
                if area:
                    content_parts.append(f"| {area} | {years} |")
        else:
            content_parts.append("| [VERIFY: Years of Experience] | [VERIFY] |")
        content_parts.append("")

        content_parts.append("**Work History**")
        if work_history:
            for job in work_history:
                company = job.get("company", "")
                job_title = job.get("title", "")
                dates = job.get("dates", "")
                if company:
                    content_parts.append(f"- **{company}** — {job_title}, {dates}")
        else:
            content_parts.append("- [VERIFY: Work History]")
        content_parts.append("")

        content_parts.append("**Licenses & Certifications**")
        if licenses:
            for lic in licenses:
                content_parts.append(f"- {lic}")
        else:
            content_parts.append("- [VERIFY: Licenses & Certifications]")
        content_parts.append("")

        content_parts.append("**Key Accounts**")
        if key_accounts:
            for acct in key_accounts:
                content_parts.append(f"- {acct}")
        else:
            content_parts.append("- [VERIFY: Key Accounts]")
        content_parts.append("")

        content_parts.append("**Education**")
        if education:
            for edu in education:
                content_parts.append(f"- {edu}")
        else:
            content_parts.append("- [VERIFY: Education]")
        content_parts.append("")

        content = _apply_verified_corrections(
            _sanitize_content("\n".join(content_parts)),
            rfp_client=state.get("rfp_client", ""),
        )
        raw = {"content": content, "kbRefs": [member]}

        section = _section_payload(
            section_id=sec_id,
            title=sec_title,
            mode="select",
            word_target=500,
            page_limit=state.get("page_limit"),
            page_ratio=0.05,
            designer_note_default=f"Bio for {member}. Insert exact text — no rewrites.",
            raw=raw,
            kb_sources=kb_sources,
            extra_refs=[member],
        )
        new_sections.append(section)
        await _emit_partial(state, [*existing, *new_sections])

    return {"sections": [*existing, *new_sections]}


async def _build_section_3(state: SectionsGraphState) -> dict[str, Any]:
    """Emit one section card per verified past work example from the KB — no hallucination."""
    voice = _proposal_voice_block(state)
    existing = state.get("sections") or []
    rfp_client = state.get("rfp_client", "")

    # 1. Select PAST work examples that best match THIS RFP's scope.
    # STRICT RULE: Only real past work from the KB. NEVER the current RFP client.
    selection, _ = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "Analyze the RFP requirements to understand what this client needs (sector, campaign types, media, audience, etc.).\n"
                    "Then look through the case studies knowledge base and select PAST completed projects that best match those requirements.\n\n"
                    "STRICT RULES:\n"
                    f"- Do NOT select any work related to '{rfp_client}' — that is the CURRENT client, not a past case study.\n"
                    "- Only select case studies that are EXPLICITLY listed in the knowledge base. Do NOT fabricate any.\n"
                    "- Select AT LEAST 5 case studies (aim for 5-10) that have some relevance to the RFP requirements (e.g. general digital campaign, logo design, messaging, website, or municipal/community clients).\n"
                    "- If fewer than 5 exist in the KB, return all that are available in the KB.\n"
                    'Return JSON: {"selected_studies": ["Exact Case Study Title From KB 1", "Exact Title 2", ...]}'
                )
            },
            {
                "role": "user",
                "content": (
                    f"RFP requirements summary:\n{state['rfp_context'][:15000]}\n\n"
                    f"Case studies knowledge base (ONLY use titles listed here):\n{state.get('kb_case_studies', '')[:500000]}"
                )
            }
        ]
    )
    selected_studies = selection.get("selected_studies", [])
    # DO NOT fallback to generic names — if nothing found, leave empty so no hallucination.
    selected_studies = [s for s in selected_studies if s.strip()]  # Remove blanks
    selected_studies = list(dict.fromkeys(selected_studies))  # Deduplicate

    # 2. Generate each work example as its own card
    new_sections: list[dict[str, Any]] = []
    kb_sources = state.get("kb_case_sources") or []

    for i, study in enumerate(selected_studies, 1):
        safe_id = study.lower()[:40].replace(" ", "-").replace("/", "-")
        sec_id = f"section-3-work-{i:02d}-{safe_id}"
        sec_title = f"3.{i} — {study}"

        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        f"{_section_system_preamble(state)}\n"
                        f"Write a detailed 'Our Work' case study for: '{study}'.\n\n"
                        "CRITICAL RULES:\n"
                        f"- Do NOT write about '{rfp_client}' — that is the CURRENT client this proposal is for, NOT a past case study.\n"
                        "- ONLY pull verified facts, client names, and outcomes directly from the case studies knowledge base.\n"
                        "- If facts are not in the KB, do NOT invent them. Use only what is explicitly stated.\n\n"
                        "Format and Content:\n"
                        "- Write from zö's perspective (we/our/us).\n"
                        "- Use bold text for key outcomes and impact metrics.\n"
                        "- Structure as: Client overview → Challenge → Our Approach → Key Tactics → Measurable Outcomes\n"
                        "- Use bullet points for tactics and outcomes — no long boring paragraphs.\n"
                        "- Use ASCII characters only in all text — no special Unicode or non-English characters.\n"
                        'Return JSON: {"content": "full case study content", "kbRefs": ["..."]}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Voice:\n{voice}\n\n"
                        f"Case studies knowledge base:\n{state.get('kb_case_studies', '')[:500000]}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.3,
        )
        content = _sanitize_content(raw.get("content", "").strip())
        section = _section_payload(
            section_id=sec_id,
            title=sec_title,
            mode="select",
            word_target=600,
            page_limit=state.get("page_limit"),
            page_ratio=0.03,
            designer_note_default=f"Our Work example: {study}.",
            raw={"content": content, "kbRefs": raw.get("kbRefs") or []},
            kb_sources=kb_sources,
            extra_refs=[study],
        )
        new_sections.append(section)
        await _emit_partial(state, [*existing, *new_sections])

    return {"sections": [*existing, *new_sections]}


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
    content = enforce_narrative_voice(
        str(raw.get("content", "")).strip(),
        section_id=section_id,
        title=title,
        register="narrative",
    )
    designer = str(raw.get("designerNote") or designer_note_default).strip()
    kb_refs = raw.get("kbRefs") if isinstance(raw.get("kbRefs"), list) else []
    refs = list(
        dict.fromkeys([*(str(r) for r in kb_refs), *(extra_refs or []), *kb_sources[:5]])
    )
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
        "kbRefs": refs,
    }


def _build_graph() -> Any:
    graph = StateGraph(SectionsGraphState)
    graph.add_node("fetch_knowledge_base", _fetch_knowledge_base)
    graph.add_node("synthesize_proposal_voice", _synthesize_proposal_voice)
    graph.add_node("build_section_1", _build_section_1)
    graph.add_node("build_section_2", _build_section_2)
    graph.add_node("build_section_3", _build_section_3)

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
) -> tuple[list[ProposalSection], ProposalBrandVoice, str]:
    if not llm.is_configured():
        raise LlmError(
            "LLM not configured. Set OPENROUTER_API_KEY or FIREWORKS_API_KEY.",
            status_code=503,
        )

    initial: SectionsGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "page_limit": page_limit,
        "sections": [],
    }

    logger.info("LangGraph sections 1–3 starting for rfp_id=%s", rfp_id)
    final: dict[str, Any] = dict(initial)
    token = _partial_cb_var.set(on_sections_partial)
    try:
        async for event in _SECTIONS_GRAPH.astream(initial):
            for node_name, update in event.items():
                if isinstance(update, dict):
                    final.update(update)
                if node_name not in ("build_section_1", "build_section_2", "build_section_3"):
                    continue
                if not on_sections_partial:
                    continue
                raw_sections = final.get("sections") or []
                sections = [ProposalSection.model_validate(item) for item in raw_sections]
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
    sections = [ProposalSection.model_validate(item) for item in raw_sections]
    brand_voice = ProposalBrandVoice.model_validate(final.get("brand_voice") or {})
    provider = str(final.get("provider") or _provider_name())

    logger.info(
        "LangGraph sections 1–3 complete for %s: %d sections, tone=%r, provider=%s",
        rfp_id,
        len(sections),
        brand_voice.tone,
        provider,
    )
    return sections, brand_voice, provider
