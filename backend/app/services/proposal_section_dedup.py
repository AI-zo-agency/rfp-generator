"""Cross-section anti-duplication — each section has one job; no manuscript rehash."""

from __future__ import annotations

import re
from typing import Any

from app.services.proposal_section_quality import word_count

ANTI_DUPLICATION_RULES = """## ANTI-DUPLICATION (mandatory — ZERO repetition)

Each section is INDEPENDENT and has ONE job. Do NOT re-explain material that belongs elsewhere.
If the ALREADY COVERED digests below list a fact, do not restate it — even paraphrased.

OWNED BY STATIC SECTIONS (mention once with a short pointer, never re-write):
- Company identity / Who We Are / Our Promise → Section 1.1
- Org roster, FEIN, address, certifications, insurance → Section 1.2–1.5
- Full team bios and titles → Section 2
- Full case studies with Challenge / What We Did / Outcome → Section 3
- Offeror / Vendor / Company Identification forms → short FIELD table + pointer to 1.3 only
  (never a second Business Information essay)

OWNED BY RFP TABS (write only the part THIS tab scores):
- Understanding / Opportunity → client goals, constraints, audiences — NOT company bio
- Methodology / Approach → process steps for THIS scope — NOT case studies or Who We Are
- Timeline / Schedule → phases and dates — NOT methodology paragraphs again
- Budget / Fees → compensation model and transparency — NOT approach restatement
- References → contacts only — NOT experience narratives
- Cover letter / transmittal → short offer letter; if RFP requires a physically signed
  attachment, include [DESIGNER NOTE: attach signed PDF] (do not invent signature dates
  or claim it is attached unless it is)

RULES:
1. If a fact already appears in a prior section digests block below, do NOT paste it again.
2. One brief cross-reference is OK ("As detailed in Section 3.1…") then ADD new detail only.
3. Prefer concise paragraphs over repeating brand story, MWBE status, or office locations.
4. Case study names: at most one short proof sentence outside Section 3 — NEVER paste Challenge /
   Solution / Results blocks, client quotes, or metric lists that already appear in Section 3.
5. Past performance / references tabs: use a summary TABLE plus 2–3 sentences per project — NOT
   full case-study rewrites. Point readers to Section 3 for narrative detail. NEVER reuse the
   same email (e.g. sonja@zo.agency) for three different references — distinct KB contacts only
   or [VERIFY: reference contact].
6. Each KB case study may appear IN FULL exactly once (its Section 3 tab only).
7. Cut filler openers ("We are excited…", "As a full-service agency…") when Section 1 already covers identity.
8. Stay within wordTarget — denser beats longer when facts would otherwise repeat.
9. Do NOT create near-duplicate RFP tabs that restate the same proof already covered by
   Sections 1–3 or another scored tab. One section, one job, then stop.
10. Evaluators skim — hit the scored asks, then stop. Prefer tables and short bullets over essay
    padding. Never invent length with filler when the RFP ask is already covered.
11. ZERO REPETITION: never restate founding year, FEIN, ownership, certs, insurance limits,
    team bios, or case narratives that another section already owns.
12. Primary contact: use ONLY the locked primary from manuscript locks everywhere — never name
    a second person (e.g. Haley Neff) as dedicated primary when Ron Comer is locked.
13. Schedule must fit the RFP award→launch window; never invent dates, durations, or a
    sequential multi-month plan that overruns a short award→launch window without stating
    concurrent/post-launch work. If a figure is not in the RFP or KB, use [VERIFY] — never invent.
"""


def digest_section_for_dedup(
    title: str,
    content: str,
    *,
    max_chars: int = 900,
) -> str:
    """Compact digest of what a section already covers (for other section prompts)."""
    text = re.sub(r"\s+", " ", (content or "").strip())
    if not text:
        return ""
    headings = re.findall(r"^#{1,3}\s+(.+)$", content or "", re.M)
    head_bit = ""
    if headings:
        head_bit = " | headings: " + "; ".join(h.strip()[:60] for h in headings[:6])
    excerpt = text[:max_chars]
    if len(text) > max_chars:
        excerpt = excerpt.rsplit(" ", 1)[0] + "…"
    words = word_count(content or "")
    return f"- **{title}** ({words}w){head_bit}: {excerpt}"


