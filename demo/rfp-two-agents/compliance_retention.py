"""Candidate disposition ledger — never dump raw LX into final compliance.items.

Every LangExtract / lexical candidate gets a disposition.
Final customer-facing compliance.items stay Sonnet-normalized (+ tiny
normalized anchor fillers when a form/submittal anchor is missing).
"""

from __future__ import annotations

import re
from typing import Any

from opportunity_validators import is_conditional_compliance_item, is_post_award_compliance_item

_FORM_CODE = re.compile(r"\bPC\d{3}\b", re.I)

_ANCHOR_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pc600", ("pc600",)),
    ("pc601", ("pc601",)),
    ("pc610", ("pc610",)),
    ("pc620", ("pc620",)),
    ("payment schedule", ("payment schedule",)),
    ("draft agreement", ("draft agreement",)),
    ("sow confirmation", ("confirm", "statement of work")),
    ("references", ("three", "reference")),
    ("staffing", ("staffing schedule",)),
    ("subcontractor", ("subcontractor",)),
)

# Normalized one-liners only — never paste raw LX spans into final JSON.
_ANCHOR_TEMPLATES: dict[str, dict[str, Any]] = {
    "pc600": {
        "requirement": "Submit a completed and signed Offeror's Cover Page (PC600).",
        "mandatory": True,
        "targetSection": "Required Forms / Submittal Items",
    },
    "pc601": {
        "requirement": "Submit a completed and signed Representations and Certifications form (PC601).",
        "mandatory": True,
        "targetSection": "Required Forms / Submittal Items",
    },
    "pc610": {
        "requirement": "Submit a completed and signed Small-Local Business Self-Certification Form (PC610).",
        "mandatory": True,
        "targetSection": "Required Forms / Submittal Items",
    },
    "pc620": {
        "requirement": "Submit a completed and signed Nondisclosure Indemnification Agreement (PC620) if submitting confidential/proprietary information.",
        "mandatory": False,
        "targetSection": "Required Forms / Submittal Items",
    },
    "payment schedule": {
        "requirement": "Submit a completed Payment Schedule.",
        "mandatory": True,
        "targetSection": "Submittal Items - Cost/Price",
    },
    "draft agreement": {
        "requirement": "Confirm acceptance (YES/NO) of the County Draft Agreement, including insurance requirements.",
        "mandatory": True,
        "targetSection": "Submittal Items - Draft Agreement",
    },
    "sow confirmation": {
        "requirement": "Confirm agreement to the Exhibit A – Statement of Work (SOW) requirements as stated.",
        "mandatory": True,
        "targetSection": "Submittal Items - SOW",
    },
    "references": {
        "requirement": "Provide three (3) business references for the most relevant projects within the past five (5) years.",
        "mandatory": True,
        "targetSection": "Submittal Items - References",
    },
    "staffing": {
        "requirement": "Provide a staffing schedule describing all proposed program staff with titles, qualifications, and resumes.",
        "mandatory": True,
        "targetSection": "Submittal Items - Staffing",
    },
    "subcontractor": {
        "requirement": "If subcontractors or partners will be used, identify them and describe roles, qualifications, and monitoring.",
        "mandatory": False,
        "targetSection": "Submittal Items - Subcontractors",
    },
}

_SOW_ACTORS = (
    "contractor shall",
    "contractor will",
    "contractor must",
    "the contractor shall",
    "consultant shall",
)
_BIDDER_ACTORS = (
    "offeror",
    "bidder",
    "proposer",
    "submit",
    "provide a",
    "provide three",
    "pc6",
    "payment schedule",
    "confirm acceptance",
    "confirm your agreement",
    "staffing schedule",
)
_ADVISORY = ("advised", "encouraged", "recommended", "may wish", "it is recommended")
_BUYER = ("the county may", "county will", "county shall", "board of supervisors")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").casefold()).strip()


