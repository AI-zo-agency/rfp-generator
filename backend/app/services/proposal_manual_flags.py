"""Scan manuscript + compliance gaps into structured manual-fill flags for the UI."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.models.proposal import ManualFillFlag, ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services.proposal_rfp_compliance import ComplianceGap, scan_rfp_compliance_gaps

logger = logging.getLogger(__name__)

# Broader than plan's FILL(?::[^\]]*)? — also covers [MANUAL FILL or N/A] budget stubs.
# Word boundary after FILL so [MANUAL FILLING:…] is not matched.
# See docs/architecture/t2_5_manual_fill_reachability.md (T2.5).
MANUAL_FILL_TAG_RE = re.compile(r"\[MANUAL\s+FILL\b[^\]]*\]", re.I)

# Structure stubs from ensure_missing_scored_section_stubs — meant to be drafted,
# not preserved as protected tags during Improve / full redraft.
_SECTION_DRAFT_STUB_MFILL_RE = re.compile(
    r"(?is)\[MANUAL\s+FILL:\s*Draft this RFP-required section[^\]]*\]\s*",
)


def is_section_draft_stub_manual_fill(tag: str) -> bool:
    """True for whole-section draft stubs (replace on Improve), not fact placeholders."""
    return bool(
        re.search(
            r"(?is)\[MANUAL\s+FILL:\s*Draft this RFP-required section",
            tag or "",
        )
    )


def strip_section_draft_stub_manual_fills(text: str) -> str:
    """Remove draft-this-section stubs so Improve can write real prose."""
    return _SECTION_DRAFT_STUB_MFILL_RE.sub("", text or "")


VERIFY_TAG_RE = re.compile(r"\[VERIFY:\s*([^\]]+)\]", re.I)
PLACEHOLDER_TAG_RE = re.compile(r"\[(?:PLACEHOLDER|INSERT|TBD)[^\]]+\]", re.I)
GENERIC_VERIFY_RE = re.compile(r"\[VERIFY\]", re.I)

# VERIFY_TAG_RE stops at the first ]. If contradiction text embeds a ], append
# produces a note the optional-scrub regex only partially deletes — leaving a
# gibberish tail like: `with no actual content… | RFP requires: … (pricing gui]`.
_ORPHAN_VERIFY_LEFTOVER_RE = re.compile(
    r"(?is)"
    r"(?:^|(?<=\n))"
    r"[^\n\[]{0,160}?"
    r"(?:"
    r"with\s+no\s+actual\s+content|"
    r"required\s+submiss\w*|"
    r"resolve\s+(?:RFP|fact)\s+contradiction"
    r")?"
    r"[^\n\[]{0,240}?"
    r"(?:\|\s*|[—–-]\s*)?"
    r"(?:RFP\s+requires:|Verified\s+source\s+says:)"
    r"[^\n\[]*?\]"
)


def sanitize_verify_tag_interior(text: str) -> str:
    """Keep VERIFY bodies free of [ / ] so VERIFY_TAG_RE matches the whole tag."""
    cleaned = (text or "").replace("[", "(").replace("]", ")")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def format_verify_tag(ask: str) -> str:
    """Build a single well-formed ``[VERIFY: …]`` chip."""
    interior = sanitize_verify_tag_interior(ask)
    if not interior:
        return "[VERIFY: needs review]"
    return f"[VERIFY: {interior}]"


def repair_orphan_verify_leftovers(content: str) -> tuple[str, int]:
    """Strip contradiction VERIFY tails left after a premature ``]`` closed the tag."""
    body = content or ""
    if not body.strip():
        return body, 0
    # Only operate on text *outside* well-formed VERIFY chips — otherwise a
    # legitimate ``[VERIFY: … | RFP requires: …]`` would be eaten by the orphan
    # pattern matching the interior through the closing bracket.
    parts: list[str] = []
    last = 0
    removed = 0
    for match in VERIFY_TAG_RE.finditer(body):
        chunk, n = _ORPHAN_VERIFY_LEFTOVER_RE.subn("", body[last : match.start()])
        removed += n
        parts.append(chunk)
        parts.append(match.group(0))
        last = match.end()
    chunk, n = _ORPHAN_VERIFY_LEFTOVER_RE.subn("", body[last:])
    removed += n
    parts.append(chunk)
    if not removed:
        return body, 0
    out = "".join(parts)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"  +", " ", out)
    return out.strip(), removed


def repair_orphan_verify_leftovers_in_draft(
    draft: ProposalDraft,
) -> tuple[ProposalDraft, list[str]]:
    """Draft-wide orphan VERIFY cleanup; hollow shells get a MANUAL FILL stub."""
    from app.services.proposal_section_health import SectionHealth, classify_section_health

    logs: list[str] = []
    sections: list = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        cleaned, n = repair_orphan_verify_leftovers(body)
        if not n:
            sections.append(section)
            continue
        changed = True
        title = (section.title or section.id or "section").strip()
        health = classify_section_health(cleaned)
        if health in {
            SectionHealth.EMPTY,
            SectionHealth.PLACEHOLDER_ONLY,
            SectionHealth.DRAFT_FAILED,
            SectionHealth.NO_EVIDENCE,
        } or len(cleaned.strip()) < 40:
            cleaned = (
                f"## {title}\n\n"
                f"[MANUAL FILL: Draft this RFP-required section — {title}]\n"
            )
            logs.append(
                f"orphan VERIFY leftover cleared on “{title}” → MANUAL FILL stub"
            )
        else:
            logs.append(f"orphan VERIFY leftover cleared on “{title}”")
        sections.append(
            section.model_copy(update={"content": cleaned, "status": "generated"})
        )
    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs

_MANUAL_FILL_REQUEST_RE = re.compile(
    r"(?is)"
    r"(?:fill|resolve|clear|complete|provide|enter|set|replace).{0,80}"
    r"(?:\[?\s*MANUAL\s+FILL|manual\s+fills?|manual[\s-]?fill\s+tags?)|"
    r"\[MANUAL\s+FILL|"
    r"\bmanual\s+fills?\b|"
    r"fill\s+(?:all\s+)?(?:the\s+)?(?:manual(?:\s+fill)?(?:\s+tags?)?|gaps|placeholders)",
)

_MFILL_PLACEHOLDER_RE = re.compile(r"«MFILL_(\d+)»")
# Word boundaries are load-bearing. Without \b on the trigger verbs and the
# connectors, "as" matched inside ordinary words and the tail was written into the
# manuscript as a literal value:
#     "please use the case study in the KB"   -> "e study in the KB"   (c-as-e)
#     "resolve these from the knowledge base" -> "e"                   (b-as-e)
#     "fill this in based on the case study"  -> "ed on the case study" (b-as-ed)
# Every one of those is an ordinary instruction, and each silently corrupted a
# section. See tests/test_manual_fill_value_extraction.py.
_USER_WITH_VALUE_RE = re.compile(
    r"(?is)\b(?:fill|set|use|replace|enter|provide|resolve)\b.{0,120}?"
    r"(?:\b(?:with|to|as)\b|=|:)\s*[\"']?(.+?)[\"']?\s*$"
)
_USER_IS_VALUE_RE = re.compile(
    r"(?is)(?:\b(?:is|are)\b|=|:)\s*[\"']?([^\"'\n]{1,200})[\"']?\s*$"
)

#: A one- or two-character capture is a regex artefact, not a value a user typed.
_MIN_MANUAL_FILL_VALUE_CHARS = 3


def _is_plausible_fill_value(value: str) -> bool:
    """Reject captures that are fragments rather than real values."""
    v = (value or "").strip()
    if len(v) < _MIN_MANUAL_FILL_VALUE_CHARS:
        return False
    if "[" in v or "]" in v:
        return False
    # A value that is only stopwords is a slice of the instruction, not content.
    if v.casefold() in {"the", "this", "that", "them", "these", "those", "it"}:
        return False
    return True
_FEIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?:\(\d{3}\)\s*\d{3}[-.\s]?\d{4}|\d{3}[-.\s]\d{3}[-.\s]\d{4})",
    re.I,
)


@dataclass(frozen=True)
class ManualFillTag:
    """One MANUAL FILL span in manuscript text."""

    text: str
    start: int
    end: int
    description: str


def extract_manual_fill_tags(text: str) -> list[ManualFillTag]:
    """Find all MANUAL FILL tags (colon-form, bare, and 'or N/A' variants)."""
    tags: list[ManualFillTag] = []
    for match in MANUAL_FILL_TAG_RE.finditer(text or ""):
        raw = match.group(0)
        inner = raw[1:-1].strip() if raw.startswith("[") and raw.endswith("]") else raw
        # Strip leading "MANUAL FILL" / "MANUAL FILL:" for the description.
        desc = re.sub(r"(?i)^MANUAL\s+FILL\s*:?\s*", "", inner).strip()
        tags.append(
            ManualFillTag(
                text=raw,
                start=match.start(),
                end=match.end(),
                description=desc,
            )
        )
    return tags


def user_asks_submit_handoff_fill(text: str) -> bool:
    """True when the user wants open confirm-before-submit handoffs addressed."""
    raw = (text or "").casefold()
    if "confirm before submit" in raw:
        return True
    if "fill all" in raw and ("submit" in raw or "confirm" in raw):
        return True
    return False


def is_manual_fill_request(text: str) -> bool:
    """True when the user is explicitly asking to resolve MANUAL FILL tags."""
    return bool(_MANUAL_FILL_REQUEST_RE.search(text or "")) or user_asks_submit_handoff_fill(
        text
    )


def mask_manual_fill_tags(content: str) -> tuple[str, list[str]]:
    """Replace MANUAL FILL tags with «MFILL_N» placeholders for LLM rewrites.

    Returns (masked_content, original_tag_texts_in_order).
    """
    tags = extract_manual_fill_tags(content)
    if not tags:
        return content, []
    originals = [t.text for t in tags]
    # Replace from end so offsets stay valid.
    masked = content
    for index, tag in enumerate(reversed(tags)):
        real_index = len(tags) - 1 - index
        placeholder = f"«MFILL_{real_index}»"
        masked = masked[: tag.start] + placeholder + masked[tag.end :]
    return masked, originals


def unmask_manual_fill_tags(content: str, originals: list[str]) -> str:
    """Restore «MFILL_N» placeholders to original MANUAL FILL tags."""
    if not originals:
        return content

    def repl(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        if 0 <= idx < len(originals):
            return originals[idx]
        return match.group(0)

    return _MFILL_PLACEHOLDER_RE.sub(repl, content)


def missing_manual_fill_placeholders(content: str, originals: list[str]) -> list[str]:
    """Return original tags whose «MFILL_N» placeholder is missing from content."""
    missing: list[str] = []
    for index, tag in enumerate(originals):
        if f"«MFILL_{index}»" not in content and tag not in content:
            missing.append(tag)
    return missing


def manual_fill_tags_preserved(before: str, after: str) -> bool:
    """True when every MANUAL FILL tag text from before still appears in after."""
    before_tags = [t.text for t in extract_manual_fill_tags(before)]
    if not before_tags:
        return True
    after_text = after or ""
    return all(tag in after_text for tag in before_tags)


def _user_supplied_value_for_tag(user_message: str, tag: ManualFillTag) -> str | None:
    """Extract an explicit value from the user message for this MANUAL FILL tag."""
    msg = (user_message or "").strip()
    if not msg:
        return None

    # "fill [MANUAL FILL: Title] with Director of Marketing"
    escaped = re.escape(tag.text)
    direct = re.search(
        rf"(?is){escaped}.{{0,40}}?(?:with|to|as|=|:)\s*[\"']?(.+?)[\"']?\s*$",
        msg,
    )
    if direct:
        value = direct.group(1).strip().rstrip(".")
        if (
            _is_plausible_fill_value(value)
            and value.casefold() not in tag.text.casefold()
        ):
            return value

    # Description keywords appear in message + a with/to/is value.
    desc = (tag.description or "").strip()
    desc_tokens = [
        t
        for t in re.findall(r"[A-Za-z]{3,}", desc)
        if t.casefold() not in {"sonja", "ella", "the", "and", "for", "from", "with"}
    ]
    if desc_tokens and any(tok.casefold() in msg.casefold() for tok in desc_tokens[:4]):
        # Scan the message with bracketed tags removed. Leftmost-match means the
        # ":" inside "[MANUAL FILL: Title]" would otherwise win over the real
        # "with", capturing "Title] with Director of Marketing".
        msg_wo_tags = MANUAL_FILL_TAG_RE.sub(" ", msg)
        with_match = _USER_WITH_VALUE_RE.search(msg_wo_tags)
        if with_match:
            value = with_match.group(1).strip().rstrip(".")
            if _is_plausible_fill_value(value):
                return value
        is_match = _USER_IS_VALUE_RE.search(msg_wo_tags)
        if is_match:
            value = is_match.group(1).strip().rstrip(".")
            if _is_plausible_fill_value(value) and len(value) < 200:
                return value

    # Single-tag shortcut: "the budget is $45,000" / "use $45,000" when only one tag.
    return None


def _kb_value_for_manual_fill(tag: ManualFillTag, blob: str) -> str | None:
    """Best-effort KB lookup for a MANUAL FILL description — never invents."""
    if not (blob or "").strip():
        return None
    field = (tag.description or tag.text).casefold()
    if any(k in field for k in ("fein", "ein", "tax id", "federal employer")):
        m = _FEIN_RE.search(blob)
        return m.group(0) if m else None
    if "email" in field or "e-mail" in field:
        emails = _EMAIL_RE.findall(blob)
        return next(
            (e for e in emails if "zo" in e.casefold() or "sonja" in e.casefold()),
            emails[0] if emails else None,
        )
    if any(k in field for k in ("phone", "telephone", "fax", "direct line")):
        phones = _PHONE_RE.findall(blob)
        return phones[0] if phones else None
    return None


def fill_manual_fill_tags(
    content: str,
    *,
    user_message: str = "",
    kb_blob: str = "",
) -> tuple[str, list[dict[str, str]], list[str]]:
    """Resolve MANUAL FILL tags from user text then KB. Never invents.

    Returns (updated_content, fill_log, remaining_tag_texts).
    fill_log entries: {"tag", "value", "source"} where source is user|kb.
    """
    tags = extract_manual_fill_tags(content)
    if not tags:
        return content, [], []

    fill_log: list[dict[str, str]] = []
    remaining: list[str] = []
    updated = content
    single_tag = len(tags) == 1

    for tag in reversed(tags):
        value: str | None = _user_supplied_value_for_tag(user_message, tag)
        source = "user"
        if not value and single_tag:
            # One tag in the excerpt/section: accept "with X" / "is X" / bare value-ish line.
            with_match = _USER_WITH_VALUE_RE.search(user_message or "")
            if with_match:
                candidate = with_match.group(1).strip().rstrip(".")
                if candidate and "[" not in candidate:
                    value = candidate
            if not value:
                is_match = _USER_IS_VALUE_RE.search(user_message or "")
                if is_match:
                    candidate = is_match.group(1).strip().rstrip(".")
                    if candidate and "[" not in candidate and len(candidate) < 200:
                        value = candidate
        if not value:
            value = _kb_value_for_manual_fill(tag, kb_blob)
            source = "kb"
        if not value:
            remaining.append(tag.text)
            continue
        updated = updated[: tag.start] + value + updated[tag.end :]
        fill_log.append({"tag": tag.text, "value": value, "source": source})
        logger.info(
            "MANUAL FILL resolved source=%s tag=%r value=%r",
            source,
            tag.text[:80],
            value[:80],
        )

    fill_log.reverse()
    remaining.reverse()
    still = [t.text for t in extract_manual_fill_tags(updated)]
    return updated, fill_log, still


_GAP_OWNER: dict[str, str] = {
    "insurance": "Sonja",
    "questionnaire": "Sonja",
    "budget": "Sonja",
    "workforce_data": "Ella",
    "references": "Ella",
    "requirement_coverage": "Sonja",
    "psa_acknowledgment": "Sonja",
}


def _owner_for_gap(gap: ComplianceGap) -> str:
    if gap.category == "references" and re.search(
        r"\bnew\s+jersey\b|\bNJ\b", gap.message, re.I
    ):
        return "Ella"
    return _GAP_OWNER.get(gap.category, "Sonja")


def _classify_tag(tag: str) -> Literal[
    "verify", "placeholder", "manual_fill", "compliance", "budget", "consistency", "other"
]:
    upper = tag.upper()
    if upper.startswith("[MANUAL FILL"):
        return "manual_fill"
    if upper.startswith("[VERIFY"):
        return "verify"
    if upper.startswith("[PLACEHOLDER") or upper.startswith("[INSERT") or upper.startswith("[TBD"):
        return "placeholder"
    return "other"


def _parse_owner_from_tag(tag: str) -> str | None:
    match = re.match(r"\[MANUAL\s+FILL:\s*([^—\-]+)", tag, re.I)
    if not match:
        return None
    name = match.group(1).strip()
    if name.lower().startswith("sonja"):
        return "Sonja"
    if name.lower().startswith("ella"):
        return "Ella"
    return name.split()[0] if name else None


def scan_tags_in_section(
    section_id: str,
    section_title: str,
    content: str,
    *,
    finalized: bool = False,
    kb_searched: bool = False,
) -> list[ManualFillFlag]:
    if not content.strip():
        return []

    flags: list[ManualFillFlag] = []
    patterns = (
        (MANUAL_FILL_TAG_RE, True),
        (VERIFY_TAG_RE, False),
        (PLACEHOLDER_TAG_RE, False),
        (GENERIC_VERIFY_RE, False),
    )
    for pattern, is_manual in patterns:
        for match in pattern.finditer(content):
            tag = match.group(0)
            flags.append(
                ManualFillFlag(
                    sectionId=section_id,
                    sectionTitle=section_title,
                    kind="manual_fill" if is_manual else _classify_tag(tag),
                    tag=tag,
                    highlightText=tag,
                    owner=_parse_owner_from_tag(tag),
                    finalized=finalized or is_manual,
                    kbSearched=kb_searched,
                )
            )
    return flags


def _owner_for_field(field: str) -> str:
    lower = field.casefold()
    if "reference" in lower and ("nj" in lower or "new jersey" in lower):
        return "Ella"
    if "reference" in lower:
        return "Ella"
    if any(k in lower for k in ("workforce", "diversity", "eeo", "female", "minority")):
        return "Ella"
    return "Sonja"


def convert_verify_tags_to_manual_fill(content: str) -> str:
    """Replace open VERIFY tags with owner-assigned MANUAL FILL handoff tags."""

    def repl(match: re.Match[str]) -> str:
        field = (match.group(1) or "confirm before submission").strip()
        owner = _owner_for_field(field)
        return f"[MANUAL FILL: {owner} — {field}]"

    content = VERIFY_TAG_RE.sub(repl, content)
    content = GENERIC_VERIFY_RE.sub("[MANUAL FILL: Sonja — confirm before submission]", content)
    return content


def apply_corpus_snippet_fills(
    draft: ProposalDraft,
    corpus: list,
) -> ProposalDraft:
    """Insert KB facts (FEIN, email, phone) into questionnaire sections when found in corpus."""
    from app.models.proposal import EvidenceItem

    items = [e for e in corpus if isinstance(e, EvidenceItem)]
    blob = "\n".join((e.excerpt or "")[:3000] for e in items[:100])
    if not blob.strip():
        return draft

    fein_match = _FEIN_RE.search(blob)
    fein = fein_match.group(0) if fein_match else None
    emails = _EMAIL_RE.findall(blob)
    email = next(
        (e for e in emails if "zo" in e.casefold() or "sonja" in e.casefold()),
        emails[0] if emails else None,
    )
    phones = _PHONE_RE.findall(blob)
    phone = phones[0] if phones else None

    updated_sections = []
    for section in draft.sections:
        content = section.content or ""
        title = (section.title or "").casefold()
        is_questionnaire = any(
            p in title
            for p in (
                "questionnaire",
                "vendor",
                "contractor",
                "offeror",
                "business entity",
                "administrative",
                "compliance and administrative",
            )
        )
        if is_questionnaire:
            if fein and fein not in content:
                content = VERIFY_TAG_RE.sub(
                    lambda m: fein
                    if any(k in m.group(0).casefold() for k in ("fein", "ein", "tax"))
                    else m.group(0),
                    content,
                )
                if fein not in content:
                    content = f"{content.rstrip()}\n\n**Federal EIN (FEIN):** {fein}"
            if email and email not in content:
                content = f"{content.rstrip()}\n\n**Primary business email:** {email}"
            if phone and phone not in content:
                content = f"{content.rstrip()}\n\n**Business phone:** {phone}"

        updated_sections.append(section.model_copy(update={"content": content}))

    now = draft.updated_at
    return draft.model_copy(update={"sections": updated_sections, "updated_at": now})


_PERCENT_RE = re.compile(r"\b(\d{1,2}(?:\.\d+)?)\s*%")
_DOLLAR_LIMIT_RE = re.compile(
    r"\$[\d,]+(?:\.\d+)?\s*(?:million|mil|M)\b|\$[\d,]+(?:\.\d+)?",
    re.I,
)


def _section_corpus_blob(corpus: list, section_id: str, *, max_items: int = 40) -> str:
    from app.models.proposal import EvidenceItem

    tagged = [
        e
        for e in corpus
        if isinstance(e, EvidenceItem) and section_id in (e.section_ids or [])
    ]
    pool = tagged if tagged else [e for e in corpus if isinstance(e, EvidenceItem)]
    return "\n".join((e.excerpt or "")[:3000] for e in pool[:max_items])


def _replace_verify_tags_from_blob(content: str, blob: str) -> tuple[str, int]:
    """Swap [VERIFY: …] tags for KB facts when the tag field clearly matches extracted data."""
    if not content.strip() or not VERIFY_TAG_RE.search(content):
        return content, 0

    fein = _FEIN_RE.search(blob)
    fein_val = fein.group(0) if fein else None
    emails = _EMAIL_RE.findall(blob)
    phones = _PHONE_RE.findall(blob)
    percents = [float(m.group(1)) for m in _PERCENT_RE.finditer(blob)]
    female_pct = next((p for p in percents if 0 < p <= 100), None)
    dollar_limits = [m.group(0) for m in _DOLLAR_LIMIT_RE.finditer(blob)]

    def email_for_field(field: str) -> str | None:
        names = _person_names_from_field(field)
        near = _value_near_name(blob, names, _EMAIL_RE)
        if near:
            return near
        lowered = field.casefold()
        for email in emails:
            local = email.split("@", 1)[0].casefold()
            if any(token in lowered for token in local.replace(".", " ").split()):
                return email
        return next(
            (e for e in emails if "zo" in e.casefold()),
            emails[0] if emails else None,
        )

    def phone_for_field(field: str) -> str | None:
        names = _person_names_from_field(field)
        near = _value_near_name(blob, names, _PHONE_RE)
        if near:
            return near
        person_key = _normalize_person_key(field)
        if person_key:
            for name in (person_key, person_key.split()[0]):
                idx = blob.casefold().find(name)
                if idx >= 0:
                    window = blob[max(0, idx - 120) : idx + 400]
                    near_phones = _PHONE_RE.findall(window)
                    if near_phones:
                        return near_phones[0]
        return phones[0] if phones else None

    fills = 0
    updated = content

    def sub_if_keywords(
        keywords: tuple[str, ...],
        value: str | None,
        text: str,
        *,
        field_resolver: object | None = None,
    ) -> tuple[str, int]:
        count = 0

        def repl(match: re.Match[str]) -> str:
            nonlocal count
            field = (match.group(1) or "").casefold()
            from app.services.evidence_trust.legal_attestation_gate import (
                is_locked_legal_verify_tag,
            )

            if is_locked_legal_verify_tag(match.group(1) or ""):
                return match.group(0)
            if not any(k in field for k in keywords):
                return match.group(0)
            replacement = value
            if callable(field_resolver):
                replacement = field_resolver(match.group(1) or "")
            if not replacement or replacement in text:
                return match.group(0)
            count += 1
            return replacement

        return VERIFY_TAG_RE.sub(repl, text), count

    updated, n = sub_if_keywords(
        ("fein", "ein", "tax id", "federal employer"), fein_val, updated
    )
    fills += n
    updated, n = sub_if_keywords(("email", "e-mail"), None, updated, field_resolver=email_for_field)
    fills += n
    updated, n = sub_if_keywords(
        ("phone", "telephone", "fax", "direct line", "line"),
        None,
        updated,
        field_resolver=phone_for_field,
    )
    fills += n
    updated, n = sub_if_keywords(
        ("female", "woman", "women", "diversity", "minority", "workforce", "eeo"),
        f"{female_pct:g}%" if female_pct is not None else None,
        updated,
    )
    fills += n

    if dollar_limits:

        def insurance_repl(match: re.Match[str]) -> str:
            nonlocal fills
            from app.services.evidence_trust.legal_attestation_gate import (
                is_locked_legal_verify_tag,
            )

            if is_locked_legal_verify_tag(match.group(1) or ""):
                return match.group(0)
            field = (match.group(1) or "").casefold()
            if any(k in field for k in ("insurance", "liability", "umbrella", "coverage", "limit")):
                fills += 1
                return dollar_limits[0]
            return match.group(0)

        updated = VERIFY_TAG_RE.sub(insurance_repl, updated)

    return updated, fills


def _normalize_person_key(name: str) -> str:
    return re.sub(r"[^a-z\s]", "", name.casefold()).strip()


def _person_names_from_field(field: str) -> list[str]:
    """Names to search for in KB text when a VERIFY field mentions a person."""
    lowered = field.casefold()
    names: list[str] = []
    if "sonja" in lowered:
        names.extend(["sonja m. anderson", "sonja m anderson", "sonja anderson", "sonja"])
    if "todd" in lowered:
        names.extend(["todd anderson", "todd"])
    if "ella" in lowered:
        names.extend(["ella", "ella curt"])
    if "curt" in lowered:
        names.extend(["curt", "ella curt"])
    first = _normalize_person_key(field).split()
    if first and first[0] not in {n.split()[0] for n in names}:
        names.append(first[0])
    return names


def _value_near_name(blob: str, names: list[str], pattern: re.Pattern[str]) -> str | None:
    blob_lower = blob.casefold()
    for name in names:
        idx = blob_lower.find(name.casefold())
        if idx < 0:
            continue
        window = blob[max(0, idx - 160) : idx + 600]
        matches = pattern.findall(window)
        if matches:
            return matches[0]
    return None


def apply_section_evidence_fills(
    section_id: str,
    section_title: str,
    content: str,
    corpus: list,
) -> tuple[str, int]:
    """Apply Supermemory corpus facts to VERIFY tags in one section — no LLM."""
    blob = _section_corpus_blob(corpus, section_id)
    if not blob.strip():
        return content, 0

    updated, fills = _replace_verify_tags_from_blob(content, blob)

    # Questionnaire-style append only when tag swap did not already place the fact.
    title = section_title.casefold()
    is_questionnaire = any(
        p in title
        for p in (
            "questionnaire",
            "vendor",
            "contractor",
            "offeror",
            "business entity",
            "administrative",
            "compliance and administrative",
        )
    )
    if is_questionnaire:
        fein = _FEIN_RE.search(blob)
        if fein and fein.group(0) not in updated:
            updated = f"{updated.rstrip()}\n\n**Federal EIN (FEIN):** {fein.group(0)}"
            fills += 1
        emails = _EMAIL_RE.findall(blob)
        email = next(
            (e for e in emails if "zo" in e.casefold() or "sonja" in e.casefold()),
            emails[0] if emails else None,
        )
        if email and email not in updated:
            updated = f"{updated.rstrip()}\n\n**Primary business email:** {email}"
            fills += 1

    return updated, fills


def apply_finalize_handoff_to_draft(
    draft: ProposalDraft,
    gaps: list[ComplianceGap],
) -> ProposalDraft:
    """Write MANUAL FILL handoff tags into the manuscript for gaps KB could not close."""
    from datetime import datetime, timezone

    gap_flags = gaps_to_manual_fill_flags(gaps, kb_searched=True, finalized=True)
    tags_by_section: dict[str, list[str]] = {}
    for gf in gap_flags:
        tags_by_section.setdefault(gf.section_id, []).append(gf.tag)

    updated_sections = []
    for section in draft.sections:
        content = convert_verify_tags_to_manual_fill(section.content or "")
        existing_lower = content.casefold()
        for tag in tags_by_section.get(section.id, []):
            tag_lower = tag.casefold()
            if tag_lower in existing_lower:
                continue
            # Skip if an equivalent MANUAL FILL for the same field already exists.
            field_hint = tag.split("—", 1)[-1].strip().casefold() if "—" in tag else ""
            if field_hint and field_hint in existing_lower:
                continue
            content = f"{content.rstrip()}\n\n{tag}"
            existing_lower = content.casefold()
        updated_sections.append(section.model_copy(update={"content": content}))

    return draft.model_copy(
        update={
            "sections": updated_sections,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def _sanitize_manual_fill_field(field: str) -> str:
    """Strip leaked code identifiers (dotted snake_case + tabs) from field text.

    A ComplianceGap's rfp_requirement / message sometimes carries a rule_id
    like 'deterministic.manuscript_locks.primary_contact_lock_is_ron_comer' —
    concatenating that into a MANUAL FILL tag produced the DuPage-class leak
    where the visible tag read
        [MANUAL FILL: Sonja, deterministic.manuscript_locks…\tPrimary contact…]
    Drop dotted-snake_case identifier runs and embedded tabs before use.
    """
    if not field:
        return field
    sanitized = re.sub(
        r"\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+[ \t]*",
        "",
        field,
        flags=re.I,
    )
    sanitized = sanitized.replace("\t", " ")
    sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
    return sanitized.strip(" ,.-—–")


def gaps_to_manual_fill_flags(
    gaps: list[ComplianceGap],
    *,
    kb_searched: bool = True,
    finalized: bool = True,
) -> list[ManualFillFlag]:
    flags: list[ManualFillFlag] = []
    for gap in gaps:
        owner = _owner_for_gap(gap)
        raw_field = (gap.rfp_requirement or gap.message)[:100].strip()
        field = _sanitize_manual_fill_field(raw_field) or "confirm before submission"
        tag = f"[MANUAL FILL: {owner} — {field}]"
        kind: Literal[
            "verify", "placeholder", "manual_fill", "compliance", "budget", "consistency", "other"
        ] = "compliance"
        if gap.category == "budget":
            kind = "budget"
        elif gap.category in ("references", "insurance", "questionnaire", "workforce_data"):
            kind = "compliance"

        flags.append(
            ManualFillFlag(
                sectionId=gap.section_id,
                sectionTitle=gap.section_title,
                kind=kind,
                tag=tag,
                highlightText=gap.excerpt[:120] if gap.excerpt else None,
                owner=owner,
                finalized=finalized,
                kbSearched=kb_searched,
            )
        )
    return flags


def _dedupe_flags(flags: list[ManualFillFlag]) -> list[ManualFillFlag]:
    seen: set[str] = set()
    out: list[ManualFillFlag] = []
    for flag in flags:
        key = f"{flag.section_id}::{flag.kind}::{flag.tag.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(flag)
    return out


def build_presubmit_manual_fill_flags(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    kb_searched: bool = False,
    finalized: bool = False,
) -> list[ManualFillFlag]:
    """Combine in-manuscript tags with unresolved compliance gaps."""
    flags: list[ManualFillFlag] = []

    for section in draft.sections:
        flags.extend(
            scan_tags_in_section(
                section.id,
                section.title,
                section.content or "",
                finalized=finalized,
                kb_searched=kb_searched,
            )
        )

    remaining_gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    gap_flags = gaps_to_manual_fill_flags(
        remaining_gaps, kb_searched=kb_searched, finalized=finalized
    )

    section_blob: dict[str, str] = {
        s.id: (s.content or "").casefold() for s in draft.sections
    }
    for gf in gap_flags:
        blob = section_blob.get(gf.section_id, "")
        if gf.tag.casefold() in blob:
            continue
        if MANUAL_FILL_TAG_RE.search(blob):
            continue
        flags.append(gf)

    return _dedupe_flags(flags)


def summarize_manual_fill_flags(flags: list[ManualFillFlag]) -> str:
    if not flags:
        return (
            "No manual fill-ins — KB + final editor resolved all submission gaps, "
            "or run Finalize gaps to produce owner-assigned flags."
        )
    manual = sum(1 for f in flags if f.kind == "manual_fill")
    verify = sum(1 for f in flags if f.kind == "verify")
    placeholder = sum(1 for f in flags if f.kind == "placeholder")
    compliance = sum(1 for f in flags if f.kind == "compliance")
    budget = sum(1 for f in flags if f.kind == "budget")
    finalized = sum(1 for f in flags if f.finalized)
    parts: list[str] = []
    if finalized:
        parts.append(f"{finalized} finalized for Sonja/Ella")
    if manual:
        parts.append(f"{manual} MANUAL FILL")
    if verify:
        parts.append(f"{verify} VERIFY")
    if placeholder:
        parts.append(f"{placeholder} PLACEHOLDER")
    if compliance:
        parts.append(f"{compliance} compliance")
    if budget:
        parts.append(f"{budget} budget")
    return "; ".join(parts) + " — complete before submission."
