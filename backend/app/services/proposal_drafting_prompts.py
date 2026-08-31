"""Shared prompt blocks for proposal drafting."""

from __future__ import annotations

from typing import Any

# Anti-hallucination rules - CRITICAL for all proposal generation
ANTI_HALLUCINATION_RULES = """
## CRITICAL: ANTI-HALLUCINATION RULES

YOU MUST NEVER:
1. Invent statistics (retention rates, client counts, audience sizes, years of experience)
2. Cite specific numbers unless they appear VERBATIM in the evidence corpus with [E#] citation
3. Use team member names that are not in approved bio files (04_Bio_*.pdf)
4. Add certifications not explicitly listed in 01_companyfacts_verified
4b. Claim the agency is registered / qualified / authorized to conduct business in a state that is not on the 01_companyfacts / Section 1.3 State Registrations list (e.g. asserting Maryland when the verified list is Oregon, Washington, Texas, Colorado, California). That is a checkable legal fact — use [MANUAL FILL: Sonja — confirm foreign qualification] or omit it. Never invent a filing.
5. Transfer metrics from one client project to describe agency-wide capabilities
6. Round or approximate numbers - use exact figures from KB or use [VERIFY: specific field]
7. Spell names incorrectly (check exact spelling in bio file names)
8. Claim "X years of Y experience" unless that exact phrasing is in verified facts
9. Invent reference names, emails, phone numbers, or certifications when evidence is empty
10. Cite a client as proof of work type X unless 01_ClientList_Approved work type supports X
11. Name Public=Confirm clients (e.g. Thrive Guides) as settled fact — use [FLAG: Confirm…]
12. Cite 07_FIN / competitor FOIA as won zö experience
13. Assert E-Verify enrollment, affidavits under penalty of perjury, or "no conflicts of interest" disclosures unless a named human (Sonja/Operations/leadership) has confirmed — use [VERIFY: …] instead
14. Invent annual staffing hours (e.g. 400/320/280/200/160) or filler credentials like a "10-year corporate-creative partnership" (agency founded 2013 → 13 years in 2026)
15. Invent percent-time, FTE %, "X% of their time", or dedicated-allocation percentages for named people (e.g. 10%/35%/25%/25-30%) — these are almost never in KB and must not be copied from prior proposals
16. Invent individual ZO team-member hourly rates (Sonja/Curt/Justin/etc. $/hr) — those are NOT in the KB. Work/labor-category rates from 00_Guide_Pricing are OK; named-person rate cells must be [VERIFY: hourly rate — {name/role}]
17. Invent reporting diagrams, dashboards, org charts, timeline graphics, sample portals, or "attached" visuals that are not evidenced in KB / required templates
18. Add [DESIGNER NOTE] graphics/diagrams unless THIS RFP explicitly requires that visual or a verified template asset exists

LEGAL ATTESTATIONS (higher bar than ordinary claims):
- E-Verify Affidavit / Contractor Affidavit: NEVER state participation as fact. Go/No-Go treats E-Verify as unconfirmed until Sonja/Operations verifies. Keep [VERIFY: E-Verify enrollment — …] even if surrounding form language is required.
- Disclosure / conflicts of interest: NEVER pre-fill "we have no conflicts." Use [VERIFY: conflict-of-interest disclosure — must be confirmed by Sonja/leadership].
- Do NOT "clean up" these VERIFY tags during senior-editor or VERIFY-cleanup passes.

PERCENT-TIME / FTE / STAFF ALLOCATION (mandatory):
- Do NOT invent percent-time tables or reuse static "commitments the client can hold us to" % grids from other RFPs.
- If THIS RFP does NOT require percent-time / FTE / dedicated allocation %: omit that column and any percent-time table entirely. Name approved-bio roles/people only.
- If THIS RFP DOES require percent-time / FTE: every cell must be [VERIFY: percent time] until Ella/Sonja confirms — never invent 10%/35%/25% etc.
- Prefer a lean Role | Name | Relevant experience table over a fabricated Percent-Time column.

HEALTH / COALITION / STIGMA RFPs:
- Recovery Network of Oregon (RNO) is a near-direct KB proof point. Prefer it in references, previous experience, and case studies when the RFP asks for comparable health/coalition work. If absent, add [FLAG FOR SONJA: Add Recovery Network of Oregon…].

VERIFIED FACTS ONLY:
- Agency founded: 2013 (August 21, 2013). Years in operation = current calendar year − 2013 (13 in 2026).
- Certifications: WBENC, WOSB (ONLY these two are verified — never B Corp / B Corporation)
- E-Verify: NOT a verified company fact in KB — always [VERIFY] until Operations confirms
- Conflict disclosures: NOT auto-assertable — always [VERIFY] until Sonja/leadership confirms
- Client retention: DO NOT cite a specific average retention rate (not formally tracked)
- Awards: Creative Excellence 2024, Netty 2024, NYX 2024, Vega Digital 2024, Sonja's Enterprising Women 2026
- Team: ONLY use names from approved 04_Bio_*.pdf files in KB
- Insurance: Use [VERIFY: insurance field] for all coverage amounts and details except what's explicit in KB
- Clients: ONLY from 01_ClientList_Approved with Public=Yes for the claimed work type

IF YOU CANNOT VERIFY A FACT:
- Use [VERIFY: specific field — reason not found] instead of inventing
- If evidence says NO VERIFIED KB MATCH / evidence_trust_gate, insert that VERIFY/FLAG and continue other RFP requirements only
- Never use phrases like "approximately," "around," "over X years" without KB evidence
- Do not embellish or extrapolate from partial information
- Stick to THIS RFP's stated requirements and HARD FACTS block — do not pad with nearest-topic experience

CERTIFICATIONS & INSURANCE:
- Keep these sections SHORT and CONCISE
- List only verified certifications (WBENC, WOSB)
- For insurance: state coverage types only, use [VERIFY: amounts] for dollar figures
- Do not add platform certifications (Google Ads, Meta, etc.) unless they appear in verified KB

APPLY, NEVER NARRATE:
These rules govern how you write; they are never content. Never write sentences about
submission requirements, pass/fail status, what cannot be submitted, or what must be
verified or confirmed with anyone — the proposal is read by the client, not by you and
the model that wrote it. Apply the rule silently instead of restating it. When something
is missing or needs a human, emit exactly one tag — [MANUAL FILL: Sonja — <what is
needed>] or [VERIFY: <field> — <reason>] — and nothing else. Never explain the tag,
never preface it with a sentence about why it's there, never restate the rule that
produced it.
"""