def _token_set(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", _norm(s)) if t}


def is_sow_performance_candidate(text: str) -> bool:
    t = _norm(text)
    if any(a in t for a in _SOW_ACTORS) and not any(b in t for b in ("offeror", "bidder", "submit a", "pc6")):
        return True
    if "domestic preferences" in t or "recovered materials" in t:
        return True
    return False


def is_bidder_response_candidate(text: str) -> bool:
    t = _norm(text)
    if is_sow_performance_candidate(text):
        return False
    if any(a in t for a in _BUYER) and not any(b in t for b in ("offeror", "bidder", "submit")):
        return False
    if _FORM_CODE.search(text):
        return True
    if any(a in t for a in _BIDDER_ACTORS):
        return True
    if is_post_award_compliance_item({"requirement": text, "targetSection": ""}):
        return True
    return False


def collect_bidder_obligation_candidates(pack: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(row: dict[str, Any], *, source: str) -> None:
        text = str(row.get("text") or row.get("sourceText") or "").strip()
        if len(text) < 12:
            return
        if not is_bidder_response_candidate(text):
            return
        key = _norm(text)[:160]
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "id": f"{source}-{len(out)}",
                "text": text[:900],
                "page": row.get("page"),
                "source": source,
                "attributes": row.get("attributes") or {},
            }
        )

    lx = pack.get("langextract") or {}
    for h in lx.get("complianceHits") or []:
        if isinstance(h, dict):
            add(h, source="langextract")
    for h in pack.get("obligationCandidates") or []:
        if isinstance(h, dict):
            add(h, source="lexical")
    for sec_key in ("submittal_items", "required_forms", "references", "qualifications", "staffing", "pricing"):
        sec = (pack.get("sections") or {}).get(sec_key) or {}
        text = str(sec.get("text") or "")
        if not text:
            continue
        for sent in re.split(r"(?<=[.:;])\s+|\n+", text):
            s = sent.strip()
            if len(s) < 15:
                continue
            add({"text": s, "page": sec.get("anchorPage")}, source=f"section:{sec_key}")
    return out


