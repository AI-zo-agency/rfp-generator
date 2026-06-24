import "./load-env";
import { getAuthenticatedContext } from "./browser";

const URL =
  "https://app.justwin.ai/leads/c8833e28-bfbb-4bc3-8e04-6b2b73a82f10/summary";

async function main() {
  const { browser, context } = await getAuthenticatedContext();
  const page = await context.newPage();
  const captured: string[] = [];

  page.on("response", (response) => {
    const url = response.url();
    if (
      /amazonaws|\.pdf|content\/files|document|download/i.test(url) &&
      response.status() === 200
    ) {
      captured.push(`${response.status()} ${response.headers()["content-type"] ?? ""} ${url}`);
    }
  });

  try {
    await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 90000 });
    await page.waitForTimeout(5000);

    const row = page
      .locator("div, li")
      .filter({ hasText: /Digital Advertising Services for James Madison University/i })
      .filter({ hasText: /1\.34\s*MB/i })
      .last();

    console.log("before url:", page.url());
    await row.click({ timeout: 15000 });
    await page.waitForTimeout(5000);
    console.log("after url:", page.url());

    const modal = page.locator(
      '[role="dialog"], [class*="modal"], iframe, embed, object, canvas'
    );
    console.log("modal/viewer count:", await modal.count());

    for (let i = 0; i < Math.min(await modal.count(), 5); i++) {
      const el = modal.nth(i);
      const tag = await el.evaluate((node) => node.tagName.toLowerCase());
      const src =
        (await el.getAttribute("src")) ??
        (await el.getAttribute("data")) ??
        "n/a";
      console.log(`viewer ${i}:`, tag, src);
    }

    const downloadBtn = page.getByRole("button", { name: /download/i });
    console.log("download buttons:", await downloadBtn.count());

  const links = await page.locator("a[href]").evaluateAll((anchors) =>
      anchors
        .map((a) => ({
          text: (a.textContent ?? "").trim().slice(0, 60),
          href: a.getAttribute("href"),
        }))
        .filter((l) => /pdf|amazonaws|download|file/i.test(`${l.text} ${l.href ?? ""}`))
    );
    console.log("file links:", links);
    console.log("captured responses:", captured);
  } finally {
    await context.close();
    await browser.close();
  }
}

main();
