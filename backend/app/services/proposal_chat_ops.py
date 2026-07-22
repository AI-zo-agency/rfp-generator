"""Powerful proposal-chat ops: thorough duplicate audit + fabrication purge.

Pipeline for fabrication removal always runs: draft content → RFP facts → KB/ClientList.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.evidence_trust.claim_validator import validate_and_flag_section
from app.services.evidence_trust.client_list import ClientListRegistry
from app.services.evidence_trust.flags import verify_gap
from app.services.evidence_trust.load_client_list import load_client_list_registry
from app.services.proposal_fulfill_fabrication_guard import (
    portfolio_client_names,
    repair_fabricated_qualifications,
    section_has_invented_qual_content,
)
from app.services.proposal_kb_fact_checker import _dedupe_section3_case_studies
from app.services.proposal_section_quality import word_count
from app.services.proposal_voice_enforcement import is_duplicate_static_rfp_section

logger = logging.getLogger(__name__)

ChatOpKind = Literal[
    "none",
    "check_duplicates",
    "remove_duplicates",
    "remove_fabricated",
    "trust_audit",
]


@dataclass
class DuplicateFinding:
    kind: str
    section_a: str
    section_b: str
    detail: str
    severity: str = "warning"  # info | warning | critical


@dataclass
class ChatOpsReport:
    kind: ChatOpKind
    findings: list[DuplicateFinding] = field(default_factory=list)
    sections_changed: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    reply: str = ""


_DUPE_CHECK_RE = re.compile(
    r"\b("
    r"check|find|scan|audit|look\s+for|are\s+there|any|"
    r"spot|detect|review"
    r")\b.{0,40}\b(duplicat\w*|repeat\w*|redundant|same\s+(?:content|paragraph|section|case\s*stud))",
    re.I | re.S,
)
_DUPE_REMOVE_RE = re.compile(
    r"\b("
    r"remove|delete|strip|clean|dedupe|de-dupe|collapse|fix"
    r")\b.{0,40}\b(duplicat\w*|repeat\w*|redundant)",
    re.I | re.S,
)
_FAB_REMOVE_RE = re.compile(
    r"\b("
    r"remove|delete|strip|clean|purge|fix|scrub|kill|cut"
    r")\b.{0,60}\b("
    r"fabricat\w*|hallucinat\w*|invent\w*|made[\s-]?up|fake|"
    r"unverif\w*|not\s+in\s+(?:the\s+)?(?:kb|knowledge)|"
    r"confirm\s+clients?|wrong\s+(?:case\s*stud|client|claim)"
    r")",
    re.I | re.S,
)
_TRUST_AUDIT_RE = re.compile(
    r"\b("
    r"trust\s+audit|evidence\s+gate|clientlist|client\s+list|"
    r"check\s+(?:against\s+)?(?:kb|rfp|fabricat)|"
    r"verify\s+(?:facts|claims|against)"
    r")\b",
    re.I,
)

_PARA_SPLIT_RE = re.compile(r"\n\s*\n+")
_SENTENCE_OPENER_RE = re.compile(
    r"^((?:In|Across|Throughout|For|As)\s+(?:today'?s|the)\s+[^.!?]{20,120})",
    re.I,
)


def classify_chat_op(user_message: str) -> ChatOpKind:
    text = (user_message or "").strip()
    if not text:
        return "none"
    if _FAB_REMOVE_RE.search(text) or (
        re.search(r"\bfabricat", text, re.I)
        and re.search(r"\b(remove|clean|strip|purge|fix)\b", text, re.I)
    ):
        return "remove_fabricated"
    if _DUPE_REMOVE_RE.search(text):
        return "remove_duplicates"
    if _DUPE_CHECK_RE.search(text) or re.search(
        r"\bduplicat", text, re.I
    ):
        # bare "duplicates?" / "check for duplicates"
        if re.search(r"\b(remove|delete|strip|clean|dedupe)\b", text, re.I):
            return "remove_duplicates"
        return "check_duplicates"
    if _TRUST_AUDIT_RE.search(text):
        return "trust_audit"
    return "none"


def _norm_para(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip().casefold())
    t = re.sub(r"\[verify:[^\]]+\]", " ", t, flags=re.I)
    t = re.sub(r"\[flag:[^\]]+\]", " ", t, flags=re.I)
    return t.strip()


def _paragraphs(content: str) -> list[str]:
    raw = (content or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in _PARA_SPLIT_RE.split(raw) if p.strip()]
    # Also split very long single blocks by markdown headers
    out: list[str] = []
    for p in parts:
        if len(p) > 1200 and "\n#" in p:
            out.extend(x.strip() for x in re.split(r"\n(?=#)", p) if x.strip())
        else:
            out.append(p)
    return out


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Fast path: substantial containment
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 80 and shorter in longer:
        return 0.95
    return SequenceMatcher(None, a[:2000], b[:2000]).ratio()


def audit_duplicates(draft: ProposalDraft) -> list[DuplicateFinding]:
    """Thorough cross-section duplicate scan (structure + near-duplicate prose)."""
    findings: list[DuplicateFinding] = []
    sections = [s for s in draft.sections if (s.content or "").strip()]

    # 1) Duplicate static RFP tabs vs Sections 1–3
    for section in draft.sections:
        if is_duplicate_static_rfp_section(section.title or ""):
            findings.append(
                DuplicateFinding(
                    kind="static_rfp_overlap",
                    section_a=section.title or section.id,
                    section_b="Sections 1–3 (static)",
                    detail=(
                        f"RFP-mapped tab “{section.title}” overlaps zö static company/"
                        "team/experience sections — risk of duplicated narrative."
                    ),
                    severity="info",
                )
            )

    # 2) Duplicate Section 3 clients
    s3 = [
        s
        for s in draft.sections
        if s.id.startswith("section-3-work-") and not s.id.endswith("placeholder")
    ]
    seen_clients: dict[str, ProposalSection] = {}
    for section in s3:
        key = _client_key_from_section(section)
        if not key:
            continue
        prev = seen_clients.get(key)
        if prev:
            findings.append(
                DuplicateFinding(
                    kind="case_study_client",
                    section_a=prev.title or prev.id,
                    section_b=section.title or section.id,
                    detail=f"Same client key “{key}” appears in two Our Work cards.",
                    severity="critical",
                )
            )
        else:
            seen_clients[key] = section

    # 3) Duplicate bios (same person)
    bios = [
        s
        for s in draft.sections
        if s.id.startswith("section-2-bio-") and not s.id.endswith("placeholder")
    ]
    seen_bios: dict[str, ProposalSection] = {}
    for section in bios:
        key = re.sub(r"\W+", " ", (section.title or "").casefold()).strip()
        key = re.sub(r"^(bio|resume)\s*", "", key).strip()
        if len(key) < 3:
            continue
        prev = seen_bios.get(key)
        if prev:
            findings.append(
                DuplicateFinding(
                    kind="bio_person",
                    section_a=prev.title or prev.id,
                    section_b=section.title or section.id,
                    detail=f"Duplicate bio tab for “{key}”.",
                    severity="critical",
                )
            )
        else:
            seen_bios[key] = section

    # 4) Near-duplicate paragraphs across sections (thorough)
    indexed: list[tuple[ProposalSection, int, str, str]] = []
    for section in sections:
        for i, para in enumerate(_paragraphs(section.content or "")):
            norm = _norm_para(para)
            if len(norm) < 100:
                continue
            # Skip pure lists of VERIFY tags
            if norm.count("[verify") > 2 and word_count(para) < 40:
                continue
            indexed.append((section, i, para, norm))

    compared = 0
    for i in range(len(indexed)):
        sec_a, _ia, para_a, norm_a = indexed[i]
        for j in range(i + 1, len(indexed)):
            sec_b, _ib, para_b, norm_b = indexed[j]
            if sec_a.id == sec_b.id and abs(_ia - _ib) <= 1:
                continue
            compared += 1
            # Cap pairwise work for huge drafts
            if compared > 25_000:
                break
            # Length gate — skip wildly different lengths
            la, lb = len(norm_a), len(norm_b)
            if min(la, lb) / max(la, lb) < 0.55:
                continue
            ratio = _similarity(norm_a, norm_b)
            if ratio >= 0.86:
                findings.append(
                    DuplicateFinding(
                        kind="near_duplicate_prose",
                        section_a=sec_a.title or sec_a.id,
                        section_b=sec_b.title or sec_b.id,
                        detail=(
                            f"Near-duplicate prose ({ratio:.0%} similar): "
                            f"“{para_a[:120].strip()}…”"
                        ),
                        severity="warning" if ratio < 0.94 else "critical",
                    )
                )
        if compared > 25_000:
            break

    # 5) Repeated market-opener sentences reused across sections
    openers: dict[str, list[str]] = {}
    for section in sections:
        for para in _paragraphs(section.content or "")[:3]:
            m = _SENTENCE_OPENER_RE.match(para.strip())
            if not m:
                continue
            key = _norm_para(m.group(1))[:160]
            if len(key) < 40:
                continue
            openers.setdefault(key, []).append(section.title or section.id)
    for key, titles in openers.items():
        uniq = list(dict.fromkeys(titles))
        if len(uniq) >= 2:
            findings.append(
                DuplicateFinding(
                    kind="repeated_opener",
                    section_a=uniq[0],
                    section_b=", ".join(uniq[1:4]),
                    detail=f"Same market-opener reused: “{key[:100]}…”",
                    severity="warning",
                )
            )

    # Deduplicate findings by detail prefix
    seen_detail: set[str] = set()
    unique: list[DuplicateFinding] = []
    for f in findings:
        sig = f"{f.kind}|{f.section_a}|{f.section_b}|{f.detail[:80]}"
        if sig in seen_detail:
            continue
        seen_detail.add(sig)
        unique.append(f)
    return unique


def _client_key_from_section(section: ProposalSection) -> str:
    title = (section.title or "").strip()
    if "—" in title:
        title = title.split("—", 1)[1].strip()
    elif " - " in title:
        title = title.split(" - ", 1)[1].strip()
    title = re.sub(r"^[\d.]+\s*", "", title).strip()
    return re.sub(r"\W+", " ", title.casefold()).strip()


def apply_duplicate_removals(
    draft: ProposalDraft,
    findings: list[DuplicateFinding],
) -> tuple[ProposalDraft, list[str]]:
    """Remove clear duplicate case studies / bios; strip weaker near-dupe paragraphs."""
    logs: list[str] = []
    sections = list(draft.sections)

    # Section 3 client dedupe (reuse fact-checker helper)
    sections, removed_s3 = _dedupe_section3_case_studies(sections)
    if removed_s3:
        logs.append(f"Removed {removed_s3} duplicate Our Work card(s) for the same client.")

    # Bio dedupe — keep first
    keep_bio: dict[str, str] = {}
    drop_ids: set[str] = set()
    for section in sections:
        if not section.id.startswith("section-2-bio-") or section.id.endswith("placeholder"):
            continue
        key = re.sub(r"\W+", " ", (section.title or "").casefold()).strip()
        key = re.sub(r"^(bio|resume)\s*", "", key).strip()
        if len(key) < 3:
            continue
        if key in keep_bio:
            drop_ids.add(section.id)
            logs.append(f"Removed duplicate bio “{section.title}” (kept “{keep_bio[key]}”).")
        else:
            keep_bio[key] = section.title or section.id
    if drop_ids:
        sections = [s for s in sections if s.id not in drop_ids]

    # Near-duplicate prose: remove paragraph from the shorter section when critical
    critical_pairs = [
        f for f in findings if f.kind == "near_duplicate_prose" and f.severity == "critical"
    ]
    by_title = {(s.title or s.id): s for s in sections}
    for finding in critical_pairs[:20]:
        a = by_title.get(finding.section_a)
        b = by_title.get(finding.section_b)
        if not a or not b:
            continue
        # Prefer stripping from the section with more words overall (keep focused one)
        # Actually: strip the matching para from the longer manuscript section to reduce fluff
        target = a if word_count(a.content or "") >= word_count(b.content or "") else b
        other = b if target is a else a
        paras_t = _paragraphs(target.content or "")
        paras_o = {_norm_para(p) for p in _paragraphs(other.content or "")}
        kept: list[str] = []
        stripped = 0
        for p in paras_t:
            n = _norm_para(p)
            if len(n) >= 100 and any(_similarity(n, o) >= 0.90 for o in paras_o):
                stripped += 1
                continue
            kept.append(p)
        if stripped:
            new_body = "\n\n".join(kept)
            for i, s in enumerate(sections):
                if s.id == target.id:
                    sections[i] = s.model_copy(update={"content": new_body})
                    logs.append(
                        f"Stripped {stripped} duplicate paragraph(s) from “{target.title}” "
                        f"(also in “{other.title}”)."
                    )
                    break

    if not logs:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


_KNOWN_FAKE_MARKERS = (
    "queensland tourism",
    "tourism fiji",
    "south australian tourism commission",
    "travel oregon",
    "visit bend",
    "city of sisters",
    "jane.doe@",
    "john.smith@",
)


async def _kb_support_blob_for_section(
    section: ProposalSection,
    *,
    rfp: RfpRecord,
) -> str:
    """Best-effort KB fetch for fabrication checks."""
    try:
        from app.services import proposal_knowledge_base_tools

        queries = [
            f"zö agency {rfp.client} {section.title} 03_CS 01_ClientList verified facts",
            f"zö agency {(section.content or '')[:120]} case study reference",
        ]
        parts: list[str] = []
        for q in queries[:2]:
            text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
                query=q[:220],
                limit=6,
            )
            if text:
                parts.append(text[:8000])
        return "\n\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("KB support fetch failed for chat ops: %s", exc)
        return ""


async def remove_fabricated_content(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord,
    rfp_context: str,
    research: ProposalResearchCache | None,
    registry: ClientListRegistry | None = None,
) -> tuple[ProposalDraft, list[str], list[str]]:
    """Content → RFP → KB fabrication purge across the draft."""
    logs: list[str] = []
    human: list[str] = []

    if registry is None:
        try:
            registry = await load_client_list_registry()
        except Exception:
            registry = ClientListRegistry()

    # Pass 1: deterministic fabrication guard + ClientList claim validator
    draft, fab_logs, fab_human = repair_fabricated_qualifications(
        draft, research, registry=registry
    )
    logs.extend(fab_logs)
    human.extend(fab_human)

    portfolio = set(portfolio_client_names(draft))
    sections = list(draft.sections)
    rfp_cf = (rfp_context or "").casefold()
    changed = False

    for idx, section in enumerate(sections):
        body = section.content or ""
        if not body.strip():
            continue
        original = body

        # Pass 2: known fake markers → VERIFY
        cf = body.casefold()
        if any(m in cf for m in _KNOWN_FAKE_MARKERS):
            body = (
                verify_gap(
                    section.title or "section",
                    "fabricated client/contact markers removed; "
                    "re-fill only from ClientList Public=Yes + KB",
                )
                + "\n\n"
                + re.sub(
                    r"(?i).{0,80}(travel oregon|visit bend|city of sisters|"
                    r"queensland tourism|tourism fiji).{0,80}",
                    "[removed fabricated mention]",
                    body,
                )
            )
            logs.append(f"Purged known fabricated markers in “{section.title}”.")

        # Pass 3: ClientList / claim validator again on updated body
        if registry and registry.entries:
            body, report = validate_and_flag_section(
                body,
                registry=registry,
                slot=section.title or section.id,
                allowed_client_names=portfolio or None,
            )
            if report.notes:
                logs.append(
                    f"ClientList gate ({section.title}): " + "; ".join(report.notes[:3])
                )

        # Pass 4: eval % / dollar claims not in RFP hard facts → VERIFY hint
        if re.search(r"\b\d{1,3}\s*%", body) and "evaluat" in body.casefold():
            for m in re.finditer(r"\b(\d{1,3})\s*%", body):
                token = m.group(0)
                if token.casefold() not in rfp_cf and m.group(1) not in rfp_cf:
                    body = body.replace(
                        token,
                        f"[VERIFY: {token} — not found in RFP HARD FACTS]",
                        1,
                    )
                    logs.append(
                        f"Flagged eval percent {token} in “{section.title}” (not in RFP)."
                    )

        # Pass 5: KB cross-check for reference/experience sections with emails
        if (
            re.search(r"(?i)\breferences?\b", section.title or "")
            or section_has_invented_qual_content(body, list(portfolio))
        ):
            kb_blob = await _kb_support_blob_for_section(section, rfp=rfp)
            emails = re.findall(
                r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", body
            )
            unsupported = [
                e for e in emails if e.casefold() not in (kb_blob or "").casefold()
            ]
            if unsupported and len(unsupported) >= 1:
                body = (
                    verify_gap(
                        "references",
                        "contacts not found in KB after content→RFP→KB check; "
                        f"removed {len(unsupported)} unverified email(s)",
                    )
                    + "\n\n"
                    + re.sub(
                        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
                        "[VERIFY: email — not in KB]",
                        body,
                    )
                )
                logs.append(
                    f"KB gate cleared unverified emails in “{section.title}” "
                    f"({', '.join(unsupported[:3])})."
                )

        if body != original:
            sections[idx] = section.model_copy(update={"content": body})
            changed = True

    if changed:
        draft = draft.model_copy(update={"sections": sections})
    return draft, logs, human


def format_duplicate_report(findings: list[DuplicateFinding], *, acted: bool) -> str:
    if not findings:
        return (
            "**Duplicate check (thorough):** No structural or near-duplicate issues found "
            "across case studies, bios, RFP-overlap tabs, repeated openers, or cross-section prose."
        )
    critical = [f for f in findings if f.severity == "critical"]
    warning = [f for f in findings if f.severity == "warning"]
    info = [f for f in findings if f.severity == "info"]
    lines = [
        f"**Duplicate check (thorough):** found **{len(findings)}** issue(s) "
        f"({len(critical)} critical, {len(warning)} warning, {len(info)} info)."
    ]
    if acted:
        lines.append("_Removals applied where safe (duplicate cards / critical prose)._")
    for bucket, label in (
        (critical, "Critical"),
        (warning, "Warnings"),
        (info, "Info"),
    ):
        if not bucket:
            continue
        lines.append(f"\n### {label}")
        for f in bucket[:25]:
            lines.append(
                f"- **{f.kind}** — `{f.section_a}` ↔ `{f.section_b}`: {f.detail}"
            )
        if len(bucket) > 25:
            lines.append(f"- …and {len(bucket) - 25} more")
    if not acted and critical:
        lines.append(
            "\nSay **remove duplicates** and I’ll strip duplicate Our Work/bios and "
            "critical near-duplicate paragraphs."
        )
    return "\n".join(lines)


def format_fabrication_report(logs: list[str], human: list[str]) -> str:
    if not logs and not human:
        return (
            "**Fabrication purge (content → RFP → KB):** No invented clients, Confirm-only "
            "citations, or unverified reference contacts found."
        )
    lines = [
        "**Fabrication purge (content → RFP → ClientList → KB):**",
        "I walked every section, then checked RFP HARD FACTS, then ClientList/KB.",
    ]
    for item in (logs + human)[:40]:
        lines.append(f"- {item}")
    if len(logs) + len(human) > 40:
        lines.append(f"- …and {len(logs) + len(human) - 40} more notes")
    return "\n".join(lines)


async def run_chat_ops(
    *,
    kind: ChatOpKind,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_context: str,
    research: ProposalResearchCache | None,
) -> tuple[ProposalDraft, ChatOpsReport]:
    report = ChatOpsReport(kind=kind)
    if kind == "none":
        return draft, report

    findings = audit_duplicates(draft)
    report.findings = findings

    if kind == "check_duplicates":
        report.reply = format_duplicate_report(findings, acted=False)
        report.logs.append(f"Duplicate audit: {len(findings)} findings")
        return draft, report

    if kind == "remove_duplicates":
        draft, logs = apply_duplicate_removals(draft, findings)
        # Re-audit after removals
        findings_after = audit_duplicates(draft)
        report.findings = findings_after
        report.logs.extend(logs)
        report.sections_changed = [
            s.title or s.id
            for s in draft.sections
        ]  # coarse; reply focuses on logs
        report.reply = (
            format_duplicate_report(findings_after, acted=True)
            + ("\n\n### Actions taken\n" + "\n".join(f"- {x}" for x in logs) if logs else "")
        )
        return draft, report

    if kind in {"remove_fabricated", "trust_audit"}:
        registry = await load_client_list_registry()
        draft, logs, human = await remove_fabricated_content(
            draft,
            rfp=rfp,
            rfp_context=rfp_context,
            research=research,
            registry=registry,
        )
        report.logs.extend(logs)
        fab_reply = format_fabrication_report(logs, human)
        if kind == "trust_audit":
            dupe_reply = format_duplicate_report(findings, acted=False)
            report.reply = f"{fab_reply}\n\n---\n\n{dupe_reply}"
        else:
            # Still mention dupes if critical so the assistant feels thorough
            crit = [f for f in findings if f.severity == "critical"]
            if crit:
                fab_reply += (
                    f"\n\n_Also found {len(crit)} critical duplicate issue(s) — "
                    "say **remove duplicates** to clean those._"
                )
            report.reply = fab_reply
        return draft, report

    return draft, report
