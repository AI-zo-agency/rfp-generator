"""Assemble Go/No-Go stage-one report in the Claude Project analyst format.

Report sections are built deterministically from structured data (capability
matrix, hard facts, decision matrix). Narrative bullets come from one small LLM
brief — not from a free-form stageOneReport essay.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.go_no_go import GoNoGoCapabilityRow, GoNoGoDecisionMatrixRow

_STATUS_ICON = {
    "verified": "✅",
    "partial": "🟡",
    "gap": "🔴",
    "unverified": "🔴",
}

_STATUS_LABEL = {
    "verified": "Strong",
    "partial": "Needs honest framing",
    "gap": "Real gap",
    "unverified": "Unverified",
}


def _cell(text: str) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _status_display(row: GoNoGoCapabilityRow) -> str:
    icon = _STATUS_ICON.get(row.status, "🔴")
    label = _STATUS_LABEL.get(row.status, "Gap")
    detail = (row.evidence or row.kb_source or row.downgrade_reason or "").strip()
    if row.status == "verified":
        return f"{icon} {label}" + (f", {detail[:120]}" if detail else "")
    if row.status == "partial":
        return f"{icon} {label}" + (f" — {detail[:140]}" if detail else "")
    reason = row.downgrade_reason or detail or "no verifiable KB evidence"
    return f"{icon} {label} — {reason[:160]}"


def render_capability_assessment_table(rows: list[GoNoGoCapabilityRow]) -> str:
    if not rows:
        return ""
    lines = [
        "## Capability Assessment",
        "",
        "| Requirement | Evidence | Status |",
        "| --- | --- | --- |",
    ]
    for row in rows:
        evidence = row.evidence or row.kb_source or row.downgrade_reason or "—"
        lines.append(
            f"| {_cell(row.requirement)} | {_cell(evidence)} | {_cell(_status_display(row))} |"
        )
    return "\n".join(lines)


def _parse_eval_line(line: str) -> tuple[str, int | None]:
    """Parse 'Section name: 200 points' → (section, 200)."""
    raw = (line or "").strip()
    if not raw:
        return "", None
    if ":" in raw:
        label, rest = raw.rsplit(":", 1)
        m = re.search(r"(\d{1,4})", rest)
        pts = int(m.group(1)) if m else None
        return label.strip(), pts
    m = re.search(r"(\d{1,4})\s*(?:points?|pts?|%)\b", raw, re.I)
    pts = int(m.group(1)) if m else None
    return raw, pts


def render_evaluation_criteria_table(
    evaluation_lines: list[str],
    positions: list[dict[str, Any]],
    *,
    total_points: int | None = None,
) -> str:
    by_section: dict[str, str] = {}
    for item in positions:
        section = str(item.get("section") or "").strip()
        position = str(item.get("position") or item.get("ourPosition") or "").strip()
        if section and position:
            by_section[section.casefold()] = position

    if not evaluation_lines and not positions:
        return (
            "## Evaluation Criteria\n\n"
            "Point-weighted scoring is **not disclosed** in this RFP. "
            "Describe scored question groups from the solicitation only — do not invent weights."
        )

    header_total = total_points or sum(
        pts for _, pts in (_parse_eval_line(line) for line in evaluation_lines) if pts
    )
    title = f"## Evaluation Criteria ({header_total:,} points total)" if header_total else "## Evaluation Criteria"

    lines = [title, "", "| Section | Points | Our Position |", "| --- | ---: | --- |"]

    if evaluation_lines:
        for line in evaluation_lines:
            section, pts = _parse_eval_line(line)
            pos = by_section.get(section.casefold(), "")
            if not pos:
                for key, val in by_section.items():
                    if key in section.casefold() or section.casefold() in key:
                        pos = val
                        break
            pts_cell = str(pts) if pts is not None else "—"
            lines.append(f"| {_cell(section)} | {pts_cell} | {_cell(pos or '—')} |")
    else:
        for item in positions:
            section = str(item.get("section") or "—")
            pts = item.get("points")
            pts_cell = str(pts) if pts is not None else "—"
            pos = str(item.get("position") or item.get("ourPosition") or "—")
            lines.append(f"| {_cell(section)} | {pts_cell} | {_cell(pos)} |")

    return "\n".join(lines)


def render_decision_matrix_section(
    matrix: list[GoNoGoDecisionMatrixRow],
) -> str:
    if not matrix:
        return ""
    lines = [
        "## Go/No-Go Decision Matrix",
        "",
        "| Dimension | Score | Notes |",
        "| --- | ---: | --- |",
    ]
    total = 0
    for row in matrix:
        lines.append(
            f"| {_cell(row.dimension)} | {row.score}/5 | {_cell(row.notes)} |"
        )
        total += row.score
    overall = round(total / len(matrix), 1) if matrix else 0
    lines += [
        "",
        f"**Overall Go Score — average of matrix dimensions: {overall} / 5**",
    ]
    return "\n".join(lines)


def render_recommendation_section(
    recommendation: str | None,
    *,
    conditions: list[str],
    critical_gaps: list[str],
) -> str:
    label = {
        "go": "GO",
        "no_go": "NO-GO",
        "review": "GO WITH CONDITIONS",
    }.get(recommendation or "review", "GO WITH CONDITIONS")

    lines = ["## Recommendation", "", f"**{label}**", ""]
    items = conditions or critical_gaps
    if items:
        lines.append("Two things need resolution before drafting begins, not after:" if label == "GO WITH CONDITIONS" else "Before pursuing:")
        for idx, item in enumerate(items[:8], start=1):
            lines.append(f"{idx}. {item}")
    return "\n".join(lines)


def build_stage_one_report(
    *,
    compliance_snapshot: list[str],
    capability_rows: list[GoNoGoCapabilityRow],
    capability_summary: str,
    evaluation_lines: list[str],
    evaluation_positions: list[dict[str, Any]],
    evaluation_summary: str,
    decision_matrix: list[GoNoGoDecisionMatrixRow],
    recommendation: str | None,
    conditions: list[str],
    critical_gaps: list[str],
    evaluation_total: int | None = None,
) -> str:
    """Claude Project-style Stage 1 brief — fixed section order."""
    parts: list[str] = []

    parts.append("## Compliance Snapshot")
    parts.append("")
    if compliance_snapshot:
        parts.extend(compliance_snapshot)
    else:
        parts.append(
            "Confirm submission format (portal vs PDF), geographic preference rules, "
            "registration/licensing gates, required exhibits, and exceptions to standard terms from the RFP."
        )

    cap_table = render_capability_assessment_table(capability_rows)
    if cap_table:
        parts.append("")
        parts.append(cap_table)

    if capability_summary.strip():
        parts.append("")
        parts.append(capability_summary.strip())

    eval_table = render_evaluation_criteria_table(
        evaluation_lines,
        evaluation_positions,
        total_points=evaluation_total,
    )
    parts.append("")
    parts.append(eval_table)

    if evaluation_summary.strip():
        parts.append("")
        parts.append(evaluation_summary.strip())

    matrix_section = render_decision_matrix_section(decision_matrix)
    if matrix_section:
        parts.append("")
        parts.append(matrix_section)

    rec_section = render_recommendation_section(
        recommendation,
        conditions=conditions,
        critical_gaps=critical_gaps,
    )
    parts.append("")
    parts.append(rec_section)

    return "\n\n".join(p for p in parts if p.strip())
