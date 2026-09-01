"""Phase 3: evidence-grounded drafting for RFP-mapped proposal sections."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models.proposal import EvidenceItem, ProposalBrandVoice, ProposalSection, RfpSectionMap, LossLesson
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.services.proposal_brand_voice import (
    classify_section_register,
    format_brand_voice_block,
    format_register_block,
)
from app.services.proposal_loss_lessons import format_avoidance_block
from app.services.proposal_drafting_prompts import (
    MODULAR_APPROACH_BLOCK,
    format_proof_points_block,
    format_weight_priority_block,
    DESIGNER_READY_BLOCK,
)
from app.services.proposal_voice_enforcement import (
    enforce_narrative_voice,
    is_duplicate_static_rfp_section,
    should_skip_rfp_section_as_static_duplicate,
)
from app.services.proposal_intelligence.log import log_intel_event
from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_draft_llm import (
    SECTION_DRAFT_FAILURE_PLACEHOLDER,
    chat_json_with_repair,
)
from app.services.proposal_langchain import _provider_name
from app.services.proposal_ralph import inject_ralph_into_system_prompt
from app.services.proposal_generation_cancel import ProposalGenerationCancelled

logger = logging.getLogger(__name__)

BATCH_SIZE = 1
DEFAULT_WORD_TARGET = 420
# Cap concurrent LLM calls within a single RFP's drafting run — created per
# invocation in run_drafting_graph so unrelated RFPs never wait on each other.
LLM_CONCURRENCY = 1


def _phase3_concurrency() -> int:
    """Parallel section drafting when fast_proposal_generation is enabled."""
    from app.core.config import settings

    if not settings.fast_proposal_generation:
        return 1
    return max(1, min(4, int(settings.phase3_llm_concurrency or 1)))


async def _draft_sections_parallel(
    *,
    state: DraftingGraphState,
    sections: list[dict[str, Any]],
    seed_prior: list[dict[str, Any]],
    concurrency: int,
) -> tuple[list[dict[str, Any]], str]:
    """Draft RFP tabs concurrently — each sees static seed prior only (fast path)."""
    sem = asyncio.Semaphore(concurrency)
    rfp_id = str(state.get("rfp_id") or "")
    provider = str(state.get("provider") or _provider_name())
    results_by_index: dict[int, dict[str, Any]] = {}

    async def draft_one(index: int, section: dict[str, Any]) -> None:
        nonlocal provider
        async with sem:
            if rfp_id:
                from app.services.proposal_generation_cancel import check_generation_cancelled

                await check_generation_cancelled(rfp_id)
            sec_title = str(section.get("title") or section.get("id") or "Section")
            if rfp_id:
                from app.services.proposal_pipeline_checkpoint import record_pipeline_activity

                await record_pipeline_activity(
                    rfp_id,
                    label=f"Drafting: {sec_title}",
                    detail=f"Parallel tab draft (concurrency={concurrency}).",
                    step_index=index + 1,
                    step_total=len(sections),
                )
            batch_state = {**state, "drafted_sections": seed_prior}
            try:
                batch_results, batch_provider = await _draft_batch([section], batch_state)
                provider = batch_provider
                if batch_results:
                    results_by_index[index] = batch_results[0]
            except ProposalGenerationCancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Phase 3 parallel section failed for %s (%s): %s",
                    rfp_id,
                    section.get("id"),
                    exc,
                )
                results_by_index[index] = {
                    "id": section.get("id"),
                    "title": section.get("title"),
                    "source": "rfp",
                    "mode": section.get("zoMode") or "write",
                    "content": SECTION_DRAFT_FAILURE_PLACEHOLDER,
                    "status": "outline",
                    "kbRefs": [],
                }

    await asyncio.gather(*(draft_one(i, s) for i, s in enumerate(sections)))
    ordered = [results_by_index[i] for i in range(len(sections)) if i in results_by_index]
    return ordered, provider

SectionDraftedCallback = Callable[[list["ProposalSection"], str], Awaitable[None]]
_SECTION_DRAFT_CALLBACKS: dict[str, SectionDraftedCallback] = {}

DRAFT_BATCH_PROMPT = """You draft zö agency proposal section content for a government/commercial RFP response.

## CRITICAL: ANTI-HALLUCINATION RULES (ENFORCE STRICTLY) — DO NOT RELAX THESE

