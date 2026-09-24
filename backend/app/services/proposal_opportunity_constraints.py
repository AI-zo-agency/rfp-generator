"""Phase-2 opportunity → hard constraints for Cost / SOW / Timeline writers.

Agent 1 (LangExtract + Sonnet) already extracts scope, dual NTEs, pricing
instrument cues, and timeline. Phase 3.5 and drafting must consume that pack as
authoritative constraints — not re-invent from the Pricing Guide menu.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

Focus = Literal["all", "budget", "sow", "timeline"]

# Submission / logistics lines that pollute scope.mandatory (not SOW deliverables).
# Principle-based: buyer submission/award chrome — never client-specific names.
_ADMIN_PREFIXES = (
    "submit ",
    "proposals will",
    "proposals must",
    "proposals do ",
    "proposals transmitted",
    "p roposals ",  # OCR-split "Proposals"
    "all proposals",
    "all openings",
    "bidders shall",
    "bidder shall",
    "proposal openings",
    "proposal form",
    "certificate of insurance",
    "awarded contractor",
    "in the event of any discrepancy",
    "work/specifications will",
    "demandstar",
)

_ADMIN_CONTAINS = (
    "notice of intent to award",
    "notice of award",
    "certificate of good standing",
    "transmission failures",
    "transmitted in person",
    "hard copy specification",
    "secretary of state",
    "corporate/llc certificate",
)


def plan_dict_from(obj: Any) -> dict[str, Any]:
    """Normalize research cache / execution plan / bare dict → plan dict."""
    if obj is None:
        return {}
    plan = obj
    if hasattr(obj, "proposal_execution_plan"):
        plan = getattr(obj, "proposal_execution_plan", None)
    if plan is None:
        return {}
    if hasattr(plan, "model_dump"):
        try:
            return plan.model_dump(by_alias=True)
        except Exception:  # noqa: BLE001
            return {}
    if isinstance(plan, dict):
        # Research payload may wrap the plan.
        if "opportunity" in plan or "delivery" in plan:
            return plan
        nested = plan.get("proposalExecutionPlan") or plan.get("proposal_execution_plan")
        if isinstance(nested, dict):
            return nested
        if nested is not None and hasattr(nested, "model_dump"):
            try:
                return nested.model_dump(by_alias=True)
            except Exception:  # noqa: BLE001
                return {}
        return plan
    return {}


def _opportunity_slices(plan: dict[str, Any]) -> tuple[dict, dict, dict, dict, dict]:
    opp = plan.get("opportunity") if isinstance(plan.get("opportunity"), dict) else {}
    und = opp.get("understanding") if isinstance(opp.get("understanding"), dict) else {}
    scope = opp.get("scope") if isinstance(opp.get("scope"), dict) else {}
    compliance = opp.get("compliance") if isinstance(opp.get("compliance"), dict) else {}
    delivery = plan.get("delivery") if isinstance(plan.get("delivery"), dict) else {}
    budget_plan = (
        delivery.get("budget") if isinstance(delivery.get("budget"), dict) else {}
    )
    bi = und.get("budgetIntel") if isinstance(und.get("budgetIntel"), dict) else {}
    if not bi:
        bi = und.get("budget_intel") if isinstance(und.get("budget_intel"), dict) else {}
    tl = und.get("timelineIntel") if isinstance(und.get("timelineIntel"), dict) else {}
    if not tl:
        tl = und.get("timeline_intel") if isinstance(und.get("timeline_intel"), dict) else {}
    return scope, bi, tl, budget_plan, compliance


def _is_admin_scope_line(text: str) -> bool:
    low = (text or "").strip().casefold()
    if not low:
        return True
    if any(low.startswith(p) for p in _ADMIN_PREFIXES):
        return True
    return any(p in low for p in _ADMIN_CONTAINS)


def _scope_deliverables(scope: dict[str, Any], *, limit: int = 18) -> list[str]:
    raw = scope.get("mandatory") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip() if not isinstance(item, dict) else str(
            item.get("text") or item.get("requirement") or item.get("object") or ""
        ).strip()
        if not text or _is_admin_scope_line(text):
            continue
        out.append(text[:350])
        if len(out) >= limit:
            break
    return out


def _pricing_compliance_lines(compliance: dict[str, Any], *, limit: int = 8) -> list[str]:
    items = compliance.get("items") or []
    if not isinstance(items, list):
        return []
    hits: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        req = str(item.get("requirement") or "").strip()
        if not req:
            continue
        blob = req.casefold()
        if not any(
            k in blob
            for k in (
                "pricing form",
                "price proposal",
                "cost proposal",
                "quotation form",
                "hourly",
                "fee schedule",
                "rate schedule",
                "not to exceed",
                "not-to-exceed",
                "budget",
            )
        ):
            continue
        hits.append(req[:300])
        if len(hits) >= limit:
            break
    return hits


def opportunity_pricing_format_hint(obj: Any) -> str | None:
    """Return budgetFormat when Phase-2 opportunity strongly indicates a form instrument.

    - Proposal Pricing Form + hourly / T&M → blended_rate_form
    - Role / labor-category hourly table → personnel_loading
    - Otherwise None (leave judge / LLM).
    """
    plan = plan_dict_from(obj)
    if not plan:
        return None
    scope, bi, _tl, budget_plan, compliance = _opportunity_slices(plan)
    blob_parts = [
        str(bi.get("notes") or ""),
        str(bi.get("pricingModelHint") or bi.get("pricing_model_hint") or ""),
        str(bi.get("basePricingModel") or bi.get("base_pricing_model") or ""),
        str(budget_plan.get("pricingModel") or budget_plan.get("pricing_model") or ""),
        str(budget_plan.get("ceiling") or ""),
        str(budget_plan.get("pricingStrategy") or ""),
        str(scope.get("notes") or ""),
        " ".join(_pricing_compliance_lines(compliance, limit=12)),
    ]
    blob = " ".join(blob_parts).casefold()
    if not blob.strip():
        return None

    role_hourly = any(
        k in blob
        for k in (
            "by role",
            "labor category",
            "job title",
            "classification",
            "personnel loading",
            "rate by classification",
        )
    )
    formish = any(
        k in blob
        for k in (
            "proposal pricing form",
            "pricing proposal form",
            "official pricing form",
            "quotation form",
            "cost proposal form",
        )
    )
    hourly = any(
        k in blob
        for k in ("hourly", "t&m", "time and material", "time & material", "billed hourly")
    )
    dual_track = ("part 1" in blob and "part 2" in blob) or (
        "separate" in blob and ("not-to-exceed" in blob or "not to exceed" in blob)
    )

    if role_hourly:
        logger.info("opportunity_format_hint=personnel_loading reason=role_hourly")
        return "personnel_loading"
    if formish and (hourly or dual_track):
        logger.info(
            "opportunity_format_hint=blended_rate_form reason=form+hourly_or_dual_track"
        )
        return "blended_rate_form"
    if (budget_plan.get("pricingModel") or "").strip().upper() in {"T&M", "TM", "HOURLY"}:
        if formish or hourly:
            logger.info("opportunity_format_hint=blended_rate_form reason=tm_model")
            return "blended_rate_form"
    return None


def budget_format_omits_fee_detail(budget_format: str | None) -> bool:
    """True when the scored instrument is the form/hourly table — not Fee Detail by Phase."""
    fmt = (budget_format or "").casefold().replace("-", "_")
    return fmt in {"blended_rate_form", "personnel_loading"}


def _typed_delivery_from(obj: Any) -> Any:
    """Return DeliveryConstraints when research (or dict) carries typed pack."""
    if obj is None:
        return None
    raw = getattr(obj, "delivery_constraints", None)
    if raw is None and isinstance(obj, dict):
        raw = obj.get("deliveryConstraints") or obj.get("delivery_constraints")
    if raw is None:
        return None
    if hasattr(raw, "mandatory_deliverables"):
        return raw
    if isinstance(raw, dict):
        try:
            from app.models.delivery_constraints import DeliveryConstraints

            return DeliveryConstraints.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None
    return None


def _typed_instrument_from(obj: Any) -> Any:
    """Return PricingInstrument when research (or dict) carries typed pack."""
    if obj is None:
        return None
    raw = getattr(obj, "pricing_instrument", None)
    if raw is None and isinstance(obj, dict):
        raw = obj.get("pricingInstrument") or obj.get("pricing_instrument")
    if raw is None:
        return None
    if hasattr(raw, "kind"):
        return raw
    if isinstance(raw, dict):
        try:
            from app.models.pricing_instrument import PricingInstrument

            return PricingInstrument.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None
    return None


def format_opportunity_hard_constraints(
    obj: Any,
    *,
    focus: Focus = "all",
    max_chars: int = 8_000,
) -> str:
    """Compact HARD CONSTRAINTS block for Cost / SOW / Timeline prompts."""
    plan = plan_dict_from(obj)
    if not plan:
        return ""

    scope, bi, tl, budget_plan, compliance = _opportunity_slices(plan)
    want_budget = focus in {"all", "budget"}
    want_sow = focus in {"all", "sow", "budget"}  # budget needs deliverable coverage too
    want_timeline = focus in {"all", "timeline", "budget"}
    typed_dc = _typed_delivery_from(obj)
    typed_inst = _typed_instrument_from(obj)

    lines: list[str] = [
        "=== OPPORTUNITY HARD CONSTRAINTS (Phase 2 extract — authoritative) ===",
        "Treat as binding. Do NOT invent alternate SOW, merge budget tracks, "
        "or substitute Pricing Guide default scopes (e.g. County-wide / 3 platforms) "
        "when THIS list conflicts. Guide SKUs are for internal cost build only.",
    ]

    client = ""
    und = (
        (plan.get("opportunity") or {}).get("understanding")
        if isinstance(plan.get("opportunity"), dict)
        else {}
    )
    if isinstance(und, dict):
        client = str(und.get("client") or "").strip()
    if client:
        lines.append(f"- Client / buyer entity: {client}")

    # Budget / format first (must survive max_chars truncation before long SOW lists).
    if want_budget:
        if bi:
            for keys, label in (
                (("ceiling",), "Ceiling"),
                (("maximumAgreementAmount", "maximum_agreement_amount"), "Maximum agreement"),
                (("pricingModelHint", "pricing_model_hint"), "Pricing model hint"),
                (("basePricingModel", "base_pricing_model"), "Base pricing model"),
                (("contractType", "contract_type"), "Contract type"),
                (("notes",), "Budget intel notes"),
            ):
                val = next((str(bi[k]).strip() for k in keys if bi.get(k)), "")
                if val:
                    lines.append(f"- {label}: {val[:800]}")
        if budget_plan:
            for keys, label in (
                (("pricingModel", "pricing_model"), "Delivery pricing model"),
                (("ceiling",), "Delivery ceiling / track caps"),
                (("contractType", "contract_type"), "Delivery contract type"),
                (("pricingStrategy", "pricing_strategy"), "Pricing strategy"),
            ):
                val = next(
                    (str(budget_plan[k]).strip() for k in keys if budget_plan.get(k)),
                    "",
                )
                if val:
                    lines.append(f"- {label}: {val[:900]}")
            constraints = budget_plan.get("constraints") or []
            if isinstance(constraints, list) and constraints:
                lines.append("- Budget constraints (do not violate):")
                for c in constraints[:8]:
                    text = str(c).strip()[:350]
                    if text:
                        lines.append(f"  • {text}")
        notes = str(scope.get("notes") or "").strip()
        if notes:
            lines.append(f"- Scope notes (caps / tracks / pricing): {notes[:1200]}")
        # Typed tracks win over freeform ceiling prose when present.
        typed_tracks = (
            (typed_inst.tracks if typed_inst and typed_inst.tracks else None)
            or (typed_dc.tracks if typed_dc and typed_dc.tracks else None)
        )
        if typed_tracks:
            if typed_inst and typed_inst.kind and typed_inst.kind != "none":
                lines.append(f"- Typed instrument.kind: {typed_inst.kind}")
            lines.append("- Typed tracks / NTEs (authoritative — do not merge):")
            for t in typed_tracks:
                label = getattr(t, "label", "") or getattr(t, "id", "track")
                nte = getattr(t, "nte_annual", None)
                nte_s = (
                    f"${int(nte):,}/yr NTE"
                    if nte is not None and float(nte).is_integer()
                    else (f"${nte:,.2f}/yr NTE" if nte is not None else "NTE TBD")
                )
                lines.append(f"  • {label} — {nte_s}")
            if typed_dc and typed_dc.non_commingle_tracks:
                lines.append("- Typed delivery: tracks must not be commingled.")
        pricing_reqs = _pricing_compliance_lines(compliance)
        if pricing_reqs:
            lines.append("- Compliance pricing / form obligations:")
            for req in pricing_reqs:
                lines.append(f"  • {req}")
        hint = opportunity_pricing_format_hint(plan)
        if typed_inst is not None and typed_inst.kind == "buyer_pricing_form":
            if float(getattr(typed_inst, "confidence", 0) or 0) >= 0.55:
                hint = hint or "blended_rate_form"
        elif typed_inst is not None and typed_inst.kind == "personnel_loading":
            hint = hint or "personnel_loading"
        if hint:
            lines.append(
                f"- REQUIRED budgetFormat for manuscript Cost: {hint} "
                "(official form / hourly instrument — do NOT emit Fee Detail by Phase "
                "as the scored response; internal tier build may support hours only)."
            )
            lines.append(
                "- When separate Part / track NTEs exist, emit separate subtotals and "
                "keep each track under its own cap — never one merged phase-fee total."
            )
            lines.append(
                "- Do not add Travel / Reimbursables unless THIS RFP form allows expenses; "
                "firm-fixed / form-only instruments omit separate expense lines."
            )
            lines.append(
                "- description may cite deliverables by name; do NOT invent "
                "'RFP §X, Approach Item Y' citations that are not verbatim in the RFP."
            )

    if want_timeline:
        def _pick(d: dict[str, Any], *keys: str) -> str:
            for key in keys:
                val = str(d.get(key) or "").strip()
                if val:
                    return val
            return ""

        typed_horizon = typed_dc.horizon if typed_dc else None
        if typed_horizon and any(
            (typed_horizon.base_term, typed_horizon.renewals, typed_horizon.max_term)
        ):
            lines.append(
                "- Contract / performance timeline (typed DeliveryConstraints — "
                "narrate THIS horizon — not a default 10-month cadence):"
            )
            if typed_horizon.base_term:
                lines.append(f"  • Base term: {typed_horizon.base_term}")
            if typed_horizon.renewals:
                lines.append(f"  • Renewals: {typed_horizon.renewals}")
            if typed_horizon.max_term:
                lines.append(f"  • Max term: {typed_horizon.max_term}")
        else:
            horizon = _pick(tl, "contractHorizon", "contract_horizon")
            perf_end = _pick(tl, "performanceEnd", "performance_end")
            start = _pick(
                tl, "projectStart", "project_start", "initialTermStart", "initial_term_start"
            )
            options = _pick(tl, "optionPeriods", "option_periods")
            sched = _pick(tl, "scheduleAuthority", "schedule_authority")
            go_live = _pick(tl, "goLive", "go_live")
            completion = _pick(tl, "completion")
            if any((horizon, perf_end, start, options, sched, go_live, completion)):
                lines.append(
                    "- Contract / performance timeline (narrate THIS horizon — "
                    "not a default 10-month cadence):"
                )
                if start:
                    lines.append(f"  • Start: {start}")
                if horizon:
                    lines.append(f"  • Horizon: {horizon}")
                if perf_end:
                    lines.append(f"  • Performance / funding end: {perf_end}")
                elif completion:
                    lines.append(f"  • Completion: {completion}")
                if options:
                    lines.append(f"  • Options: {options}")
                if go_live:
                    lines.append(f"  • Go-live: {go_live}")
                if sched:
                    lines.append(f"  • Schedule authority: {sched}")

    if want_sow:
        typed_musts = (
            list(typed_dc.mandatory_deliverables)
            if typed_dc and typed_dc.mandatory_deliverables
            else []
        )
        deliverables = typed_musts or _scope_deliverables(scope)
        if deliverables:
            lines.append(
                "- Mandatory SOW deliverables (price and narrate ONLY these; "
                "omit County-wide invent):"
            )
            for i, d in enumerate(deliverables[:18], 1):
                lines.append(f"  {i}. {str(d).strip()[:350]}")
        if typed_dc and typed_dc.out_of_scope:
            lines.append("- Out of scope (typed — do not invent):")
            for item in typed_dc.out_of_scope[:10]:
                text = str(item).strip()[:300]
                if text:
                    lines.append(f"  • {text}")
        if not want_budget:
            notes = str(scope.get("notes") or "").strip()
            if notes:
                lines.append(f"- Scope notes (caps / tracks / pricing): {notes[:1200]}")
        optional = scope.get("optional") or []
        if isinstance(optional, list) and optional:
            lines.append("- Optional / as-needed:")
            for item in optional[:6]:
                text = str(item).strip()[:300]
                if text and not _is_admin_scope_line(text):
                    lines.append(f"  • {text}")
        deps = scope.get("dependencies") or []
        if isinstance(deps, list) and deps:
            lines.append("- Dependencies:")
            for item in deps[:5]:
                text = str(item).strip()[:250]
                if text:
                    lines.append(f"  • {text}")
        if typed_dc and typed_dc.bidder_proposes:
            lines.append("- Bidder proposes (typed):")
            for item in typed_dc.bidder_proposes[:8]:
                text = str(item).strip()[:300]
                if text:
                    lines.append(f"  • {text}")

    # Only the header → empty signal
    if len(lines) <= 3:
        return ""

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20].rstrip() + "\n…(truncated)"
    logger.info(
        "opportunity_hard_constraints focus=%s chars=%s deliverables=%s",
        focus,
        len(text),
        len(_scope_deliverables(scope)) if want_sow else 0,
    )
    return text
