import type { JustWinLead } from "../../scripts/justwin-sync/types";
import type { RfpRecord } from "@/types/rfp";

function parseJustWinDate(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) {
    return new Date().toISOString().split("T")[0];
  }

  const withYear = new Date(`${trimmed} ${new Date().getFullYear()}`);
  if (!Number.isNaN(withYear.getTime())) {
    return withYear.toISOString().split("T")[0];
  }

  const parsed = new Date(trimmed);
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toISOString().split("T")[0];
  }

  return new Date().toISOString().split("T")[0];
}

function extractClient(title: string): string {
  const forMatch = title.match(/\bfor\s+(.+?)(?:\s*\[[A-Z]{2}\])?$/i);
  if (forMatch?.[1]) {
    return forMatch[1].trim();
  }
  return title.replace(/\s*\[[A-Z]{2}\]\s*$/, "").trim();
}

export function mapLeadToRfp(lead: JustWinLead, pdfPath?: string): RfpRecord {
  const now = new Date().toISOString();
  const id = `rfp-jw-${lead.externalId}`;

  return {
    id,
    externalId: lead.externalId,
    title: lead.title.replace(/\s*\[[A-Z]{2}\]\s*$/, "").trim(),
    client: extractClient(lead.title),
    source: "justwin",
    sector: "Public Sector",
    location: lead.location,
    dueDate: parseJustWinDate(lead.dueDate),
    receivedDate: parseJustWinDate(lead.postedDate),
    stage: "intake",
    status: "new",
    priority: lead.score >= 4 ? "high" : "medium",
    fitScore: lead.score * 20,
    worthScore: null,
    goNoGo: null,
    assignedTo: null,
    estimatedValue: null,
    lastActivity: now,
    lastActivityNote: `Synced from JustWin (${lead.tab} leads)`,
    contractRole: "prime",
    description: lead.description,
    justwinTab: lead.tab,
    pdfPath,
    justwinDetailUrl: lead.detailUrl,
    syncedAt: now,
    pdfUrl: pdfPath ? `/api/rfps/${id}/pdf` : undefined,
  };
}
