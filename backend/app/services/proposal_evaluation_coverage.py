"""Scored-criteria coverage for the proposal outline.

An RFP that publishes an evaluation-criteria response form is telling us the
proposal's table of contents: every scored parent criterion is a section the
evaluator will open, score, and total. Nothing in the outline pipeline used to
guarantee that. The planner had ~15 anti-bloat rules and one weak line about
scored tabs, so a 160-point Strategic Planning block could be folded into a
single "Exhibit 1" tab while unscored acknowledgments survived the hard cap.

This module is the deterministic backstop that runs AFTER the planner, the lean
filter, and the cap:

* ``ensure_scored_criteria_coverage`` injects a tab for any scored parent
  criterion with no home, stamped so later hygiene passes cannot drop it.
* ``min_outline_sections_for_evaluation`` raises the cap floor so a scored form
  with many parents is never squeezed out by the page-budget heuristic.
* ``criterion_char_limit`` / ``char_limit_to_word_budget`` carry the buyer's
  per-response field limit ("maximum of 4,000 characters") into writer budgets.

Sub-items (I.1, I.2 …) never become tabs of their own — they become required
sub-headings inside the parent tab, which is how the buyer's own form reads.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.proposal_outline_dedup import (
    normalize_outline_title,
    outline_title_tokens,
    outline_titles_near_duplicate,
)

logger = logging.getLogger(__name__)

# A 4,000-character field holds roughly 600 words at ~6.5 characters per word
# including the trailing space. Deliberately conservative: an over-limit
# response is rejected by the portal, an under-limit one merely leaves points
# on the table.
_CHARS_PER_WORD = 6.5

# Words below this are not a usable brief for a scored ask.
_MIN_WORD_BUDGET = 120


def _get(obj: Any, attr: str, key: str | None = None) -> Any:
    """Read a field off a Pydantic model or an alias-keyed dict."""
    if hasattr(obj, attr):
        value = getattr(obj, attr)
        if value is not None:
            return value
    if isinstance(obj, dict):
        for candidate in (key, attr):
            if candidate and obj.get(candidate) is not None:
                return obj.get(candidate)
    return None


def _as_points(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def criterion_points(criterion: Any) -> float:
    """Points attached to a criterion, falling back to the sum of its items."""
    direct = _as_points(_get(criterion, "weight"))
    if direct > 0:
        return direct
    return sum(_as_points(_get(item, "weight")) for item in criterion_items(criterion))


def criterion_items(criterion: Any) -> list[Any]:
    items = _get(criterion, "items")
    return list(items) if isinstance(items, list) else []


# Points markers that leak into a criterion's name when the source PDF wraps a
# scoring row mid-phrase ("SECTION III Strategic Planning - UP TO 160\nPOINTS
# POSSIBLE"). The extractor faithfully copies what it sees, so the fragment
# rides along into the section title an evaluator reads.
_POINTS_MARKERS = (" - up to", " – up to", " — up to", " up to")


def clean_criterion_name(name: str) -> str:
    """Strip trailing points-table wording from a criterion's heading.

    Plain string work rather than a pattern: cut at the last points marker,
    then drop any dangling "points possible" remnant. Falls back to the
    original whenever cutting would leave nothing meaningful.
    """
    text = (name or "").strip()
    if not text:
        return ""
    lowered = text.casefold()
    cut = -1
    for marker in _POINTS_MARKERS:
        found = lowered.rfind(marker)
        if found > cut:
            cut = found
    if cut > 0:
        candidate = text[:cut].strip(" -–—:\t")
        if len(candidate.split()) >= 2:
            text = candidate
    # A row wrapped mid-phrase leaves a dangling "- UP" with nothing after it.
    # Only stripped at the very end of the string, so a real name containing
    # "- up" (e.g. "Follow - up Services") is untouched.
    lowered = text.casefold()
    for dangling in (" - up", " – up", " — up"):
        if lowered.endswith(dangling):
            candidate = text[: len(text) - len(dangling)].strip(" -–—:\t")
            if len(candidate.split()) >= 2:
                text = candidate
                lowered = text.casefold()
            break

    for tail in ("points possible", "points", "possible"):
        if lowered.endswith(tail):
            trimmed = text[: len(text) - len(tail)].strip(" -–—:\t0123456789")
            if len(trimmed.split()) >= 2:
                text = trimmed
                lowered = text.casefold()
    return text.strip(" -–—:\t")


def criterion_name(criterion: Any) -> str:
    return clean_criterion_name(str(_get(criterion, "name") or ""))


def criterion_code(criterion: Any) -> str:
    return str(_get(criterion, "item_code", "itemCode") or "").strip()


# Named evaluation factors that score the offeror's approach / SOW response.
# Used only when the buyer also published a section code — abstract category
# labels without structure stay advisory (see evaluation_is_published_response_form).
_OFFEROR_RESPONSE_FACTOR = re.compile(
    r"\b("
    r"technical\s+approach|proposed\s+approach|project\s+approach|"
    r"methodology|work\s+plan|management\s+approach|"
    r"scope\s+of\s+work|statement\s+of\s+work|"
    r"understanding\s+of\s+(the\s+)?(project|scope|requirements)"
    r")\b",
    re.I,
)

# Asks that make clear the evaluator is scoring a submitted response, even when
# points sit on Qualifications / Cost and this factor's weight is null.
_ASK_NEEDS_OFFEROR_RESPONSE = re.compile(
    r"offeror.?s?\s+response|proposer.?s?\s+response|"
    r"based\s+on\s+(the\s+)?(offeror|proposer|vendor).{0,60}response|"
    r"response\s+to\s+section|"
    r"proposed\s+(approach|methodology|solution|work\s+plan)",
    re.I,
)


def criterion_requires_outline_tab(criterion: Any) -> bool:
    """True when this factor must appear as its own manuscript tab.

    Points > 0 always qualify. A coded factor that names an approach / scope /
    methodology response — or whose ask scores the offeror's response — also
    qualifies when weight is null or qualitative. Otherwise Phase 2 drops
    Technical Approach / Scope tabs while keeping only Qualifications + Cost,
    even though the evaluator opens that response section.
    """
    if not criterion_name(criterion):
        return False
    if criterion_points(criterion) > 0:
        return True
    code = criterion_code(criterion)
    if not code:
        return False
    name = criterion_name(criterion)
    if _OFFEROR_RESPONSE_FACTOR.search(name):
        return True
    ask_blob = " ".join(
        str(_get(item, "ask") or "") for item in criterion_items(criterion)
    )
    ask_blob = f"{ask_blob} {str(_get(criterion, 'description') or '')}".strip()
    return bool(_ASK_NEEDS_OFFEROR_RESPONSE.search(ask_blob))


def scored_criteria(evaluation: Any) -> list[Any]:
    """Parent criteria that must reach the outline as tabs, in RFP order.

    Includes positive-point criteria and coded offeror-response factors whose
    weight was omitted or qualitative (see ``criterion_requires_outline_tab``).
    """
    criteria = _get(evaluation, "criteria") or []
    if not isinstance(criteria, list):
        return []
    return [
        crit
        for crit in criteria
        if criterion_requires_outline_tab(crit)
    ]


def criterion_outline_title(criterion: Any) -> str:
    """The buyer's own heading for this scored block.

    Keeps the response-form code when the RFP publishes one ("SECTION III"), so
    an evaluator scoring against their form can find the tab without
    translating our wording back into theirs.
    """
    name = criterion_name(criterion)
    code = criterion_code(criterion)
    if not code:
        return name
    if normalize_outline_title(code) and normalize_outline_title(code) in normalize_outline_title(name):
        return name
    return f"{code} — {name}"


# A tab that is *about* the buyer's criteria form rather than an answer to one
# criterion. Left in the outline it absorbs scored criteria by title overlap
# ("… Response Form — … Public Relations Capabilities" swallowed a 120-point
# Public Relations criterion), and the writer produces one shapeless essay
# instead of the per-criterion answers the evaluator scores.
_RESPONSE_FORM_WRAPPER_PHRASES = (
    "evaluation criteria response",
    "evaluation criteria form",
    "criteria response form",
    "bid response form",
    "response to evaluation criteria",
    "response to the evaluation criteria",
    "evaluation criteria bid response",
)


def is_evaluation_form_wrapper_title(title: str) -> bool:
    """True when a title names the criteria RESPONSE FORM itself, not a criterion."""
    norm = normalize_outline_title(title)
    if not norm:
        return False
    return any(phrase in norm for phrase in _RESPONSE_FORM_WRAPPER_PHRASES)


def outline_covers_criterion(sections: list[Any], criterion: Any) -> bool:
    """True when some outline tab already answers this scored criterion.

    Matches on the buyer's response-form code first (an exact, unambiguous
    signal), then falls back to the shared title near-duplicate logic so a tab
    titled with the RFP's fuller wording still counts as coverage.
    """
    code = criterion_code(criterion)
    name = criterion_name(criterion)
    title = criterion_outline_title(criterion)
    code_norm = normalize_outline_title(code) if code else ""
    name_tokens = {t for t in outline_title_tokens(name) if len(t) >= 4}

    for section in sections:
        section_title = str(_get(section, "title") or "")
        if not section_title:
            continue
        # A wrapper tab is not an answer — it must not count as coverage.
        if is_evaluation_form_wrapper_title(section_title):
            continue
        if code_norm:
            section_norm = normalize_outline_title(section_title)
            if code_norm and code_norm in section_norm:
                return True
        if outline_titles_near_duplicate(section_title, title):
            return True
        if outline_titles_near_duplicate(section_title, name):
            return True
        if name_tokens:
            section_tokens = {
                t for t in outline_title_tokens(section_title) if len(t) >= 4
            }
            overlap = len(name_tokens & section_tokens)
            # Two shared content words AND most of the criterion's own wording
            # present — enough to call it the same ask without letting a single
            # common word like "Experience" absorb an unrelated criterion.
            if overlap >= 2 and overlap >= len(name_tokens) * 0.6:
                return True
    return False


def evaluation_is_published_response_form(evaluation: Any) -> bool:
    """True when the buyer published the scored SECTION LIST, not just categories.

    This is the line between two very different situations, and conflating
    them caused a documented incident (see proposal_rfp_compliance.py's
    _ADD_ELIGIBLE_SOURCES note):

    * A scoring CATEGORY name — "Technical Approach", 30 pts — is not a
      deliverable. It is satisfied by whatever section addresses it, and
      matching that abstract label to requirement-phrased prose was wrong 5
      times out of 5 on a real RFP. Auto-creating a section for it mints a
      duplicate stub beside the tab that already answers it. Those stay
      advisory for a human to judge — this function returns False for them.

    * A published response FORM — "SECTION III Strategic Planning, 160 pts,
      items III.1–III.4" — IS the required outline. The buyer numbered the
      sections and told us what goes in each. There is no judgment call to
      defer, and a missing one is simply forfeited points.

    Structure is the signal: an explicit scoredResponseForm flag, or criteria
    carrying the buyer's own item codes / numbered sub-asks.
    """
    if bool(_get(evaluation, "scored_response_form", "scoredResponseForm")):
        return True
    criteria = scored_criteria(evaluation)
    if len(criteria) < 2:
        return False
    structured = [c for c in criteria if criterion_code(c) or criterion_items(c)]
    return len(structured) >= max(2, len(criteria) // 2)


def min_outline_sections_for_evaluation(evaluation: Any) -> int:
    """Cap floor required so every scored parent criterion can hold a tab.

    The page-budget heuristic in ``max_rfp_outline_sections`` assumes narrative
    tabs of ~400 words. A scored response form breaks that assumption: the
    buyer decides how many sections exist, not our page math.
    """
    return len(scored_criteria(evaluation))


def evaluation_response_char_limit(evaluation: Any) -> int | None:
    limit = _get(evaluation, "response_char_limit", "responseCharLimit")
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def criterion_char_limit(criterion: Any, evaluation: Any = None) -> int | None:
    """Per-response character cap for this criterion.

    Item-level limits win over the criterion's own, which wins over the
    package-wide default — the buyer's most specific statement governs.
    """
    item_limits = [
        _get(item, "response_char_limit", "responseCharLimit")
        for item in criterion_items(criterion)
    ]
    values = [v for v in item_limits if isinstance(v, int) and v > 0]
    if values:
        return min(values)
    own = _get(criterion, "response_char_limit", "responseCharLimit")
    if isinstance(own, int) and own > 0:
        return own
    return evaluation_response_char_limit(evaluation) if evaluation is not None else None


def char_limit_to_word_budget(char_limit: int | None, *, responses: int = 1) -> int | None:
    """Words that fit inside ``responses`` fields of ``char_limit`` characters.

    ``responses`` is the number of separately-capped answers the tab must hold
    — a parent criterion with four scored items fills four 4,000-character
    fields, not one.
    """
    if not char_limit or char_limit <= 0:
        return None
    count = max(1, responses)
    words = int((char_limit * count) / _CHARS_PER_WORD)
    return max(_MIN_WORD_BUDGET, words)


def find_response_char_limit(rfp_text: str) -> int | None:
    """Smallest per-field character cap stated anywhere in the RFP.

    Plain string scanning rather than a pattern: walk to each mention of
    "character", read the number in front of it, and keep the smallest. Catches
    "a maximum of 4,000 characters", "4000 character limit", and
    "limited to 2,500 characters" without a grammar for each phrasing.
    """
    text = (rfp_text or "")
    if not text:
        return None
    lowered = text.casefold()
    found: list[int] = []
    cursor = 0
    while True:
        hit = lowered.find("character", cursor)
        if hit < 0:
            break
        cursor = hit + len("character")
        # Look back over a short window for the count this caps.
        window = text[max(0, hit - 60) : hit]
        digits: list[str] = []
        seen_digit = False
        for ch in reversed(window):
            if ch.isdigit():
                digits.append(ch)
                seen_digit = True
                continue
            if ch in {",", " "} and seen_digit:
                # Thousands separator or the space before "characters".
                if ch == "," and digits:
                    continue
                if ch == " " and not digits:
                    continue
                if ch == " ":
                    break
                continue
            if seen_digit:
                break
        if not digits:
            continue
        try:
            value = int("".join(reversed(digits)))
        except ValueError:
            continue
        # Character caps are field budgets, not page counts or years.
        if 200 <= value <= 100_000:
            found.append(value)
    return min(found) if found else None


def _make_section(criterion: Any, *, order: int, index: int) -> dict[str, Any]:
    points = criterion_points(criterion)
    items = criterion_items(criterion)
    codes = [str(_get(item, "item_code", "itemCode") or "").strip() for item in items]
    codes = [c for c in codes if c]
    code_tail = f" covering {', '.join(codes)}" if codes else ""
    if points > 0:
        reason = f"Scored evaluation criterion worth {points:g} points{code_tail}"
    else:
        reason = (
            "Named evaluation factor requiring an offeror response"
            f"{code_tail}"
            " (weight omitted or qualitative in extraction)"
        )
    return {
        "id": f"rfp-eval-{index}",
        "title": criterion_outline_title(criterion),
        "order": order,
        "required": True,
        "conditionalReason": reason,
        "parentId": None,
        "children": [],
        "dependencies": [],
        "evaluationWeight": points if points > 0 else None,
        "protectFromCap": True,
        "submissionInstrument": "narrative",
    }


# Instruments that belong at the BACK of a proposal — signed forms, disclosures
# and reference sheets the buyer returns, not body sections an evaluator scores.
_CLOSING_INSTRUMENTS = {"form", "disclosure", "references"}


def _is_closing_instrument(section: Any) -> bool:
    raw = _get(section, "submission_instrument", "submissionInstrument")
    return str(raw or "").strip().casefold() in _CLOSING_INSTRUMENTS


def _body_insert_index(sections: list[Any]) -> int:
    """Where new scored body tabs go: ahead of the closing/forms package.

    Falls back to appending when the planner stamped no instruments, which
    preserves its ordering rather than guessing from titles.
    """
    for index, section in enumerate(sections):
        if _is_closing_instrument(section):
            return index
    return len(sections)


def ensure_scored_criteria_coverage(
    sections: list[Any],
    evaluation: Any,
    *,
    section_factory: Any = None,
) -> tuple[list[Any], list[str], list[str]]:
    """Add a tab for every scored criterion the outline left uncovered.

    Runs last, after the lean filter and the hard cap, because those are the
    passes that drop scored work. Injected tabs carry the criterion's points
    and ``protectFromCap``, so a later hygiene pass treats them as buyer
    instruments rather than optional narrative. They are inserted in the RFP's
    own criteria order, ahead of the closing/forms package.

    Returns ``(sections, added, dropped)``.
    """
    kept = list(sections)
    added: list[str] = []
    dropped: list[str] = []

    # Titles the planner copied verbatim from a wrapped scoring row carry the
    # points fragment into the manuscript ("SECTION VII ECONOMY AND PRICE - UP").
    # Clean every title, not just the ones this pass creates.
    for section in kept:
        raw_title = str(_get(section, "title") or "")
        tidy = clean_criterion_name(raw_title)
        if tidy and tidy != raw_title:
            if hasattr(section, "title"):
                section.title = tidy
            elif isinstance(section, dict):
                section["title"] = tidy

    criteria = scored_criteria(evaluation)
    # Only auto-create sections when the buyer published the section list.
    # Loose scoring categories stay advisory — see
    # evaluation_is_published_response_form for why that distinction exists.
    if not criteria or not evaluation_is_published_response_form(evaluation):
        return kept, added, dropped

    new_sections: list[Any] = []
    for index, criterion in enumerate(criteria, start=1):
        if outline_covers_criterion(kept, criterion):
            continue
        raw = _make_section(criterion, order=0, index=index)
        if section_factory is not None:
            try:
                section: Any = section_factory(raw)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("scored-coverage section build failed: %s", exc)
                section = raw
        else:
            section = raw
        new_sections.append(section)
        weight = raw.get("evaluationWeight")
        if isinstance(weight, (int, float)) and weight > 0:
            added.append(f"{raw['title']} ({weight:g} pts)")
        else:
            added.append(f"{raw['title']} (required evaluation response)")

    # Drop the wrapper whenever the scored criteria have real tabs — whether
    # THIS pass injected them or the planner emitted them itself. Gating on
    # "we injected something" left the wrapper standing beside a correct
    # seven-section outline, i.e. an eighth tab re-covering all seven.
    criteria_have_tabs = bool(new_sections) or all(
        outline_covers_criterion(kept, crit) for crit in criteria
    )
    if criteria_have_tabs:
        surviving: list[Any] = []
        for section in kept:
            title = str(_get(section, "title") or "")
            # Drop regardless of how the planner stamped it. A criteria
            # response form is often stamped submissionInstrument="form", and
            # excluding forms left the wrapper standing next to the seven tabs
            # that now carry its entire contents. Its content lives in those
            # tabs; what the buyer needs returned is the filled-in portal
            # fields, not a second essay restating all of them.
            if is_evaluation_form_wrapper_title(title):
                dropped.append(f"{title} (criteria response-form wrapper — criteria now have own tabs)")
                continue
            surviving.append(section)
        kept = surviving
        if new_sections:
            at = _body_insert_index(kept)
            kept = kept[:at] + new_sections + kept[at:]

    for order, section in enumerate(kept, start=1):
        if hasattr(section, "order"):
            section.order = order
        elif isinstance(section, dict):
            section["order"] = order
    return kept, added, dropped


def uncovered_scored_criteria(sections: list[Any], evaluation: Any) -> list[str]:
    """Scored criteria with no outline tab — for reporting and verification."""
    labels: list[str] = []
    for crit in scored_criteria(evaluation):
        if outline_covers_criterion(sections, crit):
            continue
        points = criterion_points(crit)
        title = criterion_outline_title(crit)
        if points > 0:
            labels.append(f"{title} ({points:g} pts)")
        else:
            labels.append(f"{title} (required evaluation response)")
    return labels


def evaluation_priority_brief(evaluation: Any, *, limit: int = 24) -> str:
    """Points-ranked criteria digest for prompts.

    The planner used to receive the whole evaluation blob as JSON, where a
    160-point section and a throwaway acknowledgment read identically. This
    puts the scoreboard in front of the model in point order.
    """
    criteria = scored_criteria(evaluation)
    if not criteria:
        return "No scored evaluation criteria extracted."
    ranked = sorted(criteria, key=criterion_points, reverse=True)
    total = sum(criterion_points(c) for c in ranked)
    lines: list[str] = [
        f"SCORED EVALUATION CRITERIA — {len(ranked)} sections, {total:g} total points.",
        "Each line below is a section the evaluator scores. Every one needs its own tab.",
    ]
    for crit in ranked[:limit]:
        points = criterion_points(crit)
        if points > 0:
            share = f"{(points / total * 100):.0f}%" if total > 0 else "?"
            lines.append(
                f"- [{points:g} pts / {share}] {criterion_outline_title(crit)}"
            )
        else:
            lines.append(
                "- [required response / no points extracted] "
                f"{criterion_outline_title(crit)}"
            )
        for item in criterion_items(crit):
            code = str(_get(item, "item_code", "itemCode") or "").strip()
            ask = str(_get(item, "ask") or "").strip()
            item_points = _as_points(_get(item, "weight"))
            pts = f"{item_points:g} pts" if item_points > 0 else "scored"
            label = f"{code}: " if code else ""
            lines.append(f"    · ({pts}) {label}{ask[:180]}")
    if len(ranked) > limit:
        lines.append(f"- … and {len(ranked) - limit} more scored criteria")
    return "\n".join(lines)


def backfill_evaluation_response_limits(evaluation: Any, rfp_context: str) -> Any:
    """Fill a missing per-response character cap by reading the RFP itself.

    The extractor sees the whole RFP but reports one JSON blob; a submission
    instruction buried on page 27 ("each response form field allows a maximum
    of 4,000 characters") is exactly the detail it drops. A cap the writer
    never hears about is a response the portal rejects, so scan for it.
    """
    if evaluation is None:
        return evaluation
    if evaluation_response_char_limit(evaluation) is not None:
        return evaluation
    found = find_response_char_limit(rfp_context)
    if not found:
        return evaluation
    if hasattr(evaluation, "response_char_limit"):
        evaluation.response_char_limit = found
    elif isinstance(evaluation, dict):
        evaluation["responseCharLimit"] = found
    logger.info("Backfilled evaluation response char limit from RFP text: %d", found)
    return evaluation


def criterion_for_section_title(evaluation: Any, title: str) -> Any | None:
    """The scored criterion a drafted tab answers, or None when unscored."""
    if not (title or "").strip():
        return None
    for criterion in scored_criteria(evaluation):
        if outline_covers_criterion([{"title": title}], criterion):
            return criterion
    return None


def criterion_writer_directive(criterion: Any, evaluation: Any = None) -> str:
    """Instruction telling the writer exactly which scored asks to answer.

    The evaluator scores item by item off their own form. A tab that reads well
    but never answers III.2 loses those points regardless of prose quality, so
    the item codes and the buyer's wording go into the brief verbatim.
    """
    points = criterion_points(criterion)
    items = criterion_items(criterion)
    char_limit = criterion_char_limit(criterion, evaluation)
    parts: list[str] = [
        f"SCORED: this tab is worth {points:g} points and is scored against the RFP's "
        "evaluation criteria form.",
        "NEVER PRINT POINT VALUES. The points below are context for you, not content "
        "for the buyer. Do not write \"(40 points)\", \"[40 pts]\", \"UP TO 40 POINTS "
        "POSSIBLE\", or any scoring language in headings or body text. The evaluator "
        "already knows what each item is worth — printing it back reads as padding and "
        "makes the submission look unfinished. Headings carry the buyer's item code and "
        "title only (e.g. \"I.1 Unique Strengths\").",
    ]
    if items:
        parts.append(
            f"Answer all {len(items)} numbered asks as their own labelled sub-headings, "
            "in the RFP's order, using the buyer's own item codes as the headings:"
        )
        for item in items:
            # Deliberately no per-item point value here. Listing "[40 pts]" beside
            # each ask put "*(40 points)*" straight into the drafted headings —
            # the model treats anything in the brief as material to echo.
            code = str(_get(item, "item_code", "itemCode") or "").strip()
            ask = str(_get(item, "ask") or "").strip()
            parts.append(f"  {code or '-'}: {ask}")
        parts.append(
            "Every one of those sub-headings must appear and be answered directly — "
            "a missing or merged answer scores zero for that item."
        )
    if char_limit:
        scope = "per numbered response" if items else "for this response"
        parts.append(
            f"HARD LIMIT: {char_limit} characters {scope} — the submission portal rejects "
            "anything longer. Answer the ask and stop; do not pad toward the limit."
        )
    return "\n".join(parts)


# Phrases an RFP uses when it publishes a points table. Plain substring checks,
# not a grammar — this only has to answer "does this RFP score sections?".
_POINTS_TABLE_PHRASES = (
    "points possible",
    "total points",
    "maximum points",
    "points available",
    "point value",
    "possible points",
    "evaluation criteria",
    "weighted evaluation",
)


def rfp_publishes_a_points_table(rfp_text: str) -> bool:
    lowered = (rfp_text or "").casefold()
    if not lowered:
        return False
    return any(phrase in lowered for phrase in _POINTS_TABLE_PHRASES)


def evaluation_extraction_looks_degenerate(evaluation: Any, rfp_text: str) -> bool:
    """True when an RFP clearly scores sections but extraction returned nothing usable.

    The real failure this catches: a 1,000-point, seven-section criteria form
    came back as ONE criterion named "Evaluation Criteria Response Form" with
    no points at all. Everything downstream then behaved correctly on garbage
    input — coverage found no scored criteria to guarantee, the outline kept
    only the exhibit tabs, and 1,000 points of scored work silently vanished.

    A collapse like that is cheap to detect and expensive to miss, so it is
    worth one targeted re-extraction rather than a failed bid.
    """
    if not rfp_publishes_a_points_table(rfp_text):
        return False
    criteria = _get(evaluation, "criteria") or []
    if not isinstance(criteria, list) or not criteria:
        return True
    # Not one criterion carries points — nothing downstream can rank or cover.
    if not scored_criteria(evaluation):
        return True
    # A single criterion standing in for the whole form is the collapse above,
    # not a real one-criterion RFP: real ones do not name themselves "response
    # form". Two or more scored criteria is always treated as a genuine result.
    if len(criteria) == 1 and is_evaluation_form_wrapper_title(criterion_name(criteria[0])):
        return True
    return False


def sanitize_evaluation_criteria_names(evaluation: Any) -> Any:
    """Strip wrapped points wording from criterion names at the source.

    Names flow into outline titles, the requirement ledger, and Complete &
    clean's section specs. Cleaning once at extraction keeps every one of
    those from inheriting "- UP TO 160".
    """
    for criterion in (_get(evaluation, "criteria") or []):
        raw = str(_get(criterion, "name") or "")
        tidy = clean_criterion_name(raw)
        if tidy and tidy != raw:
            if hasattr(criterion, "name"):
                criterion.name = tidy
            elif isinstance(criterion, dict):
                criterion["name"] = tidy
    return evaluation


# ---------------------------------------------------------------------------
# Required submittals — an LLM completeness-verification agent, run as a
# SECOND, focused pass rather than folded into the outline call
#
# merge_closing_components_into_outline / the outline planner both find
# exhibits and closing forms as ONE responsibility among many inside a single
# LLM call that is also writing the outline, respecting a page cap, avoiding
# duplicates, and following ~20 other instructions. Observed on a live run
# (rfp-jw-3300d3eb): two consecutive Phase 2 runs over the SAME RFP returned
# 9 sections then 8 — the second run's extraction simply missed Exhibit 3,
# and neither run caught Exhibit 4 or 5. That is not a reasoning failure —
# it is instruction overload inside one call.
#
# The fix keeps this LLM-driven end to end, by design: rather than parsing
# RFP text for "EXHIBIT N:" with regex, a SECOND, narrowly-scoped LLM call
# is given exactly one job — read the RFP's submission instructions and the
# outline already produced, and report anything required that has no tab.
# A model with one question to answer is far more reliable than the same
# model mid-way through twenty; this is the same reason a senior editor
# reviews a draft in a separate pass rather than trying to catch every issue
# while first drafting it.
# ---------------------------------------------------------------------------

_MISSING_SUBMITTALS_SYSTEM = """You are a completeness-verification agent for an
RFP proposal outline. You are given the RFP's submission instructions and the
outline the offeror has already planned to draft. Your ONLY job: find any
submittal this RFP requires the offeror to include that has NO matching tab in
the outline below — exhibits, attachments, appendices, schedules, forms,
certifications, disclosures, signature pages, references.

Rules:
- Read the outline titles carefully before flagging anything — a tab titled
  differently from the RFP's own heading can still be the same submittal when
  it covers the same ask by meaning. Only report items with NO tab answering
  them, not items phrased differently than the RFP.
- When the RFP includes a mandatory content-format or submission-layout section,
  verify every row that section requires appears in the outline — use the buyer's
  own headings from THAT section, not generic labels.
- Do NOT flag reference-only material the RFP describes but does not ask the
  offeror to submit — sample contracts, NDAs, or attachments the RFP marks
  optional / "send only if requested" / describing what the AWARDED vendor
  must carry (insurance limits, bond terms). Those are not submittals.
- Do NOT flag anything already covered by a scored-criteria section (a tab
  answering "SECTION III — Strategic Planning" already covers that ask).
- Do NOT invent a requirement the RFP text does not state.
- List each missing item once. Use the RFP's own heading/number when it has
  one ("EXHIBIT 4 — New Mexico Resident Preference Certification"); if the RFP
  names it only in prose, write a clear plain-English title.

Return JSON only:
{
  "missing": [
    {
      "title": "the RFP's own heading, or a clear plain-English title",
      "mandatory": true,
      "reason": "one sentence: what the RFP says and why no current tab covers it"
    }
  ],
  "confidence": 0.0
}
Empty "missing" array is a complete, valid, and common answer — do not pad it.
"""


async def _find_missing_submittals_once(
    rfp_context: str,
    outline_titles: list[str],
    *,
    temperature: float,
) -> list[dict[str, Any]]:
    """One sample from the completeness-check agent. See the public wrapper
    below for why this is never called alone."""
    from app.services.proposal_intelligence.agent_base import safe_chat_json
    from app.services.proposal_rfp_excerpt import (
        closing_package_excerpt,
        submission_documents_excerpt,
    )

    titles_block = "\n".join(f"- {t}" for t in outline_titles if (t or "").strip())
    raw, _provider = await safe_chat_json(
        [
            {"role": "system", "content": _MISSING_SUBMITTALS_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Current outline ({len(outline_titles)} tabs):\n"
                    f"{titles_block or '(empty)'}\n\n"
                    "Submission-documents excerpt (what the RFP asks to be "
                    f"returned):\n{submission_documents_excerpt(rfp_context)[:24000]}\n\n"
                    "Closing/forms/attachments excerpt:\n"
                    f"{closing_package_excerpt(rfp_context)[:16000]}"
                ),
            },
        ],
        max_tokens=2048,
        temperature=temperature,
        agent_name="missing_submittals_check",
    )
    missing = raw.get("missing") if isinstance(raw, dict) else None
    if not isinstance(missing, list):
        return []
    result: list[dict[str, Any]] = []
    for item in missing:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        result.append(
            {
                "title": title,
                "mandatory": bool(item.get("mandatory", True)),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return result


async def find_missing_submittals_via_llm(
    rfp_context: str,
    outline_titles: list[str],
) -> list[dict[str, Any]]:
    """Ask the completeness-check agent TWICE, independently, and union.

    One call is still one sample. Observed on a live run (rfp-jw-3300d3eb):
    a single completeness-check call reported only a subcontractor-disclosure
    line and missed all three exhibits it had correctly found moments earlier
    in a separate test — the exact failure mode this agent exists to catch,
    now showing up inside the catcher itself. That is not a bug to patch; it
    is what "one non-deterministic call" always means, including this one.

    Two independent samples at different temperatures are far less likely to
    both miss the same requirement than one sample is to catch everything —
    the same self-consistency principle behind majority-vote verification
    elsewhere in this codebase. Findings are unioned (OR, not intersected):
    something either call flags is worth a tab, not something both must agree
    on — the asymmetric cost here is a missed mandatory submittal, not one
    extra tab a human can merge away in review.

    Still exactly zero regex/keyword parsing of the RFP text — both calls are
    the same LLM reasoning over the same excerpts, just sampled twice.
    """
    import asyncio

    first, second = await asyncio.gather(
        _find_missing_submittals_once(rfp_context, outline_titles, temperature=0.15),
        _find_missing_submittals_once(rfp_context, outline_titles, temperature=0.55),
    )
    merged: list[dict[str, Any]] = []
    seen_titles: list[str] = []
    for item in first + second:
        if any(outline_titles_near_duplicate(item["title"], t) for t in seen_titles):
            continue
        seen_titles.append(item["title"])
        merged.append(item)
    return merged


def _submittal_covered_by_title(sections: list[Any], title: str) -> bool:
    """True when some outline tab already represents this reported title.

    Same title-overlap logic ``outline_covers_criterion`` uses — a wrapper
    tab never counts, and a shared head word alone is not enough.
    """
    if is_evaluation_form_wrapper_title(title):
        return True
    title_tokens = {t for t in outline_title_tokens(title) if len(t) >= 4}
    for section in sections:
        section_title = str(_get(section, "title") or "")
        if not section_title:
            continue
        if outline_titles_near_duplicate(section_title, title):
            return True
        if title_tokens:
            section_tokens = {
                t for t in outline_title_tokens(section_title) if len(t) >= 4
            }
            overlap = len(title_tokens & section_tokens)
            if overlap >= 2 and overlap >= len(title_tokens) * 0.6:
                return True
    return False


async def ensure_missing_submittals_coverage(
    sections: list[Any],
    rfp_context: str,
    *,
    section_factory: Any = None,
) -> tuple[list[Any], list[str]]:
    """Ask the completeness agent, then inject a tab for whatever it reports.

    Injected tabs are stamped submissionInstrument="form" and
    protectFromCap=True: a submittal the buyer requires back is required,
    never optional padding.
    """
    kept = list(sections)
    added: list[str] = []
    reported = await find_missing_submittals_via_llm(
        rfp_context, [str(_get(s, "title") or "") for s in kept]
    )
    missing = [
        item
        for item in reported
        if item["mandatory"] and not _submittal_covered_by_title(kept, item["title"])
    ]
    if not missing:
        return kept, added

    next_order = 1 + max(
        (int(_as_points(_get(sec, "order")) or 0) for sec in kept), default=0
    )
    for item in missing:
        raw = {
            "id": f"rfp-submittal-{_slug(item['title'])}",
            "title": item["title"],
            "order": next_order,
            "required": True,
            "conditionalReason": item["reason"] or "Flagged missing by completeness check",
            "parentId": None,
            "children": [],
            "dependencies": [],
            "evaluationWeight": None,
            "protectFromCap": True,
            "submissionInstrument": "form",
        }
        section = section_factory(raw) if section_factory is not None else raw
        kept.append(section)
        added.append(raw["title"])
        next_order += 1
    for order, section in enumerate(kept, start=1):
        if hasattr(section, "order"):
            section.order = order
        elif isinstance(section, dict):
            section["order"] = order
    return kept, added


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").casefold()).strip("-")[:60]
