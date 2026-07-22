from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.models.go_no_go import (
    DECISION_MATRIX_DIMENSIONS,
    GoNoGoAnalysis,
    GoNoGoDimension,
    GoNoGoEvaluation,
    GoNoGoFlag,
)
from app.models.rfp import RfpRecord
from app.services import llm, supermemory
from app.services.rfp_content import combine_rfp_text, load_local_rfp_text, resolve_rfp_pdf_path
from app.services.pdf_text import IMAGE_ONLY_TEXT_THRESHOLD
from app.services.rfp_repository import get_rfp_pdf_path
from app.services.evidence_trust.rfp_hard_facts import (
    evaluation_table_is_reliable,
    extract_rfp_hard_facts,
)
from app.services.proposal_rfp_excerpt import build_priority_rfp_excerpt

EVALUATION_QUESTIONS: list[tuple[str, str]] = [
    (
        "scope_lane",
        "Does this RFP request marketing, branding, or communications work — and is the scope "
        "in zö's lane (not civil engineering, legal, clinical, software engineering, or construction)?",
    ),
    (
        "scope_capabilities",
        "Which specific scope items map to zö's documented capabilities in the knowledge base?",
    ),
    (
        "sector_fit",
        "Does the client type match zö's primary sectors (government/municipal, higher ed, "
        "healthcare, corporate, nonprofit) based on documented experience? Separate leisure/"
        "destination tourism from MCI/meetings work when the RFP excludes MCI.",
    ),
    (
        "compliance_certs",
        "Are required certifications (WBENC, WOSB, COBID, DBE, etc.) listed — and does zö hold "
        "each one as an agency-level credential per the knowledge base? Individual platform "
        "certs (Google Ads, Meta Ads) on one specialist do NOT count as agency-wide Verified.",
    ),
    (
        "compliance_registration",
        "Does the RFP require state registration in states where zö is documented as registered "
        "(OR, WA, TX, CO, CA)?",
    ),
    (
        "compliance_insurance",
        "Are insurance limits or mandatory submission documents required — and are they verified "
        "or flagged against the knowledge base?",
    ),
    (
        "offeror_presence",
        "Does the RFP require the Offeror (prime) to have or establish a physical office / "
        "local presence? If yes, treat this as a structural Offeror requirement — a "
        "subcontractor or Attachment-03 partner does NOT automatically satisfy 'Offeror must "
        "have or establish an office.' Flag for counsel/leadership before scoring as fixable.",
    ),
    (
        "team_roles",
        "What roles or specialized expertise does the RFP require — and are matching approved "
        "bios documented in the knowledge base?",
    ),
    (
        "evidence_provenance",
        "For every destination/tourism/municipal proof cited: is the source 03_CS or 06_WON "
        "(zö win/case study) vs 07_FIN (finalist/loss)? Do not treat 07_FIN as won experience. "
        "Flag competitor-authored content (e.g. Resonance) if it appears in KB excerpts.",
    ),
    (
        "worth_it",
        "Is this contract strategically and financially worth pursuing (budget, competition, "
        "timeline, sector value) independent of fit?",
    ),
]

