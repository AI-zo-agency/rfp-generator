import type { Page } from "playwright";
import type { JustWinLead } from "./types";
import { getJustWinBaseUrl } from "./browser";
import { getTitleFilter, matchesTitleFilter } from "./title-filter";

const DEFAULT_TABS: { label: RegExp; tab: JustWinLead["tab"] }[] = [
  { label: /hot leads/i, tab: "hot" },
  { label: /warm leads/i, tab: "warm" },
  { label: /^review/i, tab: "review" },
];

const FILTERED_TABS: { label: RegExp; tab: JustWinLead["tab"] }[] = [
  { label: /hot leads/i, tab: "hot" },
  { label: /warm leads/i, tab: "warm" },
  { label: /^review/i, tab: "review" },
  { label: /^archived$/i, tab: "warm" },
];

async function waitForJustWinReady(page: Page): Promise<void> {
  const loading = page.locator(".loading-state");
  if ((await loading.count()) > 0) {
    await loading
      .first()
      .waitFor({ state: "hidden", timeout: 60000 })
      .catch(() => undefined);
  }
  await page.waitForTimeout(750);
}

async function useSearchBox(page: Page, query: string): Promise<void> {
  await waitForJustWinReady(page);

  const search = page
    .locator('input[placeholder*="Search solicitations" i]')
    .first();

  if ((await search.count()) === 0) return;

  await search.fill("");
  await search.fill(query);
  await waitForJustWinReady(page);
  await page.waitForTimeout(2000);
}

async function clickTab(page: Page, label: RegExp): Promise<boolean> {
  await waitForJustWinReady(page);

  const inboxTab = page
    .locator(".lead-inbox-header [role='tab']")
    .filter({ hasText: label });

  if ((await inboxTab.count()) > 0) {
    await inboxTab.first().click({ timeout: 15000 });
    await waitForJustWinReady(page);
    return true;
  }

  const roleTab = page.getByRole("tab", { name: label });
  if ((await roleTab.count()) > 0) {
    await roleTab.first().click({ timeout: 15000 });
    await waitForJustWinReady(page);
    return true;
  }

  return false;
}

async function findStrictLeadByTitle(
  page: Page,
  titleFilter: string,
  tab: JustWinLead["tab"],
  runSearch = false
): Promise<JustWinLead | null> {
  if (runSearch) {
    await useSearchBox(page, titleFilter);
  }

  await waitForJustWinReady(page);

  const matches = page.getByText(titleFilter, { exact: false });
  const count = await matches.count();
  if (count === 0) return null;

  for (let i = 0; i < count; i++) {
    const candidate = matches.nth(i);
    const text = ((await candidate.textContent()) ?? "").replace(/\s+/g, " ").trim();
    if (!matchesTitleFilter(text, titleFilter)) continue;
    if (text.length > titleFilter.length + 30) continue;

    await waitForJustWinReady(page);
    await candidate.scrollIntoViewIfNeeded();
    await candidate.click({ timeout: 15000 });
    await page.waitForTimeout(2000);

    const detailUrl = page.url();
    if (detailUrl.includes("/login")) {
      await page.goBack({ waitUntil: "domcontentloaded" });
      continue;
    }

    const body = ((await page.locator("main, body").first().textContent()) ?? "")
      .replace(/\s+/g, " ")
      .trim();
    const dueMatch = body.match(
      /Due\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})/i
    );
    const postedMatch = body.match(
      /\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2})\b/i
    );
    const pathParts = detailUrl.split("/").filter(Boolean);
    const leadsIndex = pathParts.indexOf("leads");
    const externalId =
      leadsIndex >= 0 && pathParts[leadsIndex + 1]
        ? pathParts[leadsIndex + 1]
        : pathParts.pop() ?? `jw-${Date.now()}`;

    const locationMatch =
      text.match(/\[([A-Z]{2})\]/) ?? body.match(/\b([A-Z]{2})\b/);

    return {
      externalId,
      title: titleFilter,
      location: locationMatch?.[1] ?? "",
      postedDate: postedMatch?.[1] ?? "",
      dueDate: dueMatch?.[1] ?? "",
      score: 4,
      description: body.slice(0, 500),
      detailUrl,
      tab,
    };
  }

  return null;
}

export async function scrapeAllLeads(page: Page): Promise<JustWinLead[]> {
  const titleFilter = getTitleFilter();

  if (titleFilter) {
    console.log(`[justwin-sync] strict filter: "${titleFilter}"`);

    await waitForJustWinReady(page);
    await useSearchBox(page, titleFilter);

    let lead = await findStrictLeadByTitle(page, titleFilter, "hot", false);
    if (lead) {
      console.log(`[justwin-sync] matched via search: "${lead.title}" (${lead.detailUrl})`);
      return [lead];
    }

    for (const { label, tab } of FILTERED_TABS) {
      const clicked = await clickTab(page, label);
      if (!clicked) continue;

      await useSearchBox(page, titleFilter);
      lead = await findStrictLeadByTitle(page, titleFilter, tab, false);
      if (lead) {
        console.log(`[justwin-sync] matched in ${label}: "${lead.title}" (${lead.detailUrl})`);
        return [lead];
      }
    }

    throw new Error(
      `No RFP found matching "${titleFilter}". Check JustWin tabs or update JUSTWIN_RFP_TITLE_FILTER.`
    );
  }

  const all: JustWinLead[] = [];
  const seen = new Set<string>();

  for (const { label, tab } of DEFAULT_TABS) {
    await clickTab(page, label);
    await page.waitForTimeout(1000);

    const rows = page.locator("table tbody tr, [data-testid='lead-row']");
    const count = await rows.count();

    for (let i = 0; i < count; i++) {
      const row = rows.nth(i);
      const titleLink = row.locator("a").first();
      const title = ((await titleLink.textContent()) ?? "").trim();
      if (!title) continue;

      let detailUrl = (await titleLink.getAttribute("href")) ?? "";
      if (!detailUrl) continue;

      const baseUrl = getJustWinBaseUrl();
      if (!detailUrl.startsWith("http")) {
        detailUrl = `${baseUrl}${detailUrl.startsWith("/") ? "" : "/"}${detailUrl}`;
      }

      const externalId =
        detailUrl.split("/").filter(Boolean).pop() ?? `jw-${tab}-${i}`;

      if (seen.has(externalId)) continue;
      seen.add(externalId);

      all.push({
        externalId,
        title,
        location: title.match(/\[([A-Z]{2})\]/)?.[1] ?? "",
        postedDate: "",
        dueDate: "",
        score: 4,
        description: title,
        detailUrl,
        tab,
      });
    }
  }

  return all;
}
