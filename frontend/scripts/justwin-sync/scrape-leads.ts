import type { JustWinLead } from "./types";
import {
  LIFECYCLE_STATES,
  fetchLeadsForTab,
  type JustWinApiClient,
  type LifecycleState,
} from "./justwin-api";

function resolveTabs(targetTab: string): LifecycleState[] {
  const requested = (targetTab || "all").toLowerCase();
  if (requested === "all") return [...LIFECYCLE_STATES];

  const match = LIFECYCLE_STATES.find((tab) => tab === requested);
  if (!match) {
    throw new Error(
      `Unknown JustWin tab "${targetTab}". Expected one of: all, ${LIFECYCLE_STATES.join(", ")}`
    );
  }
  return [match];
}

/**
 * Collect JustWin leads for the selected tab(s) and posted date.
 *
 * Reads JustWin's JSON API rather than the lead table: the table renders no
 * detail links (navigation is a JS row handler) and no ARIA tab roles, so
 * scraping it produced one deduplicated row per sync regardless of selection.
 *
 * @param targetDate ISO date (YYYY-MM-DD) to match against each lead's posted
 *                   date, or empty/undefined to accept every date.
 * @param targetTab  "all" | "hot" | "warm" | "review"
 */
export async function collectLeads(
  client: JustWinApiClient,
  targetDate?: string,
  targetTab: string = "all"
): Promise<JustWinLead[]> {
  const tabs = resolveTabs(targetTab);
  const dateFilter = targetDate?.trim() || undefined;

  console.log(
    `[justwin-sync] tab(s): ${tabs.join(", ")}, posted date: ${dateFilter ?? "any"}`
  );

  const all: JustWinLead[] = [];
  const seen = new Set<string>();

  for (const tab of tabs) {
    for (const lead of await fetchLeadsForTab(client, tab, dateFilter)) {
      if (seen.has(lead.externalId)) continue;
      seen.add(lead.externalId);
      all.push(lead);
    }
  }

  return all;
}