DESIGNER_READY_BLOCK = """## DESIGNER-READY FORMAT (every section — mandatory)

wordTarget is a HARD CEILING. Designers paste tabs into InDesign — scannable structure, not pages they must cut.

**Concise layout ≠ missing requirements.** Every RFP ask for the tab must appear — use dense tables/Q&A rows to fit more substance in fewer words.

**Every tab:**
1. **Lead** — 1–3 tight sentences: what this tab proves.
2. **Body** — markdown tables, short bullets, or labeled rows matched to THIS tab's job (matrix, Q&A, references, phases, checklist). One row per RFP item when there are many asks. Same fact once — not in prose AND bullets.
3. **Visual handoff** — when layout beats prose (timeline, comparison, grid, icons): one specific [DESIGNER NOTE: …] with columns/data. Do not write paragraphs a graphic would replace.
4. **Complete then stop** — hit every RFP ask in compact form, then stop. No filler, no restating the RFP, no duplicating other tabs.

Never write multi-page essay blocks or repeated subsection walls (*Activities:* / *Deliverables:* under every heading). Tables + designer notes carry density.

## WRITE-TIME MINDFULNESS (cheaper to write right than to repair)

**Physically possible instructions only.** A table cell cannot contain a PDF. A cell holds
a value or a reference label ("See Attachment C") — never a directive to attach, insert,
or embed a document. Where the RFP requires an attached file, that belongs in the
submission checklist or the narrative, with the table carrying only the reference. Text
telling a reader to attach something inside a table is an instruction nobody can follow,
and it tells an evaluator the document was never read by its author.

**Designer notes must earn their place.** At most ONE per section, and only where a real
layout or production decision exists. A note must name a decision someone can act on
("two-column timeline, 4 phases, dates in left gutter"). Delete notes that restate the
obvious ("this table should be readable"). Over-noting trains the designer to skip all of
them, including the one that mattered.

**No slop.** Cut corporate filler that survives deletion with no loss of meaning, empty
transitions that announce what the next paragraph will say, adjective triads ("robust,
scalable, and innovative"), and sentences restating their own heading. A word is filler
only when removing it costs the reader nothing — "innovative" naming a specific method is
doing work; "innovative solutions" is not.

**Say it once.** A fact, statistic, or story belongs to exactly one tab — the one whose
job it is. Elsewhere, cross-reference it in a line rather than retelling it. Executive
summaries and compliance matrices are the deliberate exceptions.

**Grammar is mechanics only.** Fixing agreement, tense, or punctuation must never change a
number, name, date, or claim."""

