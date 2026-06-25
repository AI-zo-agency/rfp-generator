import type { Page } from "playwright";
import { getJustWinBaseUrl } from "./browser";

const DOC_TABS = [/documents/i, /attachments/i, /files/i, /rfp/i, /solicitation/i];

export async function findPdfUrls(
  page: Page,
  detailUrl: string
): Promise<string[]> {
  const baseUrl = getJustWinBaseUrl();
  const url = detailUrl.startsWith("http")
    ? detailUrl
    : `${baseUrl}${detailUrl.startsWith("/") ? "" : "/"}${detailUrl}`;

  if (!page.url().startsWith(url.split("/summary")[0])) {
    await page.goto(url, {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
  }

  await page.waitForTimeout(3000);

  for (const tabPattern of DOC_TABS) {
    const tab = page.getByRole("tab", { name: tabPattern });
    if ((await tab.count()) > 0) {
      await tab.first().click();
      await page.waitForTimeout(2000);
      break;
    }

    const textTab = page.getByText(tabPattern);
    if ((await textTab.count()) > 0) {
      await textTab.first().click();
      await page.waitForTimeout(2000);
      break;
    }
  }

  const urls = new Set<string>();

  const linkSelectors = [
    'a[href$=".pdf"]',
    'a[href*=".pdf"]',
    'a[download]',
    'a[href*="/download"]',
    'a[href*="/document"]',
    'a[href*="/file"]',
  ];

  for (const selector of linkSelectors) {
    const links = page.locator(selector);
    const count = await links.count();
    for (let i = 0; i < count; i++) {
      const href = await links.nth(i).getAttribute("href");
      if (!href) continue;
      const full = href.startsWith("http")
        ? href
        : `${baseUrl}${href.startsWith("/") ? "" : "/"}${href}`;
      if (
        full.toLowerCase().includes(".pdf") ||
        full.toLowerCase().includes("/download") ||
        full.toLowerCase().includes("/document")
      ) {
        urls.add(full);
      }
    }
  }

  const buttons = page.getByRole("button", { name: /download|pdf|document/i });
  const buttonCount = await buttons.count();
  for (let i = 0; i < buttonCount; i++) {
    const onclick = await buttons.nth(i).getAttribute("onclick");
    const dataUrl = await buttons.nth(i).getAttribute("data-url");
    const href = dataUrl ?? onclick?.match(/https?:\/\/[^'"]+\.pdf/i)?.[0];
    if (href) urls.add(href);
  }

  return [...urls];
}
