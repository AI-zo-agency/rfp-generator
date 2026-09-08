import fs from "fs";
import path from "path";
import type { Page } from "playwright";
import { createApiClient, type JustWinApiClient } from "./justwin-api";

const PDF_ROOT =
  process.env.PDF_STORAGE_PATH ?? path.join(process.cwd(), "storage", "pdfs");
const API_ROOT = process.env.JUSTWIN_API_ROOT ?? "https://api.justwin.ai";

function saveBuffer(externalId: string, buffer: Buffer): string | undefined {
  if (buffer.length < 500) return undefined;
  if (buffer.subarray(0, 4).toString() !== "%PDF") return undefined;

  const dir = path.join(PDF_ROOT, externalId);
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, "rfp.pdf");
  fs.writeFileSync(target, buffer);
  return target;
}

function scoreName(name: string): number {
  const low = name.toLowerCase();
  let score = 0;
  if (low.includes("request for proposal final")) score = 100;
  else if (low.includes("request for proposal")) score = 90;
  else if (low.includes("rfp final")) score = 85;
  else if (low.includes("solicitation")) score = 70;
  if (low.includes("invitation")) score -= 40;
  return score;
}

/**
 * Download JustWin's attached PDF, then enrich from the buyer's public portal
 * Bid Attachments when ``originating_url`` is present (Ionwave, etc.).
 *
 * Full multi-PDF merge lives in the Python sync (`portal_attachments.py`);
 * this CLI path prefers the highest-scored portal PDF when it outranks the
 * thin JustWin invitation packet.
 */
export async function downloadSolicitationPdf(
  client: JustWinApiClient,
  externalId: string
): Promise<string | undefined> {
  const leadRes = await client.page.request.get(`${API_ROOT}/leads/${externalId}`, {
    headers: client.headers,
  });
  if (!leadRes.ok()) {
    console.log(`[justwin-sync] ${externalId}: lead not found`);
    return undefined;
  }
  const lead = (await leadRes.json()) as {
    target?: string;
    documentless?: boolean;
    readonly_values?: { originating_url?: string; target_name?: string };
  };
  if (lead.documentless || !lead.target) {
    console.log(`[justwin-sync] ${externalId}: no solicitation document`);
    return undefined;
  }

  const viewRes = await client.page.request.get(
    `${API_ROOT}/targets/${lead.target}/view`,
    { headers: client.headers }
  );
  if (!viewRes.ok()) {
    console.log(`[justwin-sync] ${externalId}: no solicitation document`);
    return undefined;
  }
  const s3Url = ((await viewRes.json()) as { url?: string }).url;
  if (!s3Url) {
    console.log(`[justwin-sync] ${externalId}: no solicitation document`);
    return undefined;
  }

  const pdfResponse = await client.page.request.get(s3Url);
  if (!pdfResponse.ok()) {
    throw new Error(`Failed to download PDF from S3 (${pdfResponse.status()})`);
  }
  let best = Buffer.from(await pdfResponse.body());
  let bestLabel = "justwin";

  const originating = (lead.readonly_values?.originating_url || "").trim();
  if (originating && !/bonfire/i.test(originating)) {
    try {
      await client.page.goto(originating, {
        waitUntil: "domcontentloaded",
        timeout: 90000,
      });
      await client.page.waitForTimeout(2500);
      const anchors = await client.page.locator("a").evaluateAll((els) =>
        els.map((a) => ({
          text: (a.textContent || "").trim().slice(0, 200),
          href: (a as HTMLAnchorElement).href || a.getAttribute("href") || "",
        }))
      );
      for (const a of anchors) {
        const href = (a.href || "").trim();
        const text = (a.text || "").trim();
        if (!href || href.startsWith("javascript:")) continue;
        const blob = `${text} ${href}`.toLowerCase();
        if (
          !blob.includes(".pdf") &&
          !blob.includes("extract.aspx")
        ) {
          continue;
        }
        if (!text.toLowerCase().includes(".pdf") && !href.includes("extract.aspx")) {
          continue;
        }
        const res = await client.page.request.get(href, { timeout: 90000 });
        if (!res.ok()) continue;
        const buf = Buffer.from(await res.body());
        if (buf.length < 500 || buf.subarray(0, 4).toString() !== "%PDF") continue;
        const name = text || href;
        if (
          scoreName(name) > scoreName(bestLabel) ||
          (scoreName(name) >= scoreName(bestLabel) && buf.length > best.length)
        ) {
          best = buf;
          bestLabel = name;
          console.log(
            `[justwin-sync] portal preferred ${name} (${buf.length} bytes)`
          );
        }
      }
    } catch (err) {
      console.warn(
        `[justwin-sync] ${externalId}: portal enrich skipped:`,
        err instanceof Error ? err.message : err
      );
    }
  }

  const target = saveBuffer(externalId, best);
  if (!target) {
    throw new Error("Downloaded file was not a valid PDF");
  }

  console.log(`[justwin-sync] saved PDF (${bestLabel}): ${target}`);
  return target;
}

/** Convenience wrapper when no API client has been created yet. */
export async function downloadPdfForLead(
  page: Page,
  externalId: string
): Promise<string | undefined> {
  const client = await createApiClient(page);
  return downloadSolicitationPdf(client, externalId);
}
