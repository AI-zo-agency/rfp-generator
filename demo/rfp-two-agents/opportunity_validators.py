"""Post-LLM deterministic validators and normalizers (demo Agent 1)."""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.proposal_intelligence.agent_base import clamp_confidence

_POST_AWARD_PHRASES = (
    "if selected for award",
    "if awarded",
    "successful offeror",
    "awarded contractor",
    "after contract award",
    "upon award",
    "post-award",
    "post award",
    "prior to commencement",
    "during the contract",
    "if the offeror is selected",
    "selected for award",
    "upon selection",
    "w-9",
    "w9 form",
)

_CONDITIONAL_PHRASES = (
    "if subcontract",
    "if sub-contract",
    "subcontractors are used",
    "subcontractor will be",
    "confidential",
    "proprietary",
    "if exceptions",
    "if exception",
    "exceptions to",
    "exception to the",
    "if bidder answers",
    'if "no"',
    "if no ",
    "if applicable",
    "redline",
    "marked copy",
    "if unable",
    "if your organization",
    "if offeror has",
    "if the offeror",
    "only if",
    "when using",
    "alternate submission",
    "non-buynet",
    "if it finds",
    "should offeror find",
    "if submitting",
)

_SOFT_VERBS = (
    "are advised",
    "is advised",
    "are encouraged",
    "is encouraged",
    "is recommended",
    "are recommended",
    "may wish",
    "should consider",
)

_QUALIFIER_PATTERNS = (
    (re.compile(r"\bat least\b", re.I), "at least"),
    (re.compile(r"\bup to\b", re.I), "up to"),
    (re.compile(r"\bno more than\b", re.I), "no more than"),
    (re.compile(r"\bnot to exceed\b", re.I), "not to exceed"),
)

_ADMIN_SUCCESS_MARKERS = (
    "page number",
    "page limit",
    "font size",
    "margin",
    "table of contents",
    "bind",
    "staple",
    "file format",
    "naming convention",
)

_QUALIFICATION_OUTCOME_MARKERS = (
    "demonstrated",
    "experience",
    "qualification",
    "years of",
    "local knowledge",
    "references",
    "resume",
    "certification",
)


def _text_blob(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("requirement") or ""),
        str(item.get("targetSection") or ""),
        str(item.get("owner") or ""),
        str(item.get("evidenceNeeded") or ""),
        str(item.get("notes") or ""),
    ]
    return " ".join(parts).casefold()


def is_post_award_compliance_item(item: dict[str, Any]) -> bool:
    blob = _text_blob(item)
    target = str(item.get("targetSection") or "").casefold()
    if target.startswith("post-award") or target.startswith("post award"):
        return True
    return any(p in blob for p in _POST_AWARD_PHRASES)


def is_conditional_compliance_item(item: dict[str, Any]) -> bool:
    blob = _text_blob(item)
    if is_post_award_compliance_item(item):
        return True
    if any(p in blob for p in _CONDITIONAL_PHRASES):
        return True
    # Explicit IF/WHEN/ONLY IF in requirement
    req = str(item.get("requirement") or "")
    if re.search(r"\b(if|when|only if|unless)\b", req, re.I) and not re.search(
        r"\bif awarded a contract\b", req, re.I
    ):
        # "if awarded" handled as post-award; other ifs are conditional
        if not any(p in blob for p in _POST_AWARD_PHRASES):
            return True
    notes = str(item.get("notes") or "").casefold()
    if notes.startswith("conditional") or "only if" in notes or "applies only" in notes:
        return True
    return False


def is_advisory_compliance_item(item: dict[str, Any]) -> bool:
    blob = _text_blob(item)
    return any(s in blob for s in _SOFT_VERBS)


def normalize_compliance_mandatory_flags(cleaned: dict[str, Any]) -> list[str]:
    """RULE 1–3: bidder vs conditional vs post-award; soft verbs not mandatory."""
    fixes: list[str] = []
    compliance = cleaned.get("compliance")
    if not isinstance(compliance, dict):
        return fixes
    items = compliance.get("items")
    if not isinstance(items, list):
        return fixes
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if is_post_award_compliance_item(item):
            if item.get("mandatory") is not False:
                item["mandatory"] = False
                fixes.append(f"compliance[{i}]: post_award mandatory→false")
            ts = str(item.get("targetSection") or "")
            if ts and not ts.casefold().startswith("post"):
                item["targetSection"] = f"Post-Award / {ts}"
                fixes.append(f"compliance[{i}]: targetSection post-award prefix")
        elif is_conditional_compliance_item(item) or is_advisory_compliance_item(item):
            if item.get("mandatory") is not False:
                item["mandatory"] = False
                fixes.append(f"compliance[{i}]: conditional/advisory mandatory→false")
        owner = str(item.get("owner") or "").strip()
        if is_post_award_compliance_item(item) and owner.casefold() in ("", "bidder", "offeror"):
            item["owner"] = "contractor"
            fixes.append(f"compliance[{i}]: owner→contractor")
        elif not is_post_award_compliance_item(item) and owner.casefold() in ("", "contractor"):
            item["owner"] = "bidder"
    return fixes


