import type { ProposalBudget, ProposalOutline } from "@/types/proposal";

const BUDGET_TITLE_RE =
  /\b(budget|pricing|price\s*proposal|fee\s*schedule|cost\s*proposal|compensation)\b/i;

export function isBudgetSectionTitle(title: string): boolean {
  const t = title.toLowerCase();
  if (t.includes("budget")) return true;
  if (t.includes("pricing")) return true;
  if (t.includes("fee")) return true;
  if (t.includes("compensation")) return true;
  return BUDGET_TITLE_RE.test(title);
}

export function findBudgetSection(
  sections: ProposalOutline["sections"],
): (typeof sections)[number] | undefined {
  let best: (typeof sections)[number] | undefined;
  let bestScore = 0;
  for (const section of sections) {
    const t = section.title.toLowerCase();
    let score = 0;
    if (t.includes("budget")) score += 4;
    if (t.includes("pricing") || t.includes("price proposal")) score += 3;
    if (t.includes("fee")) score += 2;
    if (t.includes("cost")) score += 1;
    if (t.includes("compensation")) score += 2;
    if (score > bestScore) {
      bestScore = score;
      best = section;
    }
  }
  return bestScore > 0 ? best : undefined;
}

function formatUsd(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

/** Client-side fallback if API does not return an updated draft. */
export function formatBudgetAsSectionContent(budget: ProposalBudget): string {
  const lines: string[] = [];

  if (budget.qualifyingLanguage?.trim()) {
    lines.push(budget.qualifyingLanguage.trim(), "");
  }

  lines.push("## Budget Summary");
  if (budget.rfpBudgetCap != null) {
    lines.push(`- **RFP budget cap:** ${formatUsd(budget.rfpBudgetCap)}`);
  }
  if (budget.agencyRevenueEstimate != null) {
    lines.push(
      `- **Agency revenue estimate (zö fee income only):** ${formatUsd(budget.agencyRevenueEstimate)}`,
    );
  }
  if (budget.agencyFeeSubtotal != null && budget.clientMediaPassthrough) {
    lines.push(
      `- **Agency fee subtotal:** ${formatUsd(budget.agencyFeeSubtotal)}`,
    );
  }
  if (budget.clientMediaPassthrough != null && budget.clientMediaPassthrough > 0) {
    lines.push(
      `- **Client media pass-through (not agency revenue):** ${formatUsd(budget.clientMediaPassthrough)}`,
    );
  }
  if (budget.totalClientInvoicing != null && budget.clientMediaPassthrough) {
    lines.push(
      `- **Total estimated client invoicing:** ${formatUsd(budget.totalClientInvoicing)}`,
    );
  }
  if (budget.lumpSumTotal != null) {
    lines.push(`- **Lump sum (base term):** ${formatUsd(budget.lumpSumTotal)}`);
  }
  if (budget.directExpensesTotal != null) {
    lines.push(
      `- **Direct expenses:** ${formatUsd(budget.directExpensesTotal)}`,
    );
  }
  if (budget.pricingTier) {
    lines.push(`- **Pricing tier:** ${budget.pricingTier}`);
  }
  if (budget.feeStructure) {
    lines.push(`- **Fee structure:** ${budget.feeStructure}`);
  }
  if (budget.budgetFormat) {
    lines.push(
      `- **Budget format:** ${budget.budgetFormat.replace(/_/g, " ")}`,
    );
  }
  lines.push("");

  if (budget.scopeSummary?.trim()) {
    lines.push("## Scope Summary", budget.scopeSummary.trim(), "");
  }

  if (budget.tiers.length > 0) {
    lines.push("## Pricing Tiers");
    for (const tier of budget.tiers) {
      const rec =
        tier.id === budget.recommendedTierId ? " *(recommended)*" : "";
      lines.push(
        `### ${tier.name}${rec} — ${formatUsd(tier.total ?? null)}`,
      );
      if (tier.rationale) lines.push(tier.rationale, "");
    }
  }

  if (budget.lineItems.length > 0) {
    lines.push(
      "## Budget Line Items",
      "",
      "| Category | Description | Qty | Unit | Rate | Extended |",
      "| --- | --- | ---: | --- | ---: | ---: |",
    );
    for (const item of budget.lineItems) {
      const desc = item.namedPerson
        ? `${item.roleTitle || item.description} — ${item.namedPerson}`
        : item.description;
      lines.push(
        `| ${item.category} | ${desc} | ${item.quantity ?? "—"} | ${item.unit} | ${formatUsd(item.rate ?? null)} | ${formatUsd(item.extended ?? null)} |`,
      );
    }
    lines.push("");
  }

  if (budget.pricingFlags.length > 0) {
    lines.push("## Pricing Flags");
    for (const flag of budget.pricingFlags) {
      lines.push(`- ${flag}`);
    }
    lines.push("");
  }

  if (budget.designBrief?.trim()) {
    lines.push(`[DESIGNER NOTE: ${budget.designBrief.trim()}]`);
  }

  return lines.join("\n").trim();
}

export function mergeBudgetIntoOutline(
  outline: ProposalOutline,
  budget: ProposalBudget,
): ProposalOutline {
  const content = formatBudgetAsSectionContent(budget);
  const existing = findBudgetSection(outline.sections);
  const sections = outline.sections.map((section) =>
    section.id === existing?.id
      ? { ...section, content, status: "generated" as const }
      : section,
  );

  if (!existing) {
    sections.push({
      id: `section-budget-pricing`,
      title: "Budget & Pricing",
      content,
      status: "generated",
      source: "generated",
      mode: "write",
      wordTarget: 900,
      required: true,
      custom: false,
    });
  }

  return {
    sections,
    updatedAt: new Date().toISOString(),
  };
}