YOU MUST NEVER:
1. Invent statistics (retention rates, client counts, audience sizes, years of experience)
2. Cite specific numbers unless they appear VERBATIM in the evidence corpus with [E#] citation OR are stated in the RFP requirements / Proposal Execution Plan budget intel (e.g. RFP not-to-exceed / annual media spend)
3. Use team member names not in approved bio files (04_Bio_*.pdf in evidence)
4. Add certifications not explicitly in 01_companyfacts_verified evidence
5. Transfer metrics from one client project to describe agency-wide capabilities
6. Round or approximate numbers - use exact figures from evidence or [VERIFY: field]
7. Spell names incorrectly (check exact spelling in bio file evidence)
8. Claim "X years of Y experience" unless that exact phrasing is in verified evidence
9. Invent agency hourly rates, fee tables, or markups not grounded in 00_Guide_Pricing evidence / pricing plan
10. Invent percent-time / FTE / "X% of their time" allocations for named people (e.g. 10%/35%/25%) — omit the column if the RFP does not require it; if required use [VERIFY: percent time] only

VERIFIED FACTS ONLY (from evidence corpus):
- Agency: founded August 21, 2013; years in operation = current year − 2013 (13 in 2026). Never say a different year count than Business Information.
- Certifications: WBENC, WOSB ONLY (no B Corp, MBE/DBE, platform certs, or invented badges)
- Client retention: NEVER cite specific retention rate (not formally tracked per verified facts)
- Awards: Creative Excellence 2024, Netty 2024, NYX 2024, Vega Digital 2024, Sonja's Enterprising Women 2026
- Team: ONLY names from 04_Bio_*.pdf files in evidence
- Insurance/Certifications: Keep SHORT and CONCISE, list coverage types only, use [VERIFY: amounts] for dollar figures
- Percent-time / FTE: NOT a verified KB fact for pursuit staffing — never invent; omit or [VERIFY]

IF YOU CANNOT VERIFY A COMPANY FACT IN EVIDENCE:
- Use [VERIFY: specific field needed] instead of inventing — BUT only when THIS RFP
  explicitly requires that exact fact (forms, scored compliance, named contacts).
- If the RFP does NOT require the missing detail, OMIT it — do not sprinkle optional
  [VERIFY] tags for partners, backups, dashboards, or nice-to-have names.
- Never invent phones, emails, rates, clients, wins, or certs to "fill" a gap.
- Never use "approximately," "around," "over X years" without evidence citation
- Do not embellish or extrapolate from partial information

ALLOWED WITHOUT inventing company facts (plan-driven structure):
- Restate RFP requirements, goals, constraints, and stated spend ceilings from the section requirements / Opportunity Understanding / Proposal Memory
- Describe methodology phases, timeline logic, governance cadence, and persuasion structure from Delivery Plan + Winning Pattern
- For Budget narrative: use transparency/pass-through language + 00_Guide_Pricing excerpts when present; defer invented role-hour fee tables to Phase 3.5
- NEVER return empty content for Understanding / Methodology / Timeline / Budget — write the section using plan + RFP requirements, and [VERIFY] only discrete missing facts

Rules (strict):
1. Never invent unverified company facts (metrics, clients, certifications, team members, contract awards). Those require evidence [E#] or [VERIFY].
2. Use ONLY facts from the evidence corpus. Do NOT insert markers like [E1] or [E2] in the written proposal — keep the prose client-ready.
3. For requirements not covered by evidence, write [VERIFY: describe what must be confirmed] ONLY for the missing fact — prefer citing [E#] when any excerpt partially answers. Do not blank the whole section.
4. For template/layout pulls (zoMode pull/select), include [DESIGNER NOTE: ...] and reference evidence.
5. Match the BRAND VOICE and REGISTER blocks for each section.
6. NARRATIVE sections (register=narrative): first person we/our — NEVER "The Vendor", "The Offeror", or third-person agency distance. RFP form language does not apply to narrative prose.
7. PROCUREMENT sections (register=procurement): formal third-person Vendor/Offeror language is OK for attachments, forms, and compliance tables.
8. DESIGNER-READY: every tab uses short lead + tables/bullets/rows + [DESIGNER NOTE] for layout-heavy content — never multi-page essay prose.
9. Apply WRITING AVOIDANCES from lost bids/debriefs when provided — do not repeat patterns that caused past losses.
10. Lead narrative sections with PROOF POINTS — specific case studies tied to requirements ("why we win").
11. Highest evaluationWeight sections need the strongest proof — still stay at or under wordTarget (hard ceiling). Scannable layout beats long prose.
13. For non-Budget narrative sections: do NOT invent pricing tiers, agency fee tables, phase-by-phase dollar breakdowns, disbursement schedules, or lump-sum totals — those belong in Fees/Budget / Phase 3.5 only. Never embed a markdown table with Phase | Amount columns outside the Budget tab. You may still mention the RFP's stated media budget if it appears in requirements/plan (e.g. $200,000 annual).
14. When RFP requires portfolio, writing samples, or reference contacts, use evidence excerpts with [E#] citations — do not leave passive VERIFY placeholders if evidence contains samples or contacts.
15. NEVER defer required submission data to unnamed attachments or "upon request" — include reference phones, workforce %, hours tables, or PSA acknowledgments in the proposal body.
16. References: when RFP requires contact names and phone numbers, include ONLY clients whose name, title, organization, phone, AND email are present in KB evidence. If any of those fields are missing, OMIT that reference entirely — never invent an org shell with [VERIFY: contact/phone/email] rows.
17. Personnel: when RFP requires workforce diversity data, state headcount and minority/female percentages (from KB) or [VERIFY].
18. Budget section: when RFP requires staff hours per task, add hours table OR commission-model explanation with transparency estimates.
19. PSA/contract items in the RFP (insurance, living wage, MacBride, Title VI, audit rights, etc.) need brief acknowledgment sentences in the proposal.
20. References: NEVER "contact on request", "upon request", "available upon request", or "through the Bureau". NEVER invent Travel Oregon / Visit Bend / generic tourism refs. Prefer fewer complete KB references over incomplete VERIFY shells. If KB has fewer contacts than the RFP asks, list the complete ones you have and add one [MANUAL FILL: Sonja — remaining references from ClientList] — do not pad with VERIFY rows. Never claim references were "pre-cleared" or "agreed to respond" unless KB evidence says so.
21. Workforce: MWBE/diversity and Project Personnel sections must use identical headcount and % female/minority — one precise figure from HR/KB.
22. Budget SUMMARY line-item fee tables (agency fees by role/hour) are built in Phase 3.5. Do NOT invent those tables or $0 placeholders. NEVER refuse to write the Budget section itself.
23. Insurance RFPs: include a limits table (RFP requires | current policy | gap | bind-before-execution action) with ACORD fields when specified. NEVER mark Exception / compliance form rows "Compliant" or assert "meets or exceeds" RFP insurance minimums unless that coverage type AND limit already appear in Section 1.5 / companyfacts evidence — otherwise use [MANUAL FILL: Sonja — confirm on COI] or take a real exception / bind-before-execution commitment.
24. Vendor/contractor questionnaires: complete every field — FEIN, phones, email, DUNS/CAGE or N/A — from KB; never leave TBD or blank underscores.
25. NJ or geography-specific reference RFPs: use verified KB contacts; if no in-state client exists, disclose geography honestly — never [PLACEHOLDER] reference rows.
26. Project management fees must stay within 5–8% of agency fees — do not leave unresolved PM ratio flags in budget prose.
27. Address every Phase 2 uncovered requirement explicitly — compliance tables, forms, or narrative; do not assume a titled section alone satisfies the RFP.
28. If a Winning Pattern is provided, use it only for structure, flow, tone, visuals, and persuasion strategy. Never copy, paraphrase, or cite prior won proposal prose.
29. Plan-driven narrative sections (Understanding / Methodology / Timeline / Budget overview) MUST be drafted even when evidence is thin or empty. Use RFP requirements, Opportunity Understanding, Section Strategy, Winning Pattern, and Proposal Memory. Cite [E#] only when evidence exists; do not refuse to write the whole section. Use [VERIFY: specific field] only for discrete missing facts, never as the entire section body.
30. Understanding sections should restate the client's goals, constraints, audiences, success measures, and risks in zö voice before pitching solution — show we read the RFP carefully.
31. When the section title is Budget / Pricing / Fees / Cost: you MUST write full narrative covering (a) transparent compensation philosophy, (b) pass-through / no hidden media markup commitment, (c) how media spend is allocated across RFP priorities with rationale, (d) that detailed agency fee tables follow in the pricing build. Ground compensation language in 00_Guide_Pricing evidence when present. Use RFP-stated spend amounts from requirements/plan. Leave only discrete unknown agency rate cells as [VERIFY: …], never blank the whole section. If the RFP forbids altering the official Quotation/Pricing Proposal Form, do NOT restructure the form into Section A/B/C/D — mirror the buyer's field labels only and put all rationale in a separate "Supporting Budget Rationale" section.
32. Do NOT invent dashboards, reporting diagrams, org charts, timeline graphics, or "see attached" visuals. Describe reporting cadence in prose unless KB evidence / RFP-required template exists.
33. ANTI-DUPLICATION: Each section has ONE job. Do not re-write Who We Are, full bios, full case studies, FEIN/address/certs, or brand story that belongs in Sections 1–3 or another RFP tab. Do not paraphrase another RFP tab (Approach≠Methodology rewrite; Past Performance≠Sample Work dump). One brief cross-reference is OK — then add NEW RFP-specific detail only. NEVER replace an entire scored RFP tab with only "see Section 1" / "Sections 1.1–1.5 below" pointer text — evaluators read each tab separately; substance is required even when long. Prefer concise, concrete prose within wordTarget — no generic agency marketing filler. Offeror / Vendor / Company Identification forms: ONE short FIELD|RESPONSE table synced from Section 1.3 Business Information + a one-line cross-reference — NEVER a second full company profile / Who We Are dump.
34. LENGTH (Ralph): wordTarget is a HARD CEILING. Hit the scored RFP asks, then stop. Never write extra pages "for the designer to cut later." Dense and short beats long and repetitive.
35. References sections: restate the RFP's required reference count and institution type when the RFP specifies them. Never claim the RFP is silent on references if requirements list three customers, two-year public, or NJ public-college reference tables. Include ONLY references with full KB contact fields. If zö lacks enough qualifying references, state the gap honestly with [MANUAL FILL: leadership decision] — do not invent orgs and do not pad with [VERIFY: phone/email] shells.
36. KPI scope: When the RFP distinguishes agency-wide/strategic-plan KPIs from CONTRACTOR-scored KPIs, commit ONLY to the contractor set (with numeric targets from Section 2 / monitoring). Never substitute the buyer's four agency KPIs for the three contractor KPIs.
37. Cost scoring: If the RFP uses inverse cost scoring (lowest responsive price gets maximum cost points), never claim that bidding at the published ceiling earns the highest cost rating — state the tradeoff honestly.
38. Never invent an RFP "ceiling/allocation/cap" equal to your own proposed bid total. Only cite spend ceilings that appear in RFP requirements / HARD FACTS money constraints. If the bid exceeds a stated RFP envelope, say so plainly or leave a [VERIFY] for Sonja — do not relabel the bid as the buyer's ceiling.
39. Cost weight: Use the RFP's stated criteria points for cost/price (sum Criteria #4 + #5 when both exist) — do not round to a generic "10%". When cost/price is ≥25% of total points, narrative must not claim Average tier — Low tier is required by the Pricing Guide Decision Guide.
40. Budget container: When the RFP requires Attachment 01 / Excel budget worksheet, the narrative budget section must point to that file — not replace it with a PDF cost-category table.
41. HOURLY RATES: Never invent individual ZO member $/hr. If a staff-hours table is required, use labor-category / work rates from 00_Guide_Pricing evidence, or [VERIFY: hourly rate — {role}]. namedPerson is a staffing note only.
42. PERCENT-TIME / FTE: Never invent percent-time columns or reuse static % grids from other proposals. If the RFP does not require percent-time/FTE, omit that column entirely (Role | Name | experience only). If the RFP requires it, every cell is [VERIFY: percent time] — never invent 10%/35%/25%/25-30%.
43. CASE STUDIES / PAST WORK: Keep the REAL project name and what the engagement was (e.g. Rock the Locks Festival). NEVER rewrite a verified case study into a generic "municipal communications / community outreach" story the source does not support. Cover Challenge (≤40 words) and Solution (≤50 words) only, facts staying faithful to evidence [E#]. If the evidence contains a client quote, include it verbatim as Client Voice (quotation marks, speaker name/title if given) — never paraphrase or invent one. Do not add a Results/KPI/metrics list or a separate "Why Relevant" section. Prefer 2–3 strong RFP-relevant studies over a long gallery of weak/adjacent ones. NEVER assert past technical deliveries (specific platforms, integrations, audit workflows) that the included case studies / bios / companyfacts do not evidence — use adjacent verified experience or [VERIFY].
44. FIRST-PASS COMPLETENESS: Address EVERY scored/required ask for THIS section — no "details to follow." Prefer dense, scannable designer-ready answers (tables/bullets) over essay walls or thin stubs. One [VERIFY: …] per missing discrete fact only.
45. SCHEDULE / TIMELINE: Fit award→launch / contract windows stated in the RFP. Dates and milestones in a markdown pipe table (| Phase | Activities | Timing |) — methodology lives in Approach. Every Timing cell must have a week-from-award range. Never leave Timing blank. Never put spaces between every letter in headers (write PHASE not P H A S E). Never put | between individual letters. Use 4–6 columns max; wrap long cell text with normal sentences, not line breaks mid-row. Never put writer instructions in the tab ("do not restate…"). Missing calendar dates from the RFP → weeks from award, not [VERIFY] tags.
46. COVER LETTER / TRANSMITTAL: If the RFP requires a physically signed cover letter or letter of transmittal, write the short offer letter AND set designerNote (or an inline [DESIGNER NOTE: …]) to attach the signed PDF separately. Do not claim the signed file is attached. Do not invent signature dates, notary numbers, or stamp IDs.
47. Concise ≠ incomplete: hit every RFP ask for the section, then STOP. No filler, no duplicated Sections 1–3, no Approach essay pasted into Schedule.
48. TEAM / SPECIALIST ROLES: Only name roles that map to a real Section 2 bio person, or label them [MANUAL FILL: subcontractor / generalist coverage]. Never invent dedicated specialist titles with no matching named person on the roster.
49. LEGAL ATTESTATIONS (higher bar than ordinary claims): NEVER state E-Verify / Contractor Affidavit enrollment, participation in a good-faith-effort / DVBE / MWBE vendor-outreach waiver, mandatory-conference attendance, "no conflicts of interest," or any other fact sworn under penalty of perjury as settled unless it is in evidence. Use [VERIFY: field — reason] instead, even when surrounding form language pressures you to fill every field. Do NOT invent names, phone numbers, or emails to complete a vendor/subcontractor outreach or good-faith-effort contact list — [VERIFY] each missing contact individually; never fabricate a plausible-looking one to avoid a blank field.
50. APPLY, NEVER NARRATE: these rules govern how you write; they are never content. Never write a sentence ABOUT what must be verified, confirmed, or could jeopardize the bid — that is reasoning for you, not prose for the evaluator, who reads only the proposal. Apply the rule silently: emit just the [VERIFY: ...] or [MANUAL FILL: ...] tag, with no sentence explaining why it's there.

Return ONLY JSON:
{
  "sections": [
    {
      "sectionId": "rfp-sec-1",
      "content": "full section prose with [E#] citations",
      "kbRefs": ["E1", "E3"],
      "designerNote": "optional layout note or null"
    }
  ]
}"""

# Append Ralph fidelity block once at module load.
DRAFT_BATCH_PROMPT = inject_ralph_into_system_prompt(DRAFT_BATCH_PROMPT)


class DraftingGraphState(TypedDict, total=False):
    rfp_id: str
    rfp_title: str
    rfp_client: str
    rfp_sector: str
    rfp_location: str | None
    rfp_context: str
    rfp_due_date: str | None
    rfp_sections: list[dict[str, Any]]
    evidence_corpus: list[dict[str, Any]]
    execution_plan: dict[str, Any] | None
    brand_voice: dict[str, Any]
    zo_sections_context: str
    writing_avoidances: list[str]
    loss_lessons: list[dict[str, Any]]
    proof_points: list[dict[str, Any]]
    manuscript_locks: dict[str, Any] | None
    fact_ledger: dict[str, Any] | None
    evidence_allocation: dict[str, Any] | None
    drafted_sections: list[dict[str, Any]]
    provider: str
    error: str | None
    llm_semaphore: asyncio.Semaphore


# Matches the estimator in proposal_presubmit_review so the budget enforced at
# generation and the page count checked at review agree.
WORDS_PER_PAGE = 350
# Below this a section cannot say anything useful, so the allocator never
# starves one to feed another; it shrinks everything proportionally instead.
MIN_SECTION_WORDS = 150
# Hard floor. If even this cannot be met the outline itself is too large for the
# page limit — that is a planning problem (too many sections), not something a
# word budget can solve, so the allocator returns this and lets the caller warn.
ABSOLUTE_MIN_SECTION_WORDS = 50


def allocate_word_budget(
    natural_targets: list[int],
    budget: int | None,
    *,
    floor: int = MIN_SECTION_WORDS,
) -> list[int]:
    """Scale per-section word targets to fit a document-level budget.

    Without this, each section takes its natural target independently and the
    manuscript overshoots the RFP page limit by a multiple — ~21 sections at the
    800-word default is ~16,800 words against a 12-page (4,200-word) cap. The
    overshoot is only detected after every section has been paid for.

    Relative emphasis is preserved: sections keep their share of the headroom
    above ``floor``, so evaluation-weighted sections stay proportionally longer.

    The sum fits ``budget`` except when the outline is too large for it to be
    possible — more sections than ``budget // ABSOLUTE_MIN_SECTION_WORDS``. In
    that case every section gets ``ABSOLUTE_MIN_SECTION_WORDS`` and the total
    exceeds budget on purpose: the fix is to cut sections, not to emit stubs
    too short to say anything.
    """
    count = len(natural_targets)
    if count == 0:
        return []
    if not budget or budget <= 0:
        return list(natural_targets)

    total = sum(natural_targets)
    if total <= budget:
        return list(natural_targets)

    # Budget too tight to give everyone the floor — split evenly, never below
    # the hard floor. May exceed budget; see the docstring.
    if floor * count >= budget:
        return [max(ABSOLUTE_MIN_SECTION_WORDS, budget // count)] * count

    headroom = budget - floor * count
    natural_headroom = total - floor * count
    if natural_headroom <= 0:
        return [budget // count] * count

    out: list[int] = []
    for target in natural_targets:
        above_floor = max(0, target - floor)
        out.append(floor + int(headroom * (above_floor / natural_headroom)))
    return out


def allocate_words_by_points(
    requirements: list[LedgerRequirement],
    budget: int | None,
    *,
    floor: int = MIN_SECTION_WORDS,
) -> dict[str, int]:
    """Split a word budget across mandatory ledger requirements weighted by
    ``points``, so a 30-point scored criterion gets proportionally more room
    than a 10-point one — and boilerplate requirements with no points get
    none of the headroom above the floor.

    This is the deterministic ledger signal (Task 1-3's ``RequirementLedger``)
    rather than the LLM-estimated ``evaluationWeight`` on ``RfpSectionMap``,
    which may be absent or noisy. Mirrors ``allocate_word_budget``'s
    fail-safe contract:

    - No requirements -> ``{}``.
    - No/zero budget -> every mandatory requirement gets ``floor`` words
      (a neutral default, not a crash).
    - No points anywhere (or they sum to zero) -> even split across the
      mandatory requirements, never a divide-by-zero.
    - Budget too tight for everyone to clear the floor -> even split at
      (at least) the absolute floor, same as ``allocate_word_budget``.
    """
    mandatory = [r for r in requirements if r.mandatory]
    count = len(mandatory)
    if count == 0:
        return {}

    if not budget or budget <= 0:
        return {r.id: floor for r in mandatory}

    if floor * count >= budget:
        share = max(ABSOLUTE_MIN_SECTION_WORDS, budget // count)
        return {r.id: share for r in mandatory}

    total_points = sum(r.points or 0.0 for r in mandatory)
    if total_points <= 0:
        share = budget // count
        return {r.id: share for r in mandatory}

    headroom = budget - floor * count
    out: dict[str, int] = {}
    for r in mandatory:
        weight = (r.points or 0.0) / total_points
        out[r.id] = floor + int(headroom * weight)
    return out


def section_weights_from_ledger(
    ledger: RequirementLedger | None,
    budget: int | None,
) -> dict[str, int]:
    """Aggregate ``allocate_words_by_points`` onto the section(s) each
    requirement is satisfied by (``LedgerRequirement.satisfied_by``), so a
    section carrying high-point requirements can be weighted heavier than a
    boilerplate section the ledger never scored. A section satisfying more
    than one requirement sums their shares. Never raises: an empty/missing
    ledger just yields no weights, leaving section targets exactly as before.
    """
    if ledger is None or not ledger.requirements:
        return {}
    per_requirement = allocate_words_by_points(ledger.requirements, budget)
    out: dict[str, int] = {}
    for requirement in ledger.requirements:
        words = per_requirement.get(requirement.id)
        if not words:
            continue
        for section_id in requirement.satisfied_by:
            out[section_id] = out.get(section_id, 0) + words
    return out


def _word_target(section: dict[str, Any]) -> int:
    # An allocator-assigned target wins: it already accounts for the document
    # page limit and the other sections competing for it.
    assigned = section.get("wordTarget") or section.get("word_target")
    if isinstance(assigned, int) and assigned > 0:
        return assigned
    page_limit = section.get("pageLimit") or section.get("page_limit")
    if isinstance(page_limit, int) and page_limit > 0:
        # Cap so page limits do not explode into unreadably long tabs.
        return min(1200, max(350, page_limit * 300))
    weight = section.get("evaluationWeight") or section.get("evaluation_weight")
    if isinstance(weight, (int, float)) and weight > 0:
        w = int(weight)
        if w >= 30:
            return min(900, max(750, w * 32))
        if w >= 20:
            return min(750, max(580, w * 30))
        if w >= 10:
            return min(580, max(420, w * 28))
        return min(480, max(350, w * 26))
    return DEFAULT_WORD_TARGET


def _section_weight(section: dict[str, Any]) -> int:
    weight = section.get("evaluationWeight") or section.get("evaluation_weight")
    if isinstance(weight, (int, float)) and weight > 0:
        return int(weight)
    return 0


def order_sections_for_phase3_draft(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve Phase 2 outline/manuscript order for drafting sequence.

    Evaluation weight still drives word targets and prompt depth, but must not
    reorder drafting — weight-first left form/compliance tabs (weight 0) empty
    while later scored tabs filled, desyncing the UI from generation progress.
    """
    return list(sections)


def _phase3_content_is_usable(content: str | None) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if text == SECTION_DRAFT_FAILURE_PLACEHOLDER.strip():
        return False
    return True


def _static_coverage_stub(title: str) -> str:
    """Short pointer when an RFP tab is owned by Sections 1–3."""
    title_cf = (title or "").casefold()
    if re.search(
        r"certificate(?:s)?\s+of\s+insurance|proof\s+of\s+insurance|"
        r"insurance\s+certificate|\binsurance\b|\bcoi\b",
        title_cf,
    ):
        return (
            f"## {title}\n\n"
            "Coverage types, carriers, and limits are stated in "
            "**Section 1.5 — Insurance Information**. "
            "Upon contract award we will deliver the certificate of insurance "
            "(additional insured / endorsements as the RFP requires) as a "
            "separate PDF attachment — [MANUAL FILL: attach COI PDF]. "
            "This tab does not restate policy narrative."
        )
    return (
        f"## {title}\n\n"
        "Covered in Sections 1–3 (company / team / experience). "
        "See those sections for the full response; this tab is "
        "retained so the RFP outline remains complete."
    )


def partition_phase3_sections(
    rfp_sections: list[RfpSectionMap],
    existing_by_id: dict[str, ProposalSection],
    *,
    static_section_text: str = "",
) -> tuple[list[RfpSectionMap], list[ProposalSection]]:
    """Split mapped sections into ones still needing a draft vs already filled."""
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    to_draft: list[RfpSectionMap] = []
    already: list[ProposalSection] = []
    filled_by_title: list[ProposalSection] = [
        section
        for section in existing_by_id.values()
        if _phase3_content_is_usable(section.content)
    ]

    for mapped in rfp_sections:
        # Never omit Cost/Fees/Budget or scored tabs — RFP-demanded coverage.
        if should_skip_rfp_section_as_static_duplicate(
            title=mapped.title or "",
            duplicate_of_static_section=mapped.duplicate_of_static_section,
            evaluation_weight=mapped.evaluation_weight,
            static_section_text=static_section_text,
        ):
            # Still leave a short pointer stub so the tab is not missing from
            # the manuscript when Phase 2 tagged it as static-covered.
            if mapped.id not in existing_by_id:
                already.append(
                    ProposalSection(
                        id=mapped.id,
                        title=mapped.title,
                        content=_static_coverage_stub(mapped.title or ""),
                        status="generated",
                        source="generated",
                        mode="write",
                        required=True,
                    )
                )
            elif existing_by_id.get(mapped.id) and _phase3_content_is_usable(
                existing_by_id[mapped.id].content
            ):
                already.append(existing_by_id[mapped.id])
            continue

        existing = existing_by_id.get(mapped.id)
        if existing and _phase3_content_is_usable(existing.content):
            already.append(existing)
            continue

        # Different id, same ask — do not draft a second Letter of Interest /
        # Qualifications twin when a filled near-dup already exists.
        twin = next(
            (
                section
                for section in filled_by_title
                if outline_titles_near_duplicate(mapped.title or "", section.title or "")
            ),
            None,
        )
        if twin is not None:
            if twin.id not in {s.id for s in already}:
                already.append(twin)
            logger.info(
                "Phase 3 skipping near-dup tab %r (covered by filled %r)",
                mapped.title,
                twin.title,
            )
            continue

        to_draft.append(mapped)
    return to_draft, already


def _evidence_for_section(
    section_id: str,
    corpus: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tagged = [
        item
        for item in corpus
        if section_id in (item.get("sectionIds") or item.get("section_ids") or [])
    ]
    if tagged:
        return tagged[:12]
    return corpus[:10]


def _format_evidence_block(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        eid = item.get("id", "?")
        source = item.get("source", "document")
        excerpt = str(item.get("excerpt", ""))[:1000]
        lines.append(f"[{eid}] {source}\n{excerpt}")
    return "\n\n".join(lines) if lines else "(No evidence items tagged for this section.)"


def _brand_voice_block(
    brand_voice: dict[str, Any] | None,
    *,
    register: str = "narrative",
    rfp_client: str = "",
) -> str:
    reg = "procurement" if register == "procurement" else "narrative"
    return format_brand_voice_block(
        brand_voice,
        rfp_client=rfp_client,
        register=reg,  # type: ignore[arg-type]
    )


def _chunk_sections(sections: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [sections[i : i + size] for i in range(0, len(sections), size)]


def _extract_kb_refs(content: str, declared: list[str] | None) -> list[str]:
    """Extract evidence citations from content. KB references removed - returns empty list."""
    # KB references are no longer included in proposals
    return []


def _plan_section_brief(state: DraftingGraphState, section_id: str) -> dict[str, Any] | None:
    plan = state.get("execution_plan") or {}
    writing = plan.get("writing") or {}
    plans = (writing.get("sectionPlans") or {}).get("plans") or []
    for item in plans:
        if isinstance(item, dict) and str(item.get("sectionId") or "") == section_id:
            return item
    return None


def _plan_retrieval_entry(state: DraftingGraphState, section_id: str) -> dict[str, Any] | None:
    plan = state.get("execution_plan") or {}
    writing = plan.get("writing") or {}
    entries = (writing.get("retrievalPlan") or {}).get("entries") or []
    for item in entries:
        if isinstance(item, dict) and str(item.get("sectionId") or "") == section_id:
            return item
    return None


_PLAN_DRIVEN_TITLE_HINTS = (
    "cover letter",
    "executive summary",
    "understanding",
    "challenge",
    "requirement",
    "methodology",
    "approach",
    "process",
    "timeline",
    "schedule",
    "work plan",
    "project plan",
    "budget",
    "pricing",
    "fees",
    "cost",
    "qualification",
    "relevant experience",
    "firm experience",
    "team experience",
    "past performance",
    "similar project",
)


_WHOLE_SECTION_VERIFY_RE = re.compile(
    r"^\[VERIFY:\s*Draft content for .+ — (?:insufficient evidence in corpus|writer returned empty prose)",
    re.I | re.S,
)


def _is_qualifications_narrative(title: str) -> bool:
    lower = (title or "").strip().lower()
    return any(
        hint in lower
        for hint in (
            "qualification",
            "relevant experience",
            "offeror qualification",
            "vendor qualification",
            "firm experience",
        )
    )


def _is_plan_driven_narrative(*, title: str, register: str) -> bool:
    if register != "narrative":
        return False
    lower = (title or "").strip().lower()
    if _is_qualifications_narrative(title):
        return True
    return any(hint in lower for hint in _PLAN_DRIVEN_TITLE_HINTS)


def _is_whole_section_verify_placeholder(content: str) -> bool:
    return bool(_WHOLE_SECTION_VERIFY_RE.match((content or "").strip()))


def _section_prose_missing(content: str) -> bool:
    stripped = (content or "").strip()
    return not stripped or _is_whole_section_verify_placeholder(stripped)


def _looks_truncated_prose(content: str) -> bool:
    """Detect mid-sentence cutoffs from max-output token limits.

    Must check the string's actual ENDING, not "anywhere in the last N
    chars" — an unanchored search treats an earlier, complete sentence that
    happens to fall inside that window as proof the string isn't truncated,
    even when its real last characters are a dangling clause.
    """
    stripped = (content or "").rstrip()
    if len(stripped) < 350:
        return False
    if stripped.endswith("]"):
        return False
    # A closing quote/paren/markdown-emphasis mark after the real terminal
    # punctuation still counts as a complete sentence.
    end = stripped.rstrip("\"')*_")
    return not end.endswith((".", "!", "?"))


def _empty_draft_fallback(
    *,
    title: str,
    register: str,
    requirements: list[Any] | None,
    has_plan_context: bool,
) -> str:
    reqs = [str(r) for r in (requirements or [])[:3]]
    req_tail = f" Requirements: {'; '.join(reqs)}" if reqs else ""
    if _is_plan_driven_narrative(title=title, register=register) and has_plan_context:
        return (
            f"[VERIFY: Draft content for {title} — writer returned empty prose; "
            f"re-run Phase 3 for this section using plan/winning-pattern context.{req_tail}]"
        )
    return (
        f"[VERIFY: Draft content for {title} — "
        f"insufficient evidence in corpus.{req_tail}]"
    )


def _format_plan_context(state: DraftingGraphState, section_id: str) -> str:
    plan = state.get("execution_plan") or {}
    if not plan:
        return ""
    lines: list[str] = []
    memory = (plan.get("proposalMemory") or {}).get("facts") or {}
    if memory:
        lines.append("Proposal Memory (normalized facts — prefer these):")
        lines.append(json.dumps(memory, indent=2)[:3000])
    understanding = (plan.get("opportunity") or {}).get("understanding") or {}
    if isinstance(understanding, dict) and any(understanding.values()):
        lines.append("Opportunity Understanding (restate in zö voice; do not invent facts):")
        lines.append(json.dumps(understanding, indent=2)[:3000])
    brief = _plan_section_brief(state, section_id)
    section_title = ""
    for mapped in state.get("rfp_sections") or []:
        mid = getattr(mapped, "id", None) or (
            mapped.get("id") if isinstance(mapped, dict) else None
        )
        if str(mid or "") == str(section_id):
            section_title = str(
                getattr(mapped, "title", None)
                or (mapped.get("title") if isinstance(mapped, dict) else "")
                or ""
            )
            break
    if brief:
        lines.append("Section Strategy (explain the plan — do not invent methodology/budget):")
        lines.append(
            json.dumps(
                {
                    "purpose": brief.get("purpose"),
                    "keyMessages": brief.get("keyMessages"),
                    "writerInstructions": brief.get("writerInstructions"),
                    "successDefinition": brief.get("successDefinition"),
                    "wordBudget": brief.get("wordBudget"),
                    "tone": brief.get("tone"),
                },
                indent=2,
            )
        )
        winning_pattern = brief.get("winningPattern") or {}
        if isinstance(winning_pattern, dict) and any(winning_pattern.values()):
            lines.append(
                "Winning Pattern (structure and persuasion guidance only — "
                "Do not copy prior proposal prose):"
            )
            lines.append(json.dumps(winning_pattern, indent=2)[:3000])
    strategy = (plan.get("opportunity") or {}).get("strategy") or {}
    if strategy.get("winningTheme") or strategy.get("whyUs"):
        lines.append(
            "Opportunity strategy themes:\n"
            f"- winningTheme: {strategy.get('winningTheme')}\n"
            f"- whyUs: {strategy.get('whyUs')}"
        )
    methodology = (plan.get("delivery") or {}).get("methodology") or {}
    if isinstance(methodology, dict) and (methodology.get("phases") or methodology.get("confidence")):
        lines.append(
            "Delivery Methodology Plan (explain this structure in zö voice — do not invent phases):"
        )
        lines.append(json.dumps(methodology, indent=2)[:3000])
    timeline = (plan.get("delivery") or {}).get("timeline") or {}
    if isinstance(timeline, dict) and any(timeline.values()):
        lines.append(
            "Delivery Timeline Plan (use for Schedule tabs — dates/milestones only; "
            "do not restate Approach methodology paragraphs):"
        )
        lines.append(json.dumps(timeline, indent=2)[:2500])
    budget_plan = (plan.get("delivery") or {}).get("budget") or {}
    if isinstance(budget_plan, dict) and any(budget_plan.values()):
        lines.append(
            "Delivery Budget Plan (use for Budget narrative — transparency/model/allocation; "
            "do not invent role-hour fee tables):"
        )
        lines.append(json.dumps(budget_plan, indent=2)[:3000])

    try:
        from app.services.proposal_consistency_enforcement import (
            format_rfp_calendar_constraint,
        )

        cal = format_rfp_calendar_constraint(
            rfp_due_date=str(state.get("rfp_due_date") or "") or None,
            rfp_context_excerpt=str(state.get("rfp_context") or "")[:12000],
            section_title=section_title or str(section_id),
        )
        if cal:
            lines.append(cal)
    except Exception:
        pass

    return "\n\n".join(lines)


async def _retry_plan_driven_section(
    section: dict[str, Any],
    state: DraftingGraphState,
    *,
    payload: dict[str, Any],
    max_tokens: int = 16000,
    reason: str = "empty",
) -> dict[str, Any] | None:
    """One focused retry when a plan-driven narrative section fails or truncates."""
    sid = str(section.get("id") or "")
    title = str(section.get("title") or sid)
    plan_ctx = str(payload.get("planContext") or _format_plan_context(state, sid)).strip()
    retry_user = (
        f"Client: {state.get('rfp_client')}\n"
        f"RFP: {state.get('rfp_title')}\n\n"
        f"Draft ONLY this narrative section now. Return non-empty prose.\n"
        f"Retry reason: {reason}.\n"
        "Use plan context, proof points, agency capabilities, and RFP requirements. "
        "Cite [E#] only if evidence is present.\n"
        "Do NOT return empty content. Do NOT return a whole-section VERIFY.\n"
        "For qualifications: use KB case studies and agency facts when present; "
        "otherwise write capability-aligned narrative from plan memory (no invented client names).\n\n"
        f"Plan context:\n{plan_ctx[:6000]}\n\n"
        f"Section payload:\n{json.dumps(payload, indent=2)[:5000]}\n\n"
        "Return JSON: {\"sections\":[{\"sectionId\":\""
        f"{sid}"
        "\",\"content\":\"full prose\",\"kbRefs\":[],\"designerNote\":null}]}"
    )
    try:
        raw, _provider = await chat_json_with_repair(
            [
                {"role": "system", "content": DRAFT_BATCH_PROMPT},
                {"role": "user", "content": retry_user},
            ],
            max_tokens=max_tokens,
            temperature=0.4,
        )
    except LlmError as exc:
        logger.warning("Plan-driven retry failed for %s: %s", sid, exc)
        return None
    drafted = raw.get("sections") if isinstance(raw, dict) else None
    if not isinstance(drafted, list):
        return None
    for item in drafted:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("sectionId") or item.get("id") or "").strip()
        if item_id == sid and str(item.get("content") or "").strip():
            logger.info("Plan-driven retry recovered empty section %s (%s)", sid, title)
            return item
    return None


async def _ensure_jit_evidence(
    state: DraftingGraphState,
    section_id: str,
) -> list[dict[str, Any]]:
    """Prefer shared Phase 2 corpus; JIT only on miss when flagged (W6 / T6.1)."""
    from app.core.config import settings as app_settings
    from app.services.proposal_evidence_gate import (
        EvidenceDecision,
        decide_evidence_action,
    )

    corpus = list(state.get("evidence_corpus") or [])
    tagged = _evidence_for_section(section_id, corpus)

    entry_raw = _plan_retrieval_entry(state, section_id)
    section_title = ""
    for section in state.get("rfp_sections") or []:
        if str(section.get("id") or "") == section_id:
            section_title = str(section.get("title") or "")
            break
    gate = decide_evidence_action(section_id=section_id, section_title=section_title)
    logger.info(
        "drafting_evidence_gate section_id=%s decision=%s reason=%s",
        section_id,
        gate.action.value,
        gate.reason,
    )

    # Plan-driven / manual / cleanup: do not pull random KB facts into the writer.
    if gate.action in {
        EvidenceDecision.WRITE_FROM_PLAN,
        EvidenceDecision.MANUAL_FILL,
        EvidenceDecision.DETERMINISTIC_CLEANUP,
        EvidenceDecision.VERIFY_FIELD,
    }:
        logger.info(
            "phase3_jit_skipped_by_gate section=%s decision=%s",
            section_id,
            gate.action.value,
        )
        return []

    is_budget_section = (
        gate.action == EvidenceDecision.WRITE_FROM_CANONICAL_BUDGET
        or any(k in section_title.lower() for k in ("budget", "pricing", "fees", "cost"))
    )

    # Budget narrative must ground in 00_Guide_Pricing — always supplement.
    if is_budget_section:
        from app.services.proposal_intelligence.jit_retrieval import retrieve_for_section
        from app.services.proposal_intelligence.schemas import RetrievalEntry

        pricing_entry = RetrievalEntry.model_validate(
            {
                "sectionId": section_id,
                "requiredAssets": ["00_Guide_Pricing pricing guide"],
                "queries": [
                    "00_Guide_Pricing tier ranges Low Average High discovery strategy content digital media project management",
                    "00_Guide_Pricing transparent compensation pass-through media markup agency fees",
                ],
                "priority": "required",
                "expectedSources": ["pricing"],
                "whyNeeded": "Budget narrative must follow pricing guide rules",
            }
        )
        start = len(corpus) + 1
        items = await retrieve_for_section(
            pricing_entry,
            rfp_client=str(state.get("rfp_client") or ""),
            start_index=start,
        )
        for item in items:
            dumped = item.model_dump(by_alias=True)
            dumped["sectionIds"] = list(
                dict.fromkeys([*(dumped.get("sectionIds") or []), section_id])
            )
            corpus.append(dumped)
        state["evidence_corpus"] = corpus
        tagged = _evidence_for_section(section_id, corpus)
        if tagged:
            return tagged

    if tagged:
        logger.debug(
            "phase3_using_shared_corpus section=%s hits=%d",
            section_id,
            len(tagged),
        )
        return tagged

    if not app_settings.jit_retrieval_on_miss:
        logger.info(
            "phase3_jit_skipped_no_section_hits section=%s corpus_size=%d",
            section_id,
            len(corpus),
        )
        return corpus[:12]

    if not entry_raw:
        return corpus[:12]

    from app.services.proposal_intelligence.jit_retrieval import retrieve_for_section
    from app.services.proposal_intelligence.schemas import RetrievalEntry

    try:
        entry = RetrievalEntry.model_validate(entry_raw)
    except Exception:
        return corpus[:12]

    logger.info(
        "phase3_jit_fallback section=%s reason=no_shared_corpus_hits",
        section_id,
    )
    start = len(corpus) + 1
    items = await retrieve_for_section(
        entry,
        rfp_client=str(state.get("rfp_client") or ""),
        start_index=start,
    )
    for item in items:
        corpus.append(item.model_dump(by_alias=True))
    state["evidence_corpus"] = corpus
    return _evidence_for_section(section_id, corpus) or corpus[:12]


#: Extra attempts for a single section before writing a failure placeholder.
#: Bounded at one so worst-case Phase 3 latency stays predictable.
_SINGLE_SECTION_RETRIES = 1
_SINGLE_SECTION_RETRY_BACKOFF_SECONDS = 3.0


async def _draft_single_with_retry(
    section: dict[str, Any],
    state: DraftingGraphState,
) -> tuple[list[dict[str, Any]], str]:
    """Draft one section, retrying once on LlmError before giving up.

    The common causes here are transient — provider rate limits and brief
    outages. Without a retry a single blip permanently converts a section into a
    placeholder that only chat can recover, so one cheap backoff attempt removes
    most placeholders at the source.
    """
    attempt = 0
    while True:
        try:
            return await _draft_batch_once([section], state)
        except LlmError as exc:
            if attempt >= _SINGLE_SECTION_RETRIES:
                raise
            delay = _SINGLE_SECTION_RETRY_BACKOFF_SECONDS * (2**attempt)
            attempt += 1
            logger.warning(
                "Phase 3 section %s attempt %d failed (%s) — retrying in %.1fs",
                section.get("id") or "",
                attempt,
                exc,
                delay,
            )
            await asyncio.sleep(delay)


async def _draft_batch(
    batch: list[dict[str, Any]],
    state: DraftingGraphState,
) -> tuple[list[dict[str, Any]], str]:
    try:
        return await _draft_batch_once(batch, state)
    except LlmError as exc:
        if len(batch) <= 1:
            raise
        logger.warning(
            "Phase 3 batch of %d failed (%s) — retrying one section at a time",
            len(batch),
            exc,
        )
        merged: list[dict[str, Any]] = []
        provider = state.get("provider") or _provider_name()
        for section in batch:
            try:
                results, batch_provider = await _draft_single_with_retry(section, state)
                merged.extend(results)
                provider = batch_provider
            except LlmError as single_exc:
                sid = str(section.get("id") or "")
                merged.append(
                    {
                        "id": sid,
                        "title": str(section.get("title") or sid),
                        "pageLimit": section.get("pageLimit"),
                        "wordTarget": _word_target(section),
                        "required": True,
                        "custom": False,
                        "source": "rfp",
                        "mode": section.get("zoMode") or "write",
                        "content": SECTION_DRAFT_FAILURE_PLACEHOLDER,
                        "status": "outline",
                        "kbRefs": [],
                    }
                )
                logger.warning(
                    "Phase 3 section %s draft failed after JSON repair: %s",
                    sid,
                    single_exc,
                )
        return merged, provider


def _build_draft_prompt_zones(
    *,
    batch: list[dict[str, Any]],
    batch_payload: list[dict[str, Any]],
    state: DraftingGraphState,
) -> tuple[str, str, str]:
    """Assemble the drafting prompt as (zone_a, zone_b, zone_c).

    Pure and synchronous so the zone boundaries can be tested directly — the
    cache saving depends entirely on zone_a staying byte-identical across the
    batches of a run, and that is only worth asserting if it is cheap to assert.
    """
    narrative_sections = [p for p in batch_payload if p.get("register") == "narrative"]
    procurement_sections = [
        p for p in batch_payload if p.get("register") == "procurement"
    ]

    # ------------------------------------------------------------------
    # Prompt zones (see docs/superpowers/specs/2026-08-11-proposal-llm-cost-
    # optimization-design.md).
    #
    # The assembled prompt is the same text it always was; only the ORDER
    # changed, so that the parts which repeat across batches sit in a stable
    # prefix that Anthropic can cache.
    #
    #   zone_a  — constant for the whole run. Cached once, read by every batch.
    #   zone_b  — the prior-sections block. Append-only, so batch N reuses
    #             batch N-1's cached copy and writes only the delta.
    #   zone_c  — anything derived from this batch. Never cached.
    #
    # THE RULE: a block belongs in zone_a only if its content is independent of
    # `batch`, `batch_payload` and `narrative_sections`. If it reads any of
    # those, it is zone_c. Putting a batch-dependent block in zone_a does not
    # corrupt the prompt, but it silently destroys the cache hit for every
    # batch — which is why test_draft_prompt_zones.py asserts zone_a is
    # byte-identical across batches.
    # ------------------------------------------------------------------
    zone_a = (
        f"Client: {state['rfp_client']}\n"
        f"Sector: {state['rfp_sector']}\n"
        f"Location: {state.get('rfp_location') or ''}\n"
        f"RFP: {state['rfp_title']}\n\n"
    )
    zone_c = ""
    if narrative_sections:
        zone_c += (
            "NARRATIVE sections in this batch (first person we/our — never The Vendor):\n"
            f"{format_register_block('narrative')}\n\n"
            f"Brand voice for narrative sections:\n"
            f"{_brand_voice_block(state.get('brand_voice'), register='narrative', rfp_client=state['rfp_client'])}\n\n"
        )
    if procurement_sections:
        zone_c += (
            "PROCUREMENT sections in this batch (formal third-person OK):\n"
            f"{format_register_block('procurement')}\n\n"
        )
    zo_ctx = (state.get("zo_sections_context") or "").strip()
    if zo_ctx:
        # Framing matters more than the content here. This block used to be
        # labelled a reference that barred only word-for-word copying, which
        # invited restating the same facts in fresh wording — a tab titled
        # "A brief description of the firm, including the year the firm was
        # established" duly repeated the founding date and entity type already
        # covered by 1.1 and 1.3. These sections are ALREADY IN THE DOCUMENT.
        zone_a += (
            "ALREADY WRITTEN — these sections are in this proposal already. The "
            "reader will have read them before reaching your section.\n"
            "Do NOT restate their facts in ANY form, including paraphrase, "
            "summary, or 'as noted above' recaps: company history, founding "
            "date, entity type, ownership, certifications, insurance limits, "
            "office locations, team member bios or credentials, and case-study "
            "narratives all belong to these sections and must not be repeated.\n"
            "If your RFP section needs one of these facts, write ONE short "
            "cross-reference (e.g. 'see Company Overview') and spend your words "
            "on what THIS section uniquely requires. Only re-state a fact when "
            "the RFP explicitly demands it inside your section (a required form "
            "field or a numbered submission item).\n\n"
            f"{zo_ctx[:6000]}\n\n"
        )

    from app.services.proposal_section_dedup import (
        format_anti_duplication_rules,
        format_prior_sections_block,
    )

    zone_a += f"{format_anti_duplication_rules()}\n\n"
    from app.services.proposal_budget_slots import money_slots_prompt_hint

    zone_a += f"{money_slots_prompt_hint()}\n\n"
    prior = state.get("drafted_sections") or []
    batch_ids = {
        str(s.get("id") or "") for s in batch if s.get("id")
    }
    prior_block = format_prior_sections_block(prior, exclude_ids=batch_ids)
    zone_b = f"{prior_block}\n\n" if prior_block else ""

    from app.services.proposal_manuscript_locks import format_manuscript_locks_block
    from app.models.proposal import ManuscriptLocks

    locks_raw = state.get("manuscript_locks")
    locks = None
    if isinstance(locks_raw, ManuscriptLocks):
        locks = locks_raw
    elif isinstance(locks_raw, dict):
        try:
            locks = ManuscriptLocks.model_validate(locks_raw)
        except Exception:
            locks = None
    locks_block = format_manuscript_locks_block(locks)
    if locks_block:
        zone_a += f"{locks_block}\n\n"

    avoid_block = format_avoidance_block(
        state.get("writing_avoidances") or [],
        [
            LossLesson.model_validate(item)
            for item in (state.get("loss_lessons") or [])
            if isinstance(item, dict)
        ],
    )
    if avoid_block:
        zone_a += f"{avoid_block}\n\n"

    weight_block = format_weight_priority_block(state.get("rfp_sections") or [])
    if weight_block:
        zone_a += f"{weight_block}\n\n"

    proof_points = state.get("proof_points") or []
    if proof_points and narrative_sections:
        for payload in batch_payload:
            if payload.get("register") != "narrative":
                continue
            block = format_proof_points_block(
                proof_points,
                section_id=str(payload.get("sectionId") or ""),
                section_title=str(payload.get("title") or ""),
            )
            if block:
                zone_c += f"{block}\n\n"
                break

    from app.models.evidence_allocation import EvidenceAllocationLedger
    from app.services.evidence_allocator import drafting_exclusion_contract

    alloc_raw = state.get("evidence_allocation")
    alloc_ledger = None
    if isinstance(alloc_raw, EvidenceAllocationLedger):
        alloc_ledger = alloc_raw
    elif isinstance(alloc_raw, dict):
        try:
            alloc_ledger = EvidenceAllocationLedger.model_validate(alloc_raw)
        except Exception:
            alloc_ledger = None
    if alloc_ledger:
        for payload in batch_payload:
            contract = drafting_exclusion_contract(
                alloc_ledger,
                section_id=str(payload.get("sectionId") or ""),
            )
            if contract:
                zone_c += (
                    f"For section {payload.get('sectionId')}:\n{contract}\n\n"
                )

    zone_c += f"{DESIGNER_READY_BLOCK}\n\n"

    for payload in batch_payload:
        plan_ctx = str(payload.get("planContext") or "").strip()
        if plan_ctx:
            zone_c += (
                f"Execution plan context for {payload.get('sectionId')}:\n{plan_ctx}\n\n"
            )
        from app.services.proposal_evidence_gate import (
            EvidenceDecision,
            EvidenceGateResult,
            evidence_policy_prompt_stanza,
        )

        policy = str(payload.get("evidencePolicy") or "")
        if policy:
            decision = EvidenceGateResult(
                action=EvidenceDecision(policy),
                reason=str(payload.get("evidencePolicyReason") or ""),
                requires_retrieval=policy == EvidenceDecision.RETRIEVE_THEN_WRITE.value,
                safe_plan_driven=policy == EvidenceDecision.WRITE_FROM_PLAN.value,
            )
            zone_c += (
                evidence_policy_prompt_stanza(
                    decision, section_id=str(payload.get("sectionId") or "")
                )
                + "\n\n"
            )
        if _is_plan_driven_narrative(
            title=str(payload.get("title") or ""),
            register=str(payload.get("register") or ""),
        ) or policy == EvidenceDecision.WRITE_FROM_PLAN.value:
            evidence_text = str(payload.get("evidence") or "")
            thin_evidence = (
                not evidence_text.strip()
                or "No evidence items tagged" in evidence_text
            )
            if thin_evidence:
                zone_c += (
                    f"IMPORTANT for {payload.get('sectionId')} ({payload.get('title')}): "
                    "Evidence is thin or empty. Still draft full submission-ready narrative "
                    "from Opportunity Understanding, Section Strategy, Winning Pattern, "
                    "RFP requirements, and Proposal Memory. Do not return an empty content "
                    "field or a whole-section VERIFY about insufficient evidence.\n\n"
                )
            if _is_qualifications_narrative(str(payload.get("title") or "")):
                zone_c += (
                    f"QUALIFICATIONS SECTION {payload.get('sectionId')}: "
                    "Write full experience narrative using retrieved case studies, references, "
                    "and agency credentials when present. If geo-specific case studies are "
                    "missing, describe transferable place-branding / economic development "
                    "capabilities without inventing false project names or metrics.\n\n"
                )
            title_lower = str(payload.get("title") or "").lower()
            if any(
                k in title_lower
                for k in (
                    "team qualification",
                    "agency team",
                    "staffing",
                    "personnel",
                    "key personnel",
                    "project team",
                )
            ):
                zone_c += (
                    f"TEAM / STAFFING SECTION {payload.get('sectionId')}: "
                    "Do NOT invent percent-time / FTE percentages. If the RFP does not "
                    "require percent-time or dedicated FTE %, omit that column entirely "
                    "(Role | Name | relevant experience only — names from approved bios). "
                    "If the RFP requires percent-time, every cell must be "
                    "[VERIFY: percent time] — never invent 10%/35%/25% grids or reuse "
                    "static tables from other proposals.\n\n"
                )
            if any(k in title_lower for k in ("budget", "pricing", "fees", "cost")):
                zone_c += (
                    f"BUDGET NARRATIVE REQUIRED for {payload.get('sectionId')}: "
                    "Write transparency, pass-through media buys, compensation model, and "
                    "allocation rationale using RFP spend figures from requirements/plan. "
                    "Do not invent agency fee line-item tables. Do not return empty content.\n\n"
                )
            if any(
                k in title_lower
                for k in (
                    "schedule",
                    "timeline",
                    "delivery schedule",
                    "project schedule",
                    "work schedule",
                )
            ):
                zone_c += (
                    f"SCHEDULE / TIMELINE SECTION {payload.get('sectionId')}: "
                    "Dates, milestones, and owners ONLY — do not restate Approach phases. "
                    "Fit entirely inside the RFP award→launch / contract window from RFP "
                    "context / Delivery Timeline Plan. Never invent a longer sequential "
                    "plan than the RFP allows. Missing dates → [VERIFY: …], never fabricate.\n\n"
                )
            if any(
                k in title_lower
                for k in (
                    "cover letter",
                    "letter of transmittal",
                    "transmittal letter",
                )
            ):
                zone_c += (
                    f"COVER LETTER SECTION {payload.get('sectionId')}: "
                    "Write a complete short offer letter addressing RFP submission asks. "
                    "If the RFP requires a physically signed cover letter / transmittal, "
                    "include designerNote instructing attachment of the signed PDF — "
                    "do not invent signature dates, notary numbers, or claim the PDF is attached.\n\n"
                )
        zone_c += (
            f"DESIGNER-COMPACT {payload.get('sectionId')}: "
            f"wordTarget {payload.get('wordTarget')} max — cover EVERY RFP ask in dense "
            "tables/bullets + [DESIGNER NOTE]. Complete substance, compact layout.\n\n"
        )

    zone_c += f"Sections to draft:\n{json.dumps(batch_payload, indent=2)}"

    return zone_a, zone_b, zone_c


async def _draft_batch_once(
    batch: list[dict[str, Any]],
    state: DraftingGraphState,
) -> tuple[list[dict[str, Any]], str]:
    batch_payload: list[dict[str, Any]] = []

    for section in batch:
        sid = str(section.get("id") or "")
        title = str(section.get("title") or sid)
        log_intel_event(
            "SECTION_GENERATE_NEXT",
            rfp_id=state.get("rfp_id"),
            section_id=sid,
            title=title,
            phase="phase-3",
        )
        evidence = await _ensure_jit_evidence(state, sid)
        zo_mode = str(section.get("zoMode") or section.get("zo_mode") or "write")
        register = classify_section_register(
            section_id=sid,
            title=title,
            zo_mode=zo_mode,
        )
        brief = _plan_section_brief(state, sid)
        word_target = (
            int(brief.get("wordBudget") or 0)
            if brief and brief.get("wordBudget")
            else _word_target(section)
        )
        from app.services.proposal_evidence_gate import decide_evidence_action

        gate = decide_evidence_action(section_id=sid, section_title=title)
        batch_payload.append(
            {
                "sectionId": sid,
                "title": title,
                "register": register,
                "requirements": section.get("requirements") or [],
                "zoMode": zo_mode,
                "wordTarget": word_target or _word_target(section),
                "uncoveredRequirements": section.get("uncoveredRequirements")
                or section.get("uncovered_requirements")
                or [],
                "evidence": _format_evidence_block(evidence),
                "planContext": _format_plan_context(state, sid),
                "evidencePolicy": gate.action.value,
                "evidencePolicyReason": gate.reason,
            }
        )

    zone_a, zone_b, zone_c = _build_draft_prompt_zones(
        batch=batch,
        batch_payload=batch_payload,
        state=state,
    )
    # zone_a and zone_b are handed to the LLM layer as cache breakpoints; zone_c
    # is the volatile remainder. Concatenated, they are the same prompt text the
    # model has always received.
    cache_prefix = [zone for zone in (zone_a, zone_b) if zone]
    user_content = zone_c

    # Keep ≤ Fireworks 8192 output cap so prefer/fallback Fireworks can serve
    # when Gemini/OpenRouter are unavailable (expired models / credit exhaustion).
    draft_max_tokens = 16000

    async with state["llm_semaphore"]:
        from app.services.llm_call_context import llm_call_context

        draft_node = "draft_sections"
        if batch:
            sid = str(batch[0].get("id") or "").strip()
            title = str(batch[0].get("title") or "").strip()
            draft_node = f"draft_sections:{sid or title or 'section'}"
        with llm_call_context(
            rfp_id=str(state.get("rfp_id") or ""),
            node_name=draft_node,
        ):
            raw, provider = await chat_json_with_repair(
                [
                    {"role": "system", "content": DRAFT_BATCH_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=draft_max_tokens,
                temperature=0.35,
                node_name=draft_node,
                rfp_id=str(state.get("rfp_id") or "") or None,
                cache_prefix=cache_prefix,
            )

    results: list[dict[str, Any]] = []
    drafted = raw.get("sections", [])
    drafted_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(drafted, list):
        for item in drafted:
            if isinstance(item, dict):
                sid = str(item.get("sectionId") or item.get("id") or "").strip()
                if sid:
                    drafted_by_id[sid] = item

    payload_by_id = {
        str(p.get("sectionId") or ""): p for p in batch_payload if p.get("sectionId")
    }

    for section in batch:
        sid = str(section.get("id") or "")
        item = drafted_by_id.get(sid, {})
        content = str(item.get("content", "")).strip()
        zo_mode = str(section.get("zoMode") or section.get("zo_mode") or "write")
        title = str(section.get("title") or sid)
        register = classify_section_register(
            section_id=sid,
            title=title,
            zo_mode=zo_mode,
        )
        section_payload = payload_by_id.get(sid) or {
            "sectionId": sid,
            "title": title,
            "register": register,
            "requirements": section.get("requirements") or [],
            "wordTarget": _word_target(section),
            "evidence": "(retry — use plan context)",
            "planContext": _format_plan_context(state, sid),
        }
        if _section_prose_missing(content) and _is_plan_driven_narrative(
            title=title, register=register
        ):
            logger.warning(
                "Phase 3 empty prose for plan-driven section %s (%s) — retrying once",
                sid,
                title,
            )
            retried = await _retry_plan_driven_section(
                section,
                state,
                payload=section_payload,
                reason="empty or verify-only",
            )
            if retried:
                item = retried
                content = str(item.get("content") or "").strip()
        elif _looks_truncated_prose(content) and _is_plan_driven_narrative(
            title=title, register=register
        ):
            logger.warning(
                "Phase 3 truncated prose for section %s (%s) — retrying with higher token limit",
                sid,
                title,
            )
            retried = await _retry_plan_driven_section(
                section,
                state,
                payload=section_payload,
                max_tokens=16_384,
                reason="previous draft ended mid-sentence (output limit)",
            )
            if retried:
                candidate = str(retried.get("content") or "").strip()
                if candidate and len(candidate) > len(content):
                    item = retried
                    content = candidate
        if _section_prose_missing(content):
            plan_ctx = _format_plan_context(state, sid)
            content = _empty_draft_fallback(
                title=title,
                register=register,
                requirements=section.get("requirements") or [],
                has_plan_context=bool(plan_ctx.strip()),
            )
        else:
            content = enforce_narrative_voice(
                content,
                section_id=sid,
                title=title,
                zo_mode=zo_mode,
            )
        kb_refs = _extract_kb_refs(content, item.get("kbRefs") or item.get("kb_refs"))
        results.append(
            {
                "id": sid,
                "title": str(section.get("title") or sid),
                "pageLimit": section.get("pageLimit") or section.get("page_limit"),
                "wordTarget": _word_target(section),
                "required": True,
                "custom": False,
                "source": "rfp",
                "mode": section.get("zoMode") or section.get("zo_mode") or "write",
                "content": content,
                "designerNote": item.get("designerNote") or item.get("designer_note"),
                "status": "generated" if content else "outline",
                "kbRefs": kb_refs,
            }
        )

    return results, provider


async def _draft_all_sections(state: DraftingGraphState) -> dict[str, Any]:
    sections = state.get("rfp_sections") or []
    static_section_text = state.get("zo_sections_context") or ""

    def _skip(s: dict[str, Any]) -> bool:
        weight = s.get("evaluationWeight")
        if weight is None:
            weight = s.get("evaluation_weight")
        return should_skip_rfp_section_as_static_duplicate(
            title=str(s.get("title") or ""),
            duplicate_of_static_section=(
                str(s.get("duplicateOfStaticSection") or s.get("duplicate_of_static_section") or "")
                or None
            ),
            evaluation_weight=weight,
            static_section_text=static_section_text,
        )

    skipped = [s for s in sections if _skip(s)]
    sections = [s for s in sections if not _skip(s)]
    sections = order_sections_for_phase3_draft(sections)
    # Retain outline completeness: static-covered tabs get a short pointer stub
    # instead of vanishing from the manuscript.
    stub_drafted: list[dict[str, Any]] = []
    for s in skipped:
        title = str(s.get("title") or s.get("id") or "Section")
        sid = str(s.get("id") or "")
        if not sid:
            continue
        stub_drafted.append(
            {
                "id": sid,
                "title": title,
                "content": _static_coverage_stub(title),
                "status": "generated",
                "source": "generated",
                "mode": "write",
                "required": True,
            }
        )
    if skipped:
        logger.info(
            "Phase 3 stubbing %d RFP sections (duplicate of static Sections 1–3): %s",
            len(skipped),
            [s.get("title") for s in skipped[:5]],
        )
    if not sections and not stub_drafted:
        return {"error": "No RFP sections to draft. Run Phase 2 first."}

    all_drafted: list[dict[str, Any]] = list(stub_drafted)
    # Keep static / already-filled digests for every batch (anti-repetition).
    seed_prior: list[dict[str, Any]] = [
        s
        for s in (state.get("drafted_sections") or [])
        if isinstance(s, dict) and str(s.get("content") or "").strip()
    ]
    provider = state.get("provider") or _provider_name()

    concurrency = _phase3_concurrency()
    if concurrency > 1 and len(sections) > 1:
        logger.info(
            "Phase 3 parallel drafting for %s: %d sections, concurrency=%d",
            state.get("rfp_id"),
            len(sections),
            concurrency,
        )
        parallel_results, provider = await _draft_sections_parallel(
            state=state,
            sections=sections,
            seed_prior=seed_prior,
            concurrency=concurrency,
        )
        all_drafted.extend(parallel_results)
        return {
            "drafted_sections": all_drafted,
            "provider": provider,
            "evidence_corpus": state.get("evidence_corpus") or [],
        }

    batches = _chunk_sections(sections, BATCH_SIZE)
    logger.info(
        "Phase 3 drafting for %s: %d sections in %d batches",
        state.get("rfp_id"),
        len(sections),
        len(batches),
    )

    for index, batch in enumerate(batches, start=1):
        rfp_id = str(state.get("rfp_id") or "")
        if rfp_id:
            from app.services.proposal_generation_cancel import check_generation_cancelled

            await check_generation_cancelled(rfp_id)
        if batch and rfp_id:
            sec = batch[0]
            sec_title = str(sec.get("title") or sec.get("id") or "Section")
            from app.services.proposal_pipeline_checkpoint import record_pipeline_activity

            await record_pipeline_activity(
                rfp_id,
                label=f"Drafting: {sec_title}",
                detail="LLM writing this RFP tab (not a context limit — one section per request).",
                step_index=index,
                step_total=len(batches),
            )
        try:
            # Seed + already-drafted so each batch avoids repeating them
            batch_state = {
                **state,
                "drafted_sections": [*seed_prior, *all_drafted],
            }
            batch_results, batch_provider = await _draft_batch(batch, batch_state)
            all_drafted.extend(batch_results)
            provider = batch_provider
            logger.info(
                "Phase 3 batch %d/%d complete for %s (%d sections)",
                index,
                len(batches),
                state.get("rfp_id"),
                len(batch_results),
            )
            try:
                from app.core.step_debug_logger import (
                    classify_section_outcome,
                    step_trace,
                    summarize_sections,
                )

                section_outcomes = [
                    {
                        "id": str(item.get("id") or ""),
                        "title": str(item.get("title") or "")[:80],
                        "outcome": classify_section_outcome(
                            str(item.get("content") or "")
                        ),
                        "chars": len(str(item.get("content") or "")),
                        "evidence_refs": len(item.get("kbRefs") or []),
                    }
                    for item in batch_results
                ]
                step_trace(
                    "phase3_section_batch_outcomes",
                    rfp_id=str(state.get("rfp_id") or "") or None,
                    batch_index=index,
                    batch_total=len(batches),
                    outcomes=section_outcomes,
                    **summarize_sections(batch_results),
                )
            except Exception:  # noqa: BLE001
                pass
            callback = _SECTION_DRAFT_CALLBACKS.get(str(state.get("rfp_id") or ""))
            if callback:
                drafted_sections = [
                    ProposalSection.model_validate(item) for item in all_drafted
                ]
                await callback(drafted_sections, provider)
        except LlmError as exc:
            logger.warning(
                "Phase 3 batch %d failed for %s: %s",
                index,
                state.get("rfp_id"),
                exc,
            )
            try:
                from app.core.step_debug_logger import step_trace

                step_trace(
                    "phase3_section_batch_failed",
                    rfp_id=str(state.get("rfp_id") or "") or None,
                    batch_index=index,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc)[:300],
                    section_ids=[str(s.get("id") or "") for s in batch],
                )
            except Exception:  # noqa: BLE001
                pass
            for section in batch:
                sid = str(section.get("id") or "")
                all_drafted.append(
                    {
                        "id": sid,
                        "title": str(section.get("title") or sid),
                        "pageLimit": section.get("pageLimit"),
                        "wordTarget": _word_target(section),
                        "required": True,
                        "custom": False,
                        "source": "rfp",
                        "mode": section.get("zoMode") or "write",
                        "content": SECTION_DRAFT_FAILURE_PLACEHOLDER,
                        "status": "outline",
                        "kbRefs": [],
                    }
                )
            logger.warning(
                "Phase 3 batch %d failed for %s after repair: %s",
                index,
                state.get("rfp_id"),
                exc,
            )

    return {
        "drafted_sections": all_drafted,
        "provider": provider,
        "evidence_corpus": state.get("evidence_corpus") or [],
    }


def _build_graph() -> Any:
    graph = StateGraph(DraftingGraphState)
    graph.add_node("draft_sections", _draft_all_sections)
    graph.add_edge(START, "draft_sections")
    graph.add_edge("draft_sections", END)
    return graph.compile()


_DRAFTING_GRAPH = _build_graph()


def _zo_sections_context(sections: list[ProposalSection], *, max_chars_each: int = 1100) -> str:
    """Everything already written in Sections 1-3, so Phase 3 does not repeat it.

    Keep every subsection title in view (bios + case studies), with shorter
    excerpts so the prompt budget is not eaten by Who We Are alone.
    """
    blocks: list[str] = []
    for section in sections:
        if not (section.content or "").strip():
            continue
        blocks.append(f"### {section.title}\n{(section.content or '')[:max_chars_each]}")
    return "\n\n".join(blocks)


async def run_drafting_graph(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
    rfp_sections: list[RfpSectionMap],
    evidence_corpus: list[EvidenceItem],
    brand_voice: ProposalBrandVoice | None,
    zo_template_sections: list[ProposalSection] | None = None,
    writing_avoidances: list[str] | None = None,
    loss_lessons: list[LossLesson] | None = None,
    proof_points: list | None = None,
    manuscript_locks: dict[str, Any] | None = None,
    execution_plan: dict[str, Any] | None = None,
    fact_ledger: dict[str, Any] | None = None,
    evidence_allocation: dict[str, Any] | None = None,
    requirement_ledger: dict[str, Any] | None = None,
    doc_word_budget: int | None = None,
    prior_drafted_sections: list[ProposalSection] | None = None,
    on_sections_drafted: SectionDraftedCallback | None = None,
    rfp_due_date: str | None = None,
) -> tuple[list[ProposalSection], str, list[EvidenceItem]]:
    if not llm.is_configured():
        raise LlmError(
            "LLM not configured. Set OPENROUTER_API_KEY or FIREWORKS_API_KEY.",
            status_code=503,
        )

    plan_dict = execution_plan
    if plan_dict is not None and hasattr(plan_dict, "model_dump"):
        plan_dict = plan_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    locks_dict = manuscript_locks
    if locks_dict is not None and hasattr(locks_dict, "model_dump"):
        locks_dict = locks_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    ledger_dict = fact_ledger
    if ledger_dict is not None and hasattr(ledger_dict, "model_dump"):
        ledger_dict = ledger_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    alloc_dict = evidence_allocation
    if alloc_dict is not None and hasattr(alloc_dict, "model_dump"):
        alloc_dict = alloc_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    requirement_ledger_model: RequirementLedger | None = None
    if requirement_ledger:
        rl_raw: Any = requirement_ledger
        if hasattr(rl_raw, "model_dump"):
            rl_raw = rl_raw.model_dump(by_alias=True)  # type: ignore[union-attr]
        if isinstance(rl_raw, dict):
            try:
                requirement_ledger_model = RequirementLedger.model_validate(rl_raw)
            except Exception:  # noqa: BLE001 — a malformed ledger must never block drafting
                requirement_ledger_model = None

    section_dicts = [s.model_dump(by_alias=True) for s in rfp_sections]
    if doc_word_budget and section_dicts:
        natural = [_word_target(s) for s in section_dicts]
        if requirement_ledger_model is not None:
            # Fill in the gap only: sections that already carry an explicit
            # word/page/weight signal keep it untouched. Sections with none
            # of those (the boilerplate the 22-section MSU Denver draft
            # padded out) get weighted by the ledger's evaluation points
            # instead of the flat DEFAULT_WORD_TARGET.
            ledger_weights = section_weights_from_ledger(
                requirement_ledger_model, doc_word_budget
            )
            for i, section in enumerate(section_dicts):
                has_explicit_signal = bool(
                    section.get("wordTarget")
                    or section.get("word_target")
                    or section.get("pageLimit")
                    or section.get("page_limit")
                    or section.get("evaluationWeight")
                    or section.get("evaluation_weight")
                )
                if has_explicit_signal:
                    continue
                weight_words = ledger_weights.get(str(section.get("id") or ""))
                if weight_words:
                    natural[i] = max(MIN_SECTION_WORDS, min(1200, weight_words))
        allocated = allocate_word_budget(natural, doc_word_budget)
        for section, target in zip(section_dicts, allocated):
            section["wordTarget"] = target
        logger.info(
            "drafting page budget: sections=%d natural=%d allocated=%d budget=%d",
            len(section_dicts),
            sum(natural),
            sum(allocated),
            doc_word_budget,
        )

    initial: DraftingGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "rfp_due_date": (rfp_due_date or "").strip() or None,
        "rfp_sections": section_dicts,
        "evidence_corpus": [e.model_dump(by_alias=True) for e in evidence_corpus],
        "execution_plan": plan_dict if isinstance(plan_dict, dict) else None,
        "brand_voice": brand_voice.model_dump(by_alias=True) if brand_voice else {},
        "zo_sections_context": _zo_sections_context(zo_template_sections or []),
        "writing_avoidances": writing_avoidances or [],
        "loss_lessons": [
            lesson.model_dump(by_alias=True) for lesson in (loss_lessons or [])
        ],
        "proof_points": [
            p.model_dump(by_alias=True) if hasattr(p, "model_dump") else p
            for p in (proof_points or [])
        ],
        "manuscript_locks": locks_dict if isinstance(locks_dict, dict) else None,
        "fact_ledger": ledger_dict if isinstance(ledger_dict, dict) else None,
        "evidence_allocation": alloc_dict if isinstance(alloc_dict, dict) else None,
        # Seed already-filled RFP tabs so each new draft sees ALREADY COVERED digests.
        "drafted_sections": [
            s.model_dump(by_alias=True)
            for s in (prior_drafted_sections or [])
            if (s.content or "").strip()
        ],
        "llm_semaphore": asyncio.Semaphore(LLM_CONCURRENCY),
    }

    if on_sections_drafted:
        _SECTION_DRAFT_CALLBACKS[rfp_id] = on_sections_drafted

    logger.info("Phase 3 drafting graph starting for rfp_id=%s", rfp_id)
    try:
        final = await _DRAFTING_GRAPH.ainvoke(initial)
    finally:
        if on_sections_drafted:
            _SECTION_DRAFT_CALLBACKS.pop(rfp_id, None)

    if final.get("error"):
        raise LlmError(str(final["error"]), status_code=400)

    drafted = [
        ProposalSection.model_validate(item) for item in (final.get("drafted_sections") or [])
    ]
    provider = str(final.get("provider") or _provider_name())
    jit_corpus = [
        EvidenceItem.model_validate(item) for item in (final.get("evidence_corpus") or [])
    ]
    logger.info(
        "Phase 3 drafting complete for %s: %d sections, %d evidence items",
        rfp_id,
        len(drafted),
        len(jit_corpus),
    )
    return drafted, provider, jit_corpus


async def draft_single_rfp_section_phase3(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
    section: RfpSectionMap,
    evidence_corpus: list[EvidenceItem],
    brand_voice: ProposalBrandVoice | None,
    zo_template_sections: list[ProposalSection] | None = None,
    writing_avoidances: list[str] | None = None,
    loss_lessons: list[LossLesson] | None = None,
    proof_points: list | None = None,
    manuscript_locks: dict[str, Any] | None = None,
    execution_plan: dict[str, Any] | None = None,
    evidence_allocation: dict[str, Any] | None = None,
    rewrite_brief: str = "",
    prior_drafted_sections: list[ProposalSection] | None = None,
) -> tuple[ProposalSection, str, list[EvidenceItem]]:
    """Phase 3 drafting path for exactly one RFP-mapped section (Senior Editor tickets)."""
    if not llm.is_configured():
        raise LlmError(
            "LLM not configured. Set OPENROUTER_API_KEY or FIREWORKS_API_KEY.",
            status_code=503,
        )

    plan_dict = execution_plan
    if plan_dict is not None and hasattr(plan_dict, "model_dump"):
        plan_dict = plan_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    locks_dict = manuscript_locks
    if locks_dict is not None and hasattr(locks_dict, "model_dump"):
        locks_dict = locks_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    alloc_dict = evidence_allocation
    if alloc_dict is not None and hasattr(alloc_dict, "model_dump"):
        alloc_dict = alloc_dict.model_dump(by_alias=True)  # type: ignore[union-attr]

    section_dump = section.model_dump(by_alias=True)
    if rewrite_brief.strip():
        # Inject Senior Editor brief into uncoveredRequirements so the batch prompt sees it.
        extra = list(section_dump.get("uncoveredRequirements") or [])
        extra.append(f"Senior Editor rewrite brief: {rewrite_brief.strip()}")
        section_dump["uncoveredRequirements"] = extra

    # Sibling digests so Senior Editor redrafts do not rehash other tabs.
    prior = [
        s.model_dump(by_alias=True)
        for s in (prior_drafted_sections or [])
        if (s.content or "").strip() and s.id != section.id
    ]

    state: DraftingGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "rfp_sections": [section_dump],
        "evidence_corpus": [e.model_dump(by_alias=True) for e in evidence_corpus],
        "execution_plan": plan_dict if isinstance(plan_dict, dict) else None,
        "brand_voice": brand_voice.model_dump(by_alias=True) if brand_voice else {},
        "zo_sections_context": _zo_sections_context(zo_template_sections or []),
        "writing_avoidances": writing_avoidances or [],
        "loss_lessons": [
            lesson.model_dump(by_alias=True) for lesson in (loss_lessons or [])
        ],
        "proof_points": [
            p.model_dump(by_alias=True) if hasattr(p, "model_dump") else p
            for p in (proof_points or [])
        ],
        "manuscript_locks": locks_dict if isinstance(locks_dict, dict) else None,
        "fact_ledger": None,
        "evidence_allocation": alloc_dict if isinstance(alloc_dict, dict) else None,
        "drafted_sections": prior,
        "llm_semaphore": asyncio.Semaphore(LLM_CONCURRENCY),
    }

    results, provider = await _draft_batch([section_dump], state)
    if not results:
        raise LlmError(f"Phase 3 single-section draft returned empty for {section.id}", status_code=422)
    drafted = ProposalSection.model_validate(results[0])
    jit_corpus = [
        EvidenceItem.model_validate(item) for item in (state.get("evidence_corpus") or [])
    ]
    logger.info(
        "Phase 3 single-section draft for %s / %s (%d chars)",
        rfp_id,
        section.id,
        len(drafted.content or ""),
    )
    return drafted, provider, jit_corpus
