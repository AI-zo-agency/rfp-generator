import "./load-env";
import { getAuthenticatedContext } from "./browser";
import { findPdfUrls } from "./scrape-detail";

const LEAD_ID = "c8833e28-bfbb-4bc3-8e04-6b2b73a82f10";
const URLS = [
  `https://app.justwin.ai/leads/${LEAD_ID}/summary`,
  `https://app.justwin.ai/leads/${LEAD_ID}`,
  `https://app.justwin.ai/leads/${LEAD_ID}/documents`,
  `https://app.justwin.ai/leads/${LEAD_ID}/files`,
];

async function main() {
  const { browser, context } = await getAuthenticatedContext();
  const page = await context.newPage();

  try {
    for (const url of URLS) {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
      await page.waitForTimeout(3000);
      const body = ((await page.textContent("body")) ?? "").replace(/\s+/g, " ");
      console.log(
        JSON.stringify({
          url: page.url(),
          hasPdf: /pdf|download|document|attachment/i.test(body),
          snippet: body.slice(0, 500),
        })
      );
    }

    const pdfs = await findPdfUrls(page, URLS[0]);
    const links = await page.locator("a").evaluateAll((anchors) =>
      anchors.map((a) => ({
        text: (a.textContent ?? "").trim().slice(0, 80),
        href: a.getAttribute("href"),
      }))
    );

    const docLinks = links.filter(
      (l) =>
        /pdf|document|download|file|attachment|rfp|solicitation/i.test(
          `${l.text} ${l.href ?? ""}`
        )
    );

    const tabs = await page
      .locator('[role="tab"], nav a, button')
      .evaluateAll((els) =>
        els.map((el) => (el.textContent ?? "").trim()).filter((t) => t.length < 40)
      );

    console.log(
      JSON.stringify({ pdfs, docLinks, tabs: [...new Set(tabs)].slice(0, 30) }, null, 2)
    );
  } finally {
    await context.close();
    await browser.close();
  }
}

main();
