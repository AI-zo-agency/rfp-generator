"""RFP Cost demands — LLM extract + audit; MANUAL FILL stubs (no invented fees).

Different RFPs have different scopes. Do NOT use regex/keyword tables to decide
what Cost must cover. An LLM reads the RFP cost excerpt + Approach digest and
emits demands with verbatim quotes; another pass judges whether Cost satisfies
each demand by meaning. Missing → Sonja/Rachel MANUAL FILL (never invent $/%).

Shared by Phase 3.5 paint and Cost section chat.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.proposal import ProposalBudget

logger = logging.getLogger(__name__)

DemandKind = Literal[
    "disclosure",
    "instrument",
    "reimbursable",
    "workstream_funding",
    "assumptions",
    "other",
]
Satisfaction = Literal["grounded", "manual_fill", "missing"]

_EXTRACT_SYSTEM = """You extract what THIS RFP requires the Cost / Pricing / Fee proposal
section to include or address — comprehensively.

This runs for EVERY RFP — government, utility, nonprofit, commercial, hourly forms,
phased retainers, commission media, blended quotes, official pricing forms, etc.
Scopes differ. Do NOT assume a fixed checklist from another RFP.

Goal: Cost must cover EVERY distinct Cost/pricing ask THIS RFP (or Approach) raises
for the proposal. Silent omissions are failures. Prefer a complete demand list over
a short one.

Rules:
- Read meaning from the provided RFP cost/proposal excerpt + Approach/scope digest.
- Emit a demand for each DISTINCT ask the Cost tab must answer (not narrative fluff).
- Every demand needs a short verbatim quote from the provided text.
- kinds: disclosure | instrument | reimbursable | workstream_funding | assumptions | other
- Cover these WHEN THIS RFP actually has them (examples — not a fixed list):
  fee model / NTE / ceiling / not-to-exceed; payment / invoicing / milestone billing;
  commission vs markup vs pass-through / media compensation; hourly or classification
  rate schedule; cost assumptions (travel, markup, stock, licenses); reimbursables OR
  all-in / no separate expense billing; official pricing/cost form fields; evaluation
  cost factors the offeror must address; multi-year / option-year pricing; Approach
  workstreams that need a visible fee home; any "Cost proposal shall include/address…"
  bullets.
- One demand per distinct ask. Do not merge unrelated asks into one demand.
- Do NOT invent commission percentages or dollar amounts.
- Do NOT emit demands for topics the RFP does not raise.
- Prefer 4–24 demands when the RFP has a real Cost/pricing section. Empty list only
  when the RFP truly has no Cost/pricing asks.
- id: stable snake_case slug unique in the list.
- actions (optional string list on a demand):
  - "omit_guide_reimbursable_expenses" when THIS RFP requires all-in fees / expenses
    not billed separately (so Cost must NOT keep the default "billed at cost"
    Reimbursable Expenses paragraph).

Return JSON:
{"demands":[{"id":"string","kind":"disclosure","rfpQuote":"verbatim","requirement":"one line","actions":[]}]}
"""


_AUDIT_SYSTEM = """You judge whether a Cost / budget manuscript satisfies RFP Cost demands.

Works for every RFP type — judge THIS demand list only.

For each demand:
- grounded: Cost clearly answers it (treatment language, fee line, schedule, note, etc.)
- manual_fill: Cost already has a MANUAL FILL / confirm tag for that ask
- missing: silent — not answered and no MANUAL FILL

Judge by meaning, not keywords. Do not treat a social-only fee line as funding
unrelated Approach workstreams. A clean total alone does not satisfy disclosure asks.
Never invent fees. Never treat deleted Sonja flags as "confirmed."