def candidate_represented(candidate: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    c_norm = _norm(candidate.get("text") or "")
    c_toks = _token_set(c_norm)
    codes = {c.casefold() for c in _FORM_CODE.findall(candidate.get("text") or "")}
    for it in items:
        if not isinstance(it, dict):
            continue
        r_norm = _norm(str(it.get("requirement") or ""))
        if not r_norm:
            continue
        for code in codes:
            if code in r_norm:
                return True
        if c_norm[:50] in r_norm or r_norm[:50] in c_norm:
            return True
        r_toks = _token_set(r_norm)
        if c_toks and r_toks:
            overlap = len(c_toks & r_toks) / max(1, min(len(c_toks), len(r_toks)))
            if overlap >= 0.55 and len(c_toks & r_toks) >= 4:
                return True
    return False


def matching_item_id(candidate: dict[str, Any], items: list[dict[str, Any]]) -> str | None:
    c_norm = _norm(candidate.get("text") or "")
    codes = {c.casefold() for c in _FORM_CODE.findall(candidate.get("text") or "")}
    for it in items:
        if not isinstance(it, dict):
            continue
        r_norm = _norm(str(it.get("requirement") or ""))
        for code in codes:
            if code in r_norm:
                return str(it.get("id") or "")
        if c_norm[:40] in r_norm or r_norm[:40] in c_norm:
            return str(it.get("id") or "")
    return None


def _anchor_hits_in_text(text: str) -> list[str]:
    t = _norm(text)
    found: list[str] = []
    for name, needles in _ANCHOR_SPECS:
        if name == "sow confirmation":
            if "confirm" in t and ("statement of work" in t or "sow" in t):
                found.append(name)
            continue
        if name == "references":
            if "reference" in t and ("three" in t or "3" in t or "business reference" in t):
                found.append(name)
            continue
        if any(n in t for n in needles):
            found.append(name)
    for code in _FORM_CODE.findall(text):
        found.append(code.casefold())
    return list(dict.fromkeys(found))


def _anchors_present_in_items(items: list[dict[str, Any]]) -> set[str]:
    blob = _norm(" ".join(str(i.get("requirement") or "") for i in items if isinstance(i, dict)))
    present: set[str] = set()
    for name, needles in _ANCHOR_SPECS:
        if name.startswith("pc") and name in blob:
            present.add(name)
            continue
        if name == "references" and "reference" in blob and ("three" in blob or "3" in blob):
            present.add(name)
            continue
        if name == "sow confirmation" and "confirm" in blob and ("sow" in blob or "statement of work" in blob):
            present.add(name)
            continue
        if any(n in blob for n in needles):
            present.add(name)
    for code in _FORM_CODE.findall(blob):
        present.add(code.casefold())
    return present


def dispose_candidates(
    cleaned: dict[str, Any],
    pack: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    """Build disposition ledger; fill missing anchors with normalized templates only."""
    fixes: list[str] = []
    compliance = cleaned.get("compliance")
    if not isinstance(compliance, dict):
        compliance = {"items": [], "confidence": 0.0}
        cleaned["compliance"] = compliance
    items: list[dict[str, Any]] = [
        i for i in (compliance.get("items") or []) if isinstance(i, dict)
    ]
    # Strip any prior raw LX dumps from a previous pipeline version
    before = len(items)
    items = [i for i in items if not str(i.get("id") or "").startswith("comp-lx-")]
    if len(items) < before:
        fixes.append(f"stripped_raw_lx_items:{before - len(items)}")

    dispositions: list[dict[str, Any]] = []
    all_lx = pack.get("langextract", {}).get("complianceHits") or []
    candidates = collect_bidder_obligation_candidates(pack)
    present = _anchors_present_in_items(items)
    needed: set[str] = set()

    for i, h in enumerate(all_lx):
        if not isinstance(h, dict):
            continue
        text = str(h.get("sourceText") or h.get("text") or "")
        cid = f"lx-{i}"
        needed.update(_anchor_hits_in_text(text))
        if is_sow_performance_candidate(text):
            dispositions.append(
                {
                    "candidateId": cid,
                    "disposition": "classified_as_scope",
                    "finalTarget": "/scope/mandatory",
                    "note": text[:100],
                }
            )
            continue
        if any(a in _norm(text) for a in _ADVISORY) and "must" not in _norm(text) and "shall" not in _norm(text):
            dispositions.append({"candidateId": cid, "disposition": "excluded_advisory"})
            continue
        if any(a in _norm(text) for a in _BUYER) and not is_bidder_response_candidate(text):
            dispositions.append({"candidateId": cid, "disposition": "excluded_buyer_action"})
            continue
        if not is_bidder_response_candidate(text):
            dispositions.append({"candidateId": cid, "disposition": "excluded_non_obligation"})
            continue
        mid = matching_item_id({"text": text}, items)
        if mid:
            dispositions.append(
                {"candidateId": cid, "disposition": f"merged_into:{mid}", "finalTarget": f"/compliance/items/{mid}"}
            )
            continue
        if candidate_represented({"text": text}, items):
            dispositions.append({"candidateId": cid, "disposition": "duplicate_of:sonnet"})
            continue
        post = is_post_award_compliance_item({"requirement": text, "targetSection": ""})
        if post:
            dispositions.append(
                {
                    "candidateId": cid,
                    "disposition": "classified_as_post_award",
                    "finalTarget": "/compliance/items",
                    "note": "awaiting normalized post-award item",
                }
            )
            continue
        dispositions.append(
            {
                "candidateId": cid,
                "disposition": "excluded_non_obligation",
                "note": "covered by Sonnet narrative or non-anchor; not dumped raw",
            }
        )

    for cand in candidates:
        needed.update(_anchor_hits_in_text(str(cand.get("text") or "")))

    # Normalized template fill for missing anchors only (never raw LX text)
    pack_blob = _norm(json_blob_for_anchor(pack))
    for anchor, tmpl in _ANCHOR_TEMPLATES.items():
        if anchor in present:
            continue
        evidence_has = (
            anchor in pack_blob
            or any(anchor in _norm(c.get("text") or "") for c in candidates)
            or any(
                anchor in _norm(str(h.get("text") or h.get("sourceText") or ""))
                for h in all_lx
                if isinstance(h, dict)
            )
        )
        if not evidence_has:
            continue
        needed.add(anchor)
        cid = f"comp-anchor-{anchor.replace(' ', '-')}"
        row = {
            "id": cid,
            "requirement": tmpl["requirement"],
            "mandatory": tmpl["mandatory"],
            "sourceRef": "",
            "targetSection": tmpl["targetSection"],
            "owner": "bidder",
            "status": "open",
        }
        if is_conditional_compliance_item(row) or is_post_award_compliance_item(row):
            row["mandatory"] = False
        items.append(row)
        present.add(anchor)
        dispositions.append(
            {
                "candidateId": f"anchor:{anchor}",
                "disposition": "retained_as_compliance",
                "finalTarget": f"/compliance/items/{cid}",
                "note": "normalized template for missing anchor",
            }
        )
        fixes.append(f"anchor_template:{anchor}")

    compliance["items"] = items
    stats = compute_retention_stats(candidates, items, dispositions, present, needed)
    # Store ledger on pack for debug/trace — not on customer compliance object long-term
    pack["_candidateDisposition"] = {"dispositions": dispositions[:300], "stats": stats}
    compliance.pop("dispositionLedger", None)
    if stats.get("missingAnchors"):
        fixes.append(f"retention_missing_anchors:{stats['missingAnchors']}")
    return fixes, stats


def json_blob_for_anchor(pack: dict[str, Any]) -> str:
    parts: list[str] = []
    for sec in (pack.get("sections") or {}).values():
        if isinstance(sec, dict):
            parts.append(str(sec.get("text") or ""))
    for h in (pack.get("langextract") or {}).get("complianceHits") or []:
        if isinstance(h, dict):
            parts.append(str(h.get("text") or ""))
    return "\n".join(parts)


def compute_retention_stats(
    candidates: list[dict[str, Any]],
    items: list[dict[str, Any]],
    dispositions: list[dict[str, Any]],
    present: set[str] | None = None,
    needed: set[str] | None = None,
) -> dict[str, Any]:
    present = present or _anchors_present_in_items(items)
    if needed is None:
        needed = set()
        for c in candidates:
            needed.update(_anchor_hits_in_text(str(c.get("text") or "")))
    missing = sorted(a for a in needed if a not in present)
    covered = len(needed) - len(missing)
    rate = (covered / len(needed)) if needed else 1.0
    return {
        "candidateCount": len(candidates),
        "bidderCandidateCount": len(candidates),
        "anchorNeeded": sorted(needed),
        "anchorPresent": sorted(present & needed) if needed else sorted(present),
        "represented": covered,
        "retentionRate": round(rate, 4),
        "missingAnchors": missing,
        "materializedCount": sum(
            1 for d in dispositions if d.get("disposition") == "retained_as_compliance"
        ),
        "mergedIntoSonnet": sum(
            1 for d in dispositions if str(d.get("disposition", "")).startswith("merged")
        ),
        "classifiedAsScope": sum(
            1 for d in dispositions if d.get("disposition") == "classified_as_scope"
        ),
    }


def retention_repair_triggers(stats: dict[str, Any]) -> list[str]:
    triggers: list[str] = []
    if stats.get("missingAnchors"):
        triggers.append("missing_anchor_forms:" + ",".join(stats["missingAnchors"][:8]))
    if float(stats.get("retentionRate") or 1) < 0.95 and stats.get("anchorNeeded"):
        triggers.append("compliance_retention_below_95")
    return triggers


# Back-compat alias used by older imports/tests
materialize_missing_compliance = dispose_candidates