def format_prior_sections_block(
    prior_sections: list[dict[str, Any]] | list[Any],
    *,
    exclude_ids: set[str] | None = None,
    max_sections: int = 24,
    max_chars_each: int = 900,
) -> str:
    """Build 'already covered' digests so the LLM does not rehash other tabs."""
    exclude = exclude_ids or set()
    candidates: list[tuple[str, str, str]] = []
    for section in prior_sections:
        if hasattr(section, "model_dump"):
            data = section.model_dump(by_alias=True)
        elif isinstance(section, dict):
            data = section
        else:
            continue
        sid = str(data.get("id") or data.get("sectionId") or "")
        if sid in exclude:
            continue
        title = str(data.get("title") or sid)
        content = str(data.get("content") or "").strip()
        if not content:
            continue
        candidates.append((sid, title, content))

    # Prefer the most recent / later outline sections when over the cap so
    # Approach/Methodology digests aren't dropped while early stubs remain.
    if len(candidates) > max_sections:
        candidates = candidates[-max_sections:]

    digests = [
        digest_section_for_dedup(title, content, max_chars=max_chars_each)
        for _sid, title, content in candidates
    ]
    if not digests:
        return ""
    return (
        "## ALREADY COVERED IN OTHER SECTIONS (do not repeat — add NEW detail only)\n"
        + "\n".join(digests)
    )


def format_anti_duplication_rules() -> str:
    return ANTI_DUPLICATION_RULES


def _client_key_from_title(title: str) -> str:
    raw = (title or "").strip()
    if "—" in raw:
        raw = raw.split("—", 1)[1].strip()
    elif " - " in raw:
        raw = raw.split(" - ", 1)[1].strip()
    raw = re.sub(r"^[\d.]+\s*", "", raw).strip()
    tokens = [t for t in re.split(r"\W+", raw.casefold()) if len(t) >= 4]
    generic = {
        "city",
        "county",
        "state",
        "digital",
        "campaign",
        "department",
        "employment",
        "brewery",
        "case",
        "study",
    }
    for t in tokens:
        if t not in generic:
            return t
    return tokens[0] if tokens else ""


def _distinctive_paragraphs(content: str, *, min_len: int = 60) -> list[str]:
    paras: list[str] = []
    for block in re.split(r"\n\s*\n", content or ""):
        text = re.sub(r"\s+", " ", block.strip())
        if len(text) >= min_len and not text.startswith("[VERIFY"):
            paras.append(text)
    if not paras:
        for line in (content or "").splitlines():
            text = re.sub(r"\s+", " ", line.strip())
            if len(text) >= min_len and not text.startswith("#"):
                paras.append(text)
    return paras


def _plain_for_match(text: str) -> str:
    return re.sub(r"\*+", "", text).strip()


def compress_duplicate_case_study_sections(
    sections: list[Any],
) -> tuple[list[Any], int]:
    """Replace full case-study rewrites outside Section 3 with short pointers."""
    from app.models.proposal import ProposalSection

    s3_cards: list[tuple[str, str, str, list[str]]] = []
    for section in sections:
        if not isinstance(section, ProposalSection):
            continue
        if not section.id.startswith("section-3-work-") or section.id.endswith("placeholder"):
            continue
        key = _client_key_from_title(section.title or "")
        if not key:
            continue
        paras = _distinctive_paragraphs(section.content or "")
        s3_cards.append((key, section.id, section.title or "", paras))

    if not s3_cards:
        return sections, 0

    compressed = 0
    out: list[Any] = []
    for section in sections:
        if not isinstance(section, ProposalSection):
            out.append(section)
            continue
        if section.id.startswith("section-3-work-"):
            out.append(section)
            continue
        body = section.content or ""
        if not body.strip():
            out.append(section)
            continue

        new_body = body
        for key, sid, stitle, paras in s3_cards:
            plain_paras = [_plain_for_match(p) for p in paras]
            hits = sum(
                1
                for p in plain_paras
                if p and (p[:120] in new_body or p in new_body)
            )
            label = stitle.split("—", 1)[-1].strip() if "—" in stitle else stitle
            pointer = (
                f"See **{stitle}** in Our Work for the full case narrative "
                f"(Challenge, approach, and results)."
            )
            pattern = re.compile(
                rf"(^|\n)(#{{1,3}}\s+[^\n]*(?:{re.escape(label[:24])}|{re.escape(key)})[^\n]*\n)"
                rf"([\s\S]*?)(?=\n#{{1,3}}\s|\Z)",
                re.I,
            )
            if pattern.search(new_body):
                new_body = pattern.sub(rf"\1\2{pointer}\n\n", new_body, count=1)
                compressed += 1
                continue
            if hits < 1 and not any(p[:60] in new_body for p in plain_paras if p):
                continue
            for p in plain_paras:
                if p and p in new_body:
                    new_body = new_body.replace(p, pointer, 1)
                    compressed += 1
                    break

        if new_body != body:
            out.append(section.model_copy(update={"content": new_body.strip()}))
        else:
            out.append(section)

    return out, compressed


