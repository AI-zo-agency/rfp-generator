/**
 * Turn raw pipeline notes into scannable dashboard activity lines.
 * Old stored notes still contain full Go/No-Go essays — compress them here.
 */

export type ActivityKind = "proposal" | "go_no_go" | "personas" | "other";

export interface ActivitySummary {
  headline: string;
  kind: ActivityKind;
  scoreLabel: string | null;
}

export function summarizeActivityAction(
  raw: string,
  actor?: string | null
): ActivitySummary {
  const text = (raw || "").trim();
  const lower = text.toLowerCase();

  if (!text) {
    return { headline: "Pipeline update", kind: "other", scoreLabel: null };
  }

  const overallMatch = text.match(/Overall\s+(\d+(?:\.\d+)?)\s*\/\s*5/i);
  const worthMatch = text.match(/Worth\s+(\d+(?:\.\d+)?)\s*\/\s*5/i);
  const scoreLabel = overallMatch
    ? `${overallMatch[1]}/5`
    : worthMatch
      ? `Worth ${worthMatch[1]}/5`
      : null;

  if (
    lower.includes("go/no-go") ||
    actor === "Go/No-Go" ||
    /\b(go with conditions|no-go|marked as go)\b/i.test(text)
  ) {
    let decision = "Complete";
    if (/paused|insufficient/i.test(text)) {
      decision = "Paused";
    } else if (/marked as go/i.test(text)) {
      decision = "Marked Go";
    } else if (/\bno-go\b/i.test(text) && !/go with conditions/i.test(text)) {
      decision = "No-Go";
    } else if (/go with conditions|\breview\b/i.test(text)) {
      decision = "Review";
    } else if (/\bgo\b/i.test(text) && !/no-go/i.test(text)) {
      decision = "Go";
    }
    return {
      headline: `Go/No-Go · ${decision}`,
      kind: "go_no_go",
      scoreLabel,
    };
  }

  if (lower.includes("key personas")) {
    const ids = text.includes(":")
      ? text.split(":")[1]?.split(",").filter(Boolean).length ?? 0
      : 0;
    return {
      headline: ids > 0 ? `Key personas · ${ids} selected` : "Key personas selected",
      kind: "personas",
      scoreLabel: null,
    };
  }

  if (lower.includes("draft updated") || lower.includes("proposal draft")) {
    const sections = text.match(/(\d+)\s*\/\s*(\d+)\s*sections/i);
    return {
      headline: sections
        ? `Draft updated · ${sections[1]}/${sections[2]} sections`
        : "Draft updated",
      kind: "proposal",
      scoreLabel: null,
    };
  }

  const head = text.split(/[.—]/)[0]?.trim() || text;
  return {
    headline: head.length > 72 ? `${head.slice(0, 69).trimEnd()}…` : head,
    kind: actor === "Proposal" ? "proposal" : "other",
    scoreLabel,
  };
}
