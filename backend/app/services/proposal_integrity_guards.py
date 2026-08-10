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

        if (
            sid.startswith("section-3-work")
            or "our work" in title_cf
            or "case study" in title_cf
            or re.search(r"\b3\.\d+\b", title_cf)
        ):
            new, cs_logs = scrub_case_study_overbuild(new)
            section_logs.extend(cs_logs)
            is_dump, dump_reason = case_study_looks_like_source_dump(new)
            if is_dump:
                title_hint = (section.title or sid or "case study").strip()
                new = (
                    f"### {title_hint}\n\n"
                    f"**Challenge**\n\n"
                    f"[VERIFY: rewrite Challenge from source case study — "
                    f"rejected source dump ({dump_reason})]\n\n"
                    f"**Solution / Our Approach**\n\n"
                    f"[VERIFY: rewrite Solution from source case study — "
                    f"rejected source dump ({dump_reason})]\n\n"
                    f"Client Voice: [VERIFY: no client quote found in source material]"
                )
                section_logs.append(
                    f"Replaced case-study source dump with VERIFY stub ({dump_reason})"
                )

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


_FORBIDDEN_CASE_STUDY_HEADINGS = frozenset(
    {
        "strategy",
        "goal",
        "goals",
        "kpi",
        "kpis",
        "creative deliverables",
        "deliverables",
        "why relevant",
        "results",
        "measurable outcomes",
        "key tactics",
        "company overview",
        "client overview",
        "objectives",
        "objective",
    }
)

_ALLOWED_CASE_STUDY_HEADINGS = frozenset(
    {
        "challenge",
        "solution",
        "solution / our approach",
        "our approach",
        "client voice",
    }
)


