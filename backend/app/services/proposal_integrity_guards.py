"""Hard integrity guards — references, pricing tier, case-study fidelity, bio typos.

These run after generation so RFP-prohibited patterns cannot ship even when the LLM
slips (e.g. "available upon request", Average tier at 35% cost weight, genericized CS).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalResearchCache

logger = logging.getLogger(__name__)

_UPON_REQUEST_RE = re.compile(
    r"(?is)"
    r"(?:"
    # Only the withholding pattern on a named reference row — NOT
    # "Additional references … available on request" (often allowed / intentional).
    r"(?:reference\s+)?contact\s+(?:details?|information|info)\s+"
    r"(?:are\s+|is\s+)?(?:available\s+)?(?:upon|on)\s+request"
    r"|"
    r"contacts?\s+(?:upon|on)\s+request"
    r")",
)

_PRECLEARED_CLAIM_RE = re.compile(
    r"(?is)"
    r"[^.!?\n]*\b(?:"
    r"pre-?cleared|"
    r"have\s+agreed\s+to\s+respond|"
    r"agreed\s+to\s+(?:direct\s+)?(?:reference\s+)?(?:checks?|contact)|"
    r"each\s+has\s+agreed\s+to\s+respond"
    r")\b[^.!?\n]*[.!?]?",
)

_VERIFY_CONTACT = (
    "[VERIFY: reference contact — name, title, organization, phone, email from KB]"
)

_WE_EXPERTLY_MANAGES_RE = re.compile(
    r"\bWe\s+expertly\s+manages\b",
    re.I,
)

_COST_LABEL_RE = re.compile(
    r"\b(?:"
    r"cost|price|pricing|fee|fees|compensation|"
    r"cost\s*/\s*price|price\s+reasonableness|grand\s+total"
    r")\b",
    re.I,
)

_AVG_TIER_CLAIM_RE = re.compile(
    r"(?i)\b(?:industry\s+)?Average\s+tier\b|"
    r"Pricing\s+is\s+built\s+from\s+the\s+industry\s+Average",
)


def scrub_reference_withholding(content: str) -> tuple[str, list[str]]:
    """Replace 'upon request' contact deferrals + cut unverified pre-clear claims."""
    text = content or ""
    logs: list[str] = []
    if not text.strip():
        return text, logs

    if _UPON_REQUEST_RE.search(text):
        text = _UPON_REQUEST_RE.sub(_VERIFY_CONTACT, text)
        logs.append("Replaced contact 'upon request' with [VERIFY] contact fields")

    if _PRECLEARED_CLAIM_RE.search(text):
        text = _PRECLEARED_CLAIM_RE.sub("", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        logs.append("Removed unverified pre-cleared / agreed-to-respond reference claim")

    return text, logs


def fix_known_bio_typos(content: str) -> tuple[str, list[str]]:
    """Deterministic template typos that recur across proposals."""
    text = content or ""
    logs: list[str] = []
    if _WE_EXPERTLY_MANAGES_RE.search(text):
        text = _WE_EXPERTLY_MANAGES_RE.sub("The agency expertly manages", text)
        logs.append("Fixed bio typo: We expertly manages → The agency expertly manages")
    return text, logs


def apply_manuscript_integrity_guards(draft: ProposalDraft) -> tuple[ProposalDraft, list[str]]:
    """Run deterministic integrity scrubs across all sections."""
    logs: list[str] = []
    sections = []
    changed = False
    for section in draft.sections:
        content = section.content or ""
        title_cf = (section.title or "").casefold()
        sid = section.id or ""
        new = content
        section_logs: list[str] = []

        if "reference" in title_cf or "reference" in sid.casefold():
            new, ref_logs = scrub_reference_withholding(new)
            section_logs.extend(ref_logs)
        else:
            # Still strip upon-request deferrals anywhere (RFP often forbids withholding).
            scrubbed, ref_logs = scrub_reference_withholding(new)
            if ref_logs:
                new, section_logs = scrubbed, list(ref_logs)

        if sid.startswith("section-2-") or "bio" in title_cf:
            new, bio_logs = fix_known_bio_typos(new)
            section_logs.extend(bio_logs)

        if new != content:
            changed = True
            sections.append(section.model_copy(update={"content": new}))
            for line in section_logs:
                logs.append(f"{sid}: {line}")
        else:
            sections.append(section)

    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def infer_cost_weight_pct(
    rfp_text: str,
    research: ProposalResearchCache | None = None,
) -> float | None:
    """Infer cost/price evaluation weight as a percent of total points (0–100)."""
    # Prefer Stage 2 / intelligence costWeight when present.
    if research is not None:
        plan = getattr(research, "proposal_execution_plan", None)
        if plan is not None:
            raw = getattr(plan, "cost_weight", None)
            if raw is None and isinstance(plan, dict):
                raw = plan.get("costWeight") or plan.get("cost_weight")
            try:
                if raw is not None:
                    val = float(raw)
                    if 0 < val <= 1:
                        return val * 100.0
                    if 0 < val <= 100:
                        return val
            except (TypeError, ValueError):
                pass

        # Sum evaluationWeight on price/cost-mapped sections vs total weights.
        sections = getattr(research, "rfp_sections", None) or []
        cost_w = 0
        total_w = 0
        for sec in sections:
            w = getattr(sec, "evaluation_weight", None)
            if w is None:
                continue
            try:
                wi = int(w)
            except (TypeError, ValueError):
                continue
            if wi <= 0:
                continue
            total_w += wi
            title = (getattr(sec, "title", None) or "").casefold()
            if _COST_LABEL_RE.search(title):
                cost_w += wi
        if total_w >= 40 and cost_w > 0:
            return round(100.0 * cost_w / total_w, 1)

    from app.services.evidence_trust.rfp_hard_facts import extract_rfp_hard_facts

    facts = extract_rfp_hard_facts(rfp_text or "")
    lines = facts.get("evaluation_lines") or []
    total = int(facts.get("evaluation_total") or 0)
    if not lines or total <= 0:
        # Fallback: Price X of Y points phrasing.
        m = re.search(
            r"(?is)\b(?:price|cost|pricing)\b.{0,60}?"
            r"(\d{1,3}(?:,\d{3})*|\d{2,4})\s*(?:of|/)\s*"
            r"(\d{1,3}(?:,\d{3})*|\d{2,4})\s*points?",
            rfp_text or "",
        )
        if m:
            num = int(m.group(1).replace(",", ""))
            den = int(m.group(2).replace(",", ""))
            if den > 0 and num <= den:
                return round(100.0 * num / den, 1)
        return None

    cost_pts = 0
    for line in lines:
        label, _, pts_s = line.partition(":")
        if not _COST_LABEL_RE.search(label):
            continue
        try:
            cost_pts += int(pts_s.strip().split()[0])
        except (ValueError, IndexError):
            continue
    if cost_pts <= 0:
        return None
    return round(100.0 * cost_pts / total, 1)


def enforce_pricing_tier_for_cost_weight(
    budget: ProposalBudget,
    *,
    cost_weight_pct: float | None,
) -> tuple[ProposalBudget, list[str]]:
    """Force Low tier when RFP cost weight ≥25% (Pricing Guide Decision Guide)."""
    logs: list[str] = []
    if cost_weight_pct is None or cost_weight_pct < 25:
        return budget, logs

    tier = (budget.pricing_tier or "").strip()
    if tier.casefold() == "low":
        rationale = (budget.rfp_budget_notes or "").strip()
        note = (
            f"Cost weight ~{cost_weight_pct:.0f}% (≥25%) → Low tier per Pricing Guide "
            "Decision Guide."
        )
        if note.casefold() not in rationale.casefold():
            updated_notes = f"{note}\n{rationale}".strip() if rationale else note
            budget = budget.model_copy(update={"rfp_budget_notes": updated_notes[:4000]})
            logs.append(note)
        return budget, logs

    flags = list(budget.pricing_flags or [])
    flag = (
        f"[PRICING FLAG: Cost weight ~{cost_weight_pct:.0f}% (≥25%) — "
        f"Pricing Guide requires Low tier; was '{tier or 'unset'}'. "
        "Auto-set to Low. Confirm with Sonja before submission.]"
    )
    if flag not in flags:
        flags.append(flag)

    notes = (budget.rfp_budget_notes or "").strip()
    override_note = (
        f"AUTO TIER OVERRIDE: cost weight ~{cost_weight_pct:.0f}% ≥25% → Low tier "
        f"(was {tier or 'unset'}) per 00_Guide_Pricing Decision Guide."
    )
    notes = f"{override_note}\n{notes}".strip() if notes else override_note

    # Fix narrative that still claims Average tier.
    fee = budget.fee_structure or ""
    if _AVG_TIER_CLAIM_RE.search(fee):
        fee = _AVG_TIER_CLAIM_RE.sub("Low tier", fee)

    budget = budget.model_copy(
        update={
            "pricing_tier": "Low",
            "pricing_flags": flags,
            "rfp_budget_notes": notes[:4000],
            "fee_structure": fee,
        }
    )
    logs.append(
        f"Forced pricing tier Low (was {tier or 'unset'}) — cost weight "
        f"{cost_weight_pct:.0f}% ≥25%"
    )
    return budget, logs


def case_study_fidelity_ok(source_text: str, written: str) -> tuple[bool, str]:
    """Heuristic: distinctive source proper nouns should survive in the write-up."""
    src = source_text or ""
    out = written or ""
    if len(src) < 80 or len(out) < 40:
        return True, ""

    distinctive: list[str] = []
    # Festival / campaign style names with lowercase connectors (Rock the Locks Festival).
    for m in re.finditer(
        r"\b([A-Z][A-Za-z0-9'’&-]+(?:\s+(?:the|of|and|for|a|an)\s+[A-Z][A-Za-z0-9'’&-]+)+"
        r"(?:\s+[A-Z][A-Za-z0-9'’&-]+)*)\b",
        src,
    ):
        distinctive.append(m.group(1).strip())
    # Title-ish tokens: multi-word Capitalized phrases.
    for c in re.findall(
        r"\b([A-Z][A-Za-z0-9'’&-]{2,}(?:\s+[A-Z][A-Za-z0-9'’&-]{2,}){1,4})\b",
        src,
    ):
        distinctive.append(c)

    skip = {
        "city of",
        "state of",
        "united states",
        "case study",
        "digital campaign",
        "our approach",
        "why relevant",
    }
    cleaned: list[str] = []
    for c in distinctive:
        if c.casefold() in skip:
            continue
        if re.search(
            r"(?i)festival|locks|rodeo|fair|campaign|initiative|network|county|city",
            c,
        ) or len(c.split()) >= 3:
            cleaned.append(c)
    distinctive = list(dict.fromkeys(cleaned))[:8]
    if not distinctive:
        return True, ""

    missing = []
    for d in distinctive:
        tokens = [
            t
            for t in re.findall(r"[A-Za-z]{3,}", d)
            if t.casefold()
            not in {"the", "and", "for", "digital", "campaign", "case", "study"}
        ]
        if not tokens:
            continue
        hit = sum(1 for t in tokens if t.casefold() in out.casefold())
        if hit < max(1, (len(tokens) + 1) // 2):
            missing.append(d)

    # Also catch when core tokens like "Locks" vanish from a festival source.
    core_tokens = []
    for d in distinctive:
        for tok in re.findall(r"[A-Za-z]{4,}", d):
            if tok.casefold() in {
                "festival",
                "campaign",
                "digital",
                "county",
                "city",
                "study",
            }:
                continue
            core_tokens.append(tok)
    core_missing = [
        t for t in dict.fromkeys(core_tokens) if t.casefold() not in out.casefold()
    ]

    if len(missing) >= max(1, (len(distinctive) + 1) // 2) or (
        len(core_missing) >= 2 and len(missing) >= 1
    ):
        return False, (
            "Case study write-up dropped source project names "
            f"({', '.join((missing or core_missing)[:3])}) — likely genericized away "
            "from the verified file."
        )
    return True, ""
