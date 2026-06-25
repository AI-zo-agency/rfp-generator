import fs from "fs";
import path from "path";
import { NextResponse } from "next/server";
import { getRfpPdfPath } from "@/lib/db";

export const runtime = "nodejs";

function resolvePdfPath(rfpId: string, recorded: string | null): string | null {
  const pdfRoot =
    process.env.PDF_STORAGE_PATH ?? path.join(process.cwd(), "storage", "pdfs");
  const candidates = [
    path.join(pdfRoot, rfpId, "rfp.pdf"),
    ...(recorded
      ? [
          recorded,
          path.join(process.cwd(), recorded),
          path.resolve(process.cwd(), recorded),
        ]
      : []),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;
  const pdfPath = resolvePdfPath(id, getRfpPdfPath(id));

  if (!pdfPath) {
    return NextResponse.json({ error: "PDF not found" }, { status: 404 });
  }

  const buffer = fs.readFileSync(pdfPath);
  return new NextResponse(buffer, {
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": "inline",
    },
  });
}
