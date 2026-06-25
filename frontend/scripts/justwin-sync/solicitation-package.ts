import fs from "fs";
import path from "path";
import type { Page } from "playwright";
import { getJustWinBaseUrl } from "./browser";
import { getTitleFilter } from "./title-filter";

const PDF_ROOT =
  process.env.PDF_STORAGE_PATH ?? path.join(process.cwd(), "storage", "pdfs");

function leadIdFromDetailUrl(detailUrl: string): string {
  const parts = detailUrl.split("/").filter(Boolean);
  const leadsIndex = parts.indexOf("leads");
  if (leadsIndex >= 0 && parts[leadsIndex + 1]) {
    return parts[leadsIndex + 1];
  }
  throw new Error(`Could not parse lead id from ${detailUrl}`);
}

async function getAuthHeaders(page: Page): Promise<Record<string, string>> {
  const token = await page.evaluate(() => localStorage.getItem("token"));
  if (!token) {
    throw new Error("JustWin auth token not found in browser session");
  }
  return { Authorization: `Bearer ${token}` };
}

async function ensureOnDetailPage(page: Page, detailUrl: string): Promise<void> {
  const baseUrl = getJustWinBaseUrl();
  const url = detailUrl.startsWith("http")
    ? detailUrl
    : `${baseUrl}${detailUrl.startsWith("/") ? "" : "/"}${detailUrl}`;

  await page.goto(url, {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await page.waitForTimeout(4000);
  await page
    .getByRole("heading", { name: /^solicitation package$/i })
    .first()
    .waitFor({ state: "visible", timeout: 60000 });
}

async function findPackageDocument(page: Page) {
  const titleFilter = getTitleFilter();

  if (titleFilter) {
    const byTitle = page
      .locator("div, li, a, button")
      .filter({
        hasText: new RegExp(titleFilter.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"),
      })
      .filter({ hasText: /\d+(\.\d+)?\s*MB/i })
      .filter({ hasNotText: /view original solicitation/i });

    if ((await byTitle.count()) > 0) {
      return byTitle.last();
    }
  }

  const packageHeading = page.getByRole("heading", {
    name: /^solicitation package$/i,
  });
  const section = packageHeading.locator(
    "xpath=ancestor::*[.//*[contains(text(),'View Original Solicitation')] or .//*[contains(text(),'MB')]][1]"
  );

  const packageRows = section
    .locator("div, li, a, button")
    .filter({ hasText: /\d+(\.\d+)?\s*MB/i })
    .filter({ hasNotText: /view original solicitation/i });

  if ((await packageRows.count()) > 0) {
    return packageRows.first();
  }

  const listedRows = section
    .locator("div, li, a, button")
    .filter({ hasNotText: /view original solicitation|add file|add link|solicitation package/i });

  const count = await listedRows.count();
  const items: ReturnType<Page["locator"]>[] = [];

  for (let i = 0; i < count; i++) {
    const row = listedRows.nth(i);
    const text = ((await row.textContent()) ?? "").replace(/\s+/g, " ").trim();
    if (text.length < 8) continue;
    items.push(row);
  }

  if (items.length >= 2) {
    return items[1];
  }

  return items[0] ?? null;
}

async function saveBuffer(
  externalId: string,
  buffer: Buffer
): Promise<string | undefined> {
  if (buffer.length < 500) return undefined;
  if (buffer.slice(0, 4).toString() !== "%PDF") return undefined;

  const dir = path.join(PDF_ROOT, externalId);
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, "rfp.pdf");
  fs.writeFileSync(target, buffer);
  return target;
}

async function resolveS3Url(
  page: Page,
  detailUrl: string,
  clickTarget: ReturnType<Page["locator"]>
): Promise<string> {
  let s3Url: string | null = null;

  const onResponse = async (response: import("playwright").Response) => {
    if (!/\/targets\/[^/]+\/view$/.test(response.url())) return;
    if (response.status() !== 200) return;
    try {
      const payload = (await response.json()) as { url?: string };
      if (payload.url) s3Url = payload.url;
    } catch {
      // ignore
    }
  };

  page.on("response", onResponse);
  await clickTarget.scrollIntoViewIfNeeded();
  await clickTarget.click({ timeout: 15000 });
  await page.waitForTimeout(5000);
  page.off("response", onResponse);

  if (s3Url) {
    return s3Url;
  }

  const headers = await getAuthHeaders(page);
  const leadId = leadIdFromDetailUrl(detailUrl);
  const leadResponse = await page.request.get(
    `https://api.justwin.ai/leads/${leadId}`,
    { headers }
  );
  if (!leadResponse.ok()) {
    throw new Error(`Lead API failed (${leadResponse.status()})`);
  }

  const lead = (await leadResponse.json()) as { target?: string };
  if (!lead.target) {
    throw new Error("Lead API did not return a target id");
  }

  const viewResponse = await page.request.get(
    `https://api.justwin.ai/targets/${lead.target}/view`,
    { headers }
  );
  if (!viewResponse.ok()) {
    throw new Error(`Target view API failed (${viewResponse.status()})`);
  }

  const payload = (await viewResponse.json()) as { url?: string };
  if (!payload.url) {
    throw new Error("Target view API did not return an S3 URL");
  }

  return payload.url;
}

export async function downloadPdfFromSolicitationPackage(
  page: Page,
  _context: import("playwright").BrowserContext,
  externalId: string,
  detailUrl: string
): Promise<string | undefined> {
  await ensureOnDetailPage(page, detailUrl);

  const document = await findPackageDocument(page);
  if (!document) {
    throw new Error("Could not find PDF document in Solicitation Package");
  }

  const label = ((await document.textContent()) ?? "").replace(/\s+/g, " ").trim();
  console.log(
    `[justwin-sync] solicitation package -> clicking 2nd document: "${label.slice(0, 100)}"`
  );

  const s3Url = await resolveS3Url(page, detailUrl, document);
  console.log("[justwin-sync] got S3 PDF URL from JustWin viewer");

  const pdfResponse = await page.request.get(s3Url);
  if (!pdfResponse.ok()) {
    throw new Error(`Failed to download PDF from S3 (${pdfResponse.status()})`);
  }

  const buffer = Buffer.from(await pdfResponse.body());
  const target = await saveBuffer(externalId, buffer);
  if (!target) {
    throw new Error("Downloaded file was not a valid PDF");
  }

  console.log(`[justwin-sync] saved PDF: ${target}`);
  return target;
}
