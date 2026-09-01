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
10b. Technical Proposal / Executive Summary / Overview tabs: keep a short cross-ref table if useful;
    do NOT paste whole Experience, Qualifications, Approach, Timeline, or Fee blocks that already
    exist as dedicated TOC tabs — point to those tabs instead.
11. ZERO REPETITION: never restate founding year, FEIN, ownership, certs, insurance limits,
    team bios, or case narratives that another section already owns.
12. EXCEPTION — required RFP form / evaluation-criteria response slots: if THIS tab's
    numbered ask (e.g. I.2 Active Client List) is missing or empty and another section
    already has that list/table, COPY the verified list into the required slot. That is
    form completion, not padding. Do not leave I.2 blank and point elsewhere.
13. Primary contact: use ONLY the locked primary from manuscript locks everywhere — never name
    a second person (e.g. Haley Neff) as dedicated primary when Ron Comer is locked.
14. Schedule must fit the RFP award→launch window; never invent dates, durations, or a
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


def _heading_blocks(content: str) -> list[tuple[str, str, str]]:
    """Split markdown into (heading_line, heading_text, body) blocks.

    Leading prose before the first heading is returned as ("", "", body).
    """
    text = content or ""
    if not text.strip():
        return []
    pattern = re.compile(r"(?m)^(#{1,4}\s+.+)$")
    matches = list(pattern.finditer(text))
    if not matches:
        return [("", "", text)]
    blocks: list[tuple[str, str, str]] = []
    if matches[0].start() > 0:
        lead = text[: matches[0].start()]
        if lead.strip():
            blocks.append(("", "", lead))
    for i, match in enumerate(matches):
        heading_line = match.group(1)
        heading_text = re.sub(r"^#{1,4}\s+", "", heading_line).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        blocks.append((heading_line, heading_text, body))
    return blocks


def _title_tokens_for_match(title: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "section",
        "part",
        "our",
        "of",
        "to",
        "a",
        "an",
        "or",
        "in",
        "on",
        "at",
        "by",
        "as",
        "is",
        "are",
        "this",
        "that",
        "assigned",
        "relevant",
        "similar",
        "scope",
        "projects",
        "proposed",
        "response",
        "form",
    }
    raw = re.sub(r"^\d+[.)\s:—–-]*", "", title or "")
    tokens = {
        t
        for t in re.findall(r"[a-z0-9]{4,}", raw.casefold())
        if t not in stop
    }
    return tokens


def _heading_matches_sibling_title(heading: str, sibling_title: str) -> bool:
    """True when a subsection heading is naming another TOC tab."""
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate

    h = (heading or "").strip()
    t = (sibling_title or "").strip()
    if not h or not t:
        return False
    if outline_titles_near_duplicate(h, t, threshold=0.55):
        return True
    ht = _title_tokens_for_match(h)
    tt = _title_tokens_for_match(t)
    if not ht or not tt:
        return False
    # Shared headword (experience / qualifications / fee) + ≥1 more token,
    # or ≥2 shared significant tokens.
    shared = ht & tt
    if len(shared) >= 2:
        return True
    headwords = {
        "experience",
        "qualification",
        "qualifications",
        "personnel",
        "approach",
        "timeline",
        "schedule",
        "fee",
        "fees",
        "pricing",
        "budget",
        "references",
        "methodology",
    }
    if shared & headwords and len(shared) >= 1 and (ht & tt):
        # Single strong topic word is enough when both sides share it.
        if any(w in shared for w in headwords):
            return True
    return False