Return JSON:
{"results":[{"id":"string","satisfaction":"grounded|manual_fill|missing","evidence":"short"}]}
"""


class RfpCostDemand(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: DemandKind = "other"
    rfp_quote: str = Field(default="", alias="rfpQuote")
    requirement: str = ""
    satisfaction: Satisfaction = "missing"
    evidence: str = ""
    actions: list[str] = Field(default_factory=list)


def demands_require_omit_guide_reimbursables(demands: list[RfpCostDemand]) -> bool:
    """True when LLM demands say Cost must not keep separate reimbursable billing."""
    for d in demands:
        for action in d.actions or []:
            if str(action).strip().casefold() in {
                "omit_guide_reimbursable_expenses",
                "omit_reimbursable",
                "all_in_expenses",
            }:
                return True
    return False


def approach_digest_from_draft_sections(
    sections: list | None,
    *,
    max_chars: int = 14000,
) -> str:
    """Full non-Cost manuscript for cost-demand / layout grounding.

    No keyword synonym priority list — include every filled tab (skip only the
    Cost tab that already carries Fee Detail / Proposed Investment) and let the
    cost-demands LLM judge THIS RFP's meaning.
    """
    if not sections:
        return ""
    chunks: list[str] = []
    total = 0
    for section in sections:
        title = (getattr(section, "title", None) or "").strip()
        body = (getattr(section, "content", None) or "").strip()
        if not body:
            continue
        body_cf = body.casefold()
        # Skip the Cost instrument tab itself.
        if "fee detail" in body_cf and "proposed investment" in body_cf:
            continue
        piece = f"## {title}\n{body[:2800]}"
        if total + len(piece) > max_chars:
            remain = max_chars - total
            if remain < 200:
                break
            piece = piece[:remain]
        chunks.append(piece)
        total += len(piece)
        if total >= max_chars:
            break
    return "\n\n".join(chunks)[:max_chars]


def _norm_kind(raw: str) -> DemandKind:
    k = (raw or "").strip().casefold().replace(" ", "_")
    allowed: set[str] = {
        "disclosure",
        "instrument",
        "reimbursable",
        "workstream_funding",
        "assumptions",
        "other",
    }
    return k if k in allowed else "other"  # type: ignore[return-value]


def _norm_satisfaction(raw: str) -> Satisfaction:
    s = (raw or "").strip().casefold()
    if s in {"grounded", "manual_fill", "missing"}:
        return s  # type: ignore[return-value]
    if s in {"ok", "satisfied", "met", "present"}:
        return "grounded"
    if s in {"fill", "manual", "placeholder"}:
        return "manual_fill"
    return "missing"


def _parse_demands_payload(raw: Any) -> list[RfpCostDemand]:
    if not isinstance(raw, dict):
        return []
    rows = raw.get("demands")
    if not isinstance(rows, list):
        return []
    out: list[RfpCostDemand] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or row.get("demandId") or f"demand_{i + 1}").strip()
        rid = re.sub(r"[^a-zA-Z0-9_]+", "_", rid).strip("_").casefold() or f"demand_{i + 1}"
        if rid in seen:
            continue
        seen.add(rid)
        req = str(row.get("requirement") or row.get("ask") or "").strip()
        quote = str(row.get("rfpQuote") or row.get("rfp_quote") or row.get("quote") or "").strip()
        if not req and not quote:
            continue
        actions_raw = row.get("actions") or row.get("action") or []
        actions: list[str] = []
        if isinstance(actions_raw, str) and actions_raw.strip():
            actions = [actions_raw.strip()]
        elif isinstance(actions_raw, list):
            actions = [str(a).strip() for a in actions_raw if str(a).strip()]
        out.append(
            RfpCostDemand(
                id=rid[:80],
                kind=_norm_kind(str(row.get("kind") or "other")),
                rfpQuote=quote[:500],
                requirement=req[:400] or quote[:200],
                actions=actions[:6],
            )
        )
        if len(out) >= 24:
            break
    return out


def pricing_flags_for_rfp_cost_demands(demands: list[RfpCostDemand]) -> list[str]:
    """Surface unmet / MANUAL FILL Cost demands on budget.pricingFlags."""
    flags: list[str] = []
    for d in demands:
        if d.satisfaction not in {"missing", "manual_fill"}:
            continue
        req = (d.requirement or d.id).strip()[:160]
        flags.append(f"RFP Cost demand [{d.id}] ({d.satisfaction}): {req}")
    return flags


async def extract_rfp_cost_demands(
    *,
    rfp_text: str,
    approach_digest: str = "",
) -> list[RfpCostDemand]:
    """LLM: what THIS RFP requires Cost to address (quotes required)."""
    from app.services import llm
    from app.services.proposal_rfp_excerpt import budget_and_cost_excerpt

    cost_excerpt = budget_and_cost_excerpt(rfp_text or "", max_chars=24_000)
    if not (cost_excerpt or "").strip() and not (approach_digest or "").strip():
        return []

    user = (
        "=== RFP COST / PRICING / PROPOSAL CONTENT EXCERPT ===\n"
        f"{(cost_excerpt or rfp_text or '')[:24000]}\n\n"
        "=== APPROACH / SCOPE DIGEST (for workstream fee homes) ===\n"
        f"{(approach_digest or '(none)')[:10000]}\n\n"
        "Extract EVERY distinct Cost / Pricing Proposal demand for THIS RFP. "
        "Cost must address each one (grounded text/fees or MANUAL FILL)."
    )
    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=4000,
        )
    except Exception:
        logger.warning("rfp_cost_demands extract failed", exc_info=True)
        return []
    demands = _parse_demands_payload(raw)
    logger.info("rfp_cost_demands extracted n=%s ids=%s", len(demands), [d.id for d in demands])
    return demands


async def audit_rfp_cost_demands(
    demands: list[RfpCostDemand],
    content: str,
    *,
    budget: ProposalBudget | None = None,
) -> list[RfpCostDemand]:
    """LLM: grounded / manual_fill / missing for each demand against Cost markdown."""
    if not demands:
        return []
    from app.services import llm

    ledger_hint = ""
    if budget is not None:
        lines = []
        for item in (budget.line_items or [])[:40]:
            desc = (item.description or "").strip()
            ext = item.extended
            lines.append(f"- {desc}: {ext}")
        pt = budget.client_media_passthrough
        ledger_hint = (
            f"Pass-through: {pt}\nLine items:\n" + "\n".join(lines)
            if lines or pt
            else ""
        )

    demand_blob = "\n".join(
        f"- id={d.id} kind={d.kind} requirement={d.requirement} quote={d.rfp_quote}"
        for d in demands
    )
    user = (
        "=== DEMANDS ===\n"
        f"{demand_blob}\n\n"
        "=== COST MANUSCRIPT ===\n"
        f"{(content or '')[:24000]}\n\n"
        "=== LEDGER HINT ===\n"
        f"{ledger_hint or '(none)'}\n"
    )
    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": _AUDIT_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
    except Exception:
        logger.warning("rfp_cost_demands audit failed", exc_info=True)
        return [d.model_copy(update={"satisfaction": "missing"}) for d in demands]

    by_id: dict[str, tuple[Satisfaction, str]] = {}
    if isinstance(raw, dict):
        rows = raw.get("results") or raw.get("demands") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rid = str(row.get("id") or "").strip().casefold()
                if not rid:
                    continue
                by_id[rid] = (
                    _norm_satisfaction(str(row.get("satisfaction") or "")),
                    str(row.get("evidence") or "")[:240],
                )

    out: list[RfpCostDemand] = []
    for d in demands:
        sat, evidence = by_id.get(d.id.casefold(), ("missing", ""))
        # If our own MANUAL FILL stub for this demand id is already in Cost, count it.
        if sat == "missing" and _stub_marker(d.id) in (content or ""):
            sat, evidence = "manual_fill", "stub present"
        out.append(d.model_copy(update={"satisfaction": sat, "evidence": evidence}))
    return out


def _stub_marker(demand_id: str) -> str:
    return f"<!-- rfp-cost-demand:{demand_id} -->"


def stub_markdown_for_demand(demand: RfpCostDemand) -> str:
    """Compact MANUAL FILL only — never a separate 'RFP Cost demand' section dump."""
    req = (demand.requirement or "Address this RFP Cost ask").strip()
    quote = (demand.rfp_quote or "").strip()
    quote_bit = f' RFP: "{quote[:180]}"' if quote else ""
    return (
        f"[MANUAL FILL: Sonja — {req}.{quote_bit} "
        f"Do not invent rates, commissions, or dollars; confirm from RFP/KB or Sonja.]\n"
    )


def strip_rfp_cost_demand_stub_sections(content: str) -> str:
    """Remove legacy '## RFP Cost demand — …' dump sections (and HTML markers)."""
    text = content or ""
    text = re.sub(
        r"(?ims)^##\s+RFP Cost demand[^\n]*\n+"
        r"(?:<!--\s*rfp-cost-demand:[^\n]*-->\s*\n+)?"
        r"(?:\[MANUAL FILL:[^\]]*\]\s*\n*)*",
        "",
        text,
    )
    text = re.sub(r"(?m)^<!--\s*rfp-cost-demand:[^\n]*-->\s*\n?", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + ("\n" if content.endswith("\n") else "")


def _professional_fee_total(budget: ProposalBudget | None) -> float | None:
    if budget is None:
        return None
    from app.services.proposal_budget_content import _professional_fees_and_direct

    fees, _direct = _professional_fees_and_direct(budget)
    if fees and fees > 0:
        return round(float(fees), 2)
    total = budget.lump_sum_total or budget.agency_revenue_estimate
    if total and float(total) > 0:
        return round(float(total), 2)
    return None


def _usd_plain(amount: float) -> str:
    if abs(amount - round(amount)) < 0.005:
        return f"${int(round(amount)):,}"
    return f"${amount:,.2f}"


def apply_grounded_demand_fills(
    content: str,
    demands: list[RfpCostDemand],
    *,
    budget: ProposalBudget | None = None,
) -> tuple[str, list[RfpCostDemand], list[str]]:
    """Write Cost answers that need no invented economics; skip MANUAL FILL dumps.

    Policy C: auto-write when grounded in ledger/RFP quote; only leave gaps when
    a number/% truly cannot be known.
    """
    text = strip_rfp_cost_demand_stub_sections(content or "")
    logs: list[str] = []
    updated: list[RfpCostDemand] = []
    fees = _professional_fee_total(budget)
    fee_phrase = _usd_plain(fees) if fees else None
    blocks: list[str] = []

    for demand in demands:
        if demand.satisfaction == "grounded":
            updated.append(demand)
            continue
        did = demand.id.casefold()
        req = (demand.requirement or "").casefold()
        quote = (demand.rfp_quote or "").casefold()
        blob = f"{did} {req} {quote}"
        filled = False
        paragraph = ""

        if any(k in blob for k in ("not_to_exceed", "nte", "total contract amount", "not to exceed")):
            if fee_phrase:
                paragraph = (
                    f"### Not-to-Exceed Total Contract Amount\n\n"
                    f"zö agency proposes a not-to-exceed total contract amount of "
                    f"**{fee_phrase}** for the annualized professional-fee scope in "
                    f"Fee Detail by Phase (inclusive of all professional fees shown). "
                    f"Media buy dollars directed by the client, if any, are pass-through "
                    f"at cost and are not included in this professional-fee ceiling. "
                    f"Any material change in scope will be documented in a scope addendum "
                    f"before work proceeds."
                )
                if any(
                    k in blob
                    for k in ("two-year", "two year", "option year", "option-year", "year 2")
                ):
                    paragraph += (
                        "\n\n[MANUAL FILL: Sonja — confirm whether this NTE repeats for "
                        "Year 2 of the base term and for any option year, or state the "
                        "adjusted multi-year NTE; do not invent escalation.]"
                    )
                filled = True

        elif any(
            k in blob
            for k in ("oral presentation", "oral_presentation", "offeror's expense", "offerors expense")
        ):
            paragraph = (
                "### Oral Presentation Expense\n\n"
                "If USD requires an oral presentation during selection, zö agency will "
                "make that presentation at its own expense; those costs are not billed "
                "to USD under this proposal or any resulting contract."
            )
            filled = True

        elif any(
            k in blob
            for k in ("cost effectiveness", "cost_effectiveness", "best overall value")
        ):
            fee_bit = f" totaling {fee_phrase}" if fee_phrase else ""
            paragraph = (
                "### Cost Effectiveness\n\n"
                "USD evaluates proposals for best overall value, including cost "
                "effectiveness alongside qualifications, experience, strategic approach, "
                "and demonstrated results. zö agency's transparent, flat phase-fee "
                f"structure{fee_bit} maps fees to the RFP scope items in Fee Detail by "
                "Phase so USD can judge cost effectiveness against clear deliverables — "
                "without separate expense add-ons."
            )
            filled = True

        elif any(
            k in blob
            for k in (
                "media planning",
                "media buying",
                "media_commission",
                "commission",
                "pass-through",
                "passthrough",
                "markup",
            )
        ) and demand.kind in {"disclosure", "workstream_funding", "other"}:
            paragraph = (
                "### Media Planning & Buying — Fee Treatment\n\n"
                "Media planning and buying labor for the annual scope is included in the "
                "professional phase fees in Fee Detail by Phase (not a separate media "
                "commission line). Client-directed media placement dollars, if any, are "
                "passed through at cost and are not marked up in this proposal; they are "
                "outside the professional-fee total unless USD authorizes a different "
                "arrangement in a scope addendum."
            )
            filled = True

        elif any(k in blob for k in ("subcontract", "sub-contractor", "subcontractor")):
            paragraph = (
                "### Subcontractor Cost Treatment\n\n"
                "zö agency does not plan to use subcontractors for the services described "
                "herein without the State's prior written consent. If consent is granted, "
                "any approved subcontractor costs are absorbed within the professional "
                "phase fees above unless a written scope addendum states otherwise — no "
                "separate subcontractor markup is proposed here."
            )
            filled = True

        if filled and paragraph:
            # Avoid duplicating if a similar heading already exists.
            heading = paragraph.split("\n", 1)[0].casefold()
            if heading not in text.casefold():
                blocks.append(paragraph.strip())
                logs.append(f"Grounded Cost fill: {demand.id}")
            updated.append(
                demand.model_copy(
                    update={
                        "satisfaction": "grounded",
                        "evidence": "deterministic grounded fill",
                    }
                )
            )
        else:
            updated.append(demand)

    if blocks:
        insert = "\n\n".join(blocks)
        # Place before Fee Detail if present, else before Revision Rounds, else end.
        m = re.search(r"(?im)^##\s+Fee Detail by Phase\s*$", text)
        if m:
            text = text[: m.start()].rstrip() + "\n\n" + insert + "\n\n" + text[m.start() :]
        else:
            text = text.rstrip() + "\n\n" + insert + "\n"
    return text, updated, logs


def ensure_rfp_asks_in_fee_detail_table(
    content: str,
    demands: list[RfpCostDemand],
) -> tuple[str, list[str]]:
    """Put still-missing workstream / instrument fee homes INTO Fee Detail rows.

    RFP asks that need a visible fee home belong in the table — not as prose dumps
    below it. Fee cell is MANUAL FILL (no invented dollars).
    """
    text = content or ""
    logs: list[str] = []
    if "| Phase |" not in text or "Fee Detail" not in text:
        return text, logs

    # Only fee-home style demands go in the table.
    needed: list[RfpCostDemand] = []
    for d in demands:
        if d.satisfaction not in {"missing", "manual_fill"}:
            continue
        if d.kind in {"workstream_funding", "instrument"}:
            needed.append(d)
            continue
        blob = f"{d.id} {d.requirement}".casefold()
        if any(
            k in blob
            for k in (
                "fee home",
                "media planning",
                "media buying",
                "hourly rate",
                "rate schedule",
            )
        ):
            needed.append(d)

    if not needed:
        return text, logs

    rows_to_add: list[str] = []
    for d in needed:
        phase = (d.id.replace("_", " ").strip().title() or "RFP scope item")[:60]
        # Skip if phase label or requirement already appears in the fee table block.
        table_m = re.search(
            r"(?is)(##\s+Fee Detail by Phase.*?)(?=^##\s|\Z)",
            text,
        )
        table_blob = (table_m.group(1) if table_m else text).casefold()
        if phase.casefold() in table_blob:
            continue
        req_short = (d.requirement or "RFP-required fee home")[:120]
        scope = _md_escape_cell(req_short)
        fee = (
            f"[MANUAL FILL: Sonja — confirm fee for {phase}; "
            f"do not invent dollars]"
        )
        rows_to_add.append(f"| {phase} | {scope} | {fee} |")
        logs.append(f"Fee Detail row for RFP ask: {d.id}")

    if not rows_to_add:
        return text, logs

    # Insert before the Total row of Fee Detail.
    def _inject(match: re.Match[str]) -> str:
        block = match.group(0)
        total_m = re.search(r"(?im)^\|\s*\*\*Total\*\*.*$", block)
        if not total_m:
            return block.rstrip() + "\n" + "\n".join(rows_to_add) + "\n"
        return (
            block[: total_m.start()].rstrip()
            + "\n"
            + "\n".join(rows_to_add)
            + "\n"
            + block[total_m.start() :]
        )

    new_text, n = re.subn(
        r"(?is)##\s+Fee Detail by Phase.*?(?=^##\s|\Z)",
        _inject,
        text,
        count=1,
    )
    if n == 0:
        return text, []
    return new_text, logs


def _md_escape_cell(value: str) -> str:
    return (value or "").replace("|", "/").replace("\n", " ").strip()


def apply_missing_demand_stubs(
    content: str,
    demands: list[RfpCostDemand],
) -> tuple[str, list[str]]:
    """Append compact MANUAL FILL only for still-missing non-table demands."""
    text = strip_rfp_cost_demand_stub_sections(content or "")
    logs: list[str] = []
    pending: list[str] = []
    for demand in demands:
        if demand.satisfaction != "missing":
            continue
        # Fee-home asks are handled as Fee Detail rows — skip prose stubs.
        if demand.kind in {"workstream_funding", "instrument"}:
            continue
        blob = f"{demand.id} {demand.requirement}".casefold()
        if any(
            k in blob
            for k in ("fee home", "media planning", "media buying", "rate schedule")
        ):
            continue
        marker = _stub_marker(demand.id)
        if marker in text:
            continue
        req_key = (demand.requirement or "")[:60].casefold()
        if req_key and req_key in text.casefold() and "manual fill" in text.casefold():
            continue
        did = demand.id.casefold().replace("_", " ")
        if did and did[:24] in text.casefold():
            continue
        pending.append(stub_markdown_for_demand(demand).strip())
        logs.append(f"Added compact RFP Cost MANUAL FILL: {demand.id}")
    if pending:
        text = (
            text.rstrip()
            + "\n\n### Outstanding Cost confirmations\n\n"
            + "\n\n".join(pending)
            + "\n"
        )
    return text, logs


def unmet_rfp_cost_demands(demands: list[RfpCostDemand]) -> list[RfpCostDemand]:
    return [d for d in demands if d.satisfaction == "missing"]


def format_rfp_cost_demands_for_prompt(demands: list[RfpCostDemand]) -> str:
    if not demands:
        return ""
    lines = [
        "=== RFP COST DEMANDS (mandatory — satisfy each or emit MANUAL FILL) ===",
        "Do NOT invent commission % or media-base dollars. Ground in RFP/KB or MANUAL FILL.",
        "Judge THIS RFP's asks only — scopes differ across RFPs.",
    ]
    for d in demands:
        quote = (d.rfp_quote or "").strip()
        lines.append(f"- [{d.id}] ({d.kind}) {d.requirement}")
        if quote:
            lines.append(f'  Quote: "{quote[:300]}"')
    return "\n".join(lines)


_ALIGN_REWRITE_SYSTEM = """You rewrite a Cost / Pricing Proposal manuscript so it
satisfies THIS RFP's Cost demands WHILE KEEPING A CLEAN CLIENT FEE TABLE.

