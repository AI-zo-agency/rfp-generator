import type { ComplianceCheckItem, PreSubmitIssue } from "@/types/proposal";

const CATEGORY_LABELS: Record<string, string> = {
  copy_paste: "Wrong client / copy-paste",
  placeholder: "Unfilled placeholders",
  voice: "Voice & tone",
  compliance: "Compliance",
};

/** Client-side fallback when API did not return issuesMarkdown (older cached reviews). */
export function buildIssuesMarkdown(
  client: string,
  title: string,
  summary: string,
  issues: PreSubmitIssue[],
  checklist: ComplianceCheckItem[]
): string {
  const lines = [
    `# Issues to fix — ${client}`,
    "",
    `**RFP:** ${title}`,
    "",
    summary,
    "",
  ];

  if (issues.length > 0) {
    lines.push("## Findings", "");
    const byCategory = new Map<string, PreSubmitIssue[]>();
    for (const issue of issues) {
      const key = issue.category || "other";
      const list = byCategory.get(key) ?? [];
      list.push(issue);
      byCategory.set(key, list);
    }

    const order = ["copy_paste", "placeholder", "voice", "compliance"];
    const categories = [
      ...order.filter((c) => byCategory.has(c)),
      ...[...byCategory.keys()].filter((c) => !order.includes(c)).sort(),
    ];

    for (const category of categories) {
      const catIssues = byCategory.get(category) ?? [];
      const label = CATEGORY_LABELS[category] ?? category.replace(/_/g, " ");
      lines.push(`### ${label}`, "");
      for (const issue of catIssues) {
        lines.push(`- **[${issue.severity.toUpperCase()}]** ${issue.message}`);
        if (issue.sectionTitle) {
          lines.push(`  - **Section:** ${issue.sectionTitle}`);
        }
        if (issue.excerpt) {
          lines.push(`  - **Excerpt:** \`${issue.excerpt.replace(/\n/g, " ").slice(0, 240)}\``);
        }
      }
      lines.push("");
    }
  } else {
    lines.push("## Findings", "", "_No automated findings._", "");
  }

  const failing = checklist.filter((row) => row.status !== "pass");
  if (failing.length > 0) {
    lines.push("## Compliance checklist", "");
    for (const row of failing) {
      lines.push(`- **[${row.status.toUpperCase()}]** ${row.item}`);
      if (row.notes) lines.push(`  - ${row.notes}`);
    }
    lines.push("");
  }

  return lines.join("\n").trim();
}
