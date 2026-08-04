import fs from "fs";
import path from "path";
import type { Page } from "playwright";
import { createApiClient, resolvePdfUrl, type JustWinApiClient } from "./justwin-api";

const PDF_ROOT =
  process.env.PDF_STORAGE_PATH ?? path.join(process.cwd(), "storage", "pdfs");

function saveBuffer(externalId: string, buffer: Buffer): string | undefined {
  if (buffer.length < 500) return undefined;
  if (buffer.subarray(0, 4).toString() !== "%PDF") return undefined;

  const dir = path.join(PDF_ROOT, externalId);
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, "rfp.pdf");
  fs.writeFileSync(target, buffer);
  return target;
}

/**
 * Download a lead's solicitation package PDF.
 *
 * Resolves the signed S3 URL through JustWin's API instead of clicking the
 * document card, which required being on the detail page and matched whatever
 * element happened to mention a file size.
 *
 * Returns undefined when the lead has no attached document.
 */
export async function downloadSolicitationPdf(
  client: JustWinApiClient,
  externalId: string
): Promise<string | undefined> {
  const s3Url = await resolvePdfUrl(client, externalId);
  if (!s3Url) {
    console.log(`[justwin-sync] ${externalId}: no solicitation document`);
    return undefined;
  }

  const pdfResponse = await client.page.request.get(s3Url);
  if (!pdfResponse.ok()) {
    throw new Error(`Failed to download PDF from S3 (${pdfResponse.status()})`);
  }

  const target = saveBuffer(externalId, Buffer.from(await pdfResponse.body()));
  if (!target) {
    throw new Error("Downloaded file was not a valid PDF");
  }

  console.log(`[justwin-sync] saved PDF: ${target}`);
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
