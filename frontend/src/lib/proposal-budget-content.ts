import type { ProposalOutline } from "@/types/proposal";

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
