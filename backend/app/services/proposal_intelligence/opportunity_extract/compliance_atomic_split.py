"""Deterministic split of bundled compliance rows into checkable items."""

from __future__ import annotations

import re
from typing import Any

# Fields commonly required per reference / project example (procurement forms).
_REFERENCE_ATOMS: tuple[str, ...] = (
    "organization name",
    "client or agency name",
    "address",
    "telephone number",
    "phone number",
    "email address",
    "contact name",
    "contact title",
    "title of contact",
    "relationship to offeror",
    "period of relationship",
    "description of work performed",
    "work performed",
    "summary of services",
    "contract start date",
    "contract end date",
    "project start date",
    "project end date",
    "key personnel",
    "role of key personnel",
    "specific role",
    "contract fee",
    "original contract fee",
    "contract term",
    "whether work completed within original fee",
    "whether work completed within original term",
    "fee increase",
    "delay",
    "problems encountered",
    "resolution",
    "objectives",
    "results",
    "applicability to current project",
)

_VERB_PREFIX = re.compile(
    r"^(.{0,120}?\b(?:shall|must|will)\s+(?:submit|provide|include|identify|describe|explain|list|complete|attach|certify|disclose|demonstrate))\s+",
    re.I,
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _reference_atoms_from_section(ref_text: str) -> list[str]:
    if not ref_text or len(ref_text) < 40:
        return []
    low = ref_text.casefold()
    found: list[str] = []
    for label in _REFERENCE_ATOMS:
        if label in low:
            found.append(label)
    if len(found) < 4:
        return []
    return [f"For each reference or project example, provide {label}." for label in found]


def _split_list_after_verb(requirement: str) -> list[str] | None:
    m = _VERB_PREFIX.match(requirement.strip())
    if not m:
        return None
    prefix = m.group(1).strip()
    rest = requirement[m.end() :].strip().rstrip(".")
    if rest.count(",") < 2 and "; " not in rest:
        return None
    chunks: list[str] = []
    if "; " in rest:
        parts = [p.strip() for p in rest.split(";") if p.strip()]
    else:
        parts = re.split(r",\s*(?:and\s+)?", rest)
    for part in parts:
        part = part.strip(" .;")
        if len(part) < 8:
            continue
        chunks.append(f"{prefix} {part}.")
    return chunks if len(chunks) >= 3 else None


def _split_lettered_subitems(requirement: str) -> list[str] | None:
    parts = re.split(r"\s+(?=\([a-z]\)\s+|\([a-z]\.\)\s+|[a-z]\.\s+|\d+\.\s+)", requirement.strip())
    if len(parts) < 3:
        return None
    head, *subs = parts
    head = head.strip()
    if len(head) < 15:
        return None
    out: list[str] = []
    for sub in subs:
        sub = re.sub(r"^\([a-z]\)\.?|^[a-z]\.|^\d+\.", "", sub.strip(), flags=re.I).strip()
        if len(sub) < 10:
            continue
        if _VERB_PREFIX.match(sub):
            out.append(sub if sub.endswith(".") else sub + ".")
        else:
            out.append(f"{head.rstrip('.')}: {sub}" if not sub.endswith(".") else f"{head.rstrip('.')}: {sub}")
    return out if len(out) >= 2 else None


_PROJECT_EXAMPLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("start/end", "For each project example, provide contract start/end dates."),
    ("start date", "For each project example, provide contract start/end dates."),
    ("address", "For each project example, provide agency/client address."),
    ("email", "For each project example, provide email address."),
    ("telephone", "For each project example, provide telephone number."),
    ("phone", "For each project example, provide telephone number."),
    ("description of the work", "For each project example, describe work performed."),
    ("work performed", "For each project example, describe work performed."),
    ("key personnel", "For each project example, identify key personnel."),
    ("role/responsibility", "For each project example, describe key personnel role/responsibility."),
    ("specific role", "For each project example, describe key personnel role/responsibility."),
)

_STAFFING_FIELDS: tuple[tuple[str, str], ...] = (
    ("position title", "Provide position title for each proposed staff member."),
    ("skills", "Provide skill requirements for each proposed staff position."),
    ("education", "Provide education requirements for each proposed staff position."),
    ("experience", "Provide experience requirements for each proposed staff position."),
    ("language", "Provide language fluency requirements where applicable."),
    ("resume", "Provide resumes sufficient to determine qualification for each proposed staff member."),
)


