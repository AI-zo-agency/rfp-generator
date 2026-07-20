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
        "healthcare, corporate, nonprofit) based on documented experience?",
    ),
    (
        "compliance_certs",
        "Are required certifications (WBENC, WOSB, COBID, DBE, etc.) listed — and does zö hold "
        "each one per the knowledge base?",
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
        "team_roles",
        "What roles or specialized expertise does the RFP require — and are matching approved "
        "bios documented in the knowledge base?",
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

fitScore ("AI Fit Score") — capability + sector + compliance + team vs KB:
  5 = near-perfect documented match (scope, sector case studies, compliance, team)
  4 = strong match with minor gaps
  3 = in-lane but meaningful gaps (registration, sector proof, experience minimums)
  2 = partial match; major documented gaps
  1 = marginal / mostly unverified
  0 = out of lane

worthScore ("Worth It Score") — financial return vs pursuit effort:
  5 = high disclosed value, favorable fee structure, strong win path
  4 = solid value and reasonable effort
  3 = moderate value or mixed signals
  2 = modest/undisclosed budget, heavy compliance/demo/travel overhead, or steep competition
  1 = poor return relative to effort
  0 = not worth pursuing

decisionMatrix — exactly 5 rows; each score is independent (they will often differ):
  1. Technical Capability Match — scope execution per KB
  2. Resource Availability — team bandwidth, geography, live-demo/on-site needs
  3. Financial Viability — agency revenue vs cost (use commission math when budget is mostly media spend)
  4. Strategic Value — reference value, sector/geography expansion
  5. Win Probability — competition, proximity, scoring criteria alignment, disqualification risk

Overall Go Score = arithmetic average of the 5 decisionMatrix scores (not fitScore/worthScore).
Use the full 0–5 range. Strong RFPs with local presence and high contract value should score 4–5 on several dimensions.
Weak or distant low-value RFPs should score 1–2 on Financial Viability and Win Probability."""

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

""" + SCORING_RUBRIC + """

RECOMMENDATION:
- "go": strong fit and worthwhile; deadline not passed (or extension confirmed)
- "no_go": out-of-lane OR disqualifying verified compliance gap OR poor fit + low worth OR proposal deadline passed with late-submission disqualification
- "review": Go With Conditions — fixable gaps or mixed signals; also use when deadline passed but re-solicit/override may be possible

DEADLINE CHECK (required — use today's date provided in the user prompt):
- Compare proposal deadline from the RFP (and metadata due date) against today's date.
- If deadline has passed and the RFP states late proposals are not accepted, lead the EXECUTIVE SUMMARY with that fact and cite the RFP section.
- Set recommendation to "no_go" when late submission is an explicit disqualifier and deadline has passed.
- Still complete the full analysis (capability, compliance, scoring) and add conditions for leadership override if re-solicit is possible.
- Populate the "deadline" object and mention deadline status in summary.

stageOneReport — comprehensive Markdown matching a senior analyst brief. Be exhaustive and RFP-specific:

## EXECUTIVE SUMMARY
Open with deadline status vs today's date when relevant. Client, project, solicitation number, deadline (with timezone if stated),
contract value/term, AI Fit Score X/5 (1-sentence why), Worth It Score X/5 (1-sentence why), Recommendation label.

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
### Required Industry Experience vs. Documented Experience
Sector/client-type match with named case studies from KB; flag thin reference depth.
### Required Team Roles vs. Actual Team
Map RFP roles to documented zö team members; [FLAG: ...] for account lead or presentation assignments.
Markdown table when helpful: RFP Requirement | zö Capability (KB source) | Status (Verified/Gap/[VERIFY])

## EVALUATION CRITERIA BREAKDOWN
Table: Category | Max Points | zö Strength | Vulnerability — use actual point weights from the RFP.
Include scoring formulas when stated (e.g., cost scoring). Note highest-weight sections and where to concentrate effort.

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
  "summary": "2-3 sentence executive summary for the dashboard",
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
Given an RFP excerpt, return 10-12 specific search queries to retrieve verified facts about zö's:
- certifications (WBENC, WOSB, COBID, etc.)
- state registrations and insurance
- capabilities matching the RFP scope (media buying, creative, digital, geo-fencing, etc.)
- case studies and won proposals in similar sectors/clients
- team bios for roles the RFP requires
- pricing/commission models if relevant
- compliance and submission experience

Use the client name, location, sector, and specific deliverables from the RFP in your queries.
Do NOT include HTML, JavaScript errors, or portal boilerplate in queries.
Return ONLY JSON: {"queries": ["query 1", "query 2", ...]}"""

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
    text = combine_rfp_text(content.description, content.pdf_text)
    sample = text[:25_000]
    lines = [
        f"- Client: {rfp.client}",
        f"- Sector: {rfp.sector}",
        f"- Location: {rfp.location or '(not provided)'}",
    ]
    if rfp.estimated_value is not None:
        lines.append(f"- Estimated value (metadata): ${rfp.estimated_value:,}")

    budgets = re.findall(
        r"\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|M|B|K|thousand))?",
        sample,
        flags=re.IGNORECASE,
    )
    if budgets:
        unique_budgets = list(dict.fromkeys(budgets))[:6]
        lines.append(f"- Dollar amounts in RFP: {', '.join(unique_budgets)}")

    point_weights = re.findall(
        r"(\d{1,3})\s*(?:points?|pts?\.?)(?:\s*(?:out of|/)\s*(\d{1,3}))?",
        sample,
        flags=re.IGNORECASE,
    )
    if point_weights:
        formatted = [
            f"{weight}{f'/{total}' if total else ''}"
            for weight, total in point_weights[:8]
        ]
        lines.append(f"- Evaluation point weights in RFP: {', '.join(formatted)}")

    term_matches = re.findall(
        r"(\d+)\s*(?:-|\s)?\s*(?:month|year)s?",
        sample[:8_000],
        flags=re.IGNORECASE,
    )
    if term_matches:
        lines.append(f"- Term lengths mentioned: {', '.join(term_matches[:4])}")

    lines.append(
        "- Calibrate fitScore, worthScore, and each decisionMatrix row from these "
        "RFP-specific signals — scores must differ when contract value, geography, "
        "evaluation weights, or compliance risk differ."
    )
    return "\n".join(lines)


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
            return planned[:12]
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

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for hits in results:
        for hit in hits:
            key = str(hit.get("id") or hit.get("customId") or hit.get("content", "")[:80])
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)

    formatted = supermemory.format_search_hits(merged, max_chars=KB_CONTEXT_MAX_CHARS)
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


def _apply_hard_rules(
    raw: dict[str, Any],
    *,
    deadline: dict[str, Any] | None = None,
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

## Scoring factors for THIS RFP (extracted from this solicitation — use to calibrate scores)
{_build_scoring_factors(rfp, content)}

Write a detailed stageOneReport in Markdown following the required section structure (compliance snapshot with mandatory documents, capability yes/gap lists, evaluation point tables, competitive context, flags).
Populate decisionMatrix with all 5 dimensions — derive each score dynamically from THIS RFP's budget, geography,
evaluation criteria weights, compliance risks, KB evidence, and competitive position. No default or template scores.
Use [FLAG FOR ROLE: ...] and [FLAG: ...] for every item needing human confirmation before submission.
Use tables with pipe characters for evaluation criteria and capability assessment.
Cite specific RFP requirements and specific knowledge-base evidence. Tag uncertain items [VERIFY].

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
            raw, provider = await llm.chat_json(messages, max_tokens=12_000, temperature=0.45)
            normalized = _apply_hard_rules(raw, deadline=deadline_info)
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
    fit = analysis.fit_score
    worth = analysis.worth_score
    score_bits: list[str] = []
    if fit is not None:
        score_bits.append(f"Fit {fit}/5")
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