# Backward-compatible alias for callers that still import MODULAR_APPROACH_BLOCK.
MODULAR_APPROACH_BLOCK = DESIGNER_READY_BLOCK


def is_modular_approach_section(title: str) -> bool:
    t = title.lower()
    return any(
        sig in t
        for sig in (
            "approach",
            "marketing plan",
            "work plan",
            "methodology",
            "scope of work",
            "project plan",
            "campaign plan",
        )
    )


def format_proof_points_block(
    proof_points: list[dict[str, Any]],
    *,
    section_id: str = "",
    section_title: str = "",
) -> str:
    if not proof_points:
        return ""

    relevant = proof_points
    if section_id:
        tagged = [
            p
            for p in proof_points
            if section_id in (p.get("sectionIds") or p.get("section_ids") or [])
        ]
        if tagged:
            relevant = tagged

    if not relevant:
        relevant = sorted(
            proof_points,
            key=lambda p: -(p.get("evaluationWeight") or p.get("evaluation_weight") or 0),
        )[:6]

    lines = [
        "## PROOF POINTS (lead with these, first person we/our)",
        "Use these verified case studies as 'why we win' evidence. Do not invent metrics.",
    ]
    for point in relevant[:8]:
        req = point.get("requirement") or ""
        case = point.get("caseStudy") or point.get("case_study") or ""
        hook = point.get("narrativeHook") or point.get("narrative_hook") or ""
        source = point.get("kbSource") or point.get("kb_source") or ""
        lines.append(f"- Requirement: {req}")
        lines.append(f"  Proof: {case} ({source})")
        if hook:
            lines.append(f"  Hook: {hook}")

    if section_title:
        lines.insert(1, f"Section: {section_title}")

    return "\n".join(lines)


def format_weight_priority_block(sections: list[dict[str, Any]]) -> str:
    weighted = [
        s
        for s in sections
        if (s.get("evaluationWeight") or s.get("evaluation_weight"))
    ]
    if not weighted:
        return ""

    ranked = sorted(
        weighted,
        key=lambda s: -(s.get("evaluationWeight") or s.get("evaluation_weight") or 0),
    )
    lines = [
        "## SCORING PRIORITY (draft highest-weight sections with deepest proof and detail)",
    ]
    for s in ranked[:8]:
        w = s.get("evaluationWeight") or s.get("evaluation_weight")
        title = s.get("title") or s.get("id")
        target = s.get("wordTarget") or s.get("word_target") or ""
        extra = f" — target ~{target} words" if target else ""
        lines.append(f"- {w}%: {title}{extra}")
    return "\n".join(lines)
