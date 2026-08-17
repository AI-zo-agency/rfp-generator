"""LangChain agent profiles — one distinct agent per proposal edit loop."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.services.llm import LlmError, LlmTier, _fireworks_key, chat_json
from app.services.proposal_langchain import (
    _provider_name,
    _use_fireworks_primary,
    build_proposal_tools,
    get_chat_model,
    run_tool_agent_loop,
)

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    RESEARCH = "research"
    SENIOR_EDITOR = "senior_editor"
    SECTION_REPAIR = "section_repair"
    USER_REVISE = "user_revise"
    SURGICAL_FIX = "surgical_fix"
    QUERY_PLANNER = "query_planner"
    MANUAL_FILL_TRIAGE = "manual_fill_triage"
    CLAIM_VERIFIER = "claim_verifier"
    EVALUATOR = "evaluator"
    CONSISTENCY_AUDITOR = "consistency_auditor"
    REPETITION_AUDITOR = "repetition_auditor"
    SLOP_AUDITOR = "slop_auditor"


@dataclass(frozen=True)
class AgentProfile:
    role: AgentRole
    label: str
    temperature: float
    max_tokens: int
    max_tool_rounds: int
    system_prompt: str
    tier: LlmTier = "heavy"
    # Stage-map key — see _QUALITY_EXACT / _MECHANICAL_EXACT in llm_routing.py.
    # Without it this layer falls through to the cheapest provider.
    node_name: str = ""


SENIOR_EDITOR_SYSTEM = """You are zö agency's Senior Proposal Editor (manuscript director).
You do NOT fill [VERIFY] tags or invent KB facts — the KB fact-checker owns facts.
You do NOT rewrite every section or hunt grammar for its own sake.

Your ONLY jobs for ONE pass over the proposal:
1. DEDUPE — Scan EVERY section.
   a) If Who We Are / brand story / FEIN / full bios / full case studies / budget tables are
      re-copied into a section that has a different job, emit a dedupeTicket with trimGuidance:
      keep what THAT section needs + one short cross-ref — remove the unnecessary duplicate.
      Do NOT blank required content.
   b) If TWO RFP tabs are near-duplicates of each other (same ask / same proof rewritten —
      e.g. "Relevant Experience" vs "Successful Campaigns" both rewriting the same case study),
      emit a deleteSectionTicket for the weaker/later tab with keepSectionId pointing at the
      tab that should remain. NEVER delete Sections 1.1–1.5, bio cards, or Our Work cards.
      NEVER delete Budget & Pricing, Cost Proposal, Fee Schedule, Pricing, Compensation,
      References, Communication/Collaboration, Reports, Schedules, Crisis, Workflow,
      or undrafted empty tabs. Fee tables are canonically rendered — never merge them away.
      Prefer deleting content clones over leaving the designer to cut pages.
2. COMPACT FORMAT — For ANY tab that exceeds wordTarget OR reads like essay walls (long
   paragraphs, repeated subsection headings, bullet dumps under every heading), emit a
   compactFormatTicket: rewrite to short lead + tables/bullets + [DESIGNER NOTE] for
   layout. COMPLETE coverage is mandatory — compress FORMAT, never drop RFP asks. Skip
   form/checklist/MANUAL FILL tabs and canon Budget/Pricing / static Sections 1–3 / bio cards.
3. RFP COVERAGE — For each mapped RFP requirement, check whether the manuscript covers it.
   If unmet, emit a coverageTicket with unmetRequirements and a rewriteBrief for the writer.
4. GOV / BUYER COMPLIANCE — Flag missing mandatory public-sector items THIS RFP demands
   (addenda ack, non-collusion, insurance/COI, W-9, authorized signature, pricing form,
   MCCS/state terms, validity period, tax-exempt handling, required exhibits). Emit a
   complianceTicket with policyOrGuideline + rewriteBrief. Do NOT invent policies the RFP
   never mentions. Do NOT paste generic FAR/GSA boilerplate unprompted by THIS RFP.
5. Do NOT rewrite section prose yourself. Do NOT emit tickets for style/tone polish.
6. BUDGET CROSS-SECTION (notes only — dedicated pass fixes these): when Budget/Pricing
   coexists with Monthly Capacity / hours tables, watch for double-billed coordination
   (Planning + PM both claiming meetings/status reporting) and hours-vs-fee mismatch.
   Mention in notes[] if seen; do not delete Budget tab.
