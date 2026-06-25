import fs from "fs";
import path from "path";
import type { APIRequestContext } from "playwright";

const PDF_ROOT =
  process.env.PDF_STORAGE_PATH ?? path.join(process.cwd(), "storage", "pdfs");

export async function downloadPdfs(
  request: APIRequestContext,
  externalId: string,
  pdfUrls: string[]
): Promise<string | undefined> {
  if (pdfUrls.length === 0) return undefined;

  const dir = path.join(PDF_ROOT, externalId);
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, "rfp.pdf");

  for (const pdfUrl of pdfUrls) {
    try {
      const response = await request.get(pdfUrl);
      if (!response.ok()) continue;

      const body = await response.body();
      if (body.length < 100) continue;

      fs.writeFileSync(target, body);
      return target;
    } catch {
      continue;
    }
  }

  return undefined;
}
