import "./load-env";
import { getAuthenticatedContext, getJustWinBaseUrl } from "./browser";

async function main() {
  const { browser, context } = await getAuthenticatedContext();
  const page = await context.newPage();
  await page.goto(`${getJustWinBaseUrl()}/leads`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(6000);

  for (const tabName of ["hot", "warm", "review"]) {
    if (tabName !== "hot") {
      const link = page.locator(`#${tabName}-leads-link, #${tabName}-link`);
      const n = await link.count();
      console.log(`\n### clicking #${tabName}-leads-link (count=${n})`);
      if (n > 0) {
        await link.first().click().catch((e) => console.log("click err", e.message));
        await page.waitForTimeout(4000);
      }
    } else {
      console.log(`\n### tab: hot (default)`);
    }

    // EXACTLY what scrapeAllLeads uses
    const sel = "table tbody tr, [data-testid='lead-row'], a[href*='/leads/']";
    const info = await page.evaluate((s) => {
      const nodes = Array.from(document.querySelectorAll(s));
      return {
        matchCount: nodes.length,
        breakdown: {
          tbodyTr: document.querySelectorAll("table tbody tr").length,
          leadRow: document.querySelectorAll("[data-testid='lead-row']").length,
          anchorLeads: document.querySelectorAll("a[href*='/leads/']").length,
        },
        // For each of the first 4 matched nodes: does the scraper find a usable href?
        rows: nodes.slice(0, 4).map((el) => ({
          tag: el.tagName,
          ownHref: el.getAttribute("href"),
          firstAnchorHref: el.querySelector("a")?.getAttribute("href") ?? null,
          firstAnchorText: (el.querySelector("a")?.textContent || "").replace(/\s+/g, " ").trim().slice(0, 60),
          anchorsInside: el.querySelectorAll("a").length,
          text: (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 110),
        })),
      };
    }, sel);
    console.log(JSON.stringify(info, null, 2));

    // Which tab does the app think is active, and what does the URL say?
    const active = await page.evaluate(() => {
      const el = Array.from(document.querySelectorAll("a,button,[role='tab']")).find(
        (e) => /active|selected/.test((e as HTMLElement).className?.toString() || "") &&
               /leads/i.test((e.textContent || ""))
      );
      return el ? (el.textContent || "").replace(/\s+/g, " ").trim() : null;
    });
    console.log(`activeTabLabel=${active} url=${page.url()}`);
  }

  await context.close();
  await browser.close();
}

main().catch((e) => { console.error("DIAG2 ERROR:", e); process.exit(1); });