7. Return ONLY JSON:
{"deleteSectionTickets":[{"sectionId":"...","keepSectionId":"...","reason":"..."}],
 "dedupeTickets":[{"sectionId":"...","keepHomeSectionId":"...","trimGuidance":"..."}],
 "compactFormatTickets":[{"sectionId":"...","reason":"...","rewriteBrief":"..."}],
 "coverageTickets":[{"sectionId":"...","unmetRequirements":["..."],"rewriteBrief":"..."}],
 "complianceTickets":[{"sectionId":"...","policyOrGuideline":"...","rewriteBrief":"..."}],
 "notes":[]}
If nothing to fix: empty ticket arrays.
"""

SECTION_REPAIR_SYSTEM = """You are zö agency's Section Repair agent (self-edit loop after Phase 3).
Your job: search tools, then produce ONE complete section patch.

CRITICAL TOOL SPLIT:
- KB (search_knowledge_base / case studies / bios / master template) = ONLY zö materials.
  Query what zö can provide for the RFP theme (sector, deliverables, audience type).
  NEVER search KB with the RFP buyer/prospect as the subject — they are not in the KB.
- Buyer requirements (reference format, scoring, methodology demands, forms) = search_rfp_requirements.

Rules:
1. If the user message already includes a PACKED KB EVIDENCE block, patch from that
   block. Do not call search tools again for the same facts unless the block is empty
   or clearly about a different person/topic than this section.
