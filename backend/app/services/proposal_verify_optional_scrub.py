"""RFP-aware scrub of optional [VERIFY] / [MANUAL FILL] — drop if not required; never invent."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.services import llm
from app.services.proposal_manual_flags import MANUAL_FILL_TAG_RE, VERIFY_TAG_RE
from app.services.proposal_rfp_excerpt import build_priority_rfp_excerpt
from app.services.proposal_section_health import is_dead_section

logger = logging.getLogger(__name__)

# Asks that may stay ONLY when THIS RFP actually requires them for DQ /
# scored submission — default is REMOVE (selection-critical bias).
_SELECTION_CRITICAL_ASK_RE = re.compile(
    r"(?i)"
    r"\b("
    r"fein|ein\b|tax\s*id|federal\s+employer|"
    r"insurance|coi\b|certificate\s+of\s+insurance|liability\s+limit|"
    r"e-?verify|perjury|attestation|affidavit|conflict\s+of\s+interest|"
    r"bonding|bid\s+bond|performance\s+bond|"
    r"w-?9\b|sam\.?gov|duns\b|uei\b|"
    r"reference\s+contact|references?\s+(?:with\s+)?(?:phone|email|contact)|"
    r"staffing\s+hours|percent\s*time|percent-time|%\s*time|"
    r"gross-?receipts|not[- ]to[- ]exceed|hard\s+cap|budget\s+ceiling|"
    r"estimated\s+cost|estimated\s+fee|hourly\s+rate|billing\s+rate|"
    r"unit\s+rate|labor\s+rate|dollar\s+amount|fee\s+amount|"
    r"pass-?through|commission\s+rate"
    r")\b",
)

# Dollar/rate gaps must stay visible — never blank a cost cell.
_MONEY_OR_RATE_ASK_RE = re.compile(
    r"(?i)\b("
    r"estimated\s+cost|estimated\s+fee|hourly\s+rate|billing\s+rate|"
    r"unit\s+rate|labor\s+rate|fully[\s-]?burdened|"
    r"dollar\s+amount|fee\s+amount|extended\s+amount|"
    r"pass-?through|commission(?:\s+rate)?|"
    r"not[- ]to[- ]exceed|hard\s+cap|budget\s+ceiling"
    r")\b"
    r"|\$\s*/\s*hr"
)

# Internal audit / nicety tags — never selection-critical; always strip.
_ALWAYS_REMOVE_VERIFY_ASK_RE = re.compile(
    r"(?i)"
    r"("
    r"gated\s+evidence|not\s+in\s+(?:gated\s+)?evidence\s+set|"
    r"not\s+supported\s+for|claim\s+['\"]?\w+['\"]?\s+not\s+supported|"
    r"backup\s+(?:mobile\s+)?(?:partner|vendor|firm|subcontractor)|"
    r"subcontractor\s+name|"
    r"mobile\s+app\s+partner|"
    r"unnamed\s+partner|"
    r"optional\s+(?:name|contact|partner)|"
    r"sample\s+(?:dashboard|screenshot|report\s+graphic)|"
    r"optional\s+dashboard|"
    r"dashboard\s+screenshot|"
    r"kpi\s+dashboard\s+screenshot|"
    r"designer\s+(?:note|graphic|diagram)|"
    r"week/?dates|timing\s+within\s+rfp|"
    r"fit\s+rfp\s+award|"
    r"operations\s+confirm|"
    r"missing\s+from\s+outline|draft\s+content\s+for"
    r")",
)

# Kept for backward-compatible imports / older call sites.
_KEEP_VERIFY_ASK_RE = _SELECTION_CRITICAL_ASK_RE
_OPTIONAL_VERIFY_ASK_RE = _ALWAYS_REMOVE_VERIFY_ASK_RE

_STOP_ASK_TOKENS = frozenset(
    {
        "verify",
        "provide",
        "confirm",
        "from",
        "with",
        "kb",
        "rfp",
        "field",
        "specific",
        "brief",
        "note",
        "name",
        "names",
        "title",
        "details",
        "information",
        "needed",
        "missing",
        "unknown",
        "insert",
        "tbd",
        "placeholder",
        "named",
        "but",
        "this",
        "draft",
        "gated",
        "evidence",
        "set",
        "client",
        "section",
        "that",
        "only",
        "sonja",
        "manual",
        "fill",
    }
)

_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_DOLLAR_RE = re.compile(r"\$\s*[\d,]+(?:\.\d{2})?")


def strip_verify_tags_not_required_by_rfp(
    content: str,
    rfp_text: str,
) -> tuple[str, int]:
    """Remove [VERIFY] tags that are not selection/DQ-critical for THIS RFP.

    Fail-closed: default REMOVE. Keep only locked legal tags, or asks that are
    both selection-critical in kind AND grounded in the RFP text (or no RFP
    text yet for locked/critical categories). Never invents replacements.
    """
    body = content or ""
    if not VERIFY_TAG_RE.search(body) and not re.search(r"\[VERIFY\]", body or "", re.I):
        return body, 0

    rfp_cf = (rfp_text or "").casefold()
    removed = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal removed
        ask = (match.group(1) or "").strip()
        if not ask:
            removed += 1
            return ""
        try:
            from app.services.evidence_trust.legal_attestation_gate import (
                is_locked_legal_verify_tag,
            )

            if is_locked_legal_verify_tag(ask):
                return match.group(0)
        except Exception:  # noqa: BLE001
            pass
        if _ALWAYS_REMOVE_VERIFY_ASK_RE.search(ask):
            removed += 1
            return ""
        if _MONEY_OR_RATE_ASK_RE.search(ask):
            return match.group(0)
        if _SELECTION_CRITICAL_ASK_RE.search(ask) and _rfp_mandates_placeholder_ask(
            ask, rfp_cf
        ):
            return match.group(0)
        # Default fail-closed: not selection-critical for this RFP → remove.
        removed += 1
        return ""

    out = VERIFY_TAG_RE.sub(_repl, body)
    # Bare [VERIFY] with no ask is never actionable — drop.
    bare_n = len(re.findall(r"\[VERIFY\]", out, flags=re.I))
    if bare_n:
        out = re.sub(r"\[VERIFY\]", "", out, flags=re.I)
        removed += bare_n
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"  +", " ", out)
    return out.strip() if body.strip() else out, removed


def scrub_result_introduces_fabrication(
    original: str,
    updated: str,
    *,
    rfp_text: str = "",
    kb_text: str = "",
) -> bool:
    """True when scrub output invents phones/emails/dollars not in sources."""
    allowed = f"{original or ''}\n{rfp_text or ''}\n{kb_text or ''}"

    def _novel(pattern: re.Pattern[str]) -> bool:
        for match in pattern.finditer(updated or ""):
            token = match.group(0)
            if token not in allowed and token.casefold() not in allowed.casefold():
                return True
        return False

    return _novel(_PHONE_RE) or _novel(_EMAIL_RE) or _novel(_DOLLAR_RE)


_SCRUB_VERIFY_ASK_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"(?:remove|delete|strip|scrub|drop|purge|kill|"
    r"get\s+rid\s+of|take\s+out|cut\s+out)"
    r".{0,60}"
    r"(?:\[?\s*VERIFY|verify\s+tags?|verify\s+placeholders?|verify\s+gaps?)"
    r"|"
    r"(?:clean|clear)\s+(?:out\s+|up\s+)?"
    r"(?:the\s+|all\s+|any\s+)?"
    r"(?:\[?\s*VERIFY|verify\s+tags?|verify\s+placeholders?)"
    r"|"
    r"(?:optional|unnecessary|not\s+(?:needed|necessary|required))"
    r".{0,40}"
    r"(?:\[?\s*VERIFY|verify\s+tags?)"
    r"|"
    r"(?:\[?\s*VERIFY|verify\s+tags?).{0,40}"
    r"(?:not\s+(?:needed|necessary|required)|if\s+optional)"
    r")",
)

_FILL_VERIFY_ASK_RE = re.compile(
    r"(?is)"
    r"(?:fill|resolve|complete|replace).{0,80}"
    r"(?:\[?\s*VERIFY|verify\s+tags?|gaps?|placeholders?)",
)


@dataclass
class VerifyOptionalScrubResult:
    content: str
    tags_before: int
    tags_after: int
    removed: int
    kept_required: int
    changed: bool
    note: str = ""


def count_verify_tags(text: str) -> int:
    return len(VERIFY_TAG_RE.findall(text or "")) + len(
        re.findall(r"\[VERIFY\]", text or "", flags=re.I)
    )


def count_manual_fill_tags(text: str) -> int:
    return len(MANUAL_FILL_TAG_RE.findall(text or ""))


def count_placeholder_tags(text: str) -> int:
    return count_verify_tags(text) + count_manual_fill_tags(text)


def _ask_from_manual_fill_tag(tag: str) -> str:
    """Inner instruction text from a [MANUAL FILL…] span."""
    inner = (tag or "").strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    inner = re.sub(r"(?i)^MANUAL\s+FILL\s*:?\s*", "", inner).strip()
    return inner


def _rfp_mandates_placeholder_ask(ask: str, rfp_cf: str) -> bool:
    """True when THIS RFP clearly cares about the placeholder topic."""
    if not rfp_cf.strip():
        return True
    ask_cf = ask.casefold()
    topic_needles: list[str] = []
    if re.search(r"(?i)\b(fein|ein\b|tax\s*id|federal\s+employer)\b", ask_cf):
        topic_needles.extend(
            ["fein", "ein", "tax id", "employer identification", "federal tax"]
        )
    if re.search(r"(?i)\b(insurance|coi\b|liability|coverage)\b", ask_cf):
        topic_needles.extend(
            ["insurance", "certificate of insurance", "liability", "coi"]
        )
    if re.search(r"(?i)\be-?verify\b", ask_cf):
        topic_needles.extend(["e-verify", "everify", "employment eligibility"])
    if re.search(
        r"(?i)\b(affidavit|attestation|perjury|conflict\s+of\s+interest)\b", ask_cf
    ):
        topic_needles.extend(
            ["affidavit", "attestation", "perjury", "conflict of interest"]
        )
    if re.search(r"(?i)\b(bond|bonding)\b", ask_cf):
        topic_needles.extend(["bond", "bonding", "surety"])
    if re.search(r"(?i)\bw-?9\b", ask_cf):
        topic_needles.extend(["w-9", "w9", "taxpayer"])
    if re.search(r"(?i)\b(reference|references)\b", ask_cf):
        topic_needles.extend(["reference", "references"])
    if re.search(r"(?i)\b(percent\s*time|staffing\s+hours|%\s*time)\b", ask_cf):
        topic_needles.extend(
            ["percent-time", "percent time", "% time", "fte", "hours dedicated"]
        )
    if re.search(
        r"(?i)\b(not[- ]to[- ]exceed|hard\s+cap|budget\s+ceiling|gross-?receipts)\b",
        ask_cf,
    ):
        topic_needles.extend(
            ["not to exceed", "nte", "budget ceiling", "gross receipts", "hard cap"]
        )
    if _MONEY_OR_RATE_ASK_RE.search(ask_cf):
        topic_needles.extend(
            ["cost", "budget", "price", "pricing", "fee", "rate", "subcontractor"]
        )
    if topic_needles:
        return any(n in rfp_cf for n in topic_needles)
    tokens = [
        t
        for t in re.split(r"\W+", ask_cf)
        if len(t) >= 4 and t not in _STOP_ASK_TOKENS
    ]
    if not tokens:
        return False
    hits = sum(1 for t in tokens if t in rfp_cf)
    return hits >= 2


def strip_manual_fill_tags_not_required_by_rfp(
    content: str,
    rfp_text: str,
) -> tuple[str, int]:
    """Remove [MANUAL FILL] tags that are not selection/DQ-critical for THIS RFP.

    Fail-closed: default REMOVE. Keep whole-section draft stubs, locked legal
    handoffs, and asks that are selection-critical AND grounded in the RFP.
    Never invents replacements.
    """
    body = content or ""
    if not MANUAL_FILL_TAG_RE.search(body):
        return body, 0

    rfp_cf = (rfp_text or "").casefold()
    removed = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal removed
        tag = match.group(0)
        ask = _ask_from_manual_fill_tag(tag)
        try:
            from app.services.proposal_manual_flags import (
                is_section_draft_stub_manual_fill,
            )

            if is_section_draft_stub_manual_fill(tag):
                return tag
        except Exception:  # noqa: BLE001
            pass
        try:
            from app.services.evidence_trust.legal_attestation_gate import (
                is_locked_legal_verify_tag,
            )

            # Owner prefixes ("Sonja — …") must not lock every MANUAL FILL — LEGAL
            # lock matches bare "sonja". Judge the ask body only.
            ask_body = re.sub(
                r"(?i)^(sonja|ella|operations)\s*[—\-–:]\s*",
                "",
                ask or "",
            ).strip()
            if ask_body and is_locked_legal_verify_tag(ask_body):
                return tag
        except Exception:  # noqa: BLE001
            pass
        if not ask:
            removed += 1
            return ""
        if _ALWAYS_REMOVE_VERIFY_ASK_RE.search(ask):
            removed += 1
            return ""
        # Also check ask body without owner prefix for always-remove / critical.
        ask_for_rules = re.sub(
            r"(?i)^(sonja|ella|operations)\s*[—\-–:]\s*",
            "",
            ask,
        ).strip() or ask
        if _ALWAYS_REMOVE_VERIFY_ASK_RE.search(ask_for_rules):
            removed += 1
            return ""
        if _MONEY_OR_RATE_ASK_RE.search(ask_for_rules) or _MONEY_OR_RATE_ASK_RE.search(ask):
            return tag
        if _SELECTION_CRITICAL_ASK_RE.search(ask_for_rules) and _rfp_mandates_placeholder_ask(
            ask_for_rules, rfp_cf
        ):
            return tag
        removed += 1
        return ""

    out = MANUAL_FILL_TAG_RE.sub(_repl, body)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"  +", " ", out)
    return out.strip() if body.strip() else out, removed


_EMPTY_COST_FILL = "[MANUAL FILL: estimated cost — confirm before submission]"
_MONEY_HEADER_RE = re.compile(
    r"(?i)\b(estimated\s+cost|cost|fee|price|pricing|rate|amount|investment)\b|\$"
)
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-–—]+\|[\s|:\-–—]+\|?\s*$")


def _md_table_cells(line: str) -> list[str]:
    return [c.strip() for c in (line or "").strip().strip("|").split("|")]


def restore_empty_money_table_cells(content: str) -> tuple[str, int]:
    """Put a visible MANUAL FILL back in blank cost/fee/rate table cells."""
    lines = (content or "").splitlines()
    if not lines:
        return content or "", 0
    out: list[str] = []
    restored = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if "|" not in line or _TABLE_SEP_RE.match(line):
            out.append(line)
            index += 1
            continue
        block = [line]
        cursor = index + 1
        while cursor < len(lines) and "|" in lines[cursor]:
            block.append(lines[cursor])
            cursor += 1
        data = [row for row in block if not _TABLE_SEP_RE.match(row)]
        if len(data) < 2:
            out.extend(block)
            index = cursor
            continue
        headers = _md_table_cells(data[0])
        money_cols = [
            i for i, h in enumerate(headers) if h and _MONEY_HEADER_RE.search(h)
        ]
        if not money_cols:
            out.extend(block)
            index = cursor
            continue
        rebuilt: list[str] = []
        header_seen = False
        for row in block:
            if _TABLE_SEP_RE.match(row) or not header_seen:
                if not _TABLE_SEP_RE.match(row):
                    header_seen = True
                rebuilt.append(row)
                continue
            cells = _md_table_cells(row)
            changed = False
            for col in money_cols:
                if col >= len(cells):
                    continue
                cell = cells[col].strip()
                if cell and cell not in {"—", "-", "–"}:
                    continue
                if "[MANUAL FILL" in cell.upper() or "[VERIFY" in cell.upper():
                    continue
                cells[col] = _EMPTY_COST_FILL
                changed = True
                restored += 1
            if changed:
                rebuilt.append("| " + " | ".join(cells) + " |")
            else:
                rebuilt.append(row)
        out.extend(rebuilt)
        index = cursor
    return "\n".join(out), restored


def strip_placeholder_tags_not_required_by_rfp(
    content: str,
    rfp_text: str,
) -> tuple[str, int]:
    """Strip optional [VERIFY] and [MANUAL FILL] unless RFP-critical. Never invents."""
    body, v_removed = strip_verify_tags_not_required_by_rfp(content, rfp_text)
    body, m_removed = strip_manual_fill_tags_not_required_by_rfp(body, rfp_text)
    body, _restored = restore_empty_money_table_cells(body)
    return body, v_removed + m_removed


def user_asks_scrub_optional_verify(user_message: str) -> bool:
    """True when the user wants optional/unneeded [VERIFY] tags removed (not KB-filled)."""
    raw = (user_message or "").strip()
    if not raw:
        return False
    if _SCRUB_VERIFY_ASK_RE.search(raw):
        # Explicit fill wins when both appear ("fill then remove leftovers" → fill first).
        if _FILL_VERIFY_ASK_RE.search(raw) and not re.search(
            r"(?i)\b(?:remove|strip|delete|scrub|drop)\b", raw
        ):
            return False
        return True
    return False


# Evidence-trust / claim-validator markers — including bare [FLAG: …] that the
# optional-VERIFY scrub never touched, which left Oregon Employment looking
# "broken" after a bad trust audit.
_INLINE_EVIDENCE_TAG_RE = re.compile(
    r"\[(?:VERIFY|FLAG)\s*:[^\]]*\]",
    re.I,
)
_STRIP_EVIDENCE_TAG_ASK_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"(?:remove|delete|strip|scrub|drop|purge|clear|get\s+rid\s+of|take\s+out)"
    r".{0,80}"
    r"(?:\[?\s*VERIFY|\[?\s*FLAG|verify\s+tags?|flag\s+tags?|"
    r"evidence\s+(?:tags?|flags?|markers?)|red\s+(?:flags?|tags?)|"
    r"green\s+(?:flags?|tags?))"
    r"|"
    r"(?:\[?\s*VERIFY|\[?\s*FLAG|verify\s+tags?|flag\s+tags?).{0,40}"
    r"(?:from\s+(?:this\s+)?(?:section|tab|draft)|out\s+of\s+(?:the\s+)?(?:section|draft))"
    r")",
)


def user_asks_strip_inline_evidence_tags(user_message: str) -> bool:
    """True when the user wants [VERIFY]/[FLAG] markers removed from the draft."""
    raw = (user_message or "").strip()
    if not raw:
        return False
    return bool(_STRIP_EVIDENCE_TAG_ASK_RE.search(raw))


def count_inline_evidence_tags(text: str) -> int:
    return len(_INLINE_EVIDENCE_TAG_RE.findall(text or ""))


def strip_inline_evidence_tags(content: str) -> tuple[str, int]:
    """Deterministically delete [VERIFY: …] and [FLAG: …] markers. Never invents."""
    body = content or ""
    found = _INLINE_EVIDENCE_TAG_RE.findall(body)
    if not found:
        return body, 0
    out = _INLINE_EVIDENCE_TAG_RE.sub("", body)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() + ("\n" if body.endswith("\n") else ""), len(found)


def _extract_json_content(raw: dict | list | str | None) -> tuple[str, str, int]:
    if not isinstance(raw, dict):
        return "", "", 0
    content = raw.get("content") or raw.get("updatedContent") or ""
    note = raw.get("note") or raw.get("summary") or ""
    kept = raw.get("keptRequiredCount")
    try:
        kept_n = int(kept) if kept is not None else 0
    except (TypeError, ValueError):
        kept_n = 0
    if not isinstance(content, str):
        content = str(content or "")
    if not isinstance(note, str):
        note = str(note or "")
    return content.strip(), note.strip(), max(0, kept_n)


async def scrub_optional_verify_tags(
    content: str,
    *,
    section_title: str,
    rfp_text: str,
    force: bool = False,
) -> VerifyOptionalScrubResult:
    """Drop [VERIFY]/[MANUAL FILL] the RFP does not require; never invent facts.

    When force=False and there are no placeholder tags, returns unchanged.
    """
    body = content or ""
    before_v = count_verify_tags(body)
    before_m = count_manual_fill_tags(body)
    before = before_v + before_m
    if before <= 0 and not force:
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=0,
            tags_after=0,
            removed=0,
            kept_required=0,
            changed=False,
            note="No [VERIFY]/[MANUAL FILL] tags to scrub.",
        )

    # Fast path: drop tags the RFP clearly does not require — no LLM, no invention.
    body, det_removed = strip_placeholder_tags_not_required_by_rfp(
        body, rfp_text or ""
    )
    mid = count_placeholder_tags(body)
    if mid <= 0:
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=before,
            tags_after=0,
            removed=max(det_removed, before),
            kept_required=0,
            changed=body.strip() != (content or "").strip(),
            note=(
                f"Removed {before} placeholder tag(s) not required by this RFP "
                "(deterministic scan)."
            ),
        )

    if not llm.is_configured():
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=before,
            tags_after=mid,
            removed=max(0, before - mid),
            kept_required=mid,
            changed=body.strip() != (content or "").strip(),
            note=(
                f"Deterministic RFP scan removed {max(0, before - mid)}; "
                "LLM not configured — left remaining tags."
            ),
        )

    rfp_excerpt = build_priority_rfp_excerpt(rfp_text or "", max_chars=12_000)

    # Before giving up on a tag, check whether the KB actually has the real answer —
    # a genuine KB-sourced fact beats both a bracket placeholder AND a vague generic
    # rewrite. Bounded to a short, single retrieval per section (asks are batched into
    # one question) to keep this cheap: this only fires on sections that already have
    # placeholders, and only once per section, not per tag.
    kb_context = ""
    asks = [a.strip() for a in VERIFY_TAG_RE.findall(body) if a.strip()]
    for tag in MANUAL_FILL_TAG_RE.findall(body):
        ask = _ask_from_manual_fill_tag(tag)
        if ask:
            asks.append(ask)
    if asks:
        try:
            from app.services.kb_rag_retrieve import retrieve_for_question

            question = f"{section_title}: " + "; ".join(asks[:6])
            kb_context, _labels, _queries = await retrieve_for_question(
                question, limit=4, max_chars=4_000
            )
        except Exception:
            logger.warning(
                "KB lookup for placeholder scrub failed on %s",
                section_title,
                exc_info=True,
            )
            kb_context = ""

    system = (
        "You scrub proposal manuscript [VERIFY: …] and [MANUAL FILL: …] placeholders "
        "using the RFP and the KB.\n"
        "BIAS (HARD): Default is REMOVE. Keep a placeholder ONLY when it is "
        "selection-critical for THIS RFP — i.e. dropping it would risk disqualification "
        "OR clearly cost evaluation points the RFP scores. Internal audit noise never stays.\n"
        "RULES:\n"
        "1. FIRST — check the KB EVIDENCE below. If it contains the exact fact a tag "
        "asks for (a name, number, cert, contact, partner, etc.), REPLACE the tag with that real "
        "fact, verbatim from the evidence.\n"
        "2. ALWAYS REMOVE these (never selection-critical): gated-evidence / 'not in evidence "
        "set' tags; claim-mismatch noise; optional partner/subcontractor names; week/date "
        "calendar stubs; designer notes; sample dashboard screenshots; vague 'confirm with "
        "operations' asks; redundant company-info asks already covered in Who We Are / 1.3.\n"
        "3. IF KB does NOT answer it — REMOVE the tag and rewrite the sentence/row/cell so the "
        "section still reads cleanly, WITHOUT inventing. Prefer clean prose over placeholders.\n"
        "   EXCEPTION — MONEY: never remove estimated cost, rates, fees, or dollar amounts and "
        "leave a blank table cell. If KB has no number, KEEP "
        "[MANUAL FILL: estimated cost — confirm before submission]. Empty cost cells hide the gap.\n"
        "4. KEEP a short [VERIFY: brief field] or [MANUAL FILL: Owner — field] ONLY when ALL "
        "are true: (a) the RFP EXPLICITLY mandates that exact fact for compliance or scored "
        "evaluation (FEIN, COI limits, required reference phone/email, E-Verify, affidavit, "
        "bonding, required %time when scored, estimated cost / rates / fees), (b) neither KB nor "
        "RFP already supplies it, (c) keeping it materially helps win / avoid DQ. "
        "Money placeholders: if unsure → KEEP.\n"
        "5. NEVER invent facts — no names, phones, emails, rates, certs, clients, or wins.\n"
        "6. Never leave empty brackets like [] or bare [VERIFY] / [MANUAL FILL].\n"
        "7. Preserve useful tables/structure; only change what placeholders force. Do not "
        "expand the section with AI filler or restated company boilerplate.\n"
        "8. zö voice: concrete, human, no corporate AI-slop.\n"
        "9. Return JSON only."
    )
    user = (
        f"Section title: {section_title}\n\n"
        f"RFP excerpts (source of truth for what is required):\n"
        f"{rfp_excerpt or '(no RFP text provided — treat unknown-named optional details as removable)'}\n\n"
        f"KB evidence (use ONLY if it genuinely answers a placeholder below; ignore otherwise):\n"
        f"{kb_context or '(no relevant KB evidence found)'}\n\n"
        f"Current section body:\n{body}\n\n"
        "Return JSON:\n"
        "{\n"
        '  "content": "full updated section markdown",\n'
        '  "keptRequiredCount": <int how many [VERIFY]/[MANUAL FILL] tags you intentionally kept>,\n'
        '  "note": "one short sentence: what you filled from KB vs removed vs kept"\n'
        "}"
    )
    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=6_000,
            temperature=0.15,
            tier="light",
            node_name="verify_optional_scrub",
        )
    except Exception:
        logger.exception("Optional VERIFY scrub LLM failed for %s", section_title)
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=before,
            tags_after=mid,
            removed=max(0, before - mid),
            kept_required=mid,
            changed=body.strip() != (content or "").strip(),
            note="Scrub failed — kept deterministic removals only.",
        )

    updated, note, kept_n = _extract_json_content(raw)
    if not updated:
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=before,
            tags_after=mid,
            removed=max(0, before - mid),
            kept_required=mid,
            changed=body.strip() != (content or "").strip(),
            note="Scrub returned empty content — kept deterministic removals only.",
        )

    # Guard: do not accept a near-empty wipe.
    if len(updated) < max(24, int(len(body) * 0.25)):
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=before,
            tags_after=mid,
            removed=max(0, before - mid),
            kept_required=mid,
            changed=body.strip() != (content or "").strip(),
            note="Scrub rejected — rewrite looked truncated.",
        )

    # Never accept invented phones / emails / dollars from the scrub LLM.
    if scrub_result_introduces_fabrication(
        body,
        updated,
        rfp_text=rfp_excerpt or rfp_text or "",
        kb_text=kb_context or "",
    ):
        return VerifyOptionalScrubResult(
            content=body,
            tags_before=before,
            tags_after=mid,
            removed=max(0, before - mid),
            kept_required=mid,
            changed=body.strip() != (content or "").strip(),
            note="Scrub rejected — rewrite invented contact/money facts.",
        )

    # Second deterministic pass after LLM (in case it left optional tags).
    updated, extra_removed = strip_placeholder_tags_not_required_by_rfp(
        updated, rfp_text or ""
    )

    after = count_placeholder_tags(updated)
    removed = max(0, before - after)
    return VerifyOptionalScrubResult(
        content=updated,
        tags_before=before,
        tags_after=after,
        removed=removed,
        kept_required=kept_n if kept_n else after,
        changed=updated.strip() != (content or "").strip(),
        note=note
        or (
            f"Removed {removed} optional placeholder tag(s)"
            + (f" (+{extra_removed} deterministic)" if extra_removed else "")
            + f"; kept {after} required."
        ),
    )


async def run_verify_scrub_only_scan(
    rfp_id: str,
) -> tuple[
    "PreSubmitReview",
    "ProposalResearchCache",
    "ProposalDraft",
    dict,
]:
    """Button-only Scan: read sections with [VERIFY], check RFP, remove unless critical.

    Does NOT add closing tabs, structure, budget, or KPI passes — VERIFY scrub only.
    Never invents facts.
    """
    from datetime import datetime, timezone

    from app.models.proposal import ProposalDraft, ProposalResearchCache
    from app.services.go_no_go_service import _assess_rfp_content
    from app.services.proposal_common import ProposalError, aload_rfp_for_proposal
    from app.services.proposal_draft_snapshots import push_proposal_snapshot
    from app.services.proposal_pipeline_checkpoint import record_pipeline_activity
    from app.services.proposal_presubmit_review import (
        run_presubmit_review_with_manual_flags,
    )
    from app.services.proposal_repository import (
        aget_proposal_draft,
        aget_research_cache,
        asave_proposal_draft,
        asave_research_cache,
    )
    from app.services.rfp_content import combine_rfp_text, load_local_rfp_text

    rfp, content, truncated = await aload_rfp_for_proposal(rfp_id)
    _desc, pdf_text, _exists, _missing, _pages, _img = load_local_rfp_text(
        rfp, max_chars=250_000
    )
    rfp_text = combine_rfp_text(
        _desc or (content.description or ""), pdf_text, max_chars=250_000
    )
    if len(rfp_text.strip()) < 200:
        rfp_text = truncated
    if not rfp_text.strip():
        info = _assess_rfp_content(rfp)
        rfp_text = combine_rfp_text(info.description or "", info.pdf_text or "")

    draft = await aget_proposal_draft(rfp_id)
    research = await aget_research_cache(rfp_id)
    if not draft or not any((s.content or "").strip() for s in draft.sections):
        raise ProposalError(
            "No proposal content to scan. Generate the proposal first.",
            status_code=400,
        )

    await record_pipeline_activity(
        rfp_id,
        label="Scan RFP: remove optional [VERIFY]",
        detail="Only pass — sections with [VERIFY] vs full RFP; never invent.",
        step_index=1,
        step_total=1,
        in_progress_phase="fulfill-scan",
    )

    draft = push_proposal_snapshot(draft, label="Before VERIFY scrub")
    await asave_proposal_draft(draft)

    # Task 5's ledger reconciler runs inside THIS path — the real Scan-RFP
    # button always calls mode="verify_scrub_only" (see
    # proposal_fulfill_rfp_gaps.run_fulfill_rfp_gaps), so wiring the
    # reconciler only into the legacy mode="full" body left it unreachable
    # from the UI. It is safe to run unconditionally here: deterministic,
    # zero-LLM, idempotent. ADD (Task 9) is applied, not just surfaced, but
    # only for requirements the matcher found zero matching sections for
    # (len(satisfied_by) == 0) — the same signal that already gated the
    # surfaced-only report — so it cannot create a duplicate of a
    # requirement the matcher already recognized under a different title.
    from app.services.proposal_rfp_compliance import (
        MANUAL_FILL_MARKER,
        apply_scan_ledger_pass,
    )

    draft, research, ledger_result, ledger_draft_logs = await apply_scan_ledger_pass(
        rfp_id=rfp_id,
        draft=draft,
        research=research,
        rfp=rfp,
        rfp_text=rfp_text,
    )

    # Task 17: the Scan-RFP button never touched the budget at all — this
    # module's own docstring above says so ("Does NOT add closing tabs,
    # structure, budget, or KPI passes"), and run_fulfill_budget_scan only
    # ran on mode="full", which the frontend never sends. So none of the
    # budget protections this project already built (prose arithmetic, the
    # underbid floor, RFP-forbidden travel, line-item classification) ever
    # ran on a Scan-RFP click. check_and_repair_budget_for_scan reuses the
    # SAME deterministic machinery Phase 3.5 / mode="full" already use
    # (run_budget_editor_pass + its sub-checks) but never lets a budget
    # defect raise past this point — a bad budget surfaces as a finding in
    # the report below, never a 500, and never an aborted scan. No budget
    # yet is not an error. Zero LLM calls.
    from app.services.proposal_scan_budget_check import check_and_repair_budget_for_scan

    budget_check = check_and_repair_budget_for_scan(
        rfp_id=rfp_id, draft=draft, research=research, rfp_text=rfp_text
    )
    if budget_check.changed:
        draft = budget_check.draft
        research = budget_check.research
        await asave_proposal_draft(draft)
        if research:
            await asave_research_cache(research)

    # Task 12: the button's only truncation handling used to be reporting it
    # (below) — repair_truncated_manuscript_sections exists but only ran on
    # mode="full", which this button never calls, so bios/case studies cut
    # off mid-sentence shipped that way every time. repair_truncated_sections_
    # from_kb is safe to run unconditionally here: it detects with the same
    # T1 scanner the report below uses, only ever appends a KB-grounded
    # completion to a section's existing verbatim prefix (never invents a
    # fact, never a wholesale rewrite — see the module note in
    # proposal_fulfill_truncation_repair.py), and never raises.
    from app.services.proposal_fulfill_truncation_repair import (
        repair_truncated_sections_from_kb,
    )

    titles_before_repair = {s.id: s.title for s in draft.sections}
    (
        draft,
        truncation_repaired_ids,
        truncation_still_truncated_ids,
        truncation_repair_logs,
    ) = await repair_truncated_sections_from_kb(
        draft=draft, rfp=rfp, rfp_context=rfp_text
    )
    if truncation_repaired_ids:
        await asave_proposal_draft(draft)

    # Task 15: run the RFP submission-attachment checklist inside the scan
    # path itself. It never ran here before — only inside mode="full"
    # (ensure_all_rfp_submission_requirements), and the real Scan-RFP button
    # always calls mode="verify_scrub_only" (see run_fulfill_rfp_gaps above),
    # so the scan relied entirely on whatever the compliance matrix happened
    # to capture. This is independent of that matrix: a required W-9 or
    # Certificate of Insurance the matrix missed is still reported. Zero new
    # LLM calls — outstanding_submission_checklist_for_scan is pure regex
    # over rfp_text plus a manuscript keyword scan (same cost class as the
    # VERIFY scrub already running in this function); it never drafts a
    # section, so an attachment-class item is flagged, never papered over
    # with prose. Idempotent: an item already resolved in the draft (see
    # its docstring) is dropped from both lists on the next scan.
    from app.services.proposal_rfp_submission_requirements import (
        outstanding_submission_checklist_for_scan,
    )

    outstanding_checklist = outstanding_submission_checklist_for_scan(rfp_text, draft)

    # Same consistency + signed-PDF designer note as mode=full Scan — existing draft
    # only; never invents signature dates or figures.
    try:
        from app.services.proposal_consistency_enforcement import (
            apply_consistency_enforcement,
        )

        draft, consistency_logs = apply_consistency_enforcement(
            draft,
            research=research,
            attachment_labels=list(outstanding_checklist.needs_attachment),
            rfp_text=rfp_text,
        )
        if consistency_logs:
            await asave_proposal_draft(draft)
    except Exception as exc:  # noqa: BLE001
        logger.warning("verify_scrub consistency enforcement skipped: %s", exc)
        consistency_logs = []

    verify_ids = {
        s.id for s in draft.sections if count_verify_tags(s.content or "") > 0
    }
    report: dict = {
        "mode": "verify_scrub_only",
        "sectionsScanned": len(verify_ids),
        "verifyTagsRemoved": 0,
        "verifyTagsKept": 0,
        "logs": list(consistency_logs) if consistency_logs else [],
        "closingDetected": [],
        "closingAdded": [],
        "closingAddedSections": [],
        "humanDecisionGaps": [],
        "consistencyFixesApplied": len(consistency_logs) if consistency_logs else 0,
        # Task 15 — the two categories the user must see kept visibly
        # distinct: a narrative section the pipeline can draft from the KB
        # vs a signed/scanned physical document a human must attach. Never
        # merged into one undifferentiated list — see
        # outstanding_submission_checklist_for_scan's module note.
        "submissionNeedsDraftingCount": len(outstanding_checklist.needs_drafting),
        "submissionNeedsDraftingTitles": outstanding_checklist.needs_drafting,
        "submissionNeedsAttachmentCount": len(outstanding_checklist.needs_attachment),
        "submissionNeedsAttachmentTitles": outstanding_checklist.needs_attachment,
        "inPlaceFixCount": 0,
        "ledgerMergesApplied": len(ledger_result.applied_merges),
        "ledgerCutsApplied": len(ledger_result.applied_cuts),
        "ledgerAdditionsApplied": len(ledger_result.applied_additions),
        # Titles, not just counts — the UI banner needs to say WHICH section
        # was added/merged/trimmed, not just how many.
        "ledgerAdditionsSectionTitles": [
            a.section_title for a in ledger_result.applied_additions
        ],
        "ledgerMergesSectionTitles": sorted(
            {m.owner_section_title for m in ledger_result.applied_merges}
        ),
        "ledgerCutsSectionTitles": [c.section_title for c in ledger_result.applied_cuts],
        # Set only when the ledger reconcile never ran at all (no persisted
        # ledger AND nothing to build one from) — surfaced so the banner can
        # say WHY ledger_added/merged/cut are 0 instead of reading identically
        # to "already compliant, nothing to fix". None on every other path.
        "ledgerCheckSkippedReason": ledger_result.skipped_reason,
        # A missing scored_criterion is never auto-added (see
        # proposal_rfp_compliance.py's module note — a scoring category name
        # rarely lexically matches the requirement-phrased section that
        # covers it) — surfaced here instead so the banner can say "N scored
        # criteri(a) may not be covered" and let a human judge.
        "ledgerScoredCriteriaAdvisoryCount": len(ledger_result.advisory_scored_criteria),
        "ledgerScoredCriteriaAdvisoryTitles": [
            a.requirement_text for a in ledger_result.advisory_scored_criteria
        ],
        # Task 16: administrative/procedural submission constraints (source=
        # "submission_instruction") — deadlines, delivery/labelling
        # instructions, validity windows, copy counts, format rules. Never
        # auto-added as a section (see proposal_rfp_compliance.py's
        # _ADD_ELIGIBLE_SOURCES module note) but never silently dropped
        # either — reported here as its own compliance checklist so the
        # banner can say "N submission requirement(s) to comply with" and a
        # real deadline (e.g. "Proposal must be received no later than
        # August 3, 2026 by 3:00 P.M. (ET)") stays visible.
        "ledgerSubmissionInstructionsCount": len(
            ledger_result.advisory_submission_instructions
        ),
        "ledgerSubmissionInstructionsTitles": [
            a.requirement_text for a in ledger_result.advisory_submission_instructions
        ],
        # Set only when the blast-radius guard declined to apply otherwise-
        # eligible additions this pass — see _BLAST_RADIUS_MAX_ADDITIONS /
        # _BLAST_RADIUS_MAX_GROWTH_FRACTION in proposal_rfp_compliance.py.
        "ledgerAdditionsDeclinedCount": ledger_result.declined_addition_count,
        "ledgerAdditionsDeclinedTitles": ledger_result.declined_addition_titles,
        "ledgerAdditionsDeclinedReason": ledger_result.declined_addition_reason,
        # Task 12 — sections the KB-grounded pass above completed vs. sections
        # it could not (still truncated; see truncatedSectionsCount/Titles
        # further down, which is the post-repair T1 rescan and is the source
        # of truth for what still needs a human).
        "truncationRepairedCount": len(truncation_repaired_ids),
        "truncationRepairedSectionTitles": [
            titles_before_repair.get(sid, sid) for sid in truncation_repaired_ids
        ],
        # Task 17 — budget outcome, distinct from "not checked": "none" (no
        # budget yet — not an error), "ok" (checked, clean), "repaired"
        # (deterministic fix — arithmetic/classification/prose), or
        # "needs_human" / "repaired_needs_human" (a pricing JUDGEMENT call —
        # underbid vs the guide floor, RFP-forbidden travel — reported, never
        # invented). A silent pass here would read identically to "never
        # ran", the exact ambiguity this project has already been burned by
        # twice (see the module note on proposal_scan_budget_check.py).
        "budgetStatus": budget_check.status,
        "budgetChanged": budget_check.changed,
        "budgetRepairedNotes": budget_check.repaired_notes,
        "budgetEscalationNotes": budget_check.escalation_notes,
    }
    report["logs"].extend(ledger_result.logs)
    report["logs"].extend(ledger_draft_logs)
    report["logs"].extend(budget_check.logs)
    report["logs"].extend(truncation_repair_logs)
    if budget_check.status in ("needs_human", "repaired_needs_human"):
        report["humanDecisionGaps"].append(
            "budget:needs-review — "
            + "; ".join(budget_check.escalation_notes)[:400]
        )
    if outstanding_checklist.needs_attachment:
        report["logs"].append(
            "submission-checklist:attachment — "
            f"{len(outstanding_checklist.needs_attachment)} physical document(s) this RFP "
            "requires are not yet resolved in the draft: "
            + "; ".join(outstanding_checklist.needs_attachment[:8])
        )
    if outstanding_checklist.needs_drafting:
        report["logs"].append(
            "submission-checklist:narrative — "
            f"{len(outstanding_checklist.needs_drafting)} narrative item(s) this RFP's "
            "submission instructions call for still need drafting: "
            + "; ".join(outstanding_checklist.needs_drafting[:8])
        )
    if ledger_result.applied_additions:
        added_by_id = {a.section_id: a for a in ledger_result.applied_additions}
        still_stub = [
            a
            for s in draft.sections
            if (a := added_by_id.get(s.id)) is not None
            and MANUAL_FILL_MARKER in (s.content or "")
        ]
        if still_stub:
            preview = "; ".join(a.requirement_text[:80] for a in still_stub[:5])
            report["humanDecisionGaps"].append(
                f"ledger:add-needs-content — {len(still_stub)} mandatory "
                f"requirement(s) had no matching section and KB drafting could not "
                f"fill them; a [MANUAL FILL] section remains and needs KB search / "
                f"human content: {preview}"
            )

    if not verify_ids:
        report["logs"].append("No [VERIFY] tags found in the manuscript.")
    else:
        scrubbed, scrub_logs = await scrub_draft_optional_verify_tags(
            list(draft.sections),
            rfp_text=rfp_text,
            section_filter_ids=verify_ids,
        )
        report["logs"].extend(scrub_logs)
        before = {s.id: count_verify_tags(s.content or "") for s in draft.sections}
        after = {s.id: count_verify_tags(s.content or "") for s in scrubbed}
        removed = sum(max(0, before.get(i, 0) - after.get(i, 0)) for i in before)
        kept = sum(after.values())
        report["verifyTagsRemoved"] = removed
        report["verifyTagsKept"] = kept
        report["inPlaceFixCount"] = sum(
            1 for line in scrub_logs if "removed" in line.casefold()
        )
        draft = draft.model_copy(
            update={
                "sections": scrubbed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "last_fulfill_report": report,
            }
        )
        await asave_proposal_draft(draft)

    review = run_presubmit_review_with_manual_flags(
        rfp=rfp, draft=draft, research=research, finalized=False
    )

    # Task 11: the presubmit review already runs truncation (T1) and
    # hallucination detection as part of scan_manuscript_consistency /
    # _scan_hallucinations — both were DETECTED and then discarded because
    # nothing counted them into the fulfill report the UI reads. This rescan
    # runs AFTER Task 12's repair_truncated_sections_from_kb (above) and
    # after the VERIFY scrub, so truncatedSectionsCount/Titles here is
    # exactly what's still truncated once repair has had its shot — not a
    # stale pre-repair count. See truncationRepairedCount/
    # truncationRepairedSectionTitles above for what repair already fixed.
    truncated_section_ids: list[str] = []
    for issue in review.issues:
        if issue.category == "truncation" and issue.section_id:
            if issue.section_id not in truncated_section_ids:
                truncated_section_ids.append(issue.section_id)
    truncated_titles_by_id = {s.id: s.title for s in draft.sections}
    report["truncatedSectionsCount"] = len(truncated_section_ids)
    report["truncatedSectionTitles"] = [
        truncated_titles_by_id.get(sid, sid) for sid in truncated_section_ids
    ]

    # Bug fix: truncation_repaired_ids above was accepted by
    # repair_truncated_sections_from_kb's OWN success gate
    # (looks_truncated_for_fulfill — checks only the section's trailing
    # cutoff), which is not the same detector as the T1 scan just run
    # (scan_truncation_artifacts via scan_all_t1, which also flags unbalanced
    # parens/brackets and currency fragments ANYWHERE in the section, not
    # just the tail). A section can pass the narrower repair-time check —
    # its cut-off sentence is now complete — while an unrelated artifact
    # elsewhere in the same content still trips the broader T1 rescan above.
    # Left uncorrected, that section is reported as BOTH repaired and still
    # truncated: the exact double-count a real user saw (8 repaired / 9
    # truncated naming the same 5 sections), which reads as if nothing
    # worked. truncated_section_ids (just computed, post-repair) is the
    # authoritative source of truth here, so anything still in it is by
    # definition not genuinely repaired — exclude it from the repaired
    # count/titles so the two lists are disjoint by construction.
    still_truncated_ids = set(truncated_section_ids)
    genuinely_repaired_ids = [
        sid for sid in truncation_repaired_ids if sid not in still_truncated_ids
    ]
    if len(genuinely_repaired_ids) != len(truncation_repaired_ids):
        demoted = [sid for sid in truncation_repaired_ids if sid in still_truncated_ids]
        report["logs"].append(
            "truncation-repair:kb — "
            f"{len(demoted)} section(s) passed the repair's own completion "
            "check but still trip the full T1 truncation scan (e.g. an "
            "unrelated unbalanced bracket/paren or currency fragment) — "
            "not counted as repaired: "
            + ", ".join(titles_before_repair.get(sid, sid) for sid in demoted)
        )
    report["truncationRepairedCount"] = len(genuinely_repaired_ids)
    report["truncationRepairedSectionTitles"] = [
        titles_before_repair.get(sid, sid) for sid in genuinely_repaired_ids
    ]

    report["unverifiedClaimsCount"] = sum(
        1 for issue in review.issues
        if issue.category in ("fabricated_fact", "unverified_claim")
    )

    now = datetime.now(timezone.utc).isoformat()
    updated_research = (
        research or ProposalResearchCache(rfpId=rfp_id, updatedAt=now)
    ).model_copy(update={"presubmit_review": review, "updated_at": now})
    await asave_research_cache(updated_research)
    draft = draft.model_copy(
        update={"last_fulfill_report": report, "updated_at": now}
    )
    await asave_proposal_draft(draft)

    logger.info(
        "scan-rfp:report rfp_id=%s sections_scanned=%s verify_removed=%s "
        "verify_kept=%s ledger_added=%s ledger_added_titles=%s ledger_merged=%s "
        "ledger_cut=%s ledger_scored_advisory=%s ledger_additions_declined=%s "
        "truncation_repaired=%s truncation_repaired_titles=%s "
        "truncation_still_truncated_ids=%s truncated_sections=%s truncated_titles=%s "
        "unverified_claims=%s submission_needs_drafting=%s submission_needs_attachment=%s "
        "submission_needs_attachment_titles=%s budget_status=%s budget_changed=%s",
        rfp_id,
        report.get("sectionsScanned"),
        report.get("verifyTagsRemoved"),
        report.get("verifyTagsKept"),
        report.get("ledgerAdditionsApplied"),
        report.get("ledgerAdditionsSectionTitles"),
        report.get("ledgerMergesApplied"),
        report.get("ledgerCutsApplied"),
        report.get("ledgerScoredCriteriaAdvisoryCount"),
        report.get("ledgerAdditionsDeclinedCount"),
        report.get("truncationRepairedCount"),
        report.get("truncationRepairedSectionTitles"),
        truncation_still_truncated_ids,
        report.get("truncatedSectionsCount"),
        report.get("truncatedSectionTitles"),
        report.get("unverifiedClaimsCount"),
        report.get("submissionNeedsDraftingCount"),
        report.get("submissionNeedsAttachmentCount"),
        report.get("submissionNeedsAttachmentTitles"),
        report.get("budgetStatus"),
        report.get("budgetChanged"),
    )
    return review, updated_research, draft, report


async def scrub_draft_optional_verify_tags(
    draft_sections: list,
    *,
    rfp_text: str,
    section_filter_ids: set[str] | None = None,
) -> tuple[list, list[str]]:
    """Scrub optional VERIFY/MANUAL FILL on draft sections. Returns (sections, logs).

    Each section's scrub is an independent, side-effect-free LLM call (no DB writes
    inside the loop), so sections-with-tags are scrubbed concurrently — this is the
    "scan RFP" half of the Senior Editor pass and the main latency cost when a
    manuscript has several flagged sections.
    """
    targets: list[tuple[int, Any, str]] = []
    out: list[Any] = list(draft_sections)
    for idx, section in enumerate(draft_sections):
        sid = getattr(section, "id", "") or ""
        title = getattr(section, "title", "") or sid
        content = getattr(section, "content", "") or ""
        if section_filter_ids is not None and sid not in section_filter_ids:
            continue
        if count_placeholder_tags(content) <= 0:
            continue
        if is_dead_section(content):
            # The whole body is a failure stub, not prose with optional tags in
            # it. There is nothing to scrub, and removing the stub would destroy
            # the marker chat uses to rebuild the section.
            continue
        targets.append((idx, section, title))

    if not targets:
        return out, []

    sem = asyncio.Semaphore(3)

    async def _scrub_one(section: Any, title: str) -> Any:
        async with sem:
            return await scrub_optional_verify_tags(
                getattr(section, "content", "") or "",
                section_title=title,
                rfp_text=rfp_text,
            )

    results = await asyncio.gather(
        *(_scrub_one(section, title) for _idx, section, title in targets)
    )

    logs: list[str] = []
    for (idx, section, _title), result in zip(targets, results):
        sid = getattr(section, "id", "") or ""
        if result.changed:
            out[idx] = section.model_copy(update={"content": result.content})
            logs.append(
                f"placeholder-scrub:{sid}: removed {result.removed}, "
                f"kept {result.tags_after} — {result.note[:120]}"
            )
        elif result.note:
            logs.append(f"placeholder-scrub:{sid}: {result.note[:120]}")
    return out, logs
