import "./load-env";
import fs from "fs";
import path from "path";
import { getAuthenticatedContext } from "./browser";

const URL =
  "https://app.justwin.ai/leads/c8833e28-bfbb-4bc3-8e04-6b2b73a82f10/summary";

async function main() {
  const { browser, context } = await getAuthenticatedContext();
  const page = await context.newPage();
  const apiResponses: { url: string; status: number; body?: unknown }[] = [];

  page.on("response", async (response) => {
    const url = response.url();
    if (!url.includes("api.justwin.ai")) return;
    try {
      const contentType = response.headers()["content-type"] ?? "";
      const entry: { url: string; status: number; body?: unknown } = {
        url,
        status: response.status(),
      };
      if (contentType.includes("json")) {
        entry.body = await response.json();
      }
      apiResponses.push(entry);
    } catch {
      // ignore
    }
  });

  try {
    await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 90000 });
    await page.waitForTimeout(6000);

    const row = page
      .locator("div, li")
      .filter({ hasText: /Digital Advertising Services for James Madison University/i })
      .filter({ hasText: /1\.34\s*MB/i })
      .last();

    await row.click({ timeout: 15000 });
    await page.waitForTimeout(5000);

    const out = path.join(process.cwd(), "data", "debug", "api-responses.json");
    fs.writeFileSync(out, JSON.stringify(apiResponses, null, 2));

    const docApis = apiResponses.filter((r) =>
      /document|file|package|content|attachment/i.test(r.url)
    );
    console.log(JSON.stringify(docApis, null, 2));
  } finally {
    await context.close();
    await browser.close();
  }
}

main();
