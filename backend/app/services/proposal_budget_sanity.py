"""Deterministic, no-LLM budget sanity checks.

These catch objective, internal contradictions in a priced budget — defects
that need no stated buyer ceiling (RFP budget cap/floor) to detect, because
they are contradictions in the numbers themselves. This is why a $2,200
lump-sum total built from one priced line item and one completely empty line
item shipped unflagged: `collect_under_minimum_flags` needs `rfp_budget_floor`,
and the RFP in that case stated no budget at all.

Every function here must be able to accept `None`/garbage input and return an
empty list rather than raise — this runs inline in the budget-generation
pipeline and must never break it.
"""

from __future__ import annotations

from typing import Any


def _num(value: Any) -> float | None:
    """Coerce to float, treating None/non-numeric as None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def collect_budget_sanity_flags(budget: Any) -> list[str]:
    """Objective, RFP-independent defects in a priced budget.

    These need no stated buyer ceiling to detect — they are internal
    contradictions in the numbers themselves, which is why a $2,200
    total with an empty line item shipped unflagged.
    """
    flags: list[str] = []
    try:
        line_items = getattr(budget, "line_items", None) or []
        lump_sum_total = _num(getattr(budget, "lump_sum_total", None))

        # Precompute per-line priced state.
        priced_states: list[tuple[Any, bool, float | None]] = []
        for li in line_items:
            quantity = getattr(li, "quantity", None)
            rate = getattr(li, "rate", None)
            extended = getattr(li, "extended", None)
            ext_val = _num(extended)
            qty_val = _num(quantity)
            rate_val = _num(rate)
            has_extended = ext_val not in (None, 0)
            has_rate_or_qty = (rate_val not in (None, 0)) or (qty_val not in (None, 0))
            is_priced = has_extended or has_rate_or_qty
            priced_states.append((li, is_priced, ext_val))

        any_priced = any(is_priced for _, is_priced, _ in priced_states)

        # --- EMPTY PRICED LINE ---
        if any_priced:
            for li, is_priced, _ in priced_states:
                if is_priced:
                    continue
                extended = getattr(li, "extended", None)
                rate = getattr(li, "rate", None)
                quantity = getattr(li, "quantity", None)
                ext_val = _num(extended)
                rate_val = _num(rate)
                qty_val = _num(quantity)
                empty_extended = ext_val in (None, 0)
                empty_rate_or_qty = rate_val in (None, 0) or qty_val in (None, 0)
                if empty_extended and empty_rate_or_qty:
                    label = (
                        getattr(li, "description", None)
                        or getattr(li, "category", None)
                        or getattr(li, "id", None)
                        or "unnamed line item"
                    )
                    flags.append(
                        f"Empty priced line: \"{label}\" has no quantity, rate, or "
                        "extended amount, while other line items in this budget are "
                        "priced."
                    )

        # --- TOTAL MISMATCH ---
        if lump_sum_total is not None and lump_sum_total > 0:
            extended_sum = 0.0
            any_extended = False
            for _, _, ext_val in priced_states:
                if ext_val is not None:
                    extended_sum += ext_val
                    any_extended = True
            if any_extended and abs(extended_sum - lump_sum_total) > 1.0:
                flags.append(
                    "Total mismatch: line items sum to "
                    f"{extended_sum:,.2f} but lump_sum_total is {lump_sum_total:,.2f}."
                )

        # --- SINGLE-LINE TOTAL ---
        if len(line_items) > 1 and lump_sum_total is not None and lump_sum_total > 0:
            nonzero_extended = [
                (li, ext_val)
                for li, _, ext_val in priced_states
                if ext_val is not None and ext_val != 0
            ]
            if len(nonzero_extended) == 1:
                _, only_ext = nonzero_extended[0]
                if abs(only_ext - lump_sum_total) <= 1.0:
                    other_count = len(line_items) - 1
                    flags.append(
                        "Single-line total: the entire fee rests on one line item; "
                        f"{other_count} other line item(s) are unpriced."
                    )
    except Exception:  # noqa: BLE001
        return []

    return flags


def _derive_scope_label(title: str) -> str | None:
    """Return the leading label token if the title starts with 'Word N' style."""
    if not title:
        return None
    stripped = title.strip()
    parts = stripped.split()
    if len(parts) < 2:
        return None
    word, number = parts[0], parts[1].rstrip(":—-.")
    if not number.isdigit():
        return None
    if not word[:1].isalpha():
        return None
    return f"{word} {number}"


def budget_scope_group_coverage(budget: Any, rfp_sections: Any) -> tuple[set[str], set[str]]:
    """(groups named by budget line items, groups the RFP defines).

    Groups are derived generically: an RFP section title is treated as
    defining a scope group only when several titles share a repeated leading
    word followed by a number (e.g. "Group 1 — X", "Group 2 — Y"). The shared
    leading word is not hardcoded — whatever word repeats becomes the label.
    """
    try:
        sections = list(rfp_sections or [])
        titles = [getattr(s, "title", None) or "" for s in sections]
        labels = [_derive_scope_label(t) for t in titles]
        labels = [lbl for lbl in labels if lbl]
        if len(labels) < 2:
            return set(), set()

        # Confirm a repeated leading word across at least two labels.
        leading_words = [lbl.split()[0] for lbl in labels]
        word_counts: dict[str, int] = {}
        for w in leading_words:
            word_counts[w] = word_counts.get(w, 0) + 1
        dominant_word = max(word_counts, key=lambda w: word_counts[w])
        if word_counts[dominant_word] < 2:
            return set(), set()

        rfp_groups = {lbl for lbl in labels if lbl.split()[0] == dominant_word}
        if not rfp_groups:
            return set(), set()

        line_items = getattr(budget, "line_items", None) or []
        budget_groups: set[str] = set()
        for li in line_items:
            text = " ".join(
                str(x)
                for x in (getattr(li, "description", None), getattr(li, "category", None))
                if x
            )
            for group in rfp_groups:
                if group.lower() in text.lower():
                    budget_groups.add(group)

        return budget_groups, rfp_groups
    except Exception:  # noqa: BLE001
        return set(), set()


def collect_budget_scope_gap_flags(budget: Any, rfp_sections: Any) -> list[str]:
    """Flag only when the budget prices a strict subset of the RFP's scope groups."""
    try:
        found, all_groups = budget_scope_group_coverage(budget, rfp_sections)
        if not found or not all_groups:
            return []
        if found < all_groups:
            missing = sorted(all_groups - found)
            return [
                f"Budget prices only {len(found)} of the RFP's {len(all_groups)} scope "
                f"groups ({', '.join(missing)} unpriced)."
            ]
        return []
    except Exception:  # noqa: BLE001
        return []