PLACEHOLDER_CLIENTS = frozenset(
    {"demo", "example", "test", "tbd", "client", "client name", "city of example"}
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SEARCH_NOISE_MARKERS = (
    "we're sorry but justwin",
    "doesn't work properly",
    "enable javascript",
    "please enable javascript",
)

SCORING_RUBRIC = """SCORING (integers 0–5, or null if insufficientData) — derive every score from THIS RFP only.

CRITICAL: Do NOT reuse the same scores across different RFPs. Do NOT default to 3/2. Each score must reflect
this solicitation's scope match, contract value, evaluation weights, geography, compliance risks, and competition.

fitScore (internal only — do NOT show as "AI Fit Score" in stageOneReport or summary):
  Capability + sector + compliance + team vs KB. Keep for JSON compatibility; Overall Go Score is what users see.
  5 = near-perfect documented match (scope, sector case studies, compliance, team)
  4 = strong match with minor gaps
  3 = in-lane but meaningful gaps (registration, sector proof, experience minimums)
  2 = partial match; major documented gaps
  1 = marginal / mostly unverified
  0 = out of lane

worthScore ("Worth It Score") — financial return vs pursuit effort:
  5 = high disclosed value, favorable fee structure, strong win path
  4 = solid value and reasonable effort
  3 = moderate value or mixed signals (including right-sized public-sector scopes with undisclosed budget)
  2 = modest value with heavy pursuit overhead OR clearly poor economics
  1 = poor return relative to effort
  0 = not worth pursuing
  Do NOT set Worth ≤ 2 solely because budget is undisclosed when the scope is otherwise in-lane and
  pursuit effort is normal. Undisclosed budget alone → usually Worth 3 (mixed), not 2.

decisionMatrix — exactly 5 rows; each score is independent (they will often differ):
  1. Technical Capability Match — scope execution per KB
  2. Resource Availability — team bandwidth, geography, live-demo/on-site needs
  3. Financial Viability — agency revenue vs cost (use commission math when budget is mostly media spend)
  4. Strategic Value — reference value, sector/geography expansion
  5. Win Probability — competition, proximity, scoring criteria alignment, disqualification risk

Overall Go Score = arithmetic average of the 5 decisionMatrix scores (not fitScore/worthScore).
Use the full 0–5 range. Strong RFPs with local presence and high contract value should score 4–5 on several dimensions.
Weak or distant low-value RFPs should score 1–2 on Financial Viability and Win Probability.

EVIDENCE CALIBRATION (accurate — neither reject-everything NOR invent pessimism):
- Score each matrix row against THIS RFP's stated requirements and the KB excerpts returned for the searches run.
- Do not invent KB proof. Missing evidence for a required capability → discount that row and note the gap.
- FLOORS when KB proof is strong: If KB returns a near-direct case study for the RFP's core scope
  (same work type + sector/use-case, e.g. coalition health communications for a health-policy RFP),
  Technical Capability Match should normally be ≥ 4 and Win Probability should not be ≤ 2 unless
  there is a separate structural blocker (office DQ, deadline, out-of-lane, disqualifying compliance).
- Do NOT invent evaluation point weights, percentages, or totals. If HARD FACTS say evaluation points
  were not found, write "RFP does not disclose a point-weighted table" and score Financial/Win from
  real signals only (scope fit, competition, logistics, disclosed budget). Never invent "62% cost"
  or duplicate Cost/Experience rows to justify a low score.
- Do NOT invent team-member names. Only cite people who appear in KB excerpts (bios/org). Unknown
  roles → [FLAG FOR SONJA: assign …], never a fabricated name.
- Fixable gaps (state registration, insurance verify, assign social lead) → prefer recommendation=review
  with conditions; do not tank Overall into the 2.x range solely for those.
- Do not auto-recommend no_go solely because Overall < 3; use "review" when gaps are fixable.
- Prefer "no_go" only for: out-of-lane scope, disqualifying verified compliance failure, deadline passed
  with late-submission DQ, or clearly poor fit+worth with no credible path.
- When Overall < 3, criticalGaps MUST list concrete RFP/KB-backed reasons (not invented point math).

HARD CAPS (do not overscore):
- If the RFP requires the Offeror to have/establish an office in a geography where KB shows no
  zö office, Resource Availability must be ≤ 2/5 until counsel/leadership confirms a compliant path.
  Do NOT score this as a routine "hire a local sub" fix — Offeror ≠ subcontractor unless the RFP
  expressly allows it.
- Technical Capability Match must discount MCI/meetings references when the RFP excludes MCI, and
  must not count 07_FIN finalist/loss files as won destination experience. Thin leisure-tourism
  proof after those discounts → Technical Capability usually ≤ 2–3/5, not 4–5.
- Prefer recommendation=review (not go) when an Offeror-office structural gap OR contaminated/
  competitor KB evidence remains unresolved."""

SYSTEM_PROMPT = """You are the Stage 1 Fit Analyst for zö agency (full-service marketing, branding, and media buying).

Compare the RFP against ONLY the provided knowledge-base excerpts. Never invent capabilities, certifications,
team members, insurance, case studies, or past work. Flag gaps explicitly with [VERIFY] when human follow-up is needed.

PROCESS:
1. Answer every evaluation question in "evaluations".
2. Write a comprehensive "stageOneReport" in Markdown (see structure below).
3. Set fitScore, worthScore, recommendation, dimensions, criticalGaps, and conditions.

INSUFFICIENT RFP CONTENT:
If scope, deliverables, budget, compliance, or team requirements are missing:
- insufficientData=true, recommendation=null, fitScore=null, worthScore=null
- Populate clarifyingQuestions; stageOneReport should explain what is missing
- Do NOT call missing scope "out of lane"

OUT OF LANE (no_go only when explicit):
Scope clearly outside marketing/branding/communications (engineering, legal, clinical, software dev, construction).

EVIDENCE HYGIENE (mandatory — these errors have changed real Go/No-Go outcomes):
1. Offeror office / local presence: If the RFP says the Offeror must have or establish an office,
   that is a PRIME obligation. Do NOT reframe it as "engage an Oceania/local subcontractor and
   document in Attachment 03" unless the RFP text expressly allows subcontractors to satisfy the
   Offeror office requirement. Add a critical condition for counsel/Sonja before recommending Go.
2. Individual vs agency certifications: Google Ads / Meta Ads (and similar platform certs) belonging
   to one specialist (e.g. Vishal Nihlani) are personal credentials. Mark capability rows Verified
   for agency-wide platform certification ONLY if KB shows an agency-level credential. Otherwise
   Status = Gap or [VERIFY: individual only — not agency-wide].
3. Filename provenance: 06_WON = won/usable zö win material; 07_FIN = finalist/loss — NOT a win.
   Never cite 07_FIN work (e.g. City of San Leandro) as documented won destination-marketing
   experience. If excerpts credit another agency (e.g. Resonance) or a non-zö case study sitting
   inside a FIN file, flag as contaminated/competitor intelligence — not zö experience.
4. MCI mismatch: If the RFP excludes meetings/conventions/incentives (MCI) or leisure-only
   destination work, do not count meetings-heavy references (e.g. San Francisco Travel) as
   matching destination/leisure proof without an explicit mismatch note and score discount.
5. Prefer "review" over "go" when (a) Offeror-office legality is unresolved or (b) reusable
   experience depends on 07_FIN / competitor-contaminated files.
6. HARD RFP FACTS: If the user prompt lists CONTRACT VALUE or EVALUATION CRITERIA / POINTS
   extracted from the RFP body, you MUST use those numbers in EXECUTIVE SUMMARY, Financial
   Viability, Win Probability, and the evaluation table. NEVER write "budget not disclosed",
   "contract value not disclosed", or "RFP does not specify point allocations" when those
   extractions are present. Guessing from transaction-fee caps while ignoring an explicit
   ceiling is an analytical error.
7. NEVER INVENT EVALUATION MATH: If HARD FACTS say the evaluation point table was not found,
   do NOT fabricate Category/Max Points rows, percentages, or totals (no duplicate Cost/Experience
   rows, no "62% cost-heavy"). State that weights are undisclosed and score without them.
8. NEVER INVENT PEOPLE: Do not name team members unless they appear in the KB excerpts provided.
   Common documented leads include Sonja Anderson, Todd Anderson, Ron Comer, Ella Lindau,
   Curt Schultz, Justin Bronson, Gil Aranowitz — but ONLY cite a name if the KB excerpt supports it.
   Unknown roles → [FLAG FOR SONJA: assign …], never invent a Project Lead name.
9. NEAR-DIRECT CASE STUDIES: Scan KB excerpts for closest work-type matches (e.g. Recovery Network
   of Oregon / RNO for coalition health/stigma communications). If present, cite them as Verified
   and raise Technical Capability / Win Probability accordingly — do not mark "health policy" as a
   Gap while ignoring that proof.

""" + SCORING_RUBRIC + """

RECOMMENDATION:
- "go": strong fit and worthwhile; deadline not passed (or extension confirmed); no unresolved
  Offeror-office structural gap; tourism/destination proof is 03_CS/06_WON (not 07_FIN alone)
- "no_go": out-of-lane OR disqualifying verified compliance gap OR poor fit + low worth OR proposal deadline passed with late-submission disqualification
- "review": Go With Conditions — fixable gaps or mixed signals; DEFAULT when Offeror-office
  requirement needs legal read, or when sector proof is thin after stripping MCI-mismatched /
  07_FIN citations; also use when deadline passed but re-solicit/override may be possible

DEADLINE CHECK (required — use today's date provided in the user prompt):
- Compare proposal deadline from the RFP (and metadata due date) against today's date.
- If deadline has passed and the RFP states late proposals are not accepted, lead the EXECUTIVE SUMMARY with that fact and cite the RFP section.
- Set recommendation to "no_go" when late submission is an explicit disqualifier and deadline has passed.
- Still complete the full analysis (capability, compliance, scoring) and add conditions for leadership override if re-solicit is possible.
- Populate the "deadline" object and mention deadline status in summary.

stageOneReport — comprehensive Markdown matching a senior analyst brief. Be exhaustive and RFP-specific:

## EXECUTIVE SUMMARY
Open with deadline status vs today's date when relevant. Client, project, solicitation number, deadline (with timezone if stated),
contract value/term, Worth It Score X/5 (1-sentence why), Overall Go Score X/5 (matrix average, 1-sentence why), Recommendation label.
Do NOT mention "AI Fit Score" or fitScore in the report text.

## COMPLIANCE SNAPSHOT
### Mandatory Documents Required
Bulleted pass/fail disqualifiers — every required attachment, form, reference, insurance cert, sealed package rule.
### Submission Format
Electronic vs hard copy, email/portal, subject line, page limits, separate technical/cost packages, numbering, validity period.
### Disqualification Risks
Explicit instant-rejection triggers from the RFP (pricing in technical proposal, missing signatures, late submission, etc.).
### State/Registration Requirements
Vendor registration, tax registration, DBE/MBE/WBE programs, insurance limits with dollar amounts.
Use [FLAG FOR NAME/ROLE: ...] for human follow-up on registration, certifications, or compliance posture.

## CAPABILITY ASSESSMENT
### Technical and Service Requirements vs. zö Capabilities
When the RFP lists service categories or deliverables, enumerate each with "— Yes" or "— Gap" and KB evidence.
Never mark Google Ads / Meta Ads (or similar) as agency Verified unless KB shows agency-level certs.
### Required Industry Experience vs. Documented Experience
Sector/client-type match with named case studies from KB; flag thin reference depth.
Cite 03_CS / 06_WON only as wins. Label any 07_FIN citation as finalist/loss. Note MCI mismatches.
### Required Team Roles vs. Actual Team
Map RFP roles to documented zö team members; [FLAG: ...] for account lead or presentation assignments.
### Offeror presence / office requirements
If RFP requires Offeror office establishment, call it out as structural (not a staffing/sub fix) with owner flag.
Markdown table when helpful: RFP Requirement | zö Capability (KB source + 03_CS/06_WON/07_FIN) | Status (Verified/Gap/[VERIFY])

## EVALUATION CRITERIA BREAKDOWN
If HARD FACTS include evaluation point rows: Table Category | Max Points | zö Strength | Vulnerability
using ONLY those extracted weights (they must sum consistently — never invent extra Cost/Experience rows).
If HARD FACTS say point allocations were NOT found: write clearly that the RFP does not disclose a
point-weighted table (pass/fail + scored question groups are fine to describe narratively). Do NOT
invent percentages or point totals. Note where effort should concentrate based on question groups only.

## COMPETITIVE CONTEXT
Likely competitors, zö positioning advantages (bullets), red flags for this client type (bullets).

## GO/NO-GO DECISION MATRIX
Table: Dimension | Score (X/5) | Notes

## FINAL RECOMMENDATION
GO / GO WITH CONDITIONS / NO-GO (include "— DEADLINE PASSED" when applicable).
Numbered conditions with [Owner] tags. If no_go due to deadline, note re-solicit monitoring steps.

Also populate "actionFlags" array with every [FLAG...] line from the report (full text of each flag).

Flag severity must be exactly one of: info, warning, critical (never high/medium/low).

Return ONLY valid JSON.
{
  "insufficientData": false,
  "fitScore": 0,
  "worthScore": 0,
  "recommendation": "go",
  "summary": "2-3 sentence executive summary for the dashboard (mention Worth It + Overall; never say AI Fit Score)",
  "stageOneReport": "## EXECUTIVE SUMMARY\\n...",
  "decisionMatrix": [
    {"dimension": "Technical Capability Match", "score": 0, "notes": "RFP-specific rationale citing scope and KB"},
    {"dimension": "Resource Availability", "score": 0, "notes": "RFP-specific rationale"},
    {"dimension": "Financial Viability", "score": 0, "notes": "RFP-specific rationale with budget/fee math when available"},
    {"dimension": "Strategic Value", "score": 0, "notes": "RFP-specific rationale"},
    {"dimension": "Win Probability", "score": 0, "notes": "RFP-specific rationale using evaluation criteria and competition"}
  ],
  "evaluations": [{"id": "scope_lane", "question": "...", "answer": "...", "impact": "..."}],
  "scopeMatch": {"summary": "...", "scoreImpact": "...", "flags": [{"category": "scope", "severity": "warning", "message": "..."}]},
  "sectorMatch": {"summary": "...", "scoreImpact": "...", "flags": []},
  "compliance": {"summary": "...", "scoreImpact": "...", "flags": []},
  "teamMatch": {"summary": "...", "scoreImpact": "...", "flags": []},
  "criticalGaps": [],
  "conditions": ["Condition 1 — ... [Owner]"],
  "actionFlags": ["[FLAG FOR ELLA: Confirm Tennessee registration pathway]"],
  "deadline": {
    "today": "YYYY-MM-DD",
    "dueDate": "YYYY-MM-DD",
    "daysRemaining": 0,
    "isPast": false,
    "isToday": false,
    "lateSubmissionDisqualifies": false,
    "note": "Deadline assessment narrative"
  },
  "clarifyingQuestions": []
}"""

KB_QUERY_PLANNER_PROMPT = """You plan targeted Supermemory knowledge-base searches for zö agency Go/No-Go analysis.
Given an RFP excerpt, return 14-20 specific search queries. Cover ALL four passes below that apply to THIS RFP.

PASS 1 — RFP-driven capability searches (map solicitation scope → KB proof):
- Core deliverables / service categories named in the RFP (communications, social marketing, web, brand, media, etc.)
- Sector + use-case (higher education, health policy, public sector, tourism, municipal, coalition, stigma, etc.)
- Geography / registration / insurance / preference law for the buyer state
- Team roles the RFP names (account lead, social media, creative, PM)

PASS 2 — Verification / roster / client-list hygiene:
- 01_ClientList_Approved Public Yes Confirm status for analogous clients
- 04_Bio / MasterTemplate org roster for named roles (never invent people later)
- 01_companyfacts certifications (WBENC/WOSB agency-level only; Google/Meta as individual vs agency)
- Pricing guide verified vs proposed anchors if budget/media spend matters

PASS 3 — Claim↔tag / provenance:
- Separate 03_CS and 06_WON queries for wins matching the RFP work type
- Separate 07_FIN queries so finalist/loss files are surfaced and NOT counted as wins
- Work-type tagged searches (do not rely on brand-only clients for web claims, etc.)
- If RFP excludes MCI/meetings, search leisure destination AND SF Travel/MCI separately for mismatch detection
- Near-direct analogs: health coalition / Recovery Network of Oregon / stigma / multi-language when health RFP

PASS 4 — Human-gate surfacing (still search so flags have context):
- Offeror office / establish presence requirements
- Conflicts / prior work for this buyer or university system
- Insurance / E-Verify / reciprocal preference

REQUIRED QUERY TYPES (include all that apply):
1. Agency-level certifications — WBENC, WOSB, COBID, insurance (01_companyfacts)
2. Offeror / office / geography vs subcontractor/JV as SEPARATE queries
3. Won vs finalist provenance (06_WON + 07_FIN with sector keywords)
4. Case studies (03_CS) closest to RFP scope — not only same sector label
5. Team bios (04_Bio) for specialized roles
6. Compliance/registration for the RFP jurisdiction
7. ClientList Approved entries matching work type

Use the client name, location, sector, and specific deliverables from the RFP in your queries.
Prefer filename/bucket tokens: 01_companyfacts, 01_ClientList, 03_CS, 04_Bio, 06_WON, 07_FIN.
Do NOT include HTML, JavaScript errors, or portal boilerplate in queries.
Return ONLY JSON: {"queries": ["query 1", "query 2", ...]}"""


DOCUMENTED_TEAM_SEARCH = (
    "zö agency 04_Bio MasterTemplate team roster Sonja Anderson Todd Anderson Ron Comer "
    "Ella Lindau Curt Schultz Justin Bronson Gil Aranowitz"
)


def _deterministic_evidence_queries(rfp: RfpRecord, content: RfpContentInfo) -> list[str]:
    """Always-on Supermemory queries that prevent known Go/No-Go evidence mistakes."""
    sample = combine_rfp_text(content.description, content.pdf_text)[:25_000]
    sector = (rfp.sector or "").strip()
    client = (rfp.client or "").strip()
    location = (rfp.location or "").strip()
    title = (rfp.title or "").strip()

    queries = [
        "zö agency 01_companyfacts WBENC WOSB certifications agency-level verified",
        "zö agency Google Ads Meta Ads certification Vishal Nihlani PPC individual credential not agency",
        "zö agency 06_WON won proposal destination tourism leisure visitor marketing",
        "zö agency 07_FIN finalist loss San Leandro destination marketing not a win",
        "zö agency 03_CS Deschutes Brewery Oregon Employment Department City of Umatilla case studies",
        "zö agency San Francisco Travel reference meetings conference MCI tourism",
        "zö agency office locations Hawaii Oceania physical presence geography",
        DOCUMENTED_TEAM_SEARCH,
        "zö agency 01_ClientList_Approved Public Yes Confirm work type tags",
    ]
    if sector:
        queries.append(f"zö agency 03_CS {sector} case study won experience")
        queries.append(f"zö agency 06_WON {sector} proposal past performance")
        queries.append(f"zö agency 07_FIN {sector} finalist proposal loss")
    if client:
        queries.append(f"zö agency {client} case study reference 03_CS 06_WON")
        queries.append(f"zö agency prior work conflict {client} university system")
    if location:
        queries.append(f"zö agency {location} office registration vendor presence")
    if title:
        queries.append(f"zö agency 03_CS capabilities matching {title[:120]}")

    if re.search(
        r"\boffice\b.{0,80}(?:Oceania|Hawai|Hawaii|Must have or must establish)|"
        r"(?:Oceania|Hawai|Hawaii).{0,80}\boffice\b|"
        r"Offeror must (?:have|establish).{0,40}office",
        sample,
        re.IGNORECASE | re.DOTALL,
    ):
        queries.extend(
            [
                "zö agency Offeror office Oceania Hawaii establish physical location",
                "zö agency Hawaii Oceania partner joint venture subcontractor Attachment 03",
            ]
        )

    if re.search(
        r"\bMCI\b|meetings?.{0,20}convention|exclude.{0,40}(?:meeting|convention|incentive)|"
        r"destination brand|visitor arrivals|leisure travel|tourism authorit",
        sample,
        re.IGNORECASE,
    ):
        queries.extend(
            [
                "zö agency destination brand leisure tourism visitor marketing 03_CS 06_WON",
                "zö agency MCI meetings incentives convention marketing experience",
                "zö agency San Francisco Travel Association meetings destination marketing",
            ]
        )

    if re.search(
        r"cultural advisor|Oceania.{0,40}specialist|Hawaii.{0,40}specialist|"
        r"indigenous|Native Hawaiian|malama",
        sample,
        re.IGNORECASE,
    ):
        queries.append(
            "zö agency cultural advisor Oceania Hawaii market specialist team bio 04_Bio"
        )

    if re.search(r"Google Ads|Meta Ads|platform certif", sample, re.IGNORECASE):
        queries.append(
            "zö agency platform certifications Google Ads Meta Ads individual vs company"
        )

    # Health / coalition / social marketing — catch near-direct proof (e.g. RNO)
    if re.search(
        r"health\s+polic|ARCHI|public\s+health|stigma|coalition|social\s+market|"
        r"behavioral\s+health|recovery|substance|community\s+engagement|"
        r"lived\s+experience|multi-?language|peer\s+support",
        sample,
        re.IGNORECASE,
    ) or re.search(
        r"health\s+polic|social\s+market|ARCHI|coalition",
        f"{title} {client} {sector}",
        re.IGNORECASE,
    ):
        queries.extend(
            [
                "zö agency Recovery Network of Oregon RNO coalition stigma communications case study 03_CS",
                "zö agency Oregon Recovers health stigma social marketing multi-language 03_CS 06_WON",
                "zö agency health policy communications public health coalition campaign case study",
                "zö agency social marketing communications strategy public sector 03_CS",
                "zö agency culturally sensitive messaging community engagement lived experience",
            ]
        )

    if re.search(
        r"higher\s+education|universit|college|GSU|Georgia\s+State",
        f"{sample} {title} {client}",
        re.IGNORECASE,
    ):
        queries.extend(
            [
                "zö agency University of Idaho Benedictine higher education case study ClientList",
                "zö agency university college higher education communications marketing 03_CS",
            ]
        )

    return queries


def _annotate_go_no_go_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Tag FIN vs WON and competitor markers so the analyst cannot misread provenance."""
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    title = str(
        hit.get("title")
        or metadata.get("title")
        or metadata.get("fileName")
        or hit.get("customId")
        or ""
    )
    body = supermemory.hit_text(hit)[:4000]
    label_cf = title.casefold()
    body_cf = body.casefold()
    tags: list[str] = []
    if "07_fin" in label_cf or re.search(r"\b07[_-]?fin\b", label_cf):
        tags.append(
            "PROVENANCE: 07_FIN = FINALIST/LOSS — do NOT count as won zö experience"
        )
    if "06_won" in label_cf or re.search(r"\b06[_-]?won\b", label_cf):
        tags.append("PROVENANCE: 06_WON = won material — verify content is zö's")
    if "03_cs" in label_cf:
        tags.append("PROVENANCE: 03_CS case study")
    if "resonance" in body_cf or "resonance" in label_cf:
        tags.append(
            "WARNING: excerpt may credit Resonance (competitor) — not zö experience"
        )
    if "lynchburg" in body_cf and ("resonance" in body_cf or "07_fin" in label_cf):
        tags.append(
            "WARNING: Lynchburg Economic Development content may be competitor CI"
        )
    if not tags:
        return hit
    annotated = dict(hit)
    annotated["title"] = f"{title} [{' | '.join(tags)}]"
    return annotated


def _format_go_no_go_kb_hits(
    hits: list[dict[str, Any]],
    *,
    max_chars: int,
    queries: list[str] | None = None,
) -> str:
    annotated = [_annotate_go_no_go_hit(hit) for hit in hits]
    header_parts = [
        "KB PROVENANCE LEGEND (apply before scoring):\n"
        "- 06_WON = win / reusable zö proposal material\n"
        "- 07_FIN = finalist/loss — NOT a win; never cite as documented won experience\n"
        "- Individual Google/Meta Ads certs ≠ agency-wide Verified capability\n"
        "- Offeror office requirements are prime obligations unless RFP says otherwise\n"
    ]
    if queries:
        listed = "\n".join(f"- {q}" for q in queries[:40])
        header_parts.append(
            f"\nKB SEARCHES RUN ({min(len(queries), 40)} of {len(queries)} shown) — "
            "score ONLY from returned excerpts; missing proof is a gap, not a yes:\n"
            f"{listed}\n"
        )
    header_parts.append("\n")
    header = "".join(header_parts)
    body = supermemory.format_search_hits(annotated, max_chars=max(0, max_chars - len(header)))
    if not body:
        return body
    return header + body


def _merge_kb_hits_round_robin(
    results: list[list[dict[str, Any]]],
    *,
    max_hits: int = 120,
) -> list[dict[str, Any]]:
    """Interleave hits across queries so later planned searches are not starved."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    index = 0
    while len(merged) < max_hits:
        progressed = False
        for hits in results:
            if index >= len(hits):
                continue
            hit = hits[index]
            key = str(hit.get("id") or hit.get("customId") or hit.get("content", "")[:80])
            progressed = True
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)
            if len(merged) >= max_hits:
                break
        if not progressed:
            break
        index += 1
    return merged