def _case_study_heading_key(line: str) -> str:
    key = re.sub(r"^#+\s*", "", (line or "").strip()).strip().rstrip(":").casefold()
    # **Challenge** / *Solution* — strip markdown emphasis wrappers.
    key = re.sub(r"\*+", "", key)
    key = re.sub(r"^_+|_+$", "", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key


def _looks_like_case_study_heading(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if s.startswith("#"):
        return True
    if s.endswith(".") or s.endswith("?") or s.endswith("!"):
        return False
    # Normalize markdown bold/italic so **Challenge** still counts as a heading.
    plain = re.sub(r"[*_`]", "", s).strip().rstrip(":")
    if len(plain) > 56:
        return False
    if not re.match(r"^[A-Za-z][\w\s/&'’\-]+$", plain):
        return False
    words = plain.split()
    if not (1 <= len(words) <= 8):
        return False
    key = plain.casefold()
    if key in _ALLOWED_CASE_STUDY_HEADINGS or key in _FORBIDDEN_CASE_STUDY_HEADINGS:
        return True
    if key.startswith("why this matters") or key.startswith("why matters"):
        return True
    if key.startswith("challenge") or key.startswith("solution") or key.startswith(
        "client voice"
    ):
        return True
    # Title-case short labels (Strategy, Goals, KPIs, …)
    return plain[:1].isupper() and len(words) <= 4


def prefer_case_study_kb_text(case_study_text: str) -> tuple[str, list[str]]:
    """
    Prefer 03_CS_* document blocks over 06_WON / full-proposal OCR.

    Retrieved packs often interleave the case-study PDF with a won-proposal PDF;
    feeding only 03_CS sharply reduces Challenge/Solution regurgitation failures.
    """
    text = (case_study_text or "").strip()
    if not text:
        return text, []

    blocks = re.split(r"(?m)(?=^###\s+)", text)
    blocks = [b.strip() for b in blocks if b.strip()]
    if not blocks:
        return text, []

    cs_blocks: list[str] = []
    cs_labels: list[str] = []
    other_blocks: list[str] = []
    for block in blocks:
        first = block.splitlines()[0] if block.splitlines() else ""
        label = re.sub(r"^###\s*", "", first).strip()
        label_cf = label.casefold()
        if "03_cs" in label_cf or re.search(r"(?i)\bcase\s*study\b", label):
            cs_blocks.append(block)
            if label:
                cs_labels.append(label)
        elif re.search(r"(?i)\b06_won_|\b07_fin_", label_cf):
            continue
        else:
            other_blocks.append(block)

    if cs_blocks:
        return "\n\n".join(cs_blocks).strip(), cs_labels
    if other_blocks:
        return "\n\n".join(other_blocks).strip(), []
    return text, []


def case_study_has_required_structure(content: str) -> bool:
    """True when Challenge + Solution/Our Approach headings are present."""
    body = content or ""
    has_challenge = bool(
        re.search(r"(?im)^(?:#{1,6}\s*)?\**\s*challenge\b", body)
    )
    has_solution = bool(
        re.search(
            r"(?im)^(?:#{1,6}\s*)?\**\s*(?:solution(?:\s*/\s*our\s+approach)?|our\s+approach)\b",
            body,
        )
    )
    return has_challenge and has_solution


def case_study_looks_like_source_dump(content: str) -> tuple[bool, str]:
    """
    Detect LLM regurgitation of proposal/OCR blobs instead of a case-study rewrite.

    Used after Case Study Builder so Umatilla-style TOC/cover-letter dumps are rejected.
    Also rejects "RELEVANT CASE STUDIES" catalogs that paste 3–4 projects into one card.
    """
    body = content or ""
    if not body.strip():
        return False, ""

    signals: list[str] = []
    if re.search(r"(?i)\b06_WON_", body):
        signals.append("06_WON filename in body")
    if re.search(r"(?i)\b03_CS_[^\n]{0,120}\.pdf\b", body):
        signals.append("03_CS filename in body")
    if re.search(r"(?i)\[photo\]", body):
        signals.append("photo OCR placeholder")
    if re.search(
        r"(?i)\bSECTION\s+[1-7]\b.{0,80}(?:Firm Overview|Relevant Experience|Key Personnel)",
        body,
    ):
        signals.append("proposal TOC")
    if re.search(r"(?im)^Dear\s+.+(?:Selection Committee|Committee)\b", body):
        signals.append("cover letter salutation")
    if re.search(r"(?i)\bSubmitted by:\s*zo\s*agency\b", body):
        signals.append("proposal submitter line")
    if re.search(r"(?i)\bTable of Contents\b|\bPage\s+\d+\b.*\bPage\s+\d+\b", body):
        signals.append("TOC/page index")
    if re.search(r"(?i)\brelevant\s+case\s+studies\b", body):
        signals.append("relevant case studies catalog")

    # Multiple distinct "CITY OF X" / "COUNTY" project banners in one card =
    # All Case Studies dump pasted instead of a single engagement.
    project_banners = re.findall(
        r"(?im)^(?:#{1,6}\s*)?(?:CITY|COUNTY|TOWN|UNIVERSITY|DEPARTMENT)\s+OF\s+[A-Z][A-Z\s&'-]{2,40}\s*$",
        body,
    )
    # Also catch ALL-CAPS client lines like "CITY OF MEDFORD" / "DESCHUTES COUNTY"
    # without "OF" (common in the All Case Studies master).
    allcaps_clients = re.findall(
        r"(?m)^([A-Z][A-Z0-9 &'-]{6,60})$",
        body,
    )
    allcaps_clients = [
        c
        for c in allcaps_clients
        if re.search(r"(?i)\b(city|county|department|university|town)\b", c)
        or re.search(r"(?i)\b(medford|bend|deschutes|oregon|hampton)\b", c)
    ]
    distinct_projects = list(dict.fromkeys([*project_banners, *allcaps_clients]))
    if len(distinct_projects) >= 3:
        signals.append(f"multi-project catalog ({len(distinct_projects)} clients)")

    strong = {
        "proposal TOC",
        "cover letter salutation",
        "photo OCR placeholder",
        "relevant case studies catalog",
        "multi-project catalog (3 clients)",
        "multi-project catalog (4 clients)",
        "multi-project catalog (5 clients)",
    }
    # Any multi-project catalog with 3+ clients is a hard reject.
    if any(s.startswith("multi-project catalog") for s in signals):
        return True, ", ".join(signals[:5])
    if "relevant case studies catalog" in signals:
        return True, ", ".join(signals[:5])
    if any(s in strong for s in signals) or len(signals) >= 2:
        return True, ", ".join(signals[:5])
    return False, ""


_MAX_CHALLENGE_WORDS = 40
_MAX_SOLUTION_WORDS = 50


def _cap_prose_words(text: str, max_words: int) -> str:
    words = (text or "").split()
    if len(words) <= max_words:
        return (text or "").strip()
    clipped = " ".join(words[:max_words]).rstrip(",;:.—-")
    return f"{clipped}."


def _cap_case_study_section_lengths(content: str) -> tuple[str, list[str]]:
    """Hard-cap Challenge / Solution prose so cards stay scannable."""
    text = content or ""
    if not text.strip():
        return text, []

    logs: list[str] = []
    lines = text.splitlines()
    out: list[str] = []
    section: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal buf, section
        if section is None:
            return
        body = "\n".join(buf).strip()
        if section == "challenge":
            capped = _cap_prose_words(body, _MAX_CHALLENGE_WORDS)
            if body and capped != body:
                logs.append(f"Challenge capped to {_MAX_CHALLENGE_WORDS} words")
            out.append(capped)
        elif section == "solution":
            capped = _cap_prose_words(body, _MAX_SOLUTION_WORDS)
            if body and capped != body:
                logs.append(f"Solution capped to {_MAX_SOLUTION_WORDS} words")
            out.append(capped)
        else:
            out.append(body)
        buf = []
        section = None

    for line in lines:
        if _looks_like_case_study_heading(line):
            _flush()
            key = _case_study_heading_key(line)
            out.append(line)
            if key.startswith("challenge"):
                section = "challenge"
            elif key.startswith("solution") or key == "our approach":
                section = "solution"
            elif key.startswith("client voice"):
                section = "client_voice"
            else:
                section = None
            continue
        if section in {"challenge", "solution", "client_voice"}:
            buf.append(line)
        else:
            out.append(line)
    _flush()

    cleaned = "\n".join(out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, logs


def scrub_case_study_overbuild(content: str) -> tuple[str, list[str]]:
    """
    Enforce Challenge → Solution → Client Voice shape.

    Strips KB-template dump sections (Strategy/Goals/KPIs/Creative Deliverables)
    and invented RFP bridges ("Why this matters for …") that prompts forbid but
    models still emit when copying master case-study docs. Also hard-caps
    Challenge/Solution word counts so Section 3 cards stay short.
    """
    text = content or ""
    if not text.strip():
        return text, []

    logs: list[str] = []
    removed: list[str] = []
    out_lines: list[str] = []
    skipping = False

    for line in text.splitlines():
        if _looks_like_case_study_heading(line):
            key = _case_study_heading_key(line)
            is_forbidden = (
                key in _FORBIDDEN_CASE_STUDY_HEADINGS
                or key.startswith("why this matters")
                or key.startswith("why matters")
            )
            if is_forbidden:
                skipping = True
                if key not in removed:
                    removed.append(key)
                continue
            # Resume on any other heading (allowed template or title).
            skipping = False
            out_lines.append(line)
            continue
        if skipping:
            continue
        # Orphan bridge lines without a clean heading break.
        if re.match(r"(?i)^why\s+(?:this\s+)?matters\b", line.strip()):
            skipping = True
            if "why this matters" not in removed:
                removed.append("why this matters")
            continue
        out_lines.append(line)

    cleaned = "\n".join(out_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if removed:
        logs.append(
            "Stripped case-study overbuild sections: " + ", ".join(removed[:8])
        )
        logger.info("case_study_overbuild_scrub removed=%s", removed[:8])

    cleaned, cap_logs = _cap_case_study_section_lengths(cleaned)
    logs.extend(cap_logs)
    return cleaned, logs


def case_study_fidelity_ok(source_text: str, written: str) -> tuple[bool, str]:
    """Heuristic: write-up must keep real project identity from the source.

    A focused card drawn from a multi-project master dump (All Case Studies)
    is OK when it faithfully covers at least one source project. Fail only when
    the write-up looks genericized — almost none of the distinctive source
    names survive.
    """
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

    out_cf = out.casefold()

    def _phrase_coverage(phrase: str) -> tuple[float, list[str]]:
        tokens = [
            t
            for t in re.findall(r"[A-Za-z]{3,}", phrase)
            if t.casefold()
            not in {"the", "and", "for", "digital", "campaign", "case", "study", "city", "of"}
        ]
        if not tokens:
            return 1.0, []
        hits = [t for t in tokens if t.casefold() in out_cf]
        return len(hits) / len(tokens), tokens

    covered = 0
    missing: list[str] = []
    for d in distinctive:
        ratio, _tokens = _phrase_coverage(d)
        if ratio >= 0.5:
            covered += 1
        else:
            missing.append(d)

    # At least one real source project survived → not a generic rewrite.
    if covered >= 1:
        return True, ""

    # Also catch when core tokens like "Locks" vanish from a single-project source.
    core_tokens: list[str] = []
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
        t for t in dict.fromkeys(core_tokens) if t.casefold() not in out_cf
    ]

    if covered == 0 and (
        len(missing) >= max(1, (len(distinctive) + 1) // 2)
        or (len(core_missing) >= 2 and len(missing) >= 1)
    ):
        return False, (
            "Case study write-up dropped source project names "
            f"({', '.join((missing or core_missing)[:3])}) — likely genericized away "
            "from the verified file."
        )
    return True, ""