def compress_sibling_restatement_blocks(
    sections: list[Any],
) -> tuple[list[Any], list[str]]:
    """Replace in-section H2/H3 blocks that restate a sibling TOC tab.

    Complete Scan / compact: keep unique material (e.g. a cross-ref table) and
    swap duplicated Experience / Qualifications / Fee wholes for a one-line
    pointer to the dedicated tab — never delete the summary tab itself.
    """
    from app.models.proposal import ProposalSection

    logs: list[str] = []
    if len(sections) < 2:
        return list(sections), logs

    # Dedicated siblings: substantial bodies only (the "home" for that topic).
    siblings: list[tuple[str, str, str]] = []
    for section in sections:
        sid = _section_id(section)
        title = _section_title(section)
        body = _section_content(section)
        if not sid or not title.strip():
            continue
        if word_count(body) < 20 and body.count("|") < 4:
            continue
        if _is_protected_budget_section(section) and "fee" not in title.casefold():
            # Still allow Fee Proposal as a sibling home; bare budget ledger stays.
            pass
        siblings.append((sid, title, body))

    out: list[Any] = []
    for section in sections:
        if not isinstance(section, ProposalSection):
            out.append(section)
            continue
        sid = section.id or ""
        title = section.title or ""
        body = section.content or ""
        if not body.strip() or word_count(body) < 80:
            out.append(section)
            continue
        # Don't strip the dedicated home tabs — only summary / umbrella tabs.
        title_cf = title.casefold()
        is_umbrella = bool(
            re.search(
                r"\b(?:technical\s+proposal|executive\s+summary|proposal\s+summary|"
                r"project\s+approach|overview|transmittal)\b",
                title_cf,
            )
        )
        # Also compress any tab that embeds ≥2 sibling-matching headings.
        blocks = _heading_blocks(body)
        if len(blocks) < 2 and not is_umbrella:
            out.append(section)
            continue

        changed = False
        new_parts: list[str] = []
        replaced_titles: list[str] = []
        for heading_line, heading_text, block_body in blocks:
            if not heading_text:
                new_parts.append(block_body)
                continue
            # Never replace the section's own title heading.
            if _heading_matches_sibling_title(heading_text, title):
                new_parts.append(f"{heading_line}{block_body}")
                continue
            home: tuple[str, str, str] | None = None
            for sib_id, sib_title, _sib_body in siblings:
                if sib_id == sid:
                    continue
                if _heading_matches_sibling_title(heading_text, sib_title):
                    home = (sib_id, sib_title, _sib_body)
                    break
            if home is None:
                new_parts.append(f"{heading_line}{block_body}")
                continue
            block_wc = word_count(block_body)
            # Tiny intros under a heading stay; tables / longer restates go.
            # When a dedicated sibling tab exists, even a short fee blurb is a
            # restatement — prefer the pointer.
            if block_wc < 12 and block_body.count("|") < 2:
                new_parts.append(f"{heading_line}{block_body}")
                continue
            pointer = (
                f"{heading_line}\n\n"
                f"{_pointer_for_home(home[1])}\n\n"
            )
            new_parts.append(pointer)
            replaced_titles.append(home[1])
            changed = True

        if not changed:
            out.append(section)
            continue
        new_body = "".join(new_parts)
        new_body = re.sub(r"\n{3,}", "\n\n", new_body).strip() + "\n"
        out.append(section.model_copy(update={"content": new_body}))
        logs.append(
            f"{title or sid}: compressed {len(replaced_titles)} sibling "
            f"restatement block(s) → "
            + "; ".join(replaced_titles[:4])
        )

    return out, logs


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


def _pointer_for_home(title: str) -> str:
    home = (title or "the overlapping section").strip()
    try:
        from app.services.proposal_pointer_page_integrity import (
            format_see_pointer_for_title,
        )

        return (
            f"{format_see_pointer_for_title(home)} for this narrative "
            f"(already covered there — not restated here)."
        )
    except Exception:  # noqa: BLE001
        return (
            f"See **{home}** for this narrative (already covered there — "
            "not restated here)."
        )


def _strip_shared_paragraphs_only(body: str, shared: list[str]) -> str:
    """Remove duplicated paragraphs without injecting a See-pointer."""
    new = body
    for para in shared:
        plain = _plain_for_match(para)
        if len(plain) < 80:
            continue
        if plain in new:
            new = new.replace(plain, "", 1)
    return re.sub(r"\n{3,}", "\n\n", new).strip()


def _replace_shared_paragraphs(body: str, shared: list[str], pointer: str) -> str:
    new = body
    for para in shared:
        plain = _plain_for_match(para)
        if len(plain) < 80:
            continue
        if plain in new:
            new = new.replace(plain, "", 1)
    new = re.sub(r"\n{3,}", "\n\n", new).strip()
    if not new:
        return pointer
    if pointer not in new:
        new = f"{pointer}\n\n{new}"
    return new.strip()


def _shared_paragraphs(source: str, target: str, *, min_len: int = 80) -> list[str]:
    shared: list[str] = []
    for para in _distinctive_paragraphs(source, min_len=min_len):
        plain = _plain_for_match(para)
        if len(plain) >= min_len and plain in (target or ""):
            shared.append(para)
    return shared


