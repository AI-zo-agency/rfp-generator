import "./load-env";
import fs from "fs";
import path from "path";
import { getAuthenticatedContext, getJustWinBaseUrl } from "./browser";

const FILTER = "James Madison University";

async function main() {
  const outDir = path.join(process.cwd(), "data", "debug");
  fs.mkdirSync(outDir, { recursive: true });

  const { browser, context } = await getAuthenticatedContext();
  const page = await context.newPage();

  try {
    await page.goto(`${getJustWinBaseUrl()}/leads`, {
      waitUntil: "domcontentloaded",
      timeout: 90000,
    });
    await page.waitForTimeout(2000);

    for (const tab of ["Archived", "Explore", "Warm Leads", "Review"]) {
      const tabButton = page.getByRole("tab", { name: new RegExp(tab, "i") });
      if ((await tabButton.count()) > 0) {
        await tabButton.first().click();
        await page.waitForTimeout(2000);
      }

      const search = page.locator('input[placeholder*="Search solicitations" i]').first();
      if ((await search.count()) > 0) {
        await search.fill(FILTER);
        await page.waitForTimeout(2500);
      }

      const body = ((await page.locator("main, body").first().textContent()) ?? "")
        .replace(/\s+/g, " ");

      const matches = body.match(
        new RegExp(`.{0,80}Digital Advertising.{0,120}James Madison.{0,80}`, "i")
      );

      fs.writeFileSync(
        path.join(outDir, `tab-${tab.replace(/\s+/g, "-").toLowerCase()}.txt`),
        body.slice(0, 8000)
      );

      console.log(
        JSON.stringify({
          tab,
          hasMadison: body.toLowerCase().includes("james madison"),
          hasDigitalAdvertising: body.toLowerCase().includes("digital advertising"),
          match: matches?.[0] ?? null,
        })
      );
    }
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
