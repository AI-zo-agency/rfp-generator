import { randomUUID } from "crypto";
import fs from "fs";
import path from "path";
import type { RfpPriority, RfpRecord } from "@/types/rfp";

export interface ManualRfpInput {
  title: string;
  client: string;
  location?: string;
  sector?: string;
  dueDate: string;
  description?: string;
  pageLimit?: number;
  estimatedValue?: number;
  priority?: RfpPriority;
}

export function validateManualRfpInput(
  input: ManualRfpInput
): string | null {
  const title = input.title?.trim();
  const client = input.client?.trim();
  const dueDate = input.dueDate?.trim();

  if (!title || title.length < 3) {
    return "Title is required (at least 3 characters).";
  }
  if (!client) {
    return "Client / agency is required.";
  }
  if (!dueDate || Number.isNaN(Date.parse(dueDate))) {
    return "A valid due date is required.";
  }
  return null;
}

export function buildManualRfpRecord(input: ManualRfpInput): RfpRecord {
  const now = new Date().toISOString();
  const id = `manual-${randomUUID()}`;

  return {
    id,
    externalId: id,
    title: input.title.trim(),
    client: input.client.trim(),
    source: "manual",
    sector: input.sector?.trim() || "Public Sector",
    location: input.location?.trim() || "",
    dueDate: input.dueDate,
    receivedDate: now.slice(0, 10),
    stage: "intake",
    status: "new",
    priority: input.priority ?? "medium",
    fitScore: null,
    worthScore: null,
    goNoGo: null,
    assignedTo: null,
    estimatedValue: input.estimatedValue ?? null,
    pageLimit: input.pageLimit,
    lastActivity: now,
    lastActivityNote: "Manually added",
    contractRole: "prime",
    description: input.description?.trim() || undefined,
    syncedAt: now,
  };
}

export async function saveManualRfpPdf(
  rfpId: string,
  file: File
): Promise<string> {
  if (file.size === 0) {
    throw new Error("PDF file is empty.");
  }
  if (file.type && file.type !== "application/pdf") {
    throw new Error("Only PDF files are supported.");
  }

  const pdfRoot =
    process.env.PDF_STORAGE_PATH ?? path.join(process.cwd(), "storage", "pdfs");
  const dir = path.join(pdfRoot, rfpId);
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, "rfp.pdf");
  const buffer = Buffer.from(await file.arrayBuffer());
  fs.writeFileSync(target, buffer);
  return target;
}