KB_SEARCH_LIMIT = 8
KB_CONTEXT_MAX_CHARS = 45_000
RFP_PROMPT_MAX_CHARS = 50_000

MIN_SUBSTANTIVE_CHARS = 400

logger = logging.getLogger(__name__)


class GoNoGoError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class RfpContentInfo:
    def __init__(
        self,
        *,
        pdf_path: str | None,
        pdf_path_recorded: str | None = None,
        pdf_file_missing: bool = False,
        pdf_exists: bool = False,
        pdf_page_count: int = 0,
        pdf_image_only: bool = False,
        pdf_text: str,
        description: str,
        substantive_chars: int,
        metadata_only: bool,
    ) -> None:
        self.pdf_path = pdf_path
        self.pdf_path_recorded = pdf_path_recorded
        self.pdf_file_missing = pdf_file_missing
        self.pdf_exists = pdf_exists
        self.pdf_page_count = pdf_page_count
        self.pdf_image_only = pdf_image_only
        self.pdf_text = pdf_text
        self.description = description
        self.substantive_chars = substantive_chars
        self.metadata_only = metadata_only

    @property
    def has_pdf(self) -> bool:
        return self.pdf_exists and not self.pdf_file_missing

    @property
    def pdf_extracted(self) -> bool:
        return len(self.pdf_text) >= IMAGE_ONLY_TEXT_THRESHOLD


