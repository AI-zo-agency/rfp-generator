import "./load-env";
import fs from "fs";
import path from "path";
import { getAuthenticatedContext, getJustWinBaseUrl } from "./browser";

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
    await page.waitForTimeout(3000);

    await page.screenshot({
      path: path.join(outDir, "leads.png"),
      fullPage: true,
    });

    const info = {
      url: page.url(),
      title: await page.title(),
      links: await page.locator("a").evaluateAll((anchors) =>
        anchors
          .map((a) => ({
            text: (a.textContent ?? "").trim().slice(0, 200),
            href: a.getAttribute("href"),
          }))
          .filter((l) => l.text.length > 10)
      ),
      inputs: await page.locator("input").evaluateAll((inputs) =>
        inputs.map((i) => ({
          type: i.getAttribute("type"),
          placeholder: i.getAttribute("placeholder"),
          name: i.getAttribute("name"),
        }))
      ),
      tabs: await page
        .locator('[role="tab"], button')
        .evaluateAll((els) =>
          els
            .map((el) => (el.textContent ?? "").trim())
            .filter((t) => t.length > 0 && t.length < 40)
        ),
      bodySnippet: ((await page.locator("main, body").first().textContent()) ?? "")
        .replace(/\s+/g, " ")
        .slice(0, 3000),
    };

    fs.writeFileSync(
      path.join(outDir, "leads-debug.json"),
      JSON.stringify(info, null, 2)
    );

    console.log(JSON.stringify({ ok: true, outDir, linkCount: info.links.length }));
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