_CONTENT_STOPWORDS = {
    "that",
    "this",
    "with",
    "from",
    "have",
    "been",
    "were",
    "their",
    "about",
    "which",
    "would",
    "could",
    "should",
    "agency",
    "marketing",
    "social",
    "media",
    "through",
    "across",
    "client",
    "clients",
    "our",
    "and",
    "the",
    "for",
}


def _is_static_cq_section_id(section_id: str) -> bool:
    sid = section_id or ""
    if sid.startswith("section-1-"):
        return True
    if sid.startswith("section-2-bio-") and sid != "section-2-bio-placeholder":
        return True
    if sid.startswith("section-3-work-") and not sid.endswith("placeholder"):
        return True
    return False


def _is_protected_budget_section(section: Any) -> bool:
    """Budget / Pricing tabs must never be deleted by scan/senior-editor dedupe.

    Protects known fee-tab ids and high-score dedicated titles. Pair with
    ``find_budget_section_index`` so the live canon fee tab is always excluded
    even when its title scores mid (e.g. bare ``Pricing`` = 3).
    """
    sid = _section_id(section).casefold()
    if sid in {"section-budget-pricing", "section-budget", "section-pricing"}:
        return True
    if "budget" in sid and "pricing" in sid:
        return True
    title = _section_title(section)
    try:
        from app.services.proposal_budget_content import budget_section_score

        if budget_section_score(title) >= 4:
            return True
        # Short dedicated fee-tab titles that find_budget_section_index prefers.
        return title.casefold().strip() in {
            "budget",
            "pricing",
            "fees",
            "fee schedule",
            "cost proposal",
            "budget & pricing",
            "budget and pricing",
        }
    except Exception:  # noqa: BLE001
        t = title.casefold()
        return ("budget" in t and "pricing" in t) or t.strip() in {
            "budget",
            "pricing",
            "budget & pricing",
            "cost proposal",
            "fee schedule",
        }


def _canonical_budget_section_id(sections: list[Any]) -> str | None:
    """Id of the live Budget/Pricing write target, if present."""
    try:
        from app.services.proposal_budget_content import find_budget_section_index

        typed = [s for s in sections if hasattr(s, "title")]
        if not typed:
            return None
        idx = find_budget_section_index(typed)  # type: ignore[arg-type]
        if idx is None:
            return None
        return _section_id(sections[idx]) or None
    except Exception:  # noqa: BLE001
        return None


def _content_token_set(content: str) -> set[str]:
    tokens = re.findall(r"[a-z]{4,}", (content or "").casefold())
    return {t for t in tokens if t not in _CONTENT_STOPWORDS}