Hard rules:
- MUST preserve (or restore) the markdown table under "## Fee Detail by Phase"
  with columns Phase | Scope | Fee and a Total row. Never replace the fee table
  with prose-only "Fee phases:" lists. Never delete the table.
- MUST preserve "## Hourly Rate Schedule by Classification" when present.
- Do NOT invent commission percentages, hourly rates, or new fee dollar amounts.
- Keep every existing Fee Detail / Proposed Investment / rate-table dollar EXACT.
- Do NOT create headings like "## RFP Cost demand — …" or invent «MFILL_N» tokens.
- Prefer short grounded prose that answers the demand (NTE = proposed professional
  fee total from Proposed Investment; oral presentation at offeror's expense;
  media labor in phase fees + media buy pass-through; subcontractors only with
  State consent and costs inside phase fees; cost-effectiveness narrative).
- MANUAL FILL only when a number/% truly cannot be known (e.g. Year-2 escalation
  not in RFP/KB). Use one compact [MANUAL FILL: Sonja — …] line — not a stub dump.
- «MFILL_N» tokens already in the manuscript are PROTECTED — copy unchanged.
- When the RFP requires all-in fees: DELETE "### Reimbursable Expenses" that says
  expenses will be billed at cost.
- Align billing narrative with flat phase fees (not hours×rate invoicing).
- One Fee Detail table only. Keep Investment Framing / Scope Protection / Revision
  Rounds once (no duplicated Terms).