def _is_metadata_shell(rfp: RfpRecord, substantive_chars: int) -> bool:
    client = rfp.client.strip().lower()
    title = rfp.title.strip().lower()
    return (
        substantive_chars < MIN_SUBSTANTIVE_CHARS
        or client in PLACEHOLDER_CLIENTS
        or title in {"rfp 1", "test rfp", "test manual rfp"}
        or bool(re.match(r"^rfp\s*\d+$", title))
    )


def _assess_rfp_content(rfp: RfpRecord) -> RfpContentInfo:
    description, pdf_text, pdf_exists, pdf_file_missing, page_count, image_only = load_local_rfp_text(
        rfp
    )
    pdf_path_recorded = rfp.pdf_path or get_rfp_pdf_path(rfp.id)
    resolved = resolve_rfp_pdf_path(rfp.id, pdf_path_recorded)
    substantive_chars = len(combine_rfp_text(description, pdf_text))

    return RfpContentInfo(
        pdf_path=str(resolved) if resolved else None,
        pdf_path_recorded=pdf_path_recorded,
        pdf_file_missing=pdf_file_missing,
        pdf_exists=pdf_exists,
        pdf_page_count=page_count,
        pdf_image_only=image_only,
        pdf_text=pdf_text,
        description=description,
        substantive_chars=substantive_chars,
        metadata_only=_is_metadata_shell(rfp, substantive_chars),
    )


def _pending_dimension(message: str) -> GoNoGoDimension:
    return GoNoGoDimension(
        summary=message,
        scoreImpact="Pending — full RFP content required before scoring.",
        flags=[
            GoNoGoFlag(
                category="insufficient_data",
                severity="warning",
                message=message,
            )
        ],
    )


def _default_clarifying_questions(content: RfpContentInfo) -> list[str]:
    questions: list[str] = []
    if content.pdf_image_only:
        pages = content.pdf_page_count
        page_note = f" ({pages} pages)" if pages > 0 else ""
        questions.append(
            f"The RFP PDF is stored{page_note} but appears to be a scan or image-only file — "
            "the system cannot read its text. Paste the scope into the description field, "
            "or re-upload a text-based (selectable-text) PDF."
        )
    elif content.has_pdf and not content.pdf_extracted:
        questions.append(
            "The uploaded PDF has little or no extractable text — add a description of the RFP "
            "scope or upload a text-based PDF."
        )
    questions.extend(
        [
            "Provide the full scope of work, deliverables, and services requested.",
            "Identify the issuing agency or client (legal name, department, and jurisdiction).",
            "Include budget or contract value, timeline, and submission deadline details.",
            "List required certifications, state registrations, insurance limits, and mandatory forms.",
            "Specify required team roles, staffing, and any specialized expertise.",
        ]
    )
    return questions


def _needs_input_summary(rfp: RfpRecord, content: RfpContentInfo) -> str:
    if content.pdf_image_only:
        pages = content.pdf_page_count
        page_note = f" ({pages} pages in storage)" if pages > 0 else " (in storage)"
        return (
            f"'{rfp.title}' has a PDF{page_note}, but it is image-only — no machine-readable text "
            "could be extracted for Go/No-Go scoring. Paste scope into the description field or "
            "upload a text-based PDF, then re-run analysis."
        )
    if content.pdf_file_missing:
        return (
            f"'{rfp.title}' references a PDF that is missing from storage. Re-upload the RFP PDF "
            "or add a description with the full scope, then re-run analysis."
        )
    if content.has_pdf and not content.pdf_extracted:
        return (
            f"'{rfp.title}' has a PDF with little extractable text. Add a description with the "
            "full scope or upload a text-based PDF, then re-run analysis."
        )
    return (
        f"'{rfp.title}' does not include enough substance to run Go/No-Go scoring. "
        "Add the full RFP scope (via PDF text or description), then re-run analysis."
    )


def _build_needs_input_analysis(rfp: RfpRecord, content: RfpContentInfo) -> GoNoGoAnalysis:
    questions = _default_clarifying_questions(content)
    if content.pdf_image_only:
        pages = content.pdf_page_count
        pending_msg = (
            f"The RFP PDF is in storage ({pages} pages) but is image-only — the viewer can display "
            "it, yet no text can be extracted for automated scoring."
        )
    else:
        pending_msg = (
            "This record has only basic metadata (title, client, due date) — not enough to score fit "
            "or issue a Go/No-Go decision."
        )
    evaluations = [
        GoNoGoEvaluation(
            id=qid,
            question=question,
            answer="Cannot answer — required RFP content is missing.",
            impact="Scoring blocked until full RFP is provided.",
        )
        for qid, question in EVALUATION_QUESTIONS
    ]

    return GoNoGoAnalysis(
        fitScore=None,
        worthScore=None,
        recommendation=None,
        insufficientData=True,
        summary=_needs_input_summary(rfp, content),
        evaluations=evaluations,
        scopeMatch=_pending_dimension(pending_msg),
        sectorMatch=_pending_dimension("Sector cannot be assessed without a real client or jurisdiction."),
        compliance=_pending_dimension("No compliance requirements are present to verify."),
        teamMatch=_pending_dimension("No team or staffing requirements are present to verify."),
        clarifyingQuestions=questions,
        stageOneReport="",
        provider="content-gate",
    )


def _sanitize_text_for_search(text: str, *, max_chars: int = 400) -> str:
    cleaned = _HTML_TAG_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    lowered = cleaned.lower()
    for marker in _SEARCH_NOISE_MARKERS:
        if marker in lowered:
            return ""
    if len(cleaned) < 40:
        return ""
    return cleaned[:max_chars]


def _build_scope_search_query(rfp: RfpRecord, content: RfpContentInfo) -> str:
    excerpt = _sanitize_text_for_search(
        combine_rfp_text(content.description, content.pdf_text),
        max_chars=300,
    )
    if excerpt:
        return f"zö agency capabilities {excerpt}"
    return (
        f"zö agency {rfp.title} {rfp.client} {rfp.sector} "
        f"{rfp.location or ''} scope requirements deliverables"
    ).strip()


def _build_scoring_factors(rfp: RfpRecord, content: RfpContentInfo) -> str:
    """Authoritative hard facts from FULL RFP text — never claim these are undisclosed if found."""
    text = combine_rfp_text(content.description, content.pdf_text)
    lines = [
        f"- Client: {rfp.client}",
        f"- Sector: {rfp.sector}",
        f"- Location: {rfp.location or '(not provided)'}",
    ]
    if rfp.estimated_value is not None:
        lines.append(f"- Estimated value (metadata): ${rfp.estimated_value:,}")

    hard = extract_rfp_hard_facts(text)
    if hard["contract_value_lines"]:
        lines.append("- CONTRACT VALUE (from RFP body — authoritative; do NOT say 'not disclosed'):")
        lines.extend(f"  • {row}" for row in hard["contract_value_lines"][:12])
    else:
        lines.append(
            "- Contract value: not found by extractor as a ceiling/budget — say budget is undisclosed. "
            "Do NOT cite small-business gross-receipts thresholds (e.g. $30M eligibility) as "
            "'a contract value reference found'."
        )

    eligibility = hard.get("eligibility_dollar_lines") or []
    if eligibility:
        lines.append(
            "- VENDOR/SMALL-BUSINESS ELIGIBILITY DOLLARS (NOT contract value — never cite as opportunity size):"
        )
        lines.extend(f"  • {row}" for row in eligibility[:6])

    if hard["evaluation_lines"]:
        lines.append(
            "- EVALUATION CRITERIA / POINTS (from RFP body — authoritative; "
            "do NOT say points are unspecified):"
        )
        lines.extend(f"  • {row}" for row in hard["evaluation_lines"][:16])
        if hard.get("evaluation_total"):
            lines.append(f"  • Detected point total ≈ {hard['evaluation_total']}")
    else:
        lines.append(
            "- Evaluation point table: NOT FOUND in RFP body by extractor. "
            "You MUST say point allocations are undisclosed. "
            "FORBIDDEN: inventing Category/Max Points tables, percentages "
            "(e.g. '62% cost'), duplicate Cost/Experience rows, or totals like '29 points'. "
            "Score Financial Viability and Win Probability WITHOUT invented weight math."
        )

    if hard["other_dollar_amounts"]:
        lines.append(
            "- Other dollar amounts in RFP: " + ", ".join(hard["other_dollar_amounts"][:10])
        )

    term_matches = re.findall(
        r"(\d+)\s*(?:-|\s)?\s*(?:month|year)s?",
        text[:12_000],
        flags=re.IGNORECASE,
    )
    if term_matches:
        lines.append(
            f"- Term lengths mentioned: {', '.join(list(dict.fromkeys(term_matches))[:6])}"
        )

    lines.append(
        "- REQUIRED: Quote contract value and evaluation weights in EXECUTIVE SUMMARY and "
        "EVALUATION CRITERIA BREAKDOWN when extracted above. Financial Viability and Win "
        "Probability MUST use these numbers when present — never invent 'budget unknown' or "
        "'no point allocations' when they appear here. When evaluation points were NOT found, "
        "do not invent them; do not depress scores with fake cost-weight percentages."
    )
    lines.append(
        "- TEAM NAMES: Only cite people appearing in KB excerpts. Never invent Project Leads "
        "(e.g. do not invent 'Drew Stone'). Use [FLAG FOR SONJA: assign role] instead."
    )
    return "\n".join(lines)