def normalize_evaluation_scoring(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    ev = cleaned.get("evaluation")
    if not isinstance(ev, dict):
        cleaned["evaluation"] = {
            "scoredResponseForm": False,
            "totalPoints": None,
            "responseCharLimit": None,
            "criteria": [],
            "emphasis": [],
            "confidence": 0.0,
        }
        return ["evaluation: filled default object"]
    explicit = bool(pack.get("explicitScoringLikely"))
    lx_eval = pack.get("langextract", {}).get("evaluationHits") or []
    has_lx_scoring = any(
        h.get("class") in ("scoring_criterion", "total_points")
        for h in lx_eval
        if isinstance(h, dict)
    )
    if not explicit and not has_lx_scoring:
        if ev.get("scoredResponseForm") is True:
            ev["scoredResponseForm"] = False
            fixes.append("evaluation: scoredResponseForm→false (no evidence)")
        if ev.get("totalPoints") not in (None, "", 0):
            ev["totalPoints"] = None
            fixes.append("evaluation: totalPoints→null")
        if ev.get("criteria"):
            ev["criteria"] = []
            fixes.append("evaluation: criteria→[]")
    if ev.get("scoredResponseForm") is False:
        if ev.get("criteria"):
            ev["criteria"] = []
            fixes.append("evaluation: criteria cleared (not scored form)")
        if ev.get("totalPoints") not in (None, ""):
            ev["totalPoints"] = None
            fixes.append("evaluation: totalPoints nullified")
    ev.setdefault("emphasis", ev.get("emphasis") or [])
    ev.setdefault("confidence", 0.0)
    return fixes


def normalize_success_criteria(cleaned: dict[str, Any]) -> list[str]:
    """Map theme→criterion; drop admin pollution."""
    fixes: list[str] = []
    sc = cleaned.get("successCriteria")
    if not isinstance(sc, dict):
        cleaned["successCriteria"] = {"items": [], "confidence": 0.0}
        return fixes
    items = sc.get("items")
    if not isinstance(items, list):
        sc["items"] = []
        return fixes
    out: list[dict[str, Any]] = []
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        crit = str(row.get("criterion") or row.get("theme") or row.get("name") or "").strip()
        if not crit:
            fixes.append(f"successCriteria[{i}]: dropped empty")
            continue
        low = crit.casefold()
        if any(m in low for m in _ADMIN_SUCCESS_MARKERS):
            fixes.append(f"successCriteria[{i}]: dropped admin '{crit[:40]}'")
            continue
        out.append(
            {
                "criterion": crit,
                "why": str(row.get("why") or row.get("reason") or ""),
                "recurringTheme": bool(row.get("recurringTheme", row.get("recurring_theme", True))),
            }
        )
        if "theme" in row and "criterion" not in row:
            fixes.append(f"successCriteria[{i}]: theme→criterion")
    sc["items"] = out
    sc.setdefault("confidence", 0.0)
    return fixes


def scrub_desired_outcomes(cleaned: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    u = cleaned.get("understanding")
    if not isinstance(u, dict):
        return fixes
    outcomes = u.get("desiredOutcomes")
    if not isinstance(outcomes, list):
        return fixes
    kept: list[str] = []
    for o in outcomes:
        s = str(o or "").strip()
        if not s:
            continue
        low = s.casefold()
        if any(m in low for m in _QUALIFICATION_OUTCOME_MARKERS):
            fixes.append(f"desiredOutcomes: moved qualification out '{s[:50]}'")
            continue
        kept.append(s)
    u["desiredOutcomes"] = kept
    return fixes


def _scope_strings(scope: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("mandatory", "optional", "futurePhases", "outOfScope", "dependencies"):
        for x in scope.get(key) or []:
            out.append(str(x))
    return out


def detect_scope_contradictions(cleaned: dict[str, Any]) -> list[str]:
    """Append conflict notes for frequency/date mismatches."""
    fixes: list[str] = []
    scope = cleaned.get("scope")
    if not isinstance(scope, dict):
        return fixes
    notes = str(scope.get("notes") or "")
    blobs = _scope_strings(scope)
    pack_sow = [
        str(h.get("text") or "")
        for h in (cleaned.get("_pack_sow_hits") or [])
        if isinstance(h, dict)
    ]
    blobs.extend(pack_sow)

    monthly = [b for b in blobs if re.search(r"\bmonthly\b", b, re.I) and "report" in b.casefold()]
    quarterly = [b for b in blobs if re.search(r"\bquarterly\b", b, re.I) and "report" in b.casefold()]
    if monthly and quarterly:
        msg = (
            "Reporting frequency conflict: source includes both monthly and quarterly "
            "progress/evaluation reporting language. Both requirements are preserved."
        )
        if msg not in notes:
            scope["notes"] = (notes + "\n" + msg).strip() if notes else msg
            fixes.append("scope.notes: monthly vs quarterly conflict")

    # Generic paired frequency scan
    freq_pairs = [("weekly", "monthly"), ("monthly", "annually")]
    for a, b in freq_pairs:
        has_a = any(re.search(rf"\b{a}\b", x, re.I) for x in blobs)
        has_b = any(re.search(rf"\b{b}\b", x, re.I) for x in blobs)
        if has_a and has_b and "report" in " ".join(blobs).casefold():
            tag = f"{a}_vs_{b}_reporting"
            if tag not in notes:
                scope["notes"] = (
                    (scope.get("notes") or "")
                    + f"\nPossible {a}/{b} reporting tension — verify sections."
                ).strip()
                fixes.append(f"scope.notes: {tag}")
    return fixes


def validate_qualifier_preservation(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    """Warn when output drops at least/up to from source evidence."""
    warnings: list[str] = []
    sources: list[str] = []
    for c in pack.get("obligationCandidates") or []:
        sources.append(str(c.get("text") or ""))
    for h in (pack.get("langextract") or {}).get("complianceHits") or []:
        sources.append(str(h.get("sourceText") or h.get("text") or ""))
    for h in (pack.get("langextract") or {}).get("scopeHits") or []:
        sources.append(str(h.get("sourceText") or h.get("text") or ""))

    def check_outputs(strings: list[str]) -> None:
        for out_s in strings:
            out_l = out_s.casefold()
            for src in sources:
                src_l = src.casefold()
                for _rx, label in _QUALIFIER_PATTERNS:
                    if label in src_l and label not in out_l and len(out_s) > 20:
                        # only flag if same stem words overlap
                        stem = re.sub(r"[^a-z0-9]+", " ", src_l)[:60]
                        if stem[:30] in re.sub(r"[^a-z0-9]+", " ", out_l):
                            warnings.append(f"qualifier:{label} dropped in '{out_s[:60]}'")
                            break

    scope = cleaned.get("scope") if isinstance(cleaned.get("scope"), dict) else {}
    check_outputs([str(x) for x in (scope.get("mandatory") or []) + (scope.get("optional") or [])])
    compliance = cleaned.get("compliance") if isinstance(cleaned.get("compliance"), dict) else {}
    check_outputs([str(i.get("requirement") or "") for i in (compliance.get("items") or []) if isinstance(i, dict)])
    return warnings


def dedupe_compliance_items(cleaned: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    compliance = cleaned.get("compliance")
    if not isinstance(compliance, dict):
        return fixes
    items = compliance.get("items")
    if not isinstance(items, list):
        return fixes
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = re.sub(r"\s+", " ", str(item.get("requirement") or "").casefold()).strip()
        if key in seen:
            fixes.append(f"compliance: deduped '{key[:40]}'")
            continue
        seen.add(key)
        out.append(item)
    compliance["items"] = out
    return fixes


def compute_complexity_rubric(cleaned: dict[str, Any], pack: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Deterministic 0–3 low, 4–6 moderate, 7+ high."""
    score = 0
    reasons: list[str] = []
    scope = cleaned.get("scope") if isinstance(cleaned.get("scope"), dict) else {}
    u = cleaned.get("understanding") if isinstance(cleaned.get("understanding"), dict) else {}
    tl = u.get("timelineIntel") if isinstance(u.get("timelineIntel"), dict) else {}

    mand_count = len(scope.get("mandatory") or [])
    if mand_count >= 12:
        score += 2
        reasons.append(f"{mand_count} mandatory scope lines")
    elif mand_count >= 6:
        score += 1
        reasons.append(f"{mand_count} scope lines")

    term = str(tl.get("initialTerm") or tl.get("contractTerm") or "")
    if "option" in term.casefold() or re.search(r"\b\d+\s*one[- ]year", term, re.I):
        score += 1
        reasons.append("option periods")
    if re.search(r"\b\d+\s*years?\b", term, re.I):
        score += 1
        reasons.append("multi-year term")

    services = u.get("services") or []
    if isinstance(services, list) and len(services) >= 4:
        score += 1
        reasons.append("multiple service streams")

    blob = " ".join(_scope_strings(scope) + [str(x) for x in (u.get("painPoints") or [])]).casefold()
    for tag, pts in (
        ("multilingual", 1),
        ("multicultural", 1),
        ("translation", 1),
        ("evaluation", 1),
        ("research", 1),
        ("statistical", 1),
        ("subcontract", 1),
        ("task order", 1),
        ("watershed", 1),
        ("regulatory", 1),
        ("ada", 1),
        ("web", 1),
        ("digital", 1),
    ):
        if tag in blob:
            score += pts
            reasons.append(tag)

    if int(pack.get("pageCount") or 0) > 100:
        score += 1
        reasons.append("long RFP")

    if score <= 3:
        level = "Low"
    elif score <= 6:
        level = "Moderate"
    else:
        level = "High"
    basis = "; ".join(reasons[:8]) if reasons else "default rubric"
    return level, basis, reasons


def apply_complexity(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    u = cleaned.get("understanding")
    if not isinstance(u, dict):
        return fixes
    level, basis, _ = compute_complexity_rubric(cleaned, pack)
    u["complexity"] = level
    mf = u.get("memoryFacts")
    if not isinstance(mf, dict):
        mf = {}
        u["memoryFacts"] = mf
    mf["complexityBasis"] = basis
    fixes.append(f"complexity:{level}")
    return fixes


def backfill_contract_structure(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    u = cleaned.get("understanding")
    if not isinstance(u, dict):
        return fixes
    if str(u.get("contractStructure") or "").strip():
        return fixes
    blob = json.dumps(u.get("timelineIntel") or {}) + str(pack.get("sowBoundedText") or "")[:2000]
    if re.search(r"option\s+(?:year|period)", blob, re.I):
        u["contractStructure"] = "Multi-year agreement with one-year option periods; task-order delivery"
        fixes.append("contractStructure:options")
    elif "task order" in blob.casefold():
        u["contractStructure"] = "Task-order based services agreement"
        fixes.append("contractStructure:task_order")
    else:
        u["contractStructure"] = "Professional services agreement"
        fixes.append("contractStructure:default")
    return fixes


def ensure_memory_facts(cleaned: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    u = cleaned.get("understanding")
    if not isinstance(u, dict):
        return fixes
    mf = u.get("memoryFacts")
    if not isinstance(mf, dict):
        mf = {}
        u["memoryFacts"] = mf
        fixes.append("memoryFacts: created")
    if not str(mf.get("clientName") or "").strip():
        mf["clientName"] = str(u.get("client") or "")
        fixes.append("memoryFacts.clientName filled")
    if not str(mf.get("organizationType") or "").strip():
        mf["organizationType"] = str(u.get("orgType") or "")
        fixes.append("memoryFacts.organizationType filled")
    return fixes


def update_confidence_from_coverage(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    sow_found = bool((pack.get("sections") or {}).get("scope_of_work", {}).get("found"))
    lx = pack.get("langextract") or {}
    grounded = int((lx.get("stats") or {}).get("grounded") or 0)

    scope = cleaned.get("scope") if isinstance(cleaned.get("scope"), dict) else {}
    mand = len(scope.get("mandatory") or [])
    scope_conf = 0.55
    if sow_found and mand >= 8:
        scope_conf = 0.88
    elif sow_found and mand >= 4:
        scope_conf = 0.75
    elif sow_found:
        scope_conf = 0.65
    elif mand >= 3:
        scope_conf = 0.6
    scope["confidence"] = clamp_confidence(scope_conf)
    fixes.append(f"scope.confidence:{scope_conf:.2f}")

    compliance = cleaned.get("compliance") if isinstance(cleaned.get("compliance"), dict) else {}
    n_comp = len(compliance.get("items") or [])
    c_conf = 0.7
    if n_comp >= 15 and grounded >= 50:
        c_conf = 0.92
    elif n_comp >= 8:
        c_conf = 0.82
    compliance["confidence"] = clamp_confidence(c_conf)

    u = cleaned.get("understanding") if isinstance(cleaned.get("understanding"), dict) else {}
    u_conf = 0.75 if str(u.get("client") or "") else 0.5
    if u.get("painPoints"):
        u_conf = min(0.95, u_conf + 0.05)
    u["confidence"] = clamp_confidence(u_conf)
    return fixes


def validator_repair_triggers(
    cleaned: dict[str, Any],
    pack: dict[str, Any],
    validation_warnings: list[str],
) -> list[str]:
    triggers: list[str] = [w for w in validation_warnings if w.startswith("hard:") or "retention" in w or "anchor" in w]
    scope = cleaned.get("scope") if isinstance(cleaned.get("scope"), dict) else {}
    if (pack.get("sections") or {}).get("scope_of_work", {}).get("found") and len(scope.get("mandatory") or []) < 5:
        triggers.append("thin_scope_vs_sow_section")
    if not (cleaned.get("provenance") or []):
        triggers.append("missing_provenance")
    u = cleaned.get("understanding") if isinstance(cleaned.get("understanding"), dict) else {}
    if not u.get("painPoints") and (pack.get("sections") or {}).get("purpose_background", {}).get("found"):
        triggers.append("missing_pain_points")
    if any(w.startswith("qualifier:") for w in validation_warnings):
        triggers.append("qualifier_preservation")
    comp = cleaned.get("compliance") if isinstance(cleaned.get("compliance"), dict) else {}
    for item in comp.get("items") or []:
        if not isinstance(item, dict):
            continue
        req = str(item.get("requirement") or "")
        verbs = sum(1 for v in ("provide", "submit", "include", "identify", "describe") if v in req.casefold())
        if len(req) > 180 and verbs >= 3:
            triggers.append("aggregated_compliance_items")
            break
    return triggers


def backfill_timeline_intel(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    u = cleaned.get("understanding")
    if not isinstance(u, dict):
        return fixes
    tl = u.get("timelineIntel")
    if not isinstance(tl, dict):
        tl = {}
        u["timelineIntel"] = tl

    blob_parts: list[str] = []
    for sec in (pack.get("boundedSections") or {}).values():
        if isinstance(sec, dict) and sec.get("text"):
            blob_parts.append(str(sec["text"]))
    for hit in pack.get("dateMoneyHits") or []:
        blob_parts.append(str(hit.get("text") or ""))
    blob = "\n".join(blob_parts)

    q_pat = re.compile(
        r"(questions?(?:\s+regarding|\s+about)?[^.\n]{0,40}due[^.\n]{0,80})",
        re.I,
    )
    quote_pat = re.compile(
        r"((?:quotes?|proposals?|responses?)[^.\n]{0,40}due[^.\n]{0,80})",
        re.I,
    )
    if not tl.get("questionsDue"):
        m = q_pat.search(blob)
        if m:
            tl["questionsDue"] = m.group(1).strip()[:200]
            fixes.append("timelineIntel.questionsDue backfill")
    if not tl.get("quotesDue"):
        m = quote_pat.search(blob)
        if m:
            tl["quotesDue"] = m.group(1).strip()[:200]
            fixes.append("timelineIntel.quotesDue backfill")

    if not tl.get("initialTermStart") and not tl.get("projectStart"):
        m = re.search(
            r"(July\s+1,\s+2027|contract term[^.\n]{0,60}July\s+1,\s+2027)",
            blob,
            re.I,
        )
        if m:
            tl["initialTermStart"] = "July 1, 2027"
            fixes.append("timelineIntel.initialTermStart backfill")
    return fixes


def normalize_budget_intel(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    fixes: list[str] = []
    u = cleaned.get("understanding")
    if not isinstance(u, dict):
        return fixes
    bi = u.get("budgetIntel")
    if not isinstance(bi, dict):
        bi = {}
        u["budgetIntel"] = bi
    pricing_text = str((pack.get("sections") or {}).get("pricing", {}).get("text") or "")
    draft = str((pack.get("boundedSections") or {}).get("draft_agreement", {}).get("text") or "")
    low = pricing_text.casefold()
    if pricing_text:
        bi["disclosedBudget"] = None
        bi["maximumAgreementAmount"] = None
        if "hourly" in low or "job title" in low or "unit rate" in low:
            bi["basePricingModel"] = "Hourly unit rates with annual not-to-exceed totals"
            bi["pricingModelHint"] = bi["basePricingModel"]
            fixes.append("budgetIntel:hourly NTE")
        if "firm fixed price" in low or "time and materials" in low or "t&m" in low:
            models = list(bi.get("taskOrderPricingModels") or [])
            for label in ("Firm Fixed Price", "Time and Materials Not-to-Exceed"):
                if label not in models:
                    models.append(label)
            bi["taskOrderPricingModels"] = models
            fixes.append("budgetIntel:task order models")
        if re.search(r"\$\s*#{3,}|_{3,}|\[insert", pricing_text):
            bi["disclosedBudget"] = None
            fixes.append("budgetIntel:blank schedule")
    if "firm task-order" in str(bi.get("pricingModelHint") or "").casefold() and pricing_text:
        bi["pricingModelHint"] = bi.get("basePricingModel") or bi["pricingModelHint"]
        fixes.append("budgetIntel:demote draft-agreement wording")
    if draft and not pricing_text:
        bi.setdefault("notes", "Pricing from RFQ schedule not located; ignore draft agreement placeholders.")
    return fixes


def normalize_evaluation_emphasis(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    """Canonical evaluation factors from bounded Evaluation section — not submission fragments."""
    fixes: list[str] = []
    ev = cleaned.get("evaluation")
    if not isinstance(ev, dict):
        return fixes
    text = str(
        (pack.get("boundedSections") or {}).get("evaluation", {}).get("text")
        or (pack.get("sections") or {}).get("evaluation", {}).get("text")
        or ""
    )
    # Drop polluted emphasis that is really Cost/Price submission instructions
    cur = [str(x) for x in (ev.get("emphasis") or [])]
    if any("4.1.4" in x or "cost/price information" in x.casefold() for x in cur):
        ev["emphasis"] = []
        fixes.append("evaluation.emphasis:cleared_submission_pollution")

    if len(text) < 40:
        return fixes

    canon: list[tuple[str, str]] = [
        ("technical merit", "Technical merit"),
        ("experience", "Experience"),
        ("capacity", "Capacity"),
        ("history of offeror compliance", "History of Offeror compliance"),
        ("offeror compliance", "History of Offeror compliance"),
        ("availability", "Availability"),
        ("price", "Price"),
        ("sustainability", "Sustainability and social, human health, environmental, and economic impact"),
        ("cultural competency", "Cultural competency"),
        ("overall total cost", "Overall total cost to the County"),
        ("total cost to the county", "Overall total cost to the County"),
    ]
    low = re.sub(r"\s+", " ", text.casefold())
    # Only use text after "evaluation" / section 5 if present, to avoid 4.x submission language
    cut = low.find("5. quote evaluation")
    if cut < 0:
        cut = low.find("quote evaluation")
    if cut < 0:
        cut = low.find("5.1")
    focus = low[cut:] if cut >= 0 else low

    factors: list[str] = []
    seen: set[str] = set()
    for needle, label in canon:
        if needle in focus and label not in seen:
            # Don't pick "price" from Cost/Price exhibit instructions if we're still in 4.x
            if needle == "price" and "cost/price" in focus[:200] and "5.1" not in focus[:400]:
                if "evaluations may consider" not in focus and "evaluation" not in focus[:80]:
                    continue
            seen.add(label)
            factors.append(label)
    if factors:
        ev["emphasis"] = factors
        ev["confidence"] = max(float(ev.get("confidence") or 0), 0.85)
        fixes.append(f"evaluation.emphasis:canonical({len(factors)})")
    return fixes


def backfill_understanding_fields(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    """Fill services / businessGoals / orgType from existing evidence when empty."""
    fixes: list[str] = []
    u = cleaned.get("understanding")
    if not isinstance(u, dict):
        return fixes
    blob = " ".join(
        [
            str(u.get("client") or ""),
            str(u.get("projectType") or ""),
            " ".join(str(x) for x in (u.get("painPoints") or [])),
            " ".join(str(x) for x in (u.get("desiredOutcomes") or [])),
            str(pack.get("sowExcerpt") or "")[:4000],
        ]
    ).casefold()

    if not str(u.get("orgType") or "").strip():
        if "county" in blob:
            u["orgType"] = "County Government"
            fixes.append("orgType:county")
        elif "city" in blob:
            u["orgType"] = "Municipal Government"
            fixes.append("orgType:city")

    services = u.get("services")
    if not isinstance(services, list) or not services:
        inferred: list[str] = []
        for label, needles in (
            ("Community-Based Social Marketing (CBSM)", ("cbsm", "community-based social marketing", "community based social marketing")),
            ("Public education and outreach", ("public education", "outreach")),
            ("Multilingual / multicultural communications", ("multilingual", "multicultural")),
            ("Behavior-change campaign development", ("behavior change", "behaviour change")),
            ("Survey / market research", ("survey", "market research")),
            ("Website and social media content", ("website", "social media")),
        ):
            if any(n in blob for n in needles):
                inferred.append(label)
        if inferred:
            u["services"] = inferred
            fixes.append(f"services:inferred({len(inferred)})")

    goals = u.get("businessGoals")
    if not isinstance(goals, list) or not goals:
        inferred_g: list[str] = []
        for g in u.get("desiredOutcomes") or []:
            if g:
                inferred_g.append(str(g))
        if not inferred_g:
            for label, needles in (
                ("Improve stormwater-related public awareness and BMP adoption", ("awareness", "bmp")),
                ("Support MS4 Permit public education / WQIP strategies", ("ms4", "wqip", "permit")),
                ("Deliver measurable behavior-change outcomes", ("behavior", "measurable")),
            ):
                if any(n in blob for n in needles):
                    inferred_g.append(label)
        if inferred_g:
            u["businessGoals"] = inferred_g[:6]
            fixes.append(f"businessGoals:inferred({len(inferred_g)})")
    return fixes


def scrub_project_start_inference(cleaned: dict[str, Any]) -> list[str]:
    """Never equate agreement term start with projectStart/goLive without evidence."""
    fixes: list[str] = []
    u = cleaned.get("understanding")
    if not isinstance(u, dict):
        return fixes
    tl = u.get("timelineIntel")
    if not isinstance(tl, dict):
        return fixes
    term = str(tl.get("initialTermStart") or tl.get("contractStart") or "").strip()
    proj = str(tl.get("projectStart") or "").strip()
    if term and proj and _norm_dateish(term) == _norm_dateish(proj):
        notes = str(tl.get("notes") or "").casefold()
        if "go-live" not in notes and "go live" not in notes and "project start" not in notes:
            tl["projectStart"] = None
            fixes.append("timelineIntel.projectStart:cleared_contract_start_inference")
    # Prefer exact option language
    opt = str(tl.get("optionPeriods") or "")
    if "as stated" in opt.casefold() or not opt:
        blob = str(tl.get("notes") or "") + " " + str(u.get("contractStructure") or "")
        m = re.search(r"four\s*\(?\s*4\s*\)?\s*one[- ]year\s+option", blob, re.I)
        if not m:
            m = re.search(r"four\s*\(?\s*4\s*\)?[^\n.]{0,40}option\s+period", blob, re.I)
        if m:
            tl["optionPeriods"] = "Four (4) one-year option periods"
            fixes.append("timelineIntel.optionPeriods:exact")
    return fixes


def _norm_dateish(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").casefold()).strip()


def accept_repair_if_improved(
    before: dict[str, Any],
    after: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Reject section-level repair that collapses populated content."""
    notes: list[str] = []
    out = dict(after)

    def _count_sc(d: dict[str, Any]) -> int:
        sc = d.get("successCriteria") if isinstance(d.get("successCriteria"), dict) else {}
        return len(sc.get("items") or [])

    def _count_comp(d: dict[str, Any]) -> int:
        c = d.get("compliance") if isinstance(d.get("compliance"), dict) else {}
        return len(c.get("items") or [])

    def _count_scope(d: dict[str, Any]) -> int:
        s = d.get("scope") if isinstance(d.get("scope"), dict) else {}
        return len(s.get("mandatory") or []) + len(s.get("optional") or [])

    def _emph_ok(d: dict[str, Any]) -> bool:
        ev = d.get("evaluation") if isinstance(d.get("evaluation"), dict) else {}
        emph = ev.get("emphasis") or []
        if not emph:
            return False
        bad = sum(1 for x in emph if "4.1.4" in str(x) or str(x).strip().endswith(":"))
        return bad == 0 and len(emph) >= 3

    if _count_sc(before) >= 3 and _count_sc(after) < max(2, _count_sc(before) // 2):
        out["successCriteria"] = before.get("successCriteria")
        notes.append("repair_rejected:successCriteria_degraded")
    if _count_comp(before) >= 20 and _count_comp(after) < max(10, int(_count_comp(before) * 0.5)):
        out["compliance"] = before.get("compliance")
        notes.append("repair_rejected:compliance_degraded")
    if _count_scope(before) >= 8 and _count_scope(after) < max(4, _count_scope(before) // 2):
        out["scope"] = before.get("scope")
        notes.append("repair_rejected:scope_degraded")
    if _emph_ok(before) and not _emph_ok(after):
        out["evaluation"] = before.get("evaluation")
        notes.append("repair_rejected:evaluation_degraded")
    return out, notes


def hard_schema_errors(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    u = cleaned.get("understanding") or {}
    mf = u.get("memoryFacts") or {}
    if not str(mf.get("clientName") or "").strip():
        errors.append("hard:memoryFacts.clientName")
    if not str(mf.get("organizationType") or "").strip():
        errors.append("hard:memoryFacts.organizationType")
    if not str(u.get("contractStructure") or "").strip():
        errors.append("hard:contractStructure")
    tl = u.get("timelineIntel") or {}
    if not tl.get("questionsDue") and not tl.get("quotesDue"):
        errors.append("hard:timelineIntel.deadlines")
    scope = cleaned.get("scope") or {}
    if scope.get("notes") is None:
        errors.append("hard:scope.notes")
    from compliance_retention import collect_bidder_obligation_candidates, compute_retention_stats

    items = (cleaned.get("compliance") or {}).get("items") or []
    cands = collect_bidder_obligation_candidates(pack)
    stats = compute_retention_stats(cands, items, [])
    if stats.get("missingAnchors"):
        errors.append("hard:compliance.anchors:" + ",".join(stats["missingAnchors"][:6]))
    return errors


def apply_deterministic_pipeline(
    cleaned: dict[str, Any],
    pack: dict[str, Any],
    *,
    provenance: list[dict[str, Any]] | None = None,
    skip_provenance: bool = False,
    doc: Any = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Run all post-LLM normalizers. Returns (cleaned, fixes, warnings)."""
    from compliance_atomic_split import atomize_compliance_items
    from compliance_retention import dispose_candidates, retention_repair_triggers
    from scope_retention import (
        check_sow_subsection_coverage,
        enrich_scope_reporting_notes,
        materialize_scope_from_candidates,
    )

    fixes: list[str] = []
    warnings: list[str] = []

    fixes.extend(ensure_memory_facts(cleaned))
    fixes.extend(backfill_contract_structure(cleaned, pack))
    fixes.extend(backfill_understanding_fields(cleaned, pack))
    if doc is not None:
        from timeline_extract import merge_timeline_into_understanding

        fixes.extend(merge_timeline_into_understanding(cleaned, doc, pack))
    fixes.extend(backfill_timeline_intel(cleaned, pack))
    fixes.extend(scrub_project_start_inference(cleaned))
    fixes.extend(normalize_budget_intel(cleaned, pack))
    fixes.extend(normalize_success_criteria(cleaned))
    fixes.extend(scrub_desired_outcomes(cleaned))
    fixes.extend(materialize_scope_from_candidates(cleaned, pack))
    fixes.extend(enrich_scope_reporting_notes(cleaned, pack))
    warnings.extend(check_sow_subsection_coverage(cleaned, pack))
    mat_fixes, retention_stats = dispose_candidates(cleaned, pack)
    fixes.extend(mat_fixes)
    comp = cleaned.get("compliance") if isinstance(cleaned.get("compliance"), dict) else {}
    atomized, atom_fixes = atomize_compliance_items(list(comp.get("items") or []), pack)
    comp["items"] = atomized
    fixes.extend(atom_fixes)
    warnings.extend(retention_repair_triggers(retention_stats))
    fixes.extend(normalize_compliance_mandatory_flags(cleaned))
    fixes.extend(dedupe_compliance_items(cleaned))
    fixes.extend(normalize_evaluation_scoring(cleaned, pack))
    fixes.extend(normalize_evaluation_emphasis(cleaned, pack))
    fixes.extend(apply_complexity(cleaned, pack))
    fixes.extend(detect_scope_contradictions(cleaned))
    warnings.extend(validate_qualifier_preservation(cleaned, pack))
    warnings.extend(hard_schema_errors(cleaned, pack))
    fixes.extend(update_confidence_from_coverage(cleaned, pack))

    if not skip_provenance:
        if provenance is not None:
            cleaned["provenance"] = provenance
        elif "provenance" not in cleaned:
            cleaned["provenance"] = []

    cleaned.pop("_pack_sow_hits", None)
    cleaned.pop("_retention_stats", None)
    if retention_stats:
        cleaned["_retention_stats"] = retention_stats
    return cleaned, fixes, warnings