- Return the FULL revised markdown (not a diff).

Return JSON:
{"content":"full markdown","changes":["short bullet","..."]}
"""


async def rewrite_cost_to_satisfy_demands(
    content: str,
    demands: list[RfpCostDemand],
    *,
    rfp_text: str = "",
    approach_digest: str = "",
    budget: ProposalBudget | None = None,
) -> tuple[str, list[str]]:
    """LLM rewrite Cost for unmet/manual demands — no invented fee dollars."""
    if not demands:
        return content, []
    actionable = [d for d in demands if d.satisfaction in {"missing", "manual_fill"}]
    if not actionable:
        return content, []
    focus = actionable

    from app.services import llm
    from app.services.proposal_budget_content import (
        ensure_pricing_guide_verbatim_in_budget_markdown,
        manuscript_asserts_all_in_no_separate_expenses,
        strip_guide_reimbursable_expenses_heading_block,
    )
    from app.services.proposal_manual_flags import (
        mask_manual_fill_tags,
        missing_manual_fill_placeholders,
        unmask_manual_fill_tags,
    )
    from app.services.proposal_rfp_excerpt import budget_and_cost_excerpt

    masked, mfill_originals = mask_manual_fill_tags(content or "")
    demand_blob = format_rfp_cost_demands_for_prompt(focus)
    ledger = ""
    if budget is not None:
        parts = []
        for item in (budget.line_items or [])[:50]:
            parts.append(
                f"- {(item.description or '').strip()}: extended={item.extended}"
            )
        ledger = "\n".join(parts)
    user = (
        f"{demand_blob}\n\n"
        "=== CURRENT COST MANUSCRIPT (protected «MFILL_N» must survive) ===\n"
        f"{masked[:22000]}\n\n"
        "=== RFP COST EXCERPT ===\n"
        f"{budget_and_cost_excerpt(rfp_text or '', max_chars=12000)}\n\n"
        "=== APPROACH / SCOPE DIGEST ===\n"
        f"{(approach_digest or '(none)')[:6000]}\n\n"
        "=== LEDGER LINE HINT (dollars locked) ===\n"
        f"{ledger or '(none)'}\n"
    )
    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": _ALIGN_REWRITE_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=8000,
        )
    except Exception:
        logger.warning("rfp_cost_demands align rewrite failed", exc_info=True)
        return content, []

    if not isinstance(raw, dict):
        return content, []
    new_content = str(raw.get("content") or raw.get("markdown") or "").strip()
    if len(new_content) < 80:
        return content, []

    new_content = unmask_manual_fill_tags(new_content, mfill_originals)
    # Never keep LLM-invented demand dump sections.
    new_content = strip_rfp_cost_demand_stub_sections(new_content)
    from app.services.proposal_manual_flags import scrub_orphan_mfill_placeholders

    if "«MFILL_" in new_content:
        new_content, _orphan = scrub_orphan_mfill_placeholders(new_content)
    # Always restore Fee Detail from the locked ledger after rewrite.
    if budget is not None:
        from app.services.proposal_budget_content import (
            ensure_fee_detail_table_in_budget_markdown,
        )

        new_content = ensure_fee_detail_table_in_budget_markdown(new_content, budget)

    dropped = missing_manual_fill_placeholders(new_content, mfill_originals)
    if dropped:
        new_content = (
            new_content.rstrip()
            + "\n\n### Outstanding handoffs (must not delete)\n\n"
            + "\n".join(dropped)
            + "\n"
        )

    omit_reimb = demands_require_omit_guide_reimbursables(
        focus
    ) or manuscript_asserts_all_in_no_separate_expenses(new_content)
    if omit_reimb:
        new_content = strip_guide_reimbursable_expenses_heading_block(new_content)
        new_content = ensure_pricing_guide_verbatim_in_budget_markdown(
            new_content, include_reimbursable=False
        )

    changes = raw.get("changes") or raw.get("logs") or []
    logs: list[str] = []
    if isinstance(changes, list):
        for c in changes[:12]:
            s = str(c).strip()
            if s:
                logs.append(f"RFP align: {s}")
    if dropped:
        logs.append(
            f"Restored {len(dropped)} MANUAL FILL handoff(s) the rewrite tried to drop"
        )
    if omit_reimb:
        logs.append(
            "Omitted guide Reimbursable Expenses block (RFP all-in / no separate billing)"
        )
    if not logs:
        logs.append("RFP align: rewrote Cost to address unmet RFP Cost demands")
    return new_content, logs


async def ensure_rfp_cost_demands_in_budget_markdown(
    content: str,
    *,
    rfp_text: str,
    approach_digest: str = "",
    budget: ProposalBudget | None = None,
    demands: list[RfpCostDemand] | None = None,
    rewrite: bool = False,
) -> tuple[str, list[RfpCostDemand], list[str]]:
    """Extract (unless provided) → audit → grounded fills → optional LLM → stubs.

    Runs for every RFP: demand list is LLM-extracted from THAT RFP's cost asks.
    Always restores Fee Detail by Phase from the ledger when present.
    """
    working = list(demands) if demands is not None else await extract_rfp_cost_demands(
        rfp_text=rfp_text, approach_digest=approach_digest
    )
    text = strip_rfp_cost_demand_stub_sections(content or "")
    logs: list[str] = []
    if budget is not None:
        from app.services.proposal_budget_content import (
            ensure_fee_detail_table_in_budget_markdown,
        )

        text = ensure_fee_detail_table_in_budget_markdown(text, budget)

    if not working:
        return text, [], logs

    audited = await audit_rfp_cost_demands(working, text, budget=budget)
    # Deterministic grounded fills first — avoid stub dumps for solvable asks.
    text, audited, fill_logs = apply_grounded_demand_fills(
        text, audited, budget=budget
    )
    logs.extend(fill_logs)

    should_rewrite = rewrite or any(d.satisfaction == "missing" for d in audited)
    if should_rewrite and any(
        d.satisfaction in {"missing", "manual_fill"} for d in audited
    ):
        text, align_logs = await rewrite_cost_to_satisfy_demands(
            text,
            audited,
            rfp_text=rfp_text,
            approach_digest=approach_digest,
            budget=budget,
        )
        logs.extend(align_logs)
        audited = await audit_rfp_cost_demands(audited, text, budget=budget)
        text, audited, fill_logs2 = apply_grounded_demand_fills(
            text, audited, budget=budget
        )
        logs.extend(fill_logs2)

    text, stub_logs = apply_missing_demand_stubs(text, audited)
    logs.extend(stub_logs)

    from app.services.proposal_budget_content import (
        ensure_fee_detail_table_in_budget_markdown,
        ensure_pricing_guide_verbatim_in_budget_markdown,
        manuscript_asserts_all_in_no_separate_expenses,
    )

    if budget is not None:
        text = ensure_fee_detail_table_in_budget_markdown(text, budget)

    # Workstream / instrument asks → rows inside Fee Detail (not prose dumps).
    text, row_logs = ensure_rfp_asks_in_fee_detail_table(text, audited)
    logs.extend(row_logs)

    omit_reimb = demands_require_omit_guide_reimbursables(
        audited
    ) or manuscript_asserts_all_in_no_separate_expenses(text)
    if omit_reimb:
        text = ensure_pricing_guide_verbatim_in_budget_markdown(
            text, include_reimbursable=False
        )
        if budget is not None:
            text = ensure_fee_detail_table_in_budget_markdown(text, budget)
            text, row_logs2 = ensure_rfp_asks_in_fee_detail_table(text, audited)
            logs.extend(row_logs2)

    final: list[RfpCostDemand] = []
    for d in audited:
        if d.satisfaction == "missing" and (
            _stub_marker(d.id) in text
            or (d.requirement[:40].casefold() in text.casefold() and "manual fill" in text.casefold())
        ):
            final.append(
                d.model_copy(
                    update={"satisfaction": "manual_fill", "evidence": "stub added"}
                )
            )
        else:
            final.append(d)

    from app.services.proposal_budget_content import (
        sync_proposed_investment_to_fee_detail_total,
    )

    text = sync_proposed_investment_to_fee_detail_total(text, budget)
    return text, final, logs