# Back-compat alias for tests / callers still importing the private name.
_extract_rfp_hard_facts = extract_rfp_hard_facts


_LATE_SUBMISSION_RE = re.compile(
    r"late\s+(?:proposal|bid|submission|response).{0,80}(?:not\s+(?:be\s+)?accepted|rejected|disqualified|returned)",
    re.IGNORECASE | re.DOTALL,
)
_FLAG_RE = re.compile(r"\[FLAG(?:\s+FOR\s+[^\]]+)?:[^\]]+\]", re.IGNORECASE)


def _parse_due_date(value: str | None) -> date | None:
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None


def _assess_deadline(rfp: RfpRecord, content: RfpContentInfo) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    metadata_due = _parse_due_date(rfp.due_date)
    text = combine_rfp_text(content.description, content.pdf_text)

    late_submission_disqualifies = bool(_LATE_SUBMISSION_RE.search(text[:40_000]))

    due = metadata_due
    days_remaining: int | None = None
    if due is not None:
        days_remaining = (due - today).days

    note_parts: list[str] = []
    if due is not None:
        note_parts.append(f"Metadata due date: {due.isoformat()}.")
        if days_remaining is not None:
            if days_remaining < 0:
                note_parts.append(
                    f"Deadline was {abs(days_remaining)} day(s) ago as of {today.isoformat()}."
                )
            elif days_remaining == 0:
                note_parts.append(f"Deadline is today ({today.isoformat()}).")
            else:
                note_parts.append(f"{days_remaining} day(s) remaining.")
    else:
        note_parts.append("No due date in RFP metadata — extract deadline from RFP body.")

    if late_submission_disqualifies:
        note_parts.append(
            "RFP text indicates late submissions are not accepted (explicit disqualifier)."
        )

    return {
        "today": today.isoformat(),
        "dueDate": due.isoformat() if due else None,
        "daysRemaining": days_remaining,
        "isPast": days_remaining is not None and days_remaining < 0,
        "isToday": days_remaining == 0,
        "lateSubmissionDisqualifies": late_submission_disqualifies,
        "note": " ".join(note_parts),
    }


def _build_deadline_context(deadline: dict[str, Any]) -> str:
    lines = [
        f"- Today's date (UTC): {deadline['today']}",
        f"- RFP metadata due date: {deadline.get('dueDate') or '(not set)'}",
    ]
    if deadline.get("daysRemaining") is not None:
        lines.append(f"- Days remaining (metadata): {deadline['daysRemaining']}")
    lines.append(f"- Deadline passed (metadata): {deadline.get('isPast')}")
    lines.append(
        f"- Late submission disqualification language in RFP: "
        f"{deadline.get('lateSubmissionDisqualifies')}"
    )
    lines.append(f"- Assessment: {deadline.get('note')}")
    if deadline.get("isPast") and deadline.get("lateSubmissionDisqualifies"):
        lines.append(
            "- REQUIRED: If confirmed by RFP body, set recommendation=no_go and lead "
            "EXECUTIVE SUMMARY with deadline-passed disqualification."
        )
    return "\n".join(lines)


def _extract_action_flags(*texts: str) -> list[str]:
    seen: set[str] = set()
    flags: list[str] = []
    for text in texts:
        for match in _FLAG_RE.finditer(text):
            flag = re.sub(r"\s+", " ", match.group(0)).strip()
            key = flag.casefold()
            if key not in seen:
                seen.add(key)
                flags.append(flag)
    return flags


def _truncate_rfp_text(text: str, *, max_chars: int = RFP_PROMPT_MAX_CHARS) -> str:
    return build_priority_rfp_excerpt(text, max_chars=max_chars)


async def _plan_knowledge_base_queries(
    rfp: RfpRecord,
    content: RfpContentInfo,
) -> list[str]:
    excerpt = _sanitize_text_for_search(
        combine_rfp_text(content.description, content.pdf_text),
        max_chars=8_000,
    )
    if not excerpt:
        excerpt = _truncate_rfp_text(
            combine_rfp_text(content.description, content.pdf_text),
            max_chars=8_000,
        )
    messages = [
        {"role": "system", "content": KB_QUERY_PLANNER_PROMPT},
        {
            "role": "user",
            "content": (
                f"Title: {rfp.title}\n"
                f"Client: {rfp.client}\n"
                f"Sector: {rfp.sector}\n"
                f"Location: {rfp.location or '(not provided)'}\n\n"
                f"RFP excerpt:\n{excerpt}"
            ),
        },
    ]
    try:
        raw, provider = await llm.chat_json(messages, max_tokens=1024, temperature=0.25)
        queries = raw.get("queries", [])
        if isinstance(queries, list):
            planned = [str(query).strip() for query in queries if str(query).strip()]
            logger.info(
                "Planned %d KB search queries for %s via %s",
                len(planned),
                rfp.id,
                provider,
            )
            return planned[:16]
    except llm.LlmError as exc:
        logger.warning("KB query planning failed for %s: %s", rfp.id, exc)
    return []


async def _gather_knowledge_context(
    rfp: RfpRecord,
    content: RfpContentInfo,
) -> str:
    if not supermemory.is_configured():
        return "(Knowledge base search unavailable — SUPERMEMORY_API_KEY not configured.)"

    planned = await _plan_knowledge_base_queries(rfp, content)
    sector_query = f"zö agency {rfp.sector} sector experience case studies similar clients"
    location_query = (
        f"zö agency {rfp.location} state registration vendor compliance"
        if rfp.location
        else ""
    )
    scope_query = _build_scope_search_query(rfp, content)

    queries: list[str] = []
    queries.append(sector_query)
    if location_query:
        queries.append(location_query)
    queries.append(scope_query)
    if rfp.client.strip():
        queries.append(f"zö agency {rfp.client} case study proposal references")

    rfp_sample = combine_rfp_text(content.description, content.pdf_text)[:20_000]
    if re.search(r"WCAG|Section 508|accessibility|VPAT|EPub", rfp_sample, re.IGNORECASE):
        queries.append("zö agency WCAG accessibility Section 508 VPAT compliance")
    if re.search(
        r"FTC Safeguard|data retention|data security|backup.{0,20}recovery",
        rfp_sample,
        re.IGNORECASE,
    ):
        queries.append(
            "zö agency data security FTC safeguard data retention backup recovery policy"
        )
    if re.search(
        r"higher education|university|college|TBR|community college",
        rfp_sample,
        re.IGNORECASE,
    ):
        queries.append(
            "zö agency higher education university college case studies references"
        )
    if re.search(r"housing authority|HUD|public housing", rfp_sample, re.IGNORECASE):
        queries.append("zö agency housing authority HUD public housing case study")
    queries.extend(_deterministic_evidence_queries(rfp, content))
    queries.extend(planned)

    seen_queries: set[str] = set()
    unique_queries: list[str] = []
    for query in queries:
        key = query.strip().lower()
        if not key or key in seen_queries:
            continue
        seen_queries.add(key)
        unique_queries.append(query.strip())

    async def run_query(query: str) -> list[dict[str, Any]]:
        try:
            hits = await supermemory.search_documents(
                query=query,
                limit=KB_SEARCH_LIMIT,
                filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
            )
            return [hit for hit in hits if supermemory.is_knowledge_base_hit(hit)]
        except supermemory.SupermemoryError:
            return []

    results = await asyncio.gather(*(run_query(query) for query in unique_queries))

    merged = _merge_kb_hits_round_robin(list(results))

    formatted = _format_go_no_go_kb_hits(
        merged,
        max_chars=KB_CONTEXT_MAX_CHARS,
        queries=unique_queries,
    )
    logger.info(
        "Supermemory KB search for %s: %d queries, %d unique hits, %d chars",
        rfp.id,
        len(unique_queries),
        len(merged),
        len(formatted),
    )
    return formatted or "(No knowledge base excerpts returned for this search.)"