def _content_jaccard(a: str, b: str) -> float:
    ta, tb = _content_token_set(a), _content_token_set(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _content_coverage(needle: str, haystack: str) -> float:
    """Fraction of needle tokens that also appear in haystack (0–1)."""
    tn, th = _content_token_set(needle), _content_token_set(haystack)
    if not tn or not th:
        return 0.0
    return len(tn & th) / len(tn)


def _section_eval_points(section: Any) -> float:
    for attr, key in (("evaluation_weight", "evaluationWeight"), ("points", "points")):
        if hasattr(section, attr):
            value = getattr(section, attr)
        elif isinstance(section, dict):
            value = section.get(key)
            if value is None:
                value = section.get(attr)
        else:
            value = None
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _section_title(section: Any) -> str:
    if hasattr(section, "title"):
        return str(section.title or "")
    if isinstance(section, dict):
        return str(section.get("title") or "")
    return ""


def _section_id(section: Any) -> str:
    if hasattr(section, "id"):
        return str(section.id or "")
    if isinstance(section, dict):
        return str(section.get("id") or "")
    return ""


def _section_content(section: Any) -> str:
    if hasattr(section, "content"):
        return str(section.content or "")
    if isinstance(section, dict):
        return str(section.get("content") or "")
    return ""


def _prefer_drop_b(
    *,
    pts_a: float,
    pts_b: float,
    idx_a: int,
    idx_b: int,
    wc_a: int,
    wc_b: int,
) -> bool:
    """True → drop B. Prefer scored tab, then shorter (dedicated), then earlier."""
    if pts_b > pts_a:
        return False
    if pts_a > pts_b:
        return True
    # Same score: keep the shorter dedicated answer when one is a mega restate.
    if wc_a != wc_b and max(wc_a, wc_b) >= 250 and min(wc_a, wc_b) > 0:
        if wc_b > wc_a * 1.35:
            return True
        if wc_a > wc_b * 1.35:
            return False
    return idx_b >= idx_a


def _is_protected_scan_section(section: Any) -> bool:
    """Tabs Complete Scan must never delete — scored, closing, forms, references."""
    sid = _section_id(section).casefold()
    if _is_static_cq_section_id(sid):
        return True
    if _is_protected_budget_section(section):
        return True
    if _section_eval_points(section) > 0:
        return True
    title_cf = _section_title(section).casefold()
    protected_title_hints = (
        "reference",
        "required form",
        "attachment",
        "submission",
        "compliance",
        "experience",
        "qualification",
        "capability",
        "past performance",
        "cover letter",
        "transmittal",
        "certification",
        "acknowledg",
        "insurance",
        "closing",
        "offeror",
        "transportation",
        "public sector",
        "regional",
        "five-year",
        "5-year",
        "proposal instruction",
    )
    if any(h in title_cf for h in protected_title_hints):
        return True
    if sid.startswith(("rfp-closing-", "rfp-req-", "rfp-sec-", "ledger-comp-")):
        return True
    return False


def collapse_title_near_duplicate_sections(
    sections: list[Any],
) -> tuple[list[Any], list[str]]:
    """Keep one tab when titles are the same ask — including protected References twins.

    Scan historically skipped any title containing 'reference' / 'experience', so
    'References' and 'References & Past Performance' both survived and the writer
    restated the same examples in each. Title-near-dup collapse is the exception.
    """
    from app.services.proposal_outline_dedup import (
        normalize_outline_title,
        outline_titles_near_duplicate,
    )

    drop_ids: set[str] = set()
    dropped_labels: list[str] = []
    indexed = [
        (idx, section)
        for idx, section in enumerate(sections)
        if _section_id(section) and not _is_static_cq_section_id(_section_id(section))
    ]
    canon_budget_id = _canonical_budget_section_id(sections)

    for i, (idx_a, sec_a) in enumerate(indexed):
        sid_a = _section_id(sec_a)
        if sid_a in drop_ids or sid_a == canon_budget_id or _is_protected_budget_section(sec_a):
            continue
        title_a = _section_title(sec_a)
        for idx_b, sec_b in indexed[i + 1 :]:
            sid_b = _section_id(sec_b)
            if sid_b in drop_ids or sid_b == canon_budget_id or _is_protected_budget_section(sec_b):
                continue
            title_b = _section_title(sec_b)
            if not outline_titles_near_duplicate(title_a, title_b, threshold=0.55):
                continue
            pts_a = _section_eval_points(sec_a)
            pts_b = _section_eval_points(sec_b)
            wc_a = word_count(_section_content(sec_a))
            wc_b = word_count(_section_content(sec_b))
            drop_b = _prefer_drop_b(
                pts_a=pts_a,
                pts_b=pts_b,
                idx_a=idx_a,
                idx_b=idx_b,
                wc_a=wc_a,
                wc_b=wc_b,
            )
            # Prefer the fuller RFP-phrased title when scores/length tie-break is weak.
            if wc_a == wc_b and pts_a == pts_b:
                drop_b = len(normalize_outline_title(title_b)) <= len(
                    normalize_outline_title(title_a)
                )
            if drop_b:
                drop_ids.add(sid_b)
                dropped_labels.append(
                    f"{title_b} (title near-duplicate of {title_a})"
                )
            else:
                drop_ids.add(sid_a)
                dropped_labels.append(
                    f"{title_a} (title near-duplicate of {title_b})"
                )
                break

    if not drop_ids:
        return sections, []
    kept = [s for s in sections if _section_id(s) not in drop_ids]
    return kept, dropped_labels


def prune_near_duplicate_sections(
    sections: list[Any],
    *,
    content_jaccard_threshold: float = 0.42,
    containment_threshold: float = 0.72,
) -> tuple[list[Any], list[str]]:
    """Delete near-duplicate tabs discovered from the manuscript itself.

    Detection is algorithmic only:
    - outline title similarity
    - content Jaccard overlap
    - one body largely containing another (restated child inside a longer parent)

    Never deletes Section 1–3 CQ cards. No keyword allow/deny lists.
    """
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate
    from app.services.proposal_section_quality import word_count

    drop_ids: set[str] = set()
    dropped_labels: list[str] = []
    canon_budget_id = _canonical_budget_section_id(sections)
    candidates: list[tuple[int, Any]] = []
    for idx, section in enumerate(sections):
        sid = _section_id(section)
        body = _section_content(section).strip()
        if not sid or _is_static_cq_section_id(sid):
            continue
        if sid == canon_budget_id or _is_protected_budget_section(section):
            continue
        if _is_protected_scan_section(section):
            continue
        if not body:
            continue
        if word_count(body) < 40:
            continue
        candidates.append((idx, section))

    for i, (idx_a, sec_a) in enumerate(candidates):
        sid_a = _section_id(sec_a)
        if sid_a in drop_ids:
            continue
        title_a = _section_title(sec_a)
        body_a = _section_content(sec_a)
        pts_a = _section_eval_points(sec_a)
        wc_a = word_count(body_a)
        for idx_b, sec_b in candidates[i + 1 :]:
            sid_b = _section_id(sec_b)
            if sid_b in drop_ids:
                continue
            # Never delete (or pair-delete against) the Budget / Pricing tab.
            if (
                sid_a == canon_budget_id
                or sid_b == canon_budget_id
                or _is_protected_budget_section(sec_a)
                or _is_protected_budget_section(sec_b)
                or _is_protected_scan_section(sec_a)
                or _is_protected_scan_section(sec_b)
            ):
                continue
            title_b = _section_title(sec_b)
            body_b = _section_content(sec_b)
            pts_b = _section_eval_points(sec_b)
            wc_b = word_count(body_b)

            title_dup = outline_titles_near_duplicate(title_a, title_b, threshold=0.55)
            jaccard = _content_jaccard(body_a, body_b)
            # Structural near-copy (same clauses restated) — no topic keywords.
            from difflib import SequenceMatcher

            seq_ratio = SequenceMatcher(
                None,
                body_a.casefold()[:5000],
                body_b.casefold()[:5000],
            ).ratio()
            content_dup = jaccard >= content_jaccard_threshold or seq_ratio >= 0.72
            # Shorter section is mostly restated inside the longer one.
            cover_a_in_b = _content_coverage(body_a, body_b)
            cover_b_in_a = _content_coverage(body_b, body_a)
            containment = (
                (wc_a >= 60 and wc_b >= 60)
                and (
                    (cover_a_in_b >= containment_threshold and wc_b >= wc_a)
                    or (cover_b_in_a >= containment_threshold and wc_a >= wc_b)
                )
            )
            if not title_dup and not content_dup and not containment:
                continue

            drop_b = _prefer_drop_b(
                pts_a=pts_a,
                pts_b=pts_b,
                idx_a=idx_a,
                idx_b=idx_b,
                wc_a=wc_a,
                wc_b=wc_b,
            )
            if drop_b:
                if (
                    sid_b == canon_budget_id
                    or _is_protected_budget_section(sec_b)
                    or _is_protected_scan_section(sec_b)
                ):
                    continue
                drop_ids.add(sid_b)
                reason = (
                    "title near-duplicate"
                    if title_dup
                    else ("content overlap" if content_dup else "contained restatement")
                )
                dropped_labels.append(f"{title_b} ({reason} of {title_a})")
            else:
                if (
                    sid_a == canon_budget_id
                    or _is_protected_budget_section(sec_a)
                    or _is_protected_scan_section(sec_a)
                ):
                    continue
                drop_ids.add(sid_a)
                reason = (
                    "title near-duplicate"
                    if title_dup
                    else ("content overlap" if content_dup else "contained restatement")
                )
                dropped_labels.append(f"{title_a} ({reason} of {title_b})")
                break

    if not drop_ids:
        return sections, []

    kept = [s for s in sections if _section_id(s) not in drop_ids]
    return kept, dropped_labels


def _sibling_titles_embedded(parent_body: str, sibling_titles: list[str]) -> list[str]:
    """Return sibling titles that are restated as headings / long phrases in parent."""
    from app.services.proposal_outline_dedup import normalize_outline_title, outline_title_tokens

    body_cf = (parent_body or "").casefold()
    hits: list[str] = []
    for title in sibling_titles:
        core = normalize_outline_title(title)
        if len(core) < 10:
            continue
        if core in body_cf or core[:48] in body_cf:
            hits.append(title)
            continue
        tokens = [t for t in outline_title_tokens(title) if len(t) >= 5][:6]
        if len(tokens) >= 2 and sum(1 for t in tokens if t in body_cf) >= max(2, len(tokens) - 1):
            hits.append(title)
    return hits


def remove_aggregate_restatement_sections(
    sections: list[Any],
    *,
    min_sibling_hits: int = 3,
    min_words: int = 300,
) -> tuple[list[Any], list[str]]:
    """Delete tabs that restate ≥N other live section titles inside one body.

    Discovered from the current outline + content — no fixed parent-title list.
    Prefer deleting the mega restate and keeping the dedicated sibling tabs.
    """
    from app.services.proposal_section_quality import word_count

    titles = [_section_title(s) for s in sections]
    drop_ids: set[str] = set()
    logs: list[str] = []

    canon_budget_id = _canonical_budget_section_id(sections)
    for section in sections:
        sid = _section_id(section)
        title = _section_title(section)
        body = _section_content(section)
        if not sid or _is_static_cq_section_id(sid) or not body.strip():
            continue
        if sid == canon_budget_id or _is_protected_budget_section(section):
            continue
        if _is_protected_scan_section(section):
            continue
        if word_count(body) < min_words:
            continue

        siblings = [t for t in titles if t and t != title]
        hits = _sibling_titles_embedded(body, siblings)
        if len(hits) < min_sibling_hits:
            continue

        drop_ids.add(sid)
        logs.append(
            f"{title} (removed — restates {len(hits)} other sections: "
            + ", ".join(hits[:5])
            + ("…" if len(hits) > 5 else "")
            + ")"
        )

    if not drop_ids:
        return sections, []
    kept = [s for s in sections if _section_id(s) not in drop_ids]
    return kept, logs


def dedupe_manuscript_for_scan(
    sections: list[Any],
) -> tuple[list[Any], list[str]]:
    """Scan-RFP dedupe: compress case studies → prune clones → drop mega restates."""
    from app.models.proposal import ProposalDraft

    logs: list[str] = []
    sections, n = compress_duplicate_case_study_sections(list(sections))
    if n:
        logs.append(f"Compressed {n} case-study rewrite(s)")
    try:
        from app.services.proposal_budget_content import collapse_duplicate_cost_proposal_tabs

        typed = [s for s in sections if hasattr(s, "title")]
        if len(typed) == len(sections):
            sections, cost_logs = collapse_duplicate_cost_proposal_tabs(typed)
            logs.extend(cost_logs)
    except Exception:  # noqa: BLE001
        pass
    sections, title_dups = collapse_title_near_duplicate_sections(sections)
    logs.extend(title_dups)
    # Mega parents first so pairwise prune sees the dedicated siblings cleanly.
    sections, removed = remove_aggregate_restatement_sections(sections)
    logs.extend(removed)
    sections, pruned = prune_near_duplicate_sections(sections)
    logs.extend(pruned)
    # Offeror/Company Information forms that restate 1.3 → cross-ref only (no table copy)
    draft_like = ProposalDraft(
        rfpId="dedupe-scan",
        sections=sections,
        updatedAt="1970-01-01T00:00:00Z",
    )
    compressed, company_logs = compress_rfp_company_identity_forms(draft_like)
    if company_logs:
        sections = list(compressed.sections)
        logs.extend(company_logs)
    return sections, logs


_COMPANY_IDENTITY_FORM_TITLE_RE = re.compile(
    r"(?i)\b(?:"
    r"offeror\s+identification|vendor\s+identification|"
    r"proposer\s+identification|contractor\s+identification|"
    r"company\s+information|firm\s+information|"
    r"business\s+information\s+form|identification\b.{0,40}\bform"
    r")\b"
)

_IDENTITY_FIELD_HINTS = (
    "legal name",
    "dba",
    "fein",
    "ein",
    "office address",
    "mailing address",
    "primary contact",
    "contact phone",
    "contact email",
)


def _looks_like_company_identity_table(content: str) -> bool:
    cf = (content or "").casefold()
    hits = sum(1 for hint in _IDENTITY_FIELD_HINTS if hint in cf)
    return hits >= 3


def _extract_markdown_field_table(content: str) -> str:
    """Keep the first markdown field/response-style table from business info."""
    lines = (content or "").splitlines()
    table: list[str] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            in_table = True
            table.append(stripped)
            continue
        if in_table:
            break
    if len(table) >= 3:
        return "\n".join(table)
    return ""


def user_asks_remove_company_identity_dump(user_message: str) -> bool:
    """True for 'remove this company info' on Offeror / Company Identification forms."""
    text = (user_message or "").strip()
    if not text:
        return False
    if not re.search(
        r"(?is)\b(?:remove|delete|strip|drop|cut|omit|take\s+out|get\s+rid\s+of)\b",
        text,
    ):
        return False
    return bool(
        re.search(
            r"(?is)\b("
            r"company\s+info(?:rmation)?|"
            r"business\s+info(?:rmation)?|"
            r"company\s+profile|"
            r"duplicate\s+company|"
            r"second\s+(?:company|business)|"
            r"who\s+we\s+are\s+dump"
            r")\b",
            text,
        )
    )


def is_rfp_company_identity_form_section(
    *,
    section_id: str,
    title: str,
    content: str,
) -> bool:
    """True for RFP Offeror/Company Information forms that restate Section 1.3."""
    sid = (section_id or "").casefold()
    if sid.startswith("section-1-") or sid == "section-1-business-info":
        return False
    title_cf = (title or "").casefold()
    if _COMPANY_IDENTITY_FORM_TITLE_RE.search(title or ""):
        return _looks_like_company_identity_table(content) or "form" in title_cf
    # Untitled-as-form but body is clearly a company identity FIELD table
    if _looks_like_company_identity_table(content) and any(
        token in title_cf
        for token in ("company", "offeror", "vendor", "proposer", "identification")
    ):
        return True
    return False


def compress_rfp_company_identity_forms(
    draft: Any,
) -> tuple[Any, list[str]]:
    """Collapse Offeror/Company Information form tabs that restate Business Info.

    Keeps the required form tab (buyer often needs Section 4 Form returned) but
    replaces a second full company dump with a cross-reference + the same field
    table owned by Section 1.3 — never a second Who We Are / Business Info essay.
    """
    from app.models.proposal import ProposalDraft, ProposalSection

    if not isinstance(draft, ProposalDraft):
        return draft, []

    business: ProposalSection | None = None
    for section in draft.sections:
        sid = (section.id or "").casefold()
        title_cf = (section.title or "").casefold()
        if sid == "section-1-business-info" or (
            title_cf.startswith("1.3") and "business" in title_cf
        ):
            business = section
            break
        if business is None and "business information" in title_cf:
            business = section

    if not business or not (business.content or "").strip():
        return draft, []

    biz_title = business.title or "1.3 — Business Information"

    pointer = (
        f"*Company identity for this form matches **{biz_title}** "
        "(same legal name, contacts, and addresses — not a second company profile).*\n\n"
    )
    compact = (
        pointer
        + f"See **{biz_title}** for legal name, DBA, FEIN, contacts, and addresses. "
        "Complete any form-specific fields below only if this RFP requires them here "
        "and they are not already in that tab."
    )

    logs: list[str] = []
    sections: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        if not is_rfp_company_identity_form_section(
            section_id=section.id or "",
            title=section.title or "",
            content=body,
        ):
            sections.append(section)
            continue
        # Already compressed to cross-ref only (no duplicated field table)
        if (
            "matches **" in body
            and "not a second company profile" in body.casefold()
            and not _looks_like_company_identity_table(body)
        ):
            sections.append(section)
            continue
        # Skip thin stubs / MANUAL FILL only
        if word_count(body) < 40 and "[manual fill" in body.casefold():
            sections.append(section)
            continue
        sections.append(section.model_copy(update={"content": compact, "status": "generated"}))
        changed = True
        logs.append(
            f"{section.title or section.id}: compressed company-identity form → "
            f"cross-ref {biz_title} (no second Business Information dump)"
        )

    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs
