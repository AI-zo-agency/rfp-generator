/** Turn bracket handoff tags into plain-language asks for UI (not export). */

export interface HumanizedGap {
  owner: string | null;
  title: string;
  detail: string;
  action: string;
  rawTag: string;
}

function stripBrackets(tag: string): string {
  return (tag || "").trim().replace(/^\[/, "").replace(/\]$/, "").trim();
}

function bodyWithoutPrefix(inner: string): string {
  return inner.replace(/^MANUAL\s+FILL:\s*/i, "").trim();
}

function parseManualFillInner(inner: string): { owner: string | null; body: string } {
  // Only an em dash ("—") separates owner from body in these tags — a plain
  // hyphen is common inside ordinary body text ("pre-proposal conference")
  // and must never be mistaken for the separator, or the split lands mid-word.
  const match = inner.match(/^MANUAL\s+FILL:\s*([^—]+?)\s*—\s*(.+)$/i);
  if (!match) {
    return { owner: null, body: bodyWithoutPrefix(inner) };
  }
  const owner = match[1].trim();
  // A real owner is a short name/label ("Sonja", "Ella", "Operations",
  // "Designer") — when the text before the first em dash is a whole clause
  // instead, this tag never had a named owner at all, and that dash is just
  // a natural break inside plain instructions. Splitting there would show a
  // full sentence as the bold "owner" label instead of a short tag.
  if (owner.length > 30 || /[.!?]/.test(owner)) {
    return { owner: null, body: bodyWithoutPrefix(inner) };
  }
  return { owner, body: match[2].trim() };
}

function splitCodeAndDetail(body: string): { code: string; detail: string } {
  const pipe = body.indexOf("|");
  if (pipe < 0) {
    return { code: body.trim(), detail: "" };
  }
  return {
    code: body.slice(0, pipe).trim(),
    detail: body.slice(pipe + 1).trim(),
  };
}

function cleanKpiDetail(detail: string): string {
  return detail
    .replace(/^RFQ-named KPIs? missing from Methodology\/Reporting:\s*/i, "")
    .replace(/^RFQ-named KPI missing from Methodology\/Reporting:\s*/i, "")
    .replace(/^['"]|['"]\.?$/g, "")
    .trim();
}

export function humanizeGapTag(tag: string): HumanizedGap {
  const rawTag = tag.trim();
  const inner = stripBrackets(rawTag);

  if (/^VERIFY:/i.test(inner)) {
    const field = inner.replace(/^VERIFY:\s*/i, "").trim();
    return {
      owner: null,
      title: "Confirm before submit",
      detail: field,
      action: "Replace with verified fact from KB or delete if not RFP-required.",
      rawTag,
    };
  }

  if (/^MANUAL\s+FILL:/i.test(inner)) {
    const { owner, body } = parseManualFillInner(inner);
    const { code, detail } = splitCodeAndDetail(body);
    const detailClean = detail || code;

    if (/rfq_named_kpi|RFQ-named KPI/i.test(code + detail)) {
      const kpis = cleanKpiDetail(detailClean);
      return {
        owner,
        title: "Add RFP KPIs to reporting",
        detail: kpis || "Include the named metrics in methodology / analytics prose.",
        action: "Weave the exact RFP wording into this section, or ask Sonja for approved phrasing.",
        rawTag,
      };
    }

    if (/primary_contact_lock|primary contact lock/i.test(code + detail)) {
      return {
        owner,
        title: "Primary contact mismatch",
        detail: detailClean.replace(/^Primary contact lock is/i, "Locked contact is"),
        action: "Use the locked primary contact everywhere in the proposal.",
        rawTag,
      };
    }

    if (/disqualification_risk|wet-ink|sealed package|physical signed/i.test(code + detail)) {
      return {
        owner,
        title: "Physical submission requirement",
        detail: detailClean,
        action: "Confirm packaging / signatures with Sonja before upload.",
        rawTag,
      };
    }

    if (/budget_grounding|pricing flag|DISQUALIFY RISK/i.test(code + detail)) {
      return {
        owner,
        title: "Budget needs review",
        detail: detailClean,
        action: "Open Budget refinery with Sonja — do not guess dollar amounts.",
        rawTag,
      };
    }

    if (/deterministic\.|deterministic\s/i.test(code + inner)) {
      const readable = (detailClean || code)
        .replace(/deterministic(?:\.[a-z0-9_]+)+/gi, "")
        .replace(/[_]+/g, " ")
        .replace(/\s{2,}/g, " ")
        .trim();
      if (/deferred|upon request/i.test(code + detailClean + inner)) {
        return {
          owner,
          title: "Do not defer facts",
          detail:
            "Remove “upon request” and state the knowledge-base fact, or leave a single Sonja fill — not a scan code.",
          action: "Edit this section or ask chat to replace the sentence from KB.",
          rawTag,
        };
      }
      return {
        owner,
        title: "Needs your input",
        detail: (readable || detailClean || "Complete this handoff in Content.").slice(0, 220),
        action: "Fill in Content or send the value in section chat.",
        rawTag,
      };
    }

    if (detailClean && detailClean !== code) {
      return {
        owner,
        title: "Needs your input",
        detail: detailClean,
        action: "Fill in Content or provide the fact in section chat.",
        rawTag,
      };
    }

    return {
      owner,
      title: "Needs your input",
      detail: code,
      action: "Fill in Content or provide the fact in section chat.",
      rawTag,
    };
  }

  if (/^FLAG:/i.test(inner)) {
    return {
      owner: null,
      title: "Review flagged claim",
      detail: inner.replace(/^FLAG:\s*/i, ""),
      action: "Verify against ClientList/KB or remove unsupported wording.",
      rawTag,
    };
  }

  if (/^DESIGNER NOTE:/i.test(inner)) {
    return {
      owner: "Designer",
      title: "Designer handoff",
      detail: inner.replace(/^DESIGNER NOTE:\s*/i, ""),
      action: "Complete in InDesign/export — not a narrative edit.",
      rawTag,
    };
  }

  return {
    owner: null,
    title: "Action needed",
    detail: inner,
    action: "Resolve before submit.",
    rawTag,
  };
}

/** One-line label for checklist rows. */
export function gapChecklistLabel(tag: string): string {
  const h = humanizeGapTag(tag);
  const prefix = h.owner ? `${h.owner}: ` : "";
  const detail =
    h.detail.length > 120 ? `${h.detail.slice(0, 117).trim()}…` : h.detail;
  return `${prefix}${h.title} — ${detail}`;
}

export function isInternalScanTag(tag: string): boolean {
  const inner = stripBrackets(tag || "").toLowerCase();
  return (
    inner.includes("deterministic.fabricated_fact") ||
    inner.includes("deterministic.unverified") ||
    inner.includes("deferred information") ||
    inner.includes("upon_request") ||
    inner.includes("upon request is forbidden")
  );
}

export function isManualFillTag(text: string): boolean {
  return /^\[MANUAL\s+FILL:/i.test((text || "").trim());
}