def _section_has_distinct_mandated_ask(title: str) -> bool:
    """Certifications / conference evidence tabs must not collapse to a See-pointer only."""
    t = (title or "").casefold()
    if not t:
        return False
    return any(
        hint in t
        for hint in (
            "certification",
            "conference",
            "attendance",
            "evidence of",
            "mandatory",
            "attachment",
            "declaration",
            "affidavit",
            "acknowledgement",
            "addenda",
            "non-collusion",
            "drug-free",
            "iran contracting",
            "pre-proposal",
            "preproposal",
        )
    )


def trim_overlapping_section_prose(
    sections: list[Any],
) -> tuple[list[Any], list[str]]:
    """Keep every TOC tab. Strip copied paragraphs; leave a one-line cross-ref.

    Mechanical only — no LLM. Never blanks a tab. Never deletes a heading.
    """
    from app.models.proposal import ProposalSection

    logs: list[str] = []
    working: list[Any] = list(sections)
    index_by_id = {
        _section_id(section): i
        for i, section in enumerate(working)
        if _section_id(section)
    }

    def _set(section: Any) -> None:
        sid = _section_id(section)
        idx = index_by_id.get(sid)
        if idx is not None:
            working[idx] = section

    owners: list[tuple[str, str, list[str]]] = []
    for section in working:
        sid = _section_id(section)
        if not _is_static_cq_section_id(sid) or sid.endswith("placeholder"):
            continue
        paras = _distinctive_paragraphs(_section_content(section), min_len=80)
        if paras:
            owners.append((sid, _section_title(section), paras))

    for section in list(working):
        if not isinstance(section, ProposalSection):
            continue
        sid = section.id or ""
        if _is_static_cq_section_id(sid) or _is_protected_budget_section(section):
            continue
        body = section.content or ""
        if not body.strip():
            continue
        new = body
        hits = 0
        home_title = ""
        for _oid, otitle, paras in owners:
            shared = [p for p in paras if _plain_for_match(p) in new]
            if not shared:
                continue
            if _section_has_distinct_mandated_ask(section.title or ""):
                new = _strip_shared_paragraphs_only(new, shared)
                hits += len(shared)
                continue
            home_title = otitle
            new = _replace_shared_paragraphs(new, shared, _pointer_for_home(otitle))
            hits += len(shared)
        if hits and new != body:
            if (
                word_count(new) < 20
                and not _section_has_distinct_mandated_ask(section.title or "")
            ):
                new = _pointer_for_home(home_title or "Sections 1–3")
            _set(section.model_copy(update={"content": new}))
            logs.append(
                f"{section.title or sid}: trimmed {hits} restated 1–3 paragraph(s)"
            )

    candidates: list[tuple[int, Any]] = []
    for idx, section in enumerate(working):
        sid = _section_id(section)
        body = _section_content(section).strip()
        if not sid or _is_static_cq_section_id(sid):
            continue
        if _is_protected_budget_section(section):
            continue
        if word_count(body) < 25:
            continue
        candidates.append((idx, section))

    for i, (idx_a, _sec_a_orig) in enumerate(candidates):
        for idx_b, _sec_b_orig in candidates[i + 1 :]:
            sec_a = working[idx_a]
            body_a = _section_content(sec_a)
            sec_b = working[idx_b]
            body_b = _section_content(sec_b)
            shared_in_b = _shared_paragraphs(body_a, body_b)
            shared_in_a = _shared_paragraphs(body_b, body_a)
            if not shared_in_a and not shared_in_b:
                continue
            pts_a = _section_eval_points(sec_a)
            pts_b = _section_eval_points(sec_b)
            trim_b = _prefer_drop_b(
                pts_a=pts_a,
                pts_b=pts_b,
                idx_a=idx_a,
                idx_b=idx_b,
                wc_a=word_count(body_a),
                wc_b=word_count(body_b),
            )
            if trim_b and shared_in_b and isinstance(sec_b, ProposalSection):
                if _section_has_distinct_mandated_ask(sec_b.title or ""):
                    new = _strip_shared_paragraphs_only(body_b, shared_in_b)
                else:
                    pointer = _pointer_for_home(_section_title(sec_a))
                    new = _replace_shared_paragraphs(body_b, shared_in_b, pointer)
                    if word_count(new) < 20:
                        new = pointer
                _set(sec_b.model_copy(update={"content": new}))
                logs.append(
                    f"{_section_title(sec_b)}: trimmed {len(shared_in_b)} "
                    f"paragraph(s) already in {_section_title(sec_a)}"
                )
            elif (not trim_b) and shared_in_a and isinstance(sec_a, ProposalSection):
                if _section_has_distinct_mandated_ask(sec_a.title or ""):
                    new = _strip_shared_paragraphs_only(body_a, shared_in_a)
                else:
                    pointer = _pointer_for_home(_section_title(sec_b))
                    new = _replace_shared_paragraphs(body_a, shared_in_a, pointer)
                    if word_count(new) < 20:
                        new = pointer
                _set(sec_a.model_copy(update={"content": new}))
                logs.append(
                    f"{_section_title(sec_a)}: trimmed {len(shared_in_a)} "
                    f"paragraph(s) already in {_section_title(sec_b)}"
                )

    return working, logs


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


