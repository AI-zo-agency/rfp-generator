"""Consumer-facing provenance — source-backed only (no self-quotes)."""

from __future__ import annotations

import re
from typing import Any


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").casefold()).strip()


def _evidence_pool(pack: dict[str, Any]) -> list[dict[str, Any]]:
    pool: list[dict[str, Any]] = []
    for h in (pack.get("langextract") or {}).get("complianceHits") or []:
        if isinstance(h, dict):
            pool.append(h)
    for h in (pack.get("langextract") or {}).get("scopeHits") or []:
        if isinstance(h, dict):
            pool.append(h)
    for h in (pack.get("langextract") or {}).get("factHits") or []:
        if isinstance(h, dict):
            pool.append(h)
    for h in pack.get("obligationCandidates") or []:
        if isinstance(h, dict):
            pool.append({"text": h.get("text"), "page": h.get("page"), "sourceText": h.get("text")})
    for key, sec in (pack.get("sections") or {}).items():
        if isinstance(sec, dict) and sec.get("text"):
            pool.append(
                {
                    "text": str(sec.get("text"))[:800],
                    "sourceText": str(sec.get("text"))[:800],
                    "page": sec.get("anchorPage"),
                    "section": key,
                }
            )
    for key, sec in (pack.get("boundedSections") or {}).items():
        if isinstance(sec, dict) and sec.get("text"):
            pool.append(
                {
                    "text": str(sec.get("text"))[:1200],
                    "sourceText": str(sec.get("text"))[:1200],
                    "page": sec.get("anchorPage"),
                    "section": key,
                }
            )
    return pool


def _match_source(value: str, pack: dict[str, Any]) -> dict[str, Any] | None:
    """Find an evidence span that contains/overlaps value — never return value itself."""
    val = str(value or "").strip()
    if len(val) < 3:
        return None
    val_l = _norm(val)
    tokens = [t for t in re.findall(r"[a-z0-9]{4,}", val_l)][:8]
    codes = re.findall(r"\bPC\d{3}\b", val, re.I)
    best: dict[str, Any] | None = None
    best_score = 0
    for h in _evidence_pool(pack):
        t = str(h.get("sourceText") or h.get("text") or "").strip()
        if len(t) < 12:
            continue
        tl = _norm(t)
        # Reject if evidence is basically the normalized value alone
        if tl == val_l or (len(val_l) > 20 and tl == val_l[: len(tl)]):
            continue
        score = 0
        if codes and any(c.casefold() in tl for c in codes):
            score += 10
        if val_l[:40] in tl:
            score += 8
        hit_toks = sum(1 for tok in tokens if tok in tl)
        score += hit_toks
        if score > best_score:
            best_score = score
            best = h
    if best_score < 3:
        return None
    return best


def _row(path: str, value: Any, src: dict[str, Any], *, section: str = "") -> dict[str, Any] | None:
    text = str(src.get("sourceText") or src.get("text") or "").strip()
    if not text:
        return None
    # Hard rule: sourceText must not equal the normalized value
    if _norm(text) == _norm(str(value or "")):
        return None
    page = src.get("page")
    try:
        page_i = int(page) if page is not None and str(page).strip() != "" else None
    except (TypeError, ValueError):
        page_i = None
    return {
        "path": path,
        "value": value if not isinstance(value, str) else value[:240],
        "sourcePage": page_i,
        "sourceSection": str(section or src.get("section") or "")[:120],
        "sourceText": text[:500],
    }


def build_final_provenance(cleaned: dict[str, Any], pack: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    u = cleaned.get("understanding") or {}

    for path, value, section in (
        ("/understanding/client", u.get("client"), "Purpose / Background"),
        ("/understanding/memoryFacts/clientName", (u.get("memoryFacts") or {}).get("clientName"), "Key Information"),
    ):
        if not value:
            continue
        src = _match_source(str(value), pack)
        if src:
            row = _row(path, value, src, section=section)
            if row:
                rows.append(row)

    tl = u.get("timelineIntel") or {}
    for key in ("questionsDue", "quotesDue", "initialTermStart", "optionPeriods"):
        if not tl.get(key):
            continue
        src = _match_source(str(tl.get(key)), pack)
        if src:
            row = _row(f"/understanding/timelineIntel/{key}", tl.get(key), src, section="Key Dates")
            if row:
                rows.append(row)

    bi = u.get("budgetIntel") or {}
    pricing = bi.get("basePricingModel") or bi.get("pricingModelHint")
    if pricing:
        src = _match_source(str(pricing), pack) or _match_source("payment schedule", pack)
        if src:
            row = _row("/understanding/budgetIntel/pricingModel", pricing, src, section="Payment Schedule")
            if row:
                rows.append(row)

    items = (cleaned.get("compliance") or {}).get("items") or []
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("id") or "")
        req = str(it.get("requirement") or "")
        src = _match_source(req, pack)
        if not src:
            continue
        row = _row(
            f"/compliance/items/{cid}" if cid else "/compliance/items",
            req,
            src,
            section=str(it.get("targetSection") or ""),
        )
        if row:
            rows.append(row)

    scope = cleaned.get("scope") or {}
    for bucket in ("mandatory", "optional"):
        for i, line in enumerate((scope.get(bucket) or [])[:40]):
            src = _match_source(str(line), pack)
            if not src:
                continue
            row = _row(f"/scope/{bucket}/{i}", line, src, section="Statement of Work")
            if row:
                rows.append(row)
    if scope.get("notes"):
        src = _match_source("monthly", pack) or _match_source("quarterly", pack)
        if src:
            row = _row("/scope/notes", scope.get("notes"), src, section="Statement of Work")
            if row:
                rows.append(row)

    ev = cleaned.get("evaluation") or {}
    if ev.get("scoredResponseForm") is False:
        src = _match_source("evaluation", pack) or _match_source("technical merit", pack)
        if src:
            row = _row(
                "/evaluation/scoredResponseForm",
                False,
                src,
                section="Quote Evaluation",
            )
            if row:
                rows.append(row)
    for i, emph in enumerate(ev.get("emphasis") or []):
        src = _match_source(str(emph), pack)
        if not src:
            continue
        row = _row(f"/evaluation/emphasis/{i}", emph, src, section="Quote Evaluation")
        if row:
            rows.append(row)

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        p = str(row.get("path") or "")
        if p in seen:
            continue
        seen.add(p)
        out.append(row)
        if len(out) >= 100:
            break
    return out