def _build_rfp_context(rfp: RfpRecord, content: RfpContentInfo) -> str:
    parts = [
        f"Title: {rfp.title}",
        f"Client: {rfp.client}",
        f"Sector: {rfp.sector}",
        f"Location: {rfp.location or '(not provided)'}",
        f"Due date: {rfp.due_date}",
        f"Substantive content length: {content.substantive_chars} characters",
        f"Metadata-only shell: {content.metadata_only}",
    ]
    if rfp.estimated_value is not None:
        parts.append(f"Estimated value: ${rfp.estimated_value:,}")
    if content.description:
        parts.append(f"Description/summary:\n{content.description}")
    if content.pdf_text:
        rfp_body = _truncate_rfp_text(content.pdf_text)
        parts.append(f"RFP document text (local PDF extract, {content.substantive_chars:,} chars total):\n{rfp_body}")
    elif content.pdf_file_missing:
        parts.append(
            "RFP PDF was recorded for this record but the file is missing from storage. "
            "Re-upload the PDF."
        )
    elif content.pdf_image_only:
        pages = content.pdf_page_count
        parts.append(
            f"RFP PDF is in storage ({pages} pages) but is image-only — each page is a scan with "
            "no selectable text layer. Paste scope into the description or upload a text-based PDF."
        )
    elif content.has_pdf:
        parts.append(
            "RFP PDF is attached but little or no text could be extracted locally "
            "(possible scan or image-only PDF). Add a description with the scope."
        )
    elif not content.description and not content.pdf_text:
        parts.append(
            "No RFP body content is available yet. Upload a PDF or add a description, "
            "then re-run analysis."
        )

    return "\n\n".join(parts).strip()


def _evaluation_questions_block() -> str:
    lines = ["Answer each question in the evaluations array:"]
    for qid, question in EVALUATION_QUESTIONS:
        lines.append(f"- [{qid}] {question}")
    return "\n".join(lines)


def _coerce_score(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, min(5, value))
    if isinstance(value, float):
        return max(0, min(5, int(round(value))))
    if isinstance(value, str):
        match = re.search(r"(\d)", value.strip())
        if match:
            return max(0, min(5, int(match.group(1))))
    return None