def _split_project_example_bundle(requirement: str) -> list[str] | None:
    low = requirement.casefold()
    if "for each project example" not in low and "project example" not in low:
        return None
    if requirement.count(",") < 2 and "•" not in requirement and " including " not in low:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for needle, line in _PROJECT_EXAMPLE_FIELDS:
        if needle in low and line not in seen:
            seen.add(line)
            out.append(line)
    return out if len(out) >= 3 else None


def _split_staffing_bundle(requirement: str) -> list[str] | None:
    low = requirement.casefold()
    if "staffing" not in low and "program staff" not in low:
        return None
    if len(requirement) < 80:
        return None
    out: list[str] = []
    for needle, line in _STAFFING_FIELDS:
        if needle in low:
            out.append(line)
    if "staffing schedule" in low or "provide a staffing" in low:
        out.insert(0, "Provide a staffing schedule describing all proposed program staff.")
    # dedupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq if len(uniq) >= 3 else None


def _split_comma_list_loose(requirement: str) -> list[str] | None:
    """Split 'include X, Y, and Z' / 'provide A, B, C' without requiring shall/must."""
    m = re.match(
        r"^(.{0,100}?\b(?:provide|include|identify|list|describe)\b)\s+(.+)$",
        requirement.strip(),
        re.I,
    )
    if not m:
        return None
    prefix = m.group(1).strip()
    rest = m.group(2).strip().rstrip(".")
    if rest.count(",") < 2:
        return None
    parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", rest)
    chunks = []
    for part in parts:
        part = part.strip(" .;")
        if len(part) < 8:
            continue
        chunks.append(f"{prefix} {part}.")
    return chunks if len(chunks) >= 3 else None


def _is_reference_bundle(requirement: str) -> bool:
    low = requirement.casefold()
    if "reference" not in low and "project example" not in low:
        return False
    return len(requirement) > 70 or requirement.count(",") >= 2 or "each reference" in low


def atomize_compliance_items(
    items: list[dict[str, Any]],
    pack: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split bundled requirements; preserve ids on first slice, new ids for rest."""
    pack = pack or {}
    ref_text = str(
        (pack.get("boundedSections") or {}).get("references", {}).get("text")
        or (pack.get("sections") or {}).get("references", {}).get("text")
        or (pack.get("sections") or {}).get("submittal_items", {}).get("text")
        or ""
    )
    ref_atoms = _reference_atoms_from_section(ref_text)
    out: list[dict[str, Any]] = []
    fixes: list[str] = []
    next_n = len(items) + 1

    for item in items:
        if not isinstance(item, dict):
            continue
        req = str(item.get("requirement") or "").strip()
        if not req:
            continue
        splits: list[str] | None = None
        if ref_atoms and _is_reference_bundle(req):
            splits = ref_atoms
            fixes.append(f"atomize:reference_fields({len(splits)})")
        elif ref_atoms and "reference" in req.casefold() and len(ref_atoms) >= 4:
            splits = ref_atoms
            fixes.append(f"atomize:reference_fields({len(splits)})")
        if splits is None:
            splits = _split_project_example_bundle(req)
            if splits:
                fixes.append(f"atomize:project_example({len(splits)})")
        if splits is None:
            splits = _split_staffing_bundle(req)
            if splits:
                fixes.append(f"atomize:staffing({len(splits)})")
        if splits is None:
            splits = _split_lettered_subitems(req)
            if splits:
                fixes.append("atomize:lettered")
        if splits is None:
            splits = _split_list_after_verb(req)
            if splits:
                fixes.append("atomize:verb_list")
        if splits is None:
            splits = _split_comma_list_loose(req)
            if splits:
                fixes.append("atomize:comma_list")
        if not splits or len(splits) <= 1:
            out.append(dict(item))
            continue
        base_id = str(item.get("id") or f"comp-{next_n}")
        for i, chunk in enumerate(splits):
            row = dict(item)
            row["requirement"] = chunk[:900]
            if i == 0:
                row["id"] = base_id
            else:
                row["id"] = f"{base_id}-{chr(97 + i)}" if i < 26 else f"{base_id}-s{i}"
            out.append(row)
        next_n += len(splits)

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in out:
        key = _norm(str(row.get("requirement") or ""))[:140]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped, fixes
