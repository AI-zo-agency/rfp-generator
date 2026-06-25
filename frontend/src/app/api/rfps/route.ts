import { insertManualRfp } from "@/lib/db";
import {
  buildManualRfpRecord,
  saveManualRfpPdf,
  validateManualRfpInput,
  type ManualRfpInput,
} from "@/lib/manual-rfp";
import { getDashboardData } from "@/lib/rfp-service";
import type { RfpPriority } from "@/types/rfp";
import { NextResponse } from "next/server";

export async function GET() {
  const data = await getDashboardData();

  return NextResponse.json({
    ...data,
    source: process.env.JUSTWIN_API_KEY ? "justwin" : "mock",
  });
}

function parseOptionalInt(value: FormDataEntryValue | null): number | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function formToInput(form: FormData): ManualRfpInput {
  const priority = form.get("priority");
  return {
    title: String(form.get("title") ?? ""),
    client: String(form.get("client") ?? ""),
    location: String(form.get("location") ?? ""),
    sector: String(form.get("sector") ?? ""),
    dueDate: String(form.get("dueDate") ?? ""),
    description: String(form.get("description") ?? ""),
    pageLimit: parseOptionalInt(form.get("pageLimit")),
    estimatedValue: parseOptionalInt(form.get("estimatedValue")),
    priority:
      typeof priority === "string" && priority
        ? (priority as RfpPriority)
        : undefined,
  };
}

export async function POST(request: Request) {
  try {
    const contentType = request.headers.get("content-type") ?? "";
    let input: ManualRfpInput;
    let pdfFile: File | null = null;

    if (contentType.includes("multipart/form-data")) {
      const form = await request.formData();
      input = formToInput(form);
      const file = form.get("pdf");
      pdfFile = file instanceof File && file.size > 0 ? file : null;
    } else {
      const body = (await request.json()) as ManualRfpInput;
      input = body;
    }

    const validationError = validateManualRfpInput(input);
    if (validationError) {
      return NextResponse.json({ error: validationError }, { status: 400 });
    }

    const record = buildManualRfpRecord(input);

    if (pdfFile) {
      try {
        record.pdfPath = await saveManualRfpPdf(record.id, pdfFile);
        record.pdfUrl = `/api/rfps/${record.id}/pdf`;
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Failed to save PDF.";
        return NextResponse.json({ error: message }, { status: 400 });
      }
    }

    insertManualRfp(record);

    return NextResponse.json({ ok: true, rfp: record }, { status: 201 });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to create manual RFP.";
    const status = message.includes("UNIQUE constraint") ? 409 : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