def _normalize_recommendation(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = (
        value.strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("go_with_conditions", "review")
    )
    mapping = {
        "go": "go",
        "no_go": "no_go",
        "nogo": "no_go",
        "no": "no_go",
        "review": "review",
        "conditional_go": "review",
        "conditions": "review",
        "go_with_conditions": "review",
    }
    if normalized in mapping:
        return mapping[normalized]
    if "no" in normalized and "go" in normalized:
        return "no_go"
    if "review" in normalized or "condition" in normalized:
        return "review"
    if normalized == "go":
        return "go"
    return None


def _coerce_dimension(raw: object, *, fallback_summary: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"summary": fallback_summary, "scoreImpact": "", "flags": []}
    flags = raw.get("flags")
    normalized_flags: list[dict[str, str]] = []
    if isinstance(flags, list):
        for flag in flags:
            if not isinstance(flag, dict):
                continue
            message = str(flag.get("message") or "").strip()
            if not message:
                continue
            normalized_flags.append(
                {
                    "category": str(flag.get("category") or "general"),
                    "severity": _normalize_flag_severity(flag.get("severity")),
                    "message": message,
                }
            )
    return {
        "summary": str(raw.get("summary") or fallback_summary).strip(),
        "scoreImpact": str(raw.get("scoreImpact") or raw.get("score_impact") or "").strip(),
        "flags": normalized_flags,
    }


def _coerce_evaluations(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    evaluations: list[dict[str, str]] = []
    question_by_id = {qid: question for qid, question in EVALUATION_QUESTIONS}
    for item in raw:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip()
        if not qid:
            continue
        evaluations.append(
            {
                "id": qid,
                "question": str(item.get("question") or question_by_id.get(qid, qid)).strip(),
                "answer": str(item.get("answer") or "").strip(),
                "impact": str(item.get("impact") or "").strip(),
            }
        )
    return evaluations


def _coerce_go_no_go_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize LLM output before Pydantic validation (minimax often drifts schema)."""
    raw["summary"] = str(raw.get("summary") or "Go/No-Go analysis complete.").strip()
    raw["stageOneReport"] = str(raw.get("stageOneReport") or raw.get("stage_one_report") or "").strip()

    recommendation = _normalize_recommendation(raw.get("recommendation"))
    if recommendation is not None:
        raw["recommendation"] = recommendation
    elif raw.get("insufficientData"):
        raw["recommendation"] = None
    else:
        raw["recommendation"] = "review"

    for key in ("fitScore", "worthScore"):
        coerced = _coerce_score(raw.get(key))
        if coerced is not None:
            raw[key] = coerced
        elif raw.get("insufficientData"):
            raw[key] = None

    raw["scopeMatch"] = _coerce_dimension(
        raw.get("scopeMatch"), fallback_summary="Scope match assessment."
    )
    raw["sectorMatch"] = _coerce_dimension(
        raw.get("sectorMatch"), fallback_summary="Sector fit assessment."
    )
    raw["compliance"] = _coerce_dimension(
        raw.get("compliance"), fallback_summary="Compliance assessment."
    )
    raw["teamMatch"] = _coerce_dimension(
        raw.get("teamMatch"), fallback_summary="Team match assessment."
    )

    evaluations = _coerce_evaluations(raw.get("evaluations"))
    if evaluations:
        raw["evaluations"] = evaluations

    for list_key in ("criticalGaps", "conditions", "clarifyingQuestions", "actionFlags"):
        values = raw.get(list_key)
        if isinstance(values, list):
            raw[list_key] = [str(item).strip() for item in values if str(item).strip()]

    return raw


def _normalize_flag_severity(value: object) -> str:
    if not isinstance(value, str):
        return "warning"
    normalized = value.strip().lower()
    if normalized in {"info", "warning", "critical"}:
        return normalized
    if normalized in {"high", "severe", "major", "urgent"}:
        return "critical"
    if normalized in {"low", "minor", "informational"}:
        return "info"
    if normalized in {"medium", "moderate", "caution"}:
        return "warning"
    return "warning"


def _normalize_dimension_flags(raw: dict[str, Any]) -> None:
    for dimension_key in ("scopeMatch", "sectorMatch", "compliance", "teamMatch"):
        dimension = raw.get(dimension_key)
        if not isinstance(dimension, dict):
            continue
        flags = dimension.get("flags")
        if not isinstance(flags, list):
            continue
        for flag in flags:
            if isinstance(flag, dict):
                flag["severity"] = _normalize_flag_severity(flag.get("severity"))


_INVENTED_EVAL_WEIGHT_RE = re.compile(
    r"(?:weighted\s+at\s+\d{1,3}\s*%|"
    r"\d{1,3}\s*%\s*(?:of\s+)?(?:total\s+)?(?:points?|cost)|"
    r"\(\s*\d{1,3}\s*%\s*\)|"
    r"\d{1,3}\s*%\s*cost-?weighted|"
    r"cost-?weighted|"
    r"\d{1,3}\s+of\s+\d{1,3}\s*points?|"
    r"cost-?heavy\s+evaluation|"
    r"heavy\s+cost\s+weight|"
    r"cost\s+(?:evaluation\s+)?weight(?:ed|ing)|"
    r"Max\s+Points|"
    r"points?\s*\(\d{1,3}\s*%\)|"
    r"points?\s*\(\s*\d+\s*\+\s*\d+\s*\)|"
    r"Total:\s*\d+\s*points|"
    r"Total\s+\d+\s*points|"
    r"Cost\s+\d+\s+points|"
    r"Experience\s+\d+\s+points|"
    r"combined,?\s*requiring\s+competitive\s+pricing)",
    re.IGNORECASE,
)
_INVENTED_PERSON_RE = re.compile(r"\bDrew\s+Stone\b", re.IGNORECASE)
_NAME_SPELLING_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bElla\s+Lindeau\b", re.IGNORECASE), "Ella Lindau"),
    (re.compile(r"\bLindeau\b", re.IGNORECASE), "Lindau"),
]
_MISATTRIBUTED_CONTRACT_VALUE_RE = re.compile(
    r"(?:contract\s+value[^\n.]{0,80})?(?:only\s+)?\$?\s*30\s*million[^\n.]{0,100}"
    r"(?:reference|found|mentioned|gross\s+receipts)?|"
    r"only\s+\$?\s*30\s*million\s+reference\s+found",
    re.IGNORECASE,
)
_EVAL_SECTION_RE = re.compile(
    r"(##\s*EVALUATION CRITERIA BREAKDOWN\b.*?)(?=\n##\s+|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_DISCLOSED_EVAL_SECTION = (
    "## EVALUATION CRITERIA BREAKDOWN\n"
    "Point-weighted scoring is **not disclosed** in this RFP. "
    "The solicitation uses question groups (pass/fail and scored items) without published "
    "category point totals or percentages.\n\n"
    "Cost-sensitivity is therefore **unknowable from the RFP text**. "
    "Do not invent a weighted scoring table. Describe question groups narratively only "
    "when they appear in the RFP body.\n"
)


def _text_blob_for_invention_scan(raw: dict[str, Any]) -> str:
    parts: list[str] = [
        str(raw.get("summary") or ""),
        str(raw.get("stageOneReport") or ""),
    ]
    for key in ("criticalGaps", "conditions", "actionFlags"):
        values = raw.get(key)
        if isinstance(values, list):
            parts.extend(str(v) for v in values if v)
    matrix = raw.get("decisionMatrix")
    if isinstance(matrix, list):
        for row in matrix:
            if isinstance(row, dict):
                parts.append(str(row.get("notes") or ""))
    return "\n".join(parts)


def _apply_name_spelling_fixes(text: str) -> str:
    fixed = text
    for pattern, replacement in _NAME_SPELLING_FIXES:
        fixed = pattern.sub(replacement, fixed)
    return fixed


def _scrub_string_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        text = _apply_name_spelling_fixes(item.strip())
        if not text:
            continue
        if _INVENTED_EVAL_WEIGHT_RE.search(text):
            continue
        if _MISATTRIBUTED_CONTRACT_VALUE_RE.search(text):
            continue
        cleaned.append(text)
    return cleaned


def _strip_invented_eval_claims_from_text(text: str) -> str:
    """Remove sentences/lines that assert fabricated point weights or %."""
    if not text:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        if _INVENTED_EVAL_WEIGHT_RE.search(line):
            # Drop table rows / weight claims entirely.
            continue
        kept.append(line)
    cleaned = "\n".join(kept)
    # Also drop orphaned markdown table headers left behind.
    cleaned = re.sub(
        r"(?im)^\s*\|\s*Category\s*\|\s*Max Points.*$\n?(?:^\s*\|[^\n]*$\n?)*",
        "",
        cleaned,
    )
    return cleaned


def _replace_undisclosed_eval_section(report: str) -> str:
    cleaned = _strip_invented_eval_claims_from_text(report)
    if _EVAL_SECTION_RE.search(cleaned):
        return _EVAL_SECTION_RE.sub(_DISCLOSED_EVAL_SECTION.strip() + "\n\n", cleaned, count=1)
    # Section missing but invented weights elsewhere — insert truthful section before FINAL REC.
    insert_at = re.search(r"\n##\s+FINAL RECOMMENDATION\b", cleaned, re.IGNORECASE)
    if insert_at:
        idx = insert_at.start()
        return cleaned[:idx] + "\n\n" + _DISCLOSED_EVAL_SECTION + cleaned[idx:]
    if "EVALUATION CRITERIA" not in cleaned.upper():
        return cleaned.rstrip() + "\n\n" + _DISCLOSED_EVAL_SECTION
    return cleaned


def _scrub_invented_eval_and_people(
    raw: dict[str, Any],
    *,
    evaluation_points_found: bool,
) -> None:
    """Mechanically remove fabrication failure modes that survive the LLM pass."""
    blob = _text_blob_for_invention_scan(raw)
    gaps = raw.setdefault("criticalGaps", [])
    if not isinstance(gaps, list):
        gaps = []
        raw["criticalGaps"] = gaps

    report = str(raw.get("stageOneReport") or "")
    summary = str(raw.get("summary") or "")

    has_invented_eval_language = bool(_INVENTED_EVAL_WEIGHT_RE.search(blob))
    # If extractor did not find a reliable published table, ANY point/% table in the
    # report is treated as fabrication — even if the model reuses the same fake 29/62.
    invented_weights = (not evaluation_points_found) and (
        has_invented_eval_language
        or bool(
            re.search(
                r"Max\s+Points|points?\s*\(\d{1,3}\s*%\)|Total\s*:?\s*\d+\s*points|"
                r"Cost\s+\d+\s+points|62\s*%",
                report,
                re.I,
            )
        )
    )
    invented_person = bool(_INVENTED_PERSON_RE.search(blob))
    misattributed_contract = bool(_MISATTRIBUTED_CONTRACT_VALUE_RE.search(blob))

    if invented_weights:
        report = _replace_undisclosed_eval_section(report)
        summary = _strip_invented_eval_claims_from_text(summary)
        summary = _INVENTED_EVAL_WEIGHT_RE.sub("", summary)
        summary = re.sub(r"\s{2,}", " ", summary).strip(" .")
        if summary and not summary.endswith("."):
            summary += "."
        if not summary:
            summary = (
                "Point-weighted evaluation criteria are not disclosed in the RFP; "
                "scores reflect scope fit, KB evidence, and logistics only."
            )
        matrix = raw.get("decisionMatrix")
        if isinstance(matrix, list):
            for row in matrix:
                if not isinstance(row, dict):
                    continue
                dim = str(row.get("dimension") or "").casefold()
                notes = str(row.get("notes") or "")
                if dim in {"financial viability", "win probability"} and (
                    _INVENTED_EVAL_WEIGHT_RE.search(notes)
                    or "62%" in notes
                    or int(row.get("score") or 0) <= 2
                ):
                    score = int(row.get("score") or 0)
                    if score <= 2:
                        row["score"] = min(3, score + 1)
                    row["notes"] = (
                        "Point-weighted evaluation table not disclosed in RFP — "
                        "score based on scope fit, competition, and logistics only."
                    )
                elif _INVENTED_EVAL_WEIGHT_RE.search(notes):
                    row["notes"] = _INVENTED_EVAL_WEIGHT_RE.sub("", notes).strip()
        worth = raw.get("worthScore")
        if isinstance(worth, int) and worth <= 2:
            raw["worthScore"] = 3
        gaps[:] = [
            g
            for g in gaps
            if isinstance(g, str) and not _INVENTED_EVAL_WEIGHT_RE.search(g)
        ]

    if misattributed_contract:
        report = _MISATTRIBUTED_CONTRACT_VALUE_RE.sub(
            "Contract value not disclosed in RFP",
            report,
        )
        summary = _MISATTRIBUTED_CONTRACT_VALUE_RE.sub(
            "Contract value not disclosed in RFP",
            summary,
        )
        report = re.sub(
            r"Contract\s+[Vv]alue:\s*Not disclosed in RFP\s*\([^)]*\$?\s*30\s*million[^)]*\)",
            "Contract Value: Not disclosed in RFP",
            report,
            flags=re.IGNORECASE,
        )
        msg = (
            "Contract value not disclosed — do not treat small-business gross-receipts "
            "thresholds (e.g. $30M eligibility) as opportunity size."
        )
        if not any(isinstance(g, str) and "gross-receipts" in g.lower() for g in gaps):
            gaps.append(msg)

    if invented_person:
        msg = (
            "[VERIFY: 'Drew Stone' is not a documented zö team member — "
            "remove from staffing claims; FLAG SONJA to assign Project Lead]"
        )
        if not any(isinstance(g, str) and "Drew Stone" in g for g in gaps):
            gaps.append(msg)
        report = _INVENTED_PERSON_RE.sub(
            "[FLAG FOR SONJA: assign Project Lead — unverified name removed]",
            report,
        )
        matrix = raw.get("decisionMatrix")
        if isinstance(matrix, list):
            for row in matrix:
                if isinstance(row, dict) and _INVENTED_PERSON_RE.search(
                    str(row.get("notes") or "")
                ):
                    row["notes"] = _INVENTED_PERSON_RE.sub(
                        "[unverified name removed — assign via Sonja]",
                        str(row.get("notes") or ""),
                    )

    report = _apply_name_spelling_fixes(report)
    summary = _apply_name_spelling_fixes(summary)
    raw["stageOneReport"] = report
    if summary:
        raw["summary"] = summary
    raw["criticalGaps"] = _scrub_string_list(gaps) if invented_weights else [
        _apply_name_spelling_fixes(g) if isinstance(g, str) else g for g in gaps
    ]
    raw["conditions"] = [
        _apply_name_spelling_fixes(c) if isinstance(c, str) else c
        for c in (raw.get("conditions") or [])
        if isinstance(c, str)
    ]
    if isinstance(raw.get("actionFlags"), list):
        raw["actionFlags"] = [
            _apply_name_spelling_fixes(f) if isinstance(f, str) else f
            for f in raw["actionFlags"]
            if isinstance(f, str)
        ]
    matrix = raw.get("decisionMatrix")
    if isinstance(matrix, list):
        for row in matrix:
            if isinstance(row, dict) and isinstance(row.get("notes"), str):
                row["notes"] = _apply_name_spelling_fixes(row["notes"])


def _apply_hard_rules(
    raw: dict[str, Any],
    *,
    deadline: dict[str, Any] | None = None,
    evaluation_points_found: bool = False,
) -> dict[str, Any]:
    if raw.get("insufficientData"):
        raw["recommendation"] = None
        raw["fitScore"] = None
        raw["worthScore"] = None
        gaps = raw.get("criticalGaps")
        if isinstance(gaps, list):
            raw["criticalGaps"] = [
                g
                for g in gaps
                if isinstance(g, str)
                and "outside zö's marketing/branding lane" not in g
            ]
        return raw

    scope_flags = raw.get("scopeMatch", {}).get("flags", [])
    compliance_flags = raw.get("compliance", {}).get("flags", [])

    out_of_lane = any(
        isinstance(flag, dict)
        and flag.get("severity") == "critical"
        and flag.get("category") == "out_of_lane"
        for flag in scope_flags
    )
    disqualifying = any(
        isinstance(flag, dict)
        and flag.get("severity") == "critical"
        and flag.get("category") in {"compliance", "certification", "registration"}
        for flag in compliance_flags
    )

    if out_of_lane:
        raw["recommendation"] = "no_go"
        raw["fitScore"] = min(int(raw.get("fitScore") or 0), 1)
        gaps = raw.setdefault("criticalGaps", [])
        if isinstance(gaps, list) and not any(
            isinstance(g, str) and "outside zö's marketing/branding lane" in g for g in gaps
        ):
            gaps.append("Scope is outside zö's marketing/branding lane.")

    if disqualifying and raw.get("recommendation") == "go":
        raw["recommendation"] = "review"

    for key in ("fitScore", "worthScore"):
        value = raw.get(key)
        if value is None:
            continue
        coerced = _coerce_score(value)
        if coerced is not None:
            raw[key] = coerced

    raw["decisionMatrix"] = _normalize_decision_matrix(raw.get("decisionMatrix"))
    _normalize_dimension_flags(raw)
    _scrub_invented_eval_and_people(
        raw, evaluation_points_found=evaluation_points_found
    )
    # Re-normalize matrix after possible score bumps in scrubber.
    raw["decisionMatrix"] = _normalize_decision_matrix(raw.get("decisionMatrix"))

    if deadline is not None:
        raw["deadline"] = deadline
        if deadline.get("isPast") and deadline.get("lateSubmissionDisqualifies"):
            raw["recommendation"] = "no_go"
            gaps = raw.setdefault("criticalGaps", [])
            if isinstance(gaps, list):
                msg = (
                    f"Proposal deadline passed ({deadline.get('dueDate') or 'see RFP'}) — "
                    "late submissions explicitly not accepted per RFP."
                )
                if not any(isinstance(g, str) and "deadline passed" in g.lower() for g in gaps):
                    gaps.append(msg)

    report = str(raw.get("stageOneReport") or "")
    raw_flags = raw.get("actionFlags")
    llm_flags = (
        [str(flag).strip() for flag in raw_flags if str(flag).strip()]
        if isinstance(raw_flags, list)
        else []
    )
    raw["actionFlags"] = _extract_action_flags(report, *llm_flags)

    return raw


def _normalize_decision_matrix(raw_matrix: object) -> list[dict[str, object]]:
    if not isinstance(raw_matrix, list):
        return []

    by_dimension: dict[str, dict[str, object]] = {}
    for item in raw_matrix:
        if not isinstance(item, dict):
            continue
        dimension = str(item.get("dimension") or "").strip()
        if not dimension:
            continue
        score = item.get("score")
        if score is None:
            continue
        by_dimension[dimension.casefold()] = {
            "dimension": dimension,
            "score": max(0, min(5, int(score))),
            "notes": str(item.get("notes") or "").strip(),
        }

    normalized: list[dict[str, object]] = []
    for canonical in DECISION_MATRIX_DIMENSIONS:
        match = by_dimension.get(canonical.casefold())
        if match:
            normalized.append(
                {
                    "dimension": canonical,
                    "score": match["score"],
                    "notes": match["notes"],
                }
            )
            continue
        for key, row in by_dimension.items():
            if canonical.split()[0].lower() in key:
                normalized.append(
                    {
                        "dimension": canonical,
                        "score": row["score"],
                        "notes": row["notes"],
                    }
                )
                break

    return normalized


def compute_overall_go_score(analysis: GoNoGoAnalysis) -> float | None:
    if analysis.decision_matrix:
        scores = [row.score for row in analysis.decision_matrix]
        if scores:
            return round(sum(scores) / len(scores), 1)

    fit = analysis.fit_score
    worth = analysis.worth_score
    if fit is None and worth is None:
        return None
    if fit is not None and worth is not None:
        return round((fit + worth) / 2, 1)
    return float(fit if fit is not None else worth)


async def analyze_rfp(rfp: RfpRecord) -> GoNoGoAnalysis:
    if not llm.is_configured():
        raise GoNoGoError(
            "LLM not configured. Set OPENROUTER_API_KEY (primary) or FIREWORKS_API_KEY (fallback).",
            status_code=503,
        )

    logger.info("Go/No-Go analysis starting for rfp_id=%s title=%r", rfp.id, rfp.title)

    content = _assess_rfp_content(rfp)
    logger.info(
        "RFP content assessed for %s: %d substantive chars, metadata_only=%s, "
        "pdf_extracted=%s, pdf_missing=%s",
        rfp.id,
        content.substantive_chars,
        content.metadata_only,
        content.pdf_extracted,
        content.pdf_file_missing,
    )

    if content.substantive_chars < 40 and not content.description:
        logger.info(
            "Thin RFP content for %s — returning needs-input analysis (no 400)",
            rfp.id,
        )
        return _build_needs_input_analysis(rfp, content)

    kb_context = await _gather_knowledge_context(rfp, content)
    rfp_context = _build_rfp_context(rfp, content)
    deadline_info = _assess_deadline(rfp, content)
    hard_facts = extract_rfp_hard_facts(
        combine_rfp_text(content.description, content.pdf_text)
    )
    evaluation_points_found = evaluation_table_is_reliable(hard_facts)

    thin_rfp_note = ""
    if content.metadata_only:
        thin_rfp_note = (
            "\n\nNOTE: This RFP appears thin (metadata shell or placeholder client). "
            "You MUST set insufficientData=true, recommendation=null, fitScore=null, worthScore=null, "
            "and populate clarifyingQuestions. Still answer all evaluation questions explaining what "
            "is missing. Do NOT issue no_go solely because content is missing.\n"
        )

    user_prompt = f"""Produce a full Stage 1 Fit Analysis for zö agency.

{_evaluation_questions_block()}
{thin_rfp_note}
## Deadline check (authoritative — use today's date)
{_build_deadline_context(deadline_info)}

## Scoring factors / HARD FACTS for THIS RFP (extracted from full solicitation text)
{_build_scoring_factors(rfp, content)}

Write a detailed stageOneReport in Markdown following the required section structure (compliance snapshot with mandatory documents, capability yes/gap lists, evaluation criteria, competitive context, flags).
Populate decisionMatrix with all 5 dimensions — derive each score dynamically from THIS RFP's budget, geography,
evaluation criteria weights (ONLY if listed in HARD FACTS), compliance risks, KB evidence, and competitive position.
No default or template scores. Do not invent pessimistic point tables to justify low scores.
If HARD FACTS list a contract ceiling / year budgets, Financial Viability MUST cite them (do not say undisclosed).
If HARD FACTS list evaluation point rows, Win Probability and the EVALUATION CRITERIA table MUST use them.
If HARD FACTS say evaluation points were NOT found, say so — never invent %.
When KB includes a near-direct case study for the core scope (e.g. Recovery Network of Oregon for
coalition/health communications), cite it as Verified and score Technical Capability ≥ 4 unless a
separate structural blocker exists. Fixable registration/team flags → review, not a 2.x Overall.
Use [FLAG FOR ROLE: ...] and [FLAG: ...] for every item needing human confirmation before submission.
Use tables with pipe characters for capability assessment; evaluation point tables ONLY when HARD FACTS provide them.
Cite specific RFP requirements and specific knowledge-base evidence. Tag uncertain items [VERIFY].

EVIDENCE DISCIPLINE FOR THIS RUN:
- Offeror office ≠ automatic subcontractor fix.
- Google/Meta Ads on one person ≠ agency Verified.
- 07_FIN ≠ won experience; flag Resonance/competitor text if present.
- MCI-mismatched tourism refs need an explicit discount note.
- Never invent "budget unknown" when HARD FACTS show a ceiling.
- Never invent evaluation % / point totals when HARD FACTS say not found.
- Never invent team names; never invent "Drew Stone".
- Spell Ella Lindau correctly (not Lindeau).
- Undisclosed budget alone ≠ Worth 2 and Financial 2 — usually Worth ~3 when scope is in-lane.
- Never cite small-business gross-receipts thresholds (e.g. $30M) as contract value.

## RFP
{rfp_context}

## Knowledge base excerpts (verified facts only — do not go beyond this)
{kb_context}
"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    analysis: GoNoGoAnalysis | None = None
    for attempt in range(2):
        try:
            raw, provider = await llm.chat_json(messages, max_tokens=12_000, temperature=0.25)
            normalized = _apply_hard_rules(
                raw,
                deadline=deadline_info,
                evaluation_points_found=evaluation_points_found,
            )
            normalized = _coerce_go_no_go_raw(normalized)
            analysis = GoNoGoAnalysis.model_validate({**normalized, "provider": provider})
            break
        except ValidationError as exc:
            logger.error(
                "Go/No-Go validation failed for rfp %s: %s",
                rfp.id,
                exc.errors()[:8],
            )
            raise GoNoGoError(
                f"Go/No-Go analysis validation failed: {exc.errors()[0].get('msg', exc)}",
                status_code=502,
            ) from exc
        except llm.LlmError as exc:
            logger.error(
                "LLM failed for rfp %s (attempt %d/2): %s",
                rfp.id,
                attempt + 1,
                exc,
            )
            if attempt == 0:
                continue
            if content.metadata_only:
                logger.info("Falling back to local needs-input template for %s", rfp.id)
                return _build_needs_input_analysis(rfp, content)
            raise GoNoGoError(f"Go/No-Go analysis failed: {exc}", status_code=502) from exc

    if analysis is None:
        raise GoNoGoError("Go/No-Go analysis failed after retries", status_code=502)

    logger.info(
        "Go/No-Go analysis complete for rfp_id=%s provider=%s recommendation=%s "
        "fit=%s worth=%s matrix=%s insufficient=%s",
        rfp.id,
        analysis.provider,
        analysis.recommendation,
        analysis.fit_score,
        analysis.worth_score,
        [row.score for row in analysis.decision_matrix],
        analysis.insufficient_data,
    )
    return analysis


def analysis_activity_note(analysis: GoNoGoAnalysis) -> str:
    if analysis.insufficient_data:
        return (
            "Go/No-Go analysis paused — add full RFP scope (PDF or description) and re-run. "
            f"{analysis.summary}"
        )[:500]

    label = {
        "go": "Go",
        "no_go": "No-Go",
        "review": "Review (Go With Conditions)",
    }[analysis.recommendation or "review"]
    overall = compute_overall_go_score(analysis)
    worth = analysis.worth_score
    score_bits: list[str] = []
    if worth is not None:
        score_bits.append(f"Worth {worth}/5")
    if overall is not None:
        score_bits.append(f"Overall {overall}/5")
    score_label = ", ".join(score_bits) if score_bits else "—"
    return (
        f"Go/No-Go analysis complete — {label}. "
        f"{score_label}. "
        f"{analysis.summary}"
    )[:500]


def _composite_go_score_for_note(analysis: GoNoGoAnalysis) -> float | None:
    return compute_overall_go_score(analysis)
