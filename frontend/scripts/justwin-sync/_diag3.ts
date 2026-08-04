import "./load-env";
import { getAuthenticatedContext, getJustWinBaseUrl } from "./browser";

async function main() {
  const { browser, context } = await getAuthenticatedContext();
  const page = await context.newPage();
  await page.goto(`${getJustWinBaseUrl()}/leads`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(6000);

  // 1. Do the selectors the CURRENT clickTab() relies on even exist?
  console.log("=== current clickTab selector viability ===");
  console.log("  .lead-inbox-header [role=tab] :", await page.locator(".lead-inbox-header [role='tab']").count());
  console.log("  [role=tab] anywhere           :", await page.locator("[role='tab']").count());
  console.log("  getByRole(tab,/warm leads/i)  :", await page.getByRole("tab", { name: /warm leads/i }).count());
  console.log("  #warm-leads-link              :", await page.locator("#warm-leads-link").count());
  const divFallback = page.locator("button, a, [role='tab'], div").filter({ hasText: /warm leads/i });
  console.log("  div-fallback match count      :", await divFallback.count());
  if ((await divFallback.count()) > 0) {
    const first = await divFallback.first().evaluate((el) => ({
      tag: el.tagName,
      cls: (el as HTMLElement).className?.toString().slice(0, 80),
      textLen: (el.textContent || "").length,
    }));
    console.log("  div-fallback .first() would click:", JSON.stringify(first));
  }

  // 2. Does clicking a row navigate to a detail URL?
  console.log("\n=== row click navigation (warm tab) ===");
  await page.locator("#warm-leads-link").first().click();
  await page.waitForTimeout(4000);
  const before = page.url();
  await page.locator("table tbody tr").first().click({ timeout: 15000 })
    .catch((e) => console.log("  row click err:", e.message));
  await page.waitForTimeout(4000);
  console.log("  before:", before);
  console.log("  after :", page.url());

  // 3. Is there a pagination control / total count?
  await page.goBack({ waitUntil: "domcontentloaded" }).catch(() => undefined);
  await page.waitForTimeout(3000);
  const pager = await page.evaluate(() => {
    const t = document.body.textContent || "";
    const m = t.match(/(\d+)\s*[-–]\s*(\d+)\s+of\s+(\d+)/i) || t.match(/Showing[^.]{0,60}/i);
    return {
      pagerText: m ? m[0] : null,
      nextBtn: !!document.querySelector("[aria-label*='next' i], button[title*='next' i], .pagination"),
    };
  });
  console.log("\n=== pagination ===");
  console.log(JSON.stringify(pager, null, 2));

  await context.close();
  await browser.close();
}

main().catch((e) => { console.error("DIAG3 ERROR:", e); process.exit(1); });