_DRAFT_STUB_MARKER_DEDUP = "draft this rfp-required section"


def _is_hollow_stub_section(section: Any) -> bool:
    """A bare RFP-outline stub or empty body.

    Such a tab must NEVER win a same-title dedup tie against a real drafted
    section — that is exactly how a finished section came back empty (the
    tie-breaker preferred the shorter tab, which was the blank stub).
    """
    body = _section_content(section)
    if not body.strip():
        return True
    cf = body.casefold()
    if _DRAFT_STUB_MARKER_DEDUP in cf:
        return True
    if "rfp-required outline" in cf and word_count(body) < 80:
        return True
    return False


def collapse_title_near_duplicate_sections(
    sections: list[Any],
    *,
    exact_normalized_only: bool = False,
) -> tuple[list[Any], list[str]]:
    """Keep one tab when titles are the same ask — including protected References twins.

    Scan historically skipped any title containing 'reference' / 'experience', so
    'References' and 'References & Past Performance' both survived and the writer
    restated the same examples in each. Title-near-dup collapse is the exception.

    ``exact_normalized_only=True`` (Complete Scan): only drop identical twins after
    title normalize (same RFP label twice). Does not merge soft near-dups like distinct
    TOC siblings — that stays gated behind drop_clone_tabs.
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
        norm_a = normalize_outline_title(title_a)
        for idx_b, sec_b in indexed[i + 1 :]:
            sid_b = _section_id(sec_b)
            if sid_b in drop_ids or sid_b == canon_budget_id or _is_protected_budget_section(sec_b):
                continue
            title_b = _section_title(sec_b)
            if exact_normalized_only:
                if not norm_a or norm_a != normalize_outline_title(title_b):
                    continue
            elif not outline_titles_near_duplicate(title_a, title_b, threshold=0.55):
                continue
            pts_a = _section_eval_points(sec_a)
            pts_b = _section_eval_points(sec_b)
            wc_a = word_count(_section_content(sec_a))
            wc_b = word_count(_section_content(sec_b))
            # Never drop real drafted content in favor of a blank RFP-outline
            # stub. When exactly one side is hollow, the hollow one loses —
            # regardless of scores/length. This is what prevented a finished
            # "Brand Marketing Plan" tab from being replaced by its empty twin.
            hollow_a = _is_hollow_stub_section(sec_a)
            hollow_b = _is_hollow_stub_section(sec_b)
            if hollow_a != hollow_b:
                drop_b = hollow_b
            else:
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
                    drop_b = len(normalize_outline_title(title_b)) <= len(norm_a)
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


def detect_possible_scored_duplicate_pairs(
    sections: list[Any],
    *,
    content_jaccard_threshold: float = 0.42,
    containment_threshold: float = 0.72,
) -> list[str]:
    """Flag — never delete — a protected/scored tab that looks like it answers
    the same RFP requirement as another section.

    prune_near_duplicate_sections above deliberately never compares or drops a
    protected/scored section — auto-deleting one on a heuristic misfire could
    silently forfeit RFP evaluation points. That safety leaves a real gap: two
    DIFFERENT sections can each independently answer the same underlying ask
    (a scored criterion gets its own tab, AND a sibling response-form section
    embeds the same sub-item under an unrelated title) with nothing ever
    surfacing that to a human — the proposal ships the requirement answered
    twice, uncombined. This only ever returns advisory strings for a human to
    act on; it never touches `sections`.
    """
    from app.services.proposal_outline_dedup import outline_titles_near_duplicate
    from app.services.proposal_section_quality import word_count
    from difflib import SequenceMatcher

    substantial = [
        s for s in sections if _section_id(s) and word_count(_section_content(s)) >= 40
    ]
    protected_ids = {
        _section_id(s) for s in substantial if _is_protected_scan_section(s)
    }
    flags: list[str] = []
    for i, sec_a in enumerate(substantial):
        if _section_id(sec_a) not in protected_ids:
            continue
        title_a = _section_title(sec_a)
        body_a = _section_content(sec_a)
        for sec_b in substantial[i + 1 :]:
            if _section_id(sec_b) == _section_id(sec_a):
                continue
            title_b = _section_title(sec_b)
            body_b = _section_content(sec_b)
            title_dup = outline_titles_near_duplicate(title_a, title_b, threshold=0.55)
            jaccard = _content_jaccard(body_a, body_b)
            seq_ratio = SequenceMatcher(
                None, body_a.casefold()[:5000], body_b.casefold()[:5000]
            ).ratio()
            content_dup = jaccard >= content_jaccard_threshold or seq_ratio >= 0.72
            containment = (
                _content_coverage(body_a, body_b) >= containment_threshold
                or _content_coverage(body_b, body_a) >= containment_threshold
            )
            if not title_dup and not content_dup and not containment:
                continue
            flags.append(
                f"Possible duplicate answer: “{title_a}” and “{title_b}” "
                "look like they answer the same RFP requirement — review and "
                "combine into one response instead of leaving both in the proposal."
            )
    return flags


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
    require_full_coverage: bool = False,
    coverage_threshold: float = 0.9,
) -> tuple[list[Any], list[str]]:
    """Delete tabs that restate ≥N other live section titles inside one body.

    Discovered from the current outline + content — no fixed parent-title list.
    Prefer deleting the mega restate and keeping the dedicated sibling tabs.

    ``require_full_coverage`` (Complete Scan): only delete a section when its
    own content is essentially FULLY covered by the union of the sibling
    sections it restates (≥ ``coverage_threshold`` of its tokens appear there),
    so nothing unique is ever lost. A mega-tab with genuinely unique paragraphs
    is left in place — its duplicated prose is handled by the in-place trimmer,
    never a whole-section delete.
    """
    from app.services.proposal_section_quality import word_count

    titles = [_section_title(s) for s in sections]
    body_by_title = {_section_title(s): _section_content(s) for s in sections}
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

        if require_full_coverage:
            # Combine the bodies of exactly the siblings this section restates,
            # and confirm they already carry (nearly) all of this section's
            # content before deleting it — otherwise keep it (unique content).
            haystack = "\n".join(body_by_title.get(t, "") for t in hits)
            coverage = _content_coverage(body, haystack)
            if coverage < coverage_threshold:
                logs.append(
                    f"{title} (kept — restates {len(hits)} siblings but only "
                    f"{coverage:.0%} of its content is covered by them; "
                    "trimming duplicated prose in place instead of deleting)"
                )
                continue

        drop_ids.add(sid)
        logs.append(
            f"{title} (removed — restates {len(hits)} other sections and its "
            "content is already fully covered by them: "
            + ", ".join(hits[:5])
            + ("…" if len(hits) > 5 else "")
            + ")"
        )

    if not drop_ids:
        return sections, logs
    kept = [s for s in sections if _section_id(s) not in drop_ids]
    return kept, logs


def dedupe_manuscript_for_scan(
    sections: list[Any],
    *,
    drop_clone_tabs: bool = True,
) -> tuple[list[Any], list[str]]:
    """Manuscript compact: compress restates; optionally prune clone tabs.

    ``drop_clone_tabs=False`` (Complete Scan) keeps distinct TOC siblings and only
    trims copied paragraphs — plus exact same-title twins (identical normalized
    RFP labels). Soft near-dup / content-overlap tab deletion stays off so Scan does
    not drop legitimate RFP headings the way aggressive compact used to.
    """
    from app.models.proposal import ProposalDraft

    logs: list[str] = []
    sections, n = compress_duplicate_case_study_sections(list(sections))
    if n:
        logs.append(f"Compressed {n} case-study rewrite(s)")
    if drop_clone_tabs:
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
    else:
        # Complete Scan: identical-title twins only — never soft near-dup / prune.
        sections, title_dups = collapse_title_near_duplicate_sections(
            sections,
            exact_normalized_only=True,
        )
        logs.extend(title_dups)
        # Safe even in Scan: removes ONLY a ≥300-word section whose body
        # literally restates ≥3 OTHER section titles AND whose content is
        # already fully covered by those siblings (require_full_coverage) — an
        # invented "Brand Marketing Plan" mega-tab re-covering the RFP's own
        # lettered D/E/F asks, where nothing unique is lost. A section with any
        # unique content is kept; only its duplicated prose is trimmed below.
        # Cannot merge two distinct-but-word-sharing tabs the way the soft
        # near-dup / jaccard matchers can, so it is safe outside drop_clone_tabs.
        sections, removed = remove_aggregate_restatement_sections(
            sections,
            require_full_coverage=True,
        )
        logs.extend(removed)
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
    # Order: first compress umbrella tabs that embed sibling headings
    # (Technical Proposal → pointer to §21/§22/§26), THEN trim leftover
    # shared paragraphs. Doing trim first can wrongly strip the dedicated
    # sibling down to match the umbrella copy.
    if not drop_clone_tabs:
        sections, sibling_logs = compress_sibling_restatement_blocks(sections)
        logs.extend(sibling_logs)
        sections, trim_logs = trim_overlapping_section_prose(sections)
        logs.extend(trim_logs)
    sections, ref_logs = compress_redundant_reference_deliverables(
        sections, drop_soft_overlap=drop_clone_tabs
    )
    logs.extend(ref_logs)
    return sections, logs


_REFERENCE_FAMILY_TITLE_RE = re.compile(
    r"(?i)\b(?:"
    r"references?|past\s+performance|client\s+list|active\s+client"
    r")\b"
)

_REFERENCE_FORM_TITLE_HINTS = (
    "reference form",
    "client reference",
    "reference contact",
    "attachment",
    "exhibit",
    "schedule",
    "appendix",
)


def _section_submission_instrument(section: Any) -> str | None:
    raw = None
    if hasattr(section, "submission_instrument"):
        raw = getattr(section, "submission_instrument", None)
    if raw is None and isinstance(section, dict):
        raw = section.get("submissionInstrument")
        if raw is None:
            raw = section.get("submission_instrument")
    text = str(raw or "").strip().casefold()
    return text or None


def _is_reference_family_section(section: Any) -> bool:
    inst = _section_submission_instrument(section) or ""
    if inst == "references":
        return True
    title_cf = _section_title(section).casefold()
    return bool(_REFERENCE_FAMILY_TITLE_RE.search(title_cf))


def is_rfp_reference_form_section(
    *,
    section_id: str = "",
    title: str = "",
    content: str = "",
) -> bool:
    """Buyer reference form / attachment slot — not the scored narrative tab."""
    title_cf = (title or "").casefold()
    sid_cf = (section_id or "").casefold()
    if not title_cf and not sid_cf:
        return False
    if "reference" not in title_cf and "reference" not in sid_cf:
        return False
    if any(h in title_cf for h in _REFERENCE_FORM_TITLE_HINTS):
        return True
    if title_cf.endswith(" form") or " form " in title_cf:
        return True
    # Physical signed attachment stub — keep tab but pointerize duplicate body
    body_cf = (content or "").casefold()
    if "[manual fill" in body_cf and "attach" in body_cf and "reference" in body_cf:
        return True
    return False


def _has_reference_contact_table(content: str) -> bool:
    pipe_rows = [
        line
        for line in (content or "").splitlines()
        if line.strip().startswith("|") and line.count("|") >= 3
    ]
    return len(pipe_rows) >= 2


def _content_word_jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]{3,}", (a or "").casefold()))
    tb = set(re.findall(r"[a-z0-9]{3,}", (b or "").casefold()))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _pick_canonical_reference_section(sections: list[Any]) -> Any | None:
    """Best drafted references / past-performance tab (not a hollow form stub)."""
    candidates: list[tuple[int, float, int, Any]] = []
    for idx, section in enumerate(sections):
        if not _is_reference_family_section(section):
            continue
        body = _section_content(section)
        is_form = is_rfp_reference_form_section(
            section_id=_section_id(section),
            title=_section_title(section),
            content=body,
        )
        wc = word_count(body)
        if is_form and wc < 120 and not _has_reference_contact_table(body):
            continue
        if wc < 25 and not _has_reference_contact_table(body):
            continue
        candidates.append(
            (
                idx,
                _section_eval_points(section),
                wc + (80 if _has_reference_contact_table(body) else 0),
                section,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row[1], -row[2], row[0]))
    return candidates[0][3]


def compress_redundant_reference_deliverables(
    sections: list[Any],
    *,
    drop_soft_overlap: bool = True,
) -> tuple[list[Any], list[str]]:
    """One reference narrative + pointerized forms — no duplicate contact tables.

    When the manuscript already has a drafted References / Past Performance tab,
    a separate Attachment C / reference form must not restate the same table.
    RFP-minimum procurement stubs that only repeat that narrative are dropped.

    ``drop_soft_overlap=False`` (Complete Scan) keeps the deterministic,
    hint-based moves (form pointerization, RFP-minimum-stub cross-ref) but
    disables the soft word-overlap match below that can delete a distinct,
    legitimately separate reference-family narrative tab (e.g. a real
    "Past Performance" tab next to a real "References" tab) just because they
    discuss the same client relationships in different words.
    """
    canon = _pick_canonical_reference_section(sections)
    if canon is None:
        return sections, []

    canon_id = _section_id(canon)
    canon_title = _section_title(canon) or "References"
    canon_body = _section_content(canon)
    logs: list[str] = []
    drop_ids: set[str] = set()
    updated: dict[str, Any] = {}

    try:
        from app.services.proposal_pointer_page_integrity import format_see_pointer_for_title

        see_pointer = format_see_pointer_for_title(canon_title)
    except Exception:  # noqa: BLE001
        see_pointer = f"See **{canon_title}**"

    form_pointer = (
        f"*Reference contacts for this submission are completed in **{canon_title}** "
        "(same clients and contacts — not a second reference narrative).*\n\n"
        f"{see_pointer} for the reference table and past-performance summary. "
        "If this RFP requires a physically signed reference form, include "
        "[DESIGNER NOTE: attach signed PDF of the buyer's form before submit]."
    )

    for section in sections:
        sid = _section_id(section)
        if not sid or sid == canon_id or sid in drop_ids:
            continue
        if not _is_reference_family_section(section):
            continue
        title = _section_title(section)
        body = _section_content(section)
        is_form = is_rfp_reference_form_section(
            section_id=sid,
            title=title,
            content=body,
        )
        title_cf = title.casefold()
        is_minimum_stub = "minimum" in title_cf and "reference" in title_cf

        if is_form:
            if body.strip() == form_pointer.strip():
                continue
            if word_count(body) < 40 and "[manual fill" in body.casefold():
                updated[sid] = section.model_copy(
                    update={"content": form_pointer, "status": "generated"}
                ) if hasattr(section, "model_copy") else {
                    **section,
                    "content": form_pointer,
                    "status": "generated",
                }
                logs.append(
                    f"{title}: reference form → cross-ref {canon_title} "
                    "(buyer form slot kept; no duplicate table)"
                )
                continue
            overlap = _content_word_jaccard(body, canon_body)
            if overlap >= 0.35 or _has_reference_contact_table(body):
                updated[sid] = section.model_copy(
                    update={"content": form_pointer, "status": "generated"}
                ) if hasattr(section, "model_copy") else {
                    **section,
                    "content": form_pointer,
                    "status": "generated",
                }
                logs.append(
                    f"{title}: reference form → cross-ref {canon_title} "
                    "(duplicate contact table removed)"
                )
            continue

        overlap = _content_word_jaccard(body, canon_body)
        if is_minimum_stub:
            if _has_reference_contact_table(body) and overlap < 0.55:
                updated[sid] = section.model_copy(
                    update={"content": form_pointer, "status": "generated"}
                ) if hasattr(section, "model_copy") else {
                    **section,
                    "content": form_pointer,
                    "status": "generated",
                }
                logs.append(
                    f"{title}: RFP minimum tab → cross-ref {canon_title}"
                )
            else:
                drop_ids.add(sid)
                logs.append(
                    f"{title} (RFP minimum stub — covered by {canon_title})"
                )
            continue
        if drop_soft_overlap and overlap >= 0.48 and sid != canon_id:
            drop_ids.add(sid)
            logs.append(
                f"{title} (reference narrative overlaps {canon_title})"
            )

    if not drop_ids and not updated:
        return sections, logs

    kept: list[Any] = []
    for section in sections:
        sid = _section_id(section)
        if sid in drop_ids:
            continue
        if sid in updated:
            kept.append(updated[sid])
        else:
            kept.append(section)
    return kept, logs


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