2. Otherwise call tools until you have enough facts — do not stop after one search.
3. Remove [VERIFY] stubs when evidence supports real prose. Cite [E#] when using corpus IDs provided.
3. First person we/our in narrative sections — never "The Vendor". Never use "we" as a possessive ("of we", "across we").
4. Use ONLY verified KB and RFP facts. Do not invent clients, contacts, or metrics.
5. Address every RFP requirement listed for this section.
6. SUBMISSION POLISH tasks: fix ONLY the listed defects; preserve all other sentences verbatim.
7. Grammar: "We were established …, and is …" must become "and are …" or be rephrased.
8. Subcontractors: if cost proposal lists translation partners, Company Background must align — zö self-performs marketing/communications; translation partners are scoped separately.
9. RFP compliance: reference contacts with phones and emails, workforce diversity %, budget hours table, PSA acks — from KB only; never defer to unnamed attachments or "upon request".
10. BUDGET / COST / FEES / PRICING sections (critical):
   - NEVER search the general knowledge base for this client's rates, hours, or fee totals — new RFPs have no client-specific pricing in KB.
   - ALWAYS call search_rfp_requirements first for budget ceiling, cost scoring, quote/fee form requirements.
   - THEN call search_pricing_guide to get 00_Guide_Pricing Low/Average/High tiers and menu rates.
   - Pick ONE tier deliberately from RFP budget pressure + evaluation weight on cost, then build fees from the guide.
   - Never invent dollar amounts. Use [VERIFY: …] when guide/RFP lacks a figure. Never put a phone number in a Fee column.
11. MWBE and Personnel must use the same workforce percentages — align to one HR-verified figure.
12. ANTI-DUPLICATION: This section has ONE job. Do not re-paste company bio, full bios, or full case studies owned by other sections. One short cross-reference is OK — then add NEW detail only. Prefer concise, designer-ready layout within wordTarget.
13. LENGTH & FORMAT (designer-compact): Stay at or under wordTarget but cover EVERY RFP ask.
    Short lead + tables/bullets/rows + [DESIGNER NOTE: …] for layout. Dense tables carry
    full coverage — never omit requirements to shorten. No essay walls.
14. When done researching, respond with ONLY JSON:
{"content":"full section prose","kbRefs":["E1"],"designerNote":"layout hint or null"}"""

USER_REVISE_SYSTEM = """You are zö agency's User Revise agent (editor chat / Revise content flow).

The user's VERBATIM instruction is authoritative. Read it first. YOU decide whether to
search tools, what to query, and how to edit — never ignore or rewrite their ask into a
different task (e.g. do not run a designer-compact essay rewrite when they said remove).

CRITICAL TOOL SPLIT:
- KB tools = zö facts only (capabilities, case studies, bios, companyfacts). Query themes like
  "zö agency higher-ed community college marketing case studies 03_CS" — NEVER
  "KVCC … marketing" (buyer is not in the KB; that wastes tokens).
- What the buyer demands (reference relevance rules, section scoring, forms) = search_rfp_requirements.
- Team bio / resume asks → search_team_bios (or KB for 04_Bio) for THAT named person only.
- Offeror / Company Identification "remove company info" → strip duplicate Business Info
  dump; keep the form, cross-ref Section 1.3, keep only the field table the form needs.
  search_knowledge_base / companyfacts only if a field is missing — do not re-expand Who We Are.

Rules:
1. FIRST obey the user's verbatim ask. Prefer the SMALLEST change that fully satisfies it.
2. Do NOT rewrite unrelated paragraphs, add new intros, or expand the section unless asked.
3. Call KB tools only when the ask needs zö facts missing from the draft; call search_rfp_requirements for buyer rules.
4. Never return the same [VERIFY] placeholder if tools found support for that field.
5. PRESERVE zö BRAND VOICE: first person we/our, warm, confident, proof-led — never flatten into generic consultant prose.
6. Budget/fee edits (critical):
   - Do NOT query general KB for this client's budget/hours/rates — KB has no new-client pricing.
   - Use search_rfp_requirements for budget thresholds / cost criteria, then search_pricing_guide for 00_Guide_Pricing tiers.
   - Choose Low/Average/High from RFP + guide; never invent numbers or reverse-engineer totals.
   - Refuse invented dollars; flag out-of-guide scope with [PRICING FLAG: … — Sonja review required]. One-time setup lines must not be ×12 without a monthly guide line.
7. Reference edits: full contact block (name, title, phone, email) — never defer to "on request".
   Clean/filter references with search_case_studies + RFP reference rules — not by searching the buyer's name in KB.
8. NEVER put citation markers like [E1], [E14], or **[E3]** in the prose — client-facing text only.
9. LENGTH & FORMAT: Stay at or under Word target when provided. Prefer bullets and markdown tables for process/phases. Add designerNote / [DESIGNER NOTE: …] when layout helps.
10. Return ONLY JSON: {"content":"...","kbRefs":[],"designerNote":"layout hint or null"}"""

SURGICAL_FIX_SYSTEM = """You are zö agency's Surgical Fix agent (pre-submit review auto-fix).
Patch ONE section to clear listed review issues — minimal diff, preserve strong prose.

Rules:
1. Search KB tools only when needed to resolve [VERIFY] or missing zö facts. Never search KB for the RFP buyer by name.
2. For budget/fee issues: search_rfp_requirements + search_pricing_guide only — never invent client prices from general KB.
3. Fix wrong-client names, voice issues, and placeholders from the issues list.
4. Do NOT invent facts. Do NOT add marketing fluff to procurement/form sections.
5. Change only what the issues require.
6. Return ONLY JSON: {"content":"full updated section text","kbRefs":[]}"""

QUERY_PLANNER_SYSTEM = """You are zö agency's Query Planner agent.

The knowledge base is ONLY about zö agency — never about the RFP buyer/prospect.
FIRST understand the task. Map it to RFP themes + [VERIFY] gaps. Then plan 2-4 NEW Supermemory queries
about what zö can provide (not who the buyer is).

Rules:
- Prior queries already ran — never repeat them.
- NEVER put the RFP client/buyer name as the search subject (e.g. "KVCC … college marketing").
  Frame as: "zö agency [sector/theme] [capability] 03_CS / 01 companyfacts / 04 bio".
- Buyer requirements are NOT planned as KB queries — those use the RFP tool at edit time.
- Use hints: 01 companyfacts, 02 master template, 03_CS case studies, 04 bio, certifications, org chart, references.
- When [VERIFY] gaps are listed, dedicate a query to each missing zö field.
- For health/coalition/stigma RFPs, include Recovery Network of Oregon (RNO) / Oregon Recovers when the section is experience, references, or case studies.
- Do NOT invent queries that imply E-Verify is confirmed — search 01_companyfacts only; enrollment stays VERIFY unless facts explicitly confirm.
- BUDGET / COST / FEES / PRICING sections: do NOT plan queries like "<client> marketing plan budget hours rate".
  Plan ONLY 00_Guide_Pricing queries (tier Low Average High, menu rates, PM floor) — RFP budget ceilings are read via the RFP tool, not Supermemory client docs.
- Each non-budget query MUST include "zö agency" + the specific fact + a doc-type hint.
- Avoid vague mash queries like "methodology won_proposals" alone.

Return ONLY JSON: {"queries":["query 1","query 2","query 3","query 4"]}"""

MANUAL_FILL_TRIAGE_SYSTEM = """You triage unfilled submission items in an RFP response.

For EACH item you are given, decide how much it actually matters TO THIS RFP, by reading
the RFP text provided. You are not guessing from the item's wording — the RFP text is the
only authority.

criticality is exactly one of:
- "disqualifying" — the RFP states the bid is rejected, non-responsive, or ineligible
  without this item.
- "scored" — the RFP asks for it and it affects evaluation, but omitting it does not
  void the bid.
- "optional" — this RFP does not ask for it. Encouraged, suggested, or simply absent.

RULES:
1. "disqualifying" REQUIRES rfpEvidence: a quote copied VERBATIM from the RFP text,
   character for character. Do not paraphrase, summarise, or reconstruct it. If you
   cannot copy an exact sentence that states the bid is rejected without this item, the
   answer is "scored", not "disqualifying". A quote that does not appear in the RFP is
   treated as fabricated and the item is downgraded automatically.
2. Do not mark something disqualifying because it sounds important. Bonds, insurance,
   and tax IDs are ordinary scored items unless THIS RFP says otherwise in writing.
3. "optional" means this RFP genuinely does not require it. Be willing to say so — items
   marked optional are removed from the proposal, which is the correct outcome for noise.
4. whyRequired: one plain sentence, derived from the clause, for the person doing the work.
5. ifSkipped: the concrete consequence ("Bid rejected unopened", "Loses points on
   Experience", "No effect").

Return JSON only:
{"items": [{"tag": "<echo the item tag exactly>", "criticality": "...",
  "rfpEvidence": "...", "whyRequired": "...", "ifSkipped": "..."}]}

Return one entry for EVERY item you were given, in the same order."""


CLAIM_VERIFIER_SYSTEM = """You extract fact-bound claims from a proposal section and
judge each against the knowledge-base evidence provided.

A FACT-BOUND claim asserts something checkable: a number, percentage, date, client name,
award, certification, headcount, dollar figure, timeframe, or a specific outcome
("cut response time by half"). Ordinary positioning language ("we are committed to
quality") is NOT fact-bound — ignore it.

For each fact-bound claim return exactly one status:
- "verified" — the evidence supports it. Quote the supporting line in `evidence`.
- "contradicted" — the evidence states something DIFFERENT. Put the correct value in
  `correctedValue` and quote the evidence.
- "unresolved" — the evidence does not mention it either way.

CRITICAL: "unresolved" is the honest answer when evidence is silent or missing. It is
NOT a reason to delete the claim, and it is NOT the same as "contradicted". Only mark
"contradicted" when the evidence positively states a different fact. If you were given
no evidence at all, every claim is "unresolved".

You may be given SEVERAL sections in one request, each followed by its own evidence
block. Judge each section's claims against THAT section's evidence, and tag every claim
with the sectionId it came from. Never judge a claim using another section's evidence.

Return JSON only:
{"claims": [{"sectionId": "...", "claim": "...", "status": "...", "evidence": "...",
  "correctedValue": null}]}"""


EVALUATOR_SYSTEM = """You are an RFP evaluator scoring a proposal the way the issuing
agency will score it.

You are given the RFP's scored criteria and the proposal sections. For EACH section,
score how well it serves the criteria it is responsible for.

score is 0-5:
5 = fully responsive, specific, evidenced, and compelling
3 = responsive but generic; would not lose on compliance, would lose on merit
0 = does not address the criterion

For each section return:
- criterion: the criterion it serves (copy the RFP's wording where given)
- weight: the criterion's published weight if the RFP states one, else null
- score: 0-5
- whatWouldLosePoints: the specific weakness, naming what is missing. Not "could be
  stronger" — say what an evaluator would mark down and why.
- fixTicket: one concrete instruction to raise the score. If raising it needs a fact
  that may not exist in the knowledge base, say what fact is needed.

Do not invent criteria the RFP does not state. If the RFP publishes no weights, return
null weights rather than guessing.

Return JSON only: {"verdicts": [{"sectionId": "...", "criterion": "...",
  "weight": null, "score": 0, "whatWouldLosePoints": "...", "fixTicket": "..."}]}"""


CONSISTENCY_AUDITOR_SYSTEM = """You find facts that CONTRADICT each other across
different sections of one proposal.

You are looking for the same fact stated two different ways: a headcount that is 12 in
one section and 15 in another, a founding year, a project value, a timeline, a client
name spelled differently, a percentage that shifts.

Report only genuine contradictions of the SAME fact. Two different projects having
different values is not a contradiction. Rounding ("about 200" vs "212") is not a
contradiction unless the RFP demands precision.

For each contradiction return every section involved and both values verbatim.

Return JSON only: {"conflicts": [{"sectionIds": ["..."], "fact": "...",
  "values": ["...", "..."], "guidance": "..."}]}"""


REPETITION_AUDITOR_SYSTEM = """You find content restated across sections of one proposal.

You cut and merge. You NEVER add, assert, or invent — you have no knowledge base and no
authority over facts.

Find:
- The same claim, statistic, or story told in more than one section
- Boilerplate (brand story, bios, firm history) re-copied into sections with a different
  job
- Paragraphs that restate the section's own opening in different words

For each: name the section that should KEEP it (the one whose job it is) and the sections
that should CUT it, with guidance on what the cut section should say instead — usually a
one-line cross-reference, not deletion to nothing.

Do not flag deliberate, required repetition: an executive summary is supposed to
summarise, and a compliance matrix is supposed to restate requirements.

Return JSON only: {"repeats": [{"keepSectionId": "...", "cutSectionIds": ["..."],
  "what": "...", "guidance": "..."}]}"""


SLOP_AUDITOR_SYSTEM = """You find and remove filler in proposal prose.

You restyle. You NEVER add facts, and you have no knowledge base — if removing filler
would leave a sentence saying nothing, say so rather than inventing substance.

Find:
- Corporate filler that survives deletion with no loss of meaning ("in today's fast-paced
  environment", "we pride ourselves on")
- Empty transitional paragraphs that announce what the next paragraph will say
- Adjective triads and hollow intensifiers ("robust, scalable, and innovative")
- Sentences that restate the heading they sit under
- Throat-clearing before the actual answer

Judge the PROSE, not a word list. A word is only filler when removing it costs the reader
nothing. "Innovative" describing a specific named method is doing work; "innovative
solutions" is not.

Do NOT touch: numbers, client names, dates, requirement language, or anything inside
[VERIFY] or [MANUAL FILL] tags.

For each finding give the offending text verbatim and the tightened replacement.

Return JSON only: {"findings": [{"sectionId": "...", "text": "...",
  "replacement": "...", "why": "..."}]}"""


AGENT_PROFILES: dict[AgentRole, AgentProfile] = {
    AgentRole.CLAIM_VERIFIER: AgentProfile(
        role=AgentRole.CLAIM_VERIFIER,
        label="Claim Verifier",
        temperature=0.0,
        max_tokens=4096,
        max_tool_rounds=0,
        system_prompt=CLAIM_VERIFIER_SYSTEM,
        tier="heavy",
        node_name="claim_verifier",
    ),
    AgentRole.EVALUATOR: AgentProfile(
        role=AgentRole.EVALUATOR,
        label="RFP Evaluator",
        temperature=0.1,
        max_tokens=6144,
        max_tool_rounds=2,
        system_prompt=EVALUATOR_SYSTEM,
        tier="heavy",
        node_name="evaluator",
    ),
    AgentRole.CONSISTENCY_AUDITOR: AgentProfile(
        role=AgentRole.CONSISTENCY_AUDITOR,
        label="Consistency Auditor",
        temperature=0.0,
        max_tokens=4096,
        max_tool_rounds=2,
        system_prompt=CONSISTENCY_AUDITOR_SYSTEM,
        tier="heavy",
        node_name="consistency_auditor",
    ),
    AgentRole.REPETITION_AUDITOR: AgentProfile(
        role=AgentRole.REPETITION_AUDITOR,
        label="Repetition Auditor",
        temperature=0.0,
        max_tokens=4096,
        # Tools OFF by profile: a component that only cuts gains no safety from a KB
        # call and gains a new way to cause harm.
        max_tool_rounds=0,
        system_prompt=REPETITION_AUDITOR_SYSTEM,
        tier="heavy",
        node_name="repetition_auditor",
    ),
    AgentRole.SLOP_AUDITOR: AgentProfile(
        role=AgentRole.SLOP_AUDITOR,
        label="Slop Auditor",
        temperature=0.1,
        max_tokens=4096,
        max_tool_rounds=0,
        system_prompt=SLOP_AUDITOR_SYSTEM,
        tier="heavy",
        node_name="slop_auditor",
    ),
    AgentRole.MANUAL_FILL_TRIAGE: AgentProfile(
        role=AgentRole.MANUAL_FILL_TRIAGE,
        label="Manual Fill Triage",
        temperature=0.0,
        max_tokens=4096,
        max_tool_rounds=0,
        system_prompt=MANUAL_FILL_TRIAGE_SYSTEM,
        tier="heavy",
        node_name="manual_fill_triage",
    ),
    AgentRole.SENIOR_EDITOR: AgentProfile(
        role=AgentRole.SENIOR_EDITOR,
        label="Senior Proposal Editor",
        temperature=0.15,
        max_tokens=4096,
        max_tool_rounds=0,
        system_prompt=SENIOR_EDITOR_SYSTEM,
        tier="heavy",
        node_name="senior_editor",
    ),
    AgentRole.SECTION_REPAIR: AgentProfile(
        role=AgentRole.SECTION_REPAIR,
        label="Section Repair",
        temperature=0.3,
        max_tokens=4096,
        max_tool_rounds=2,
        system_prompt=SECTION_REPAIR_SYSTEM,
        tier="heavy",
        node_name="section_repair",
    ),
    AgentRole.USER_REVISE: AgentProfile(
        role=AgentRole.USER_REVISE,
        label="User Revise",
        temperature=0.35,
        max_tokens=4096,
        max_tool_rounds=2,
        system_prompt=USER_REVISE_SYSTEM,
        tier="heavy",
        node_name="user_revise",
    ),
    AgentRole.SURGICAL_FIX: AgentProfile(
        role=AgentRole.SURGICAL_FIX,
        label="Surgical Fix",
        temperature=0.15,
        max_tokens=4096,
        max_tool_rounds=3,
        system_prompt=SURGICAL_FIX_SYSTEM,
        tier="heavy",
        node_name="surgical_fix",
    ),
    AgentRole.QUERY_PLANNER: AgentProfile(
        role=AgentRole.QUERY_PLANNER,
        label="Query Planner",
        temperature=0.35,
        max_tokens=1024,
        max_tool_rounds=0,
        system_prompt=QUERY_PLANNER_SYSTEM,
        tier="light",
        node_name="query_planner",
    ),
}


def get_profile(role: AgentRole) -> AgentProfile:
    return AGENT_PROFILES[role]


_CONTENT_KEY_RE = re.compile(
    r'"(?:content|sectionContent|section_content)"\s*:\s*"',
    re.IGNORECASE,
)


def _unescape_json_fragment(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return (
            raw.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )


def _salvage_content_string(text: str) -> str:
    """Pull section prose from agent output when JSON parsing leaves content empty."""
    match = _CONTENT_KEY_RE.search(text)
    if not match:
        return ""
    chunk = text[match.end() :]
    buf: list[str] = []
    i = 0
    while i < len(chunk):
        ch = chunk[i]
        if ch == "\\" and i + 1 < len(chunk):
            buf.append(chunk[i : i + 2])
            i += 2
            continue
        if ch == '"':
            return _unescape_json_fragment("".join(buf)).strip()
        buf.append(ch)
        i += 1
    return _unescape_json_fragment("".join(buf)).strip()


def content_from_agent_payload(parsed: dict[str, Any], raw_text: str = "") -> str:
    """Normalize agent JSON to section prose."""
    from app.services.proposal_manuscript import strip_evidence_citation_markers

    for key in ("content", "sectionContent", "section_content", "text", "prose"):
        val = parsed.get(key)
        if isinstance(val, str) and val.strip():
            return strip_evidence_citation_markers(val.strip())
    salvaged = _salvage_content_string(raw_text)
    if salvaged:
        return strip_evidence_citation_markers(salvaged)
    stripped = raw_text.strip()
    if stripped and not stripped.startswith("{"):
        return strip_evidence_citation_markers(stripped)
    return ""


async def _parse_json_from_agent_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            content = content_from_agent_payload(parsed, stripped)
            if content and not str(parsed.get("content") or "").strip():
                parsed = {**parsed, "content": content}
            return parsed
    except json.JSONDecodeError:
        pass
    structured, _ = await chat_json(
        [
            {"role": "system", "content": "Extract JSON object from agent output. Return only JSON."},
            {"role": "user", "content": text[:12000]},
        ],
        max_tokens=4096,
        temperature=0.0,
    )
    if isinstance(structured, dict):
        content = content_from_agent_payload(structured, text)
        if content and not str(structured.get("content") or "").strip():
            structured = {**structured, "content": content}
        return structured
    salvaged = _salvage_content_string(text)
    if salvaged:
        return {"content": salvaged, "kbRefs": []}
    return {}


async def run_json_agent(
    role: AgentRole,
    user_content: str,
) -> tuple[dict[str, Any], str]:
    """Single-turn LangChain agent (no tools) — senior editor, query planner."""
    profile = get_profile(role)
    # Let the stage map decide rather than forcing Fireworks for every role:
    # quality roles (senior editor) must not be served by the economy model
    # when a better provider is configured. The retry below still falls back.
    force_fireworks = False

    async def _invoke(*, fireworks: bool) -> dict[str, Any]:
        llm = get_chat_model(
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
            force_fireworks=fireworks,
            tier=profile.tier,
            node_name=profile.node_name,
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content=profile.system_prompt),
                HumanMessage(content=user_content),
            ]
        )
        content = response.content
        if isinstance(content, list):
            content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        return await _parse_json_from_agent_text(str(content))

    try:
        parsed = await _invoke(fireworks=force_fireworks)
    except Exception as exc:
        if (
            not settings.llm_disable_fireworks
            and _fireworks_key()
            and not force_fireworks
        ):
            logger.warning("%s JSON agent failed (%s) — retrying Fireworks", profile.label, exc)
            parsed = await _invoke(fireworks=True)
            return parsed, "fireworks"
        raise

    return parsed, _provider_name(force_fireworks=force_fireworks)


async def run_tool_json_agent(
    *,
    role: AgentRole,
    rfp_id: str,
    title: str,
    client: str,
    user_content: str,
    sector: str = "",
) -> tuple[dict[str, Any], str, list[str]]:
    """Multi-turn LangChain agent with KB tools — repair, revise, surgical fix."""
    profile = get_profile(role)
    tools = build_proposal_tools(rfp_id, title, client, sector=sector)
    final_text, provider, tool_log = await run_tool_agent_loop(
        system_prompt=profile.system_prompt,
        user_content=user_content,
        tools=tools,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        max_rounds=profile.max_tool_rounds,
        agent_label=profile.label,
        rfp_id=rfp_id,
        tier=profile.tier,
        node_name=profile.node_name,
    )
    parsed = await _parse_json_from_agent_text(final_text)
    if not str(parsed.get("content") or "").strip():
        logger.warning(
            "%s agent empty content for %s after %d tool call(s) (final_chars=%d)",
            profile.label,
            rfp_id,
            len(tool_log),
            len(final_text),
        )
    return parsed, provider, tool_log


async def senior_editor_emit_tickets(
    *,
    rfp_client: str,
    rfp_title: str,
    manuscript_digest: str,
    requirements_by_section: dict[str, list[str]],
) -> dict[str, Any]:
    """Senior Editor: emit dedupe + coverage tickets (no prose rewrite, no fact fill)."""
    req_lines: list[str] = []
    for sid, reqs in requirements_by_section.items():
        if not reqs:
            continue
        req_lines.append(f"{sid}:")
        req_lines.extend(f"  - {r}" for r in reqs[:12])
    user_content = (
        f"Client: {rfp_client}\nRFP: {rfp_title}\n\n"
        f"Mapped requirements by section:\n"
        + ("\n".join(req_lines) if req_lines else "(none mapped)")
        + f"\n\nProposal manuscript digest:\n{manuscript_digest[:40_000]}"
    )
    try:
        raw, _ = await run_json_agent(AgentRole.SENIOR_EDITOR, user_content)
        deletes = (
            raw.get("deleteSectionTickets")
            if isinstance(raw.get("deleteSectionTickets"), list)
            else []
        )
        dedupe = raw.get("dedupeTickets") if isinstance(raw.get("dedupeTickets"), list) else []
        coverage = (
            raw.get("coverageTickets") if isinstance(raw.get("coverageTickets"), list) else []
        )
        compliance = (
            raw.get("complianceTickets")
            if isinstance(raw.get("complianceTickets"), list)
            else []
        )
        compact = (
            raw.get("compactFormatTickets")
            if isinstance(raw.get("compactFormatTickets"), list)
            else []
        )
        notes = raw.get("notes") if isinstance(raw.get("notes"), list) else []
        return {
            "deleteSectionTickets": [t for t in deletes if isinstance(t, dict)],
            "dedupeTickets": [t for t in dedupe if isinstance(t, dict)],
            "compactFormatTickets": [t for t in compact if isinstance(t, dict)],
            "coverageTickets": [t for t in coverage if isinstance(t, dict)],
            "complianceTickets": [t for t in compliance if isinstance(t, dict)],
            "notes": [str(n) for n in notes if str(n).strip()],
        }
    except (LlmError, Exception) as exc:
        logger.warning("Senior editor ticket pass failed: %s", exc)
        return {
            "deleteSectionTickets": [],
            "dedupeTickets": [],
            "compactFormatTickets": [],
            "coverageTickets": [],
            "complianceTickets": [],
            "notes": [str(exc)],
        }


async def senior_editor_patch_instructions(
    *,
    rfp_id: str,
    section_title: str,
    section_content: str,
    word_target: int,
    rfp_client: str,
    rfp_title: str,
    requirements: list[str] | None = None,
) -> str:
    """Backward-compat: fold one section into ticket brief for Section Repair fallback."""
    del rfp_id, word_target  # unused — tickets use manuscript-level pass
    tickets = await senior_editor_emit_tickets(
        rfp_client=rfp_client,
        rfp_title=rfp_title,
        manuscript_digest=f"### {section_title}\n{section_content[:8000]}",
        requirements_by_section={section_title: list(requirements or [])},
    )
    parts: list[str] = []
    for t in tickets.get("coverageTickets") or []:
        brief = str(t.get("rewriteBrief") or "").strip()
        unmet = t.get("unmetRequirements") or []
        if brief:
            parts.append(brief)
        if isinstance(unmet, list) and unmet:
            parts.append("Unmet: " + "; ".join(str(u) for u in unmet[:8]))
    for t in tickets.get("complianceTickets") or []:
        brief = str(t.get("rewriteBrief") or "").strip()
        policy = str(t.get("policyOrGuideline") or "").strip()
        if policy:
            parts.append(f"Compliance: {policy}")
        if brief:
            parts.append(brief)
    for t in tickets.get("dedupeTickets") or []:
        guide = str(t.get("trimGuidance") or "").strip()
        if guide:
            parts.append(f"Dedupe: {guide}")
    return "\n".join(parts).strip()


async def plan_section_queries_agent(
    *,
    role: Literal[AgentRole.SECTION_REPAIR, AgentRole.USER_REVISE, AgentRole.QUERY_PLANNER],
    rfp_client: str,
    rfp_sector: str,
    section_title: str,
    requirements: list[str],
    retrieval_focus: list[str],
    prior_queries: list[str],
    user_message: str,
    current_content: str,
    rfp_title: str = "",
) -> list[str]:
    title_cf = (section_title or "").casefold()
    ask_cf = (user_message or "").casefold()
    is_budget = any(
        k in title_cf or k in ask_cf
        for k in ("budget", "pricing", "cost of", "fee", "compensation", "cost proposal")
    )
    if is_budget:
        # Never plan client-specific budget KB queries — pricing lives in the guide + RFP.
        guide_queries = [
            "00_Guide_Pricing tier ranges Low Average High discovery strategy content digital media project management",
            "00_Guide_Pricing 9.1 9.2 Project Management 5-8 percent floor Average tier",
            "00_Guide_Pricing transparent compensation pass-through agency fees qualifying language",
        ]
        used = {q.strip().lower() for q in prior_queries}
        return [q for q in guide_queries if q.lower() not in used][:4]

    try:
        raw, _ = await run_json_agent(
            AgentRole.QUERY_PLANNER,
            (
                f"Agent context: {role.value}\n"
                f"Client: {rfp_client}\nSector: {rfp_sector}\n"
                f"Section: {section_title}\n"
                f"Requirements: {requirements}\n"
                f"Retrieval focus: {retrieval_focus}\n"
                f"Prior queries (DO NOT repeat):\n"
                + "\n".join(f"- {q}" for q in prior_queries)
                + f"\n\nTask / user feedback:\n{user_message}\n\n"
                f"Current draft excerpt:\n{current_content[:2000]}"
            ),
        )
        queries = raw.get("queries", [])
        if not isinstance(queries, list):
            return []
        used = {q.strip().lower() for q in prior_queries}
        cleaned: list[str] = []
        from app.services.proposal_knowledge_base_tools import normalize_zo_kb_query

        for query in queries:
            text = normalize_zo_kb_query(
                str(query).strip(),
                rfp_client=rfp_client,
                rfp_sector=rfp_sector,
                rfp_title=rfp_title,
            )
            if text and text.lower() not in used:
                cleaned.append(text[:240])
                used.add(text.lower())
        return cleaned[:4]
    except (LlmError, Exception) as exc:
        logger.warning("Query planner agent failed: %s", exc)
        return []


async def redraft_section_agent(
    *,
    role: Literal[AgentRole.SECTION_REPAIR, AgentRole.USER_REVISE, AgentRole.SURGICAL_FIX],
    rfp_id: str,
    rfp_title: str,
    rfp_client: str,
    user_content: str,
    rfp_sector: str = "",
) -> tuple[dict[str, Any], str, list[str]]:
    """KB tool agent → JSON with content field."""
    return await run_tool_json_agent(
        role=role,
        sector=rfp_sector,
        rfp_id=rfp_id,
        title=rfp_title,
        client=rfp_client,
        user_content=user_content,
    )
