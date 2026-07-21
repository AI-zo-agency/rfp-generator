import type { GoNoGoAnalysis, RfpRecord } from "@/types/rfp";

function parseAnalysis(raw: unknown): GoNoGoAnalysis | null | undefined {
  if (raw == null) return null;
  if (typeof raw === "object") return raw as GoNoGoAnalysis;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw) as unknown;
      return typeof parsed === "object" && parsed !== null
        ? (parsed as GoNoGoAnalysis)
        : null;
    } catch {
      return null;
    }
  }
  return null;
}

/** Map Supabase `rfps` row (snake_case) → app `RfpRecord` (camelCase). */
export function mapSupabaseRfpRow(row: Record<string, unknown>): RfpRecord {
  const id = String(row.id ?? "");
  const pdfPath =
    typeof row.pdf_path === "string" ? row.pdf_path : undefined;

  return {
    id,
    externalId:
      typeof row.external_id === "string" ? row.external_id : undefined,
    title: typeof row.title === "string" ? row.title : "",
    client: typeof row.client === "string" ? row.client : "",
    source:
      row.source === "manual" || row.source === "justwin" ? row.source : "justwin",
    sector: typeof row.sector === "string" ? row.sector : "Public Sector",
    location: typeof row.location === "string" ? row.location : "",
    dueDate: typeof row.due_date === "string" ? row.due_date : "",
    receivedDate:
      typeof row.received_date === "string" ? row.received_date : "",
    stage: (row.stage as RfpRecord["stage"]) || "intake",
    status: (row.status as RfpRecord["status"]) || "new",
    priority: (row.priority as RfpRecord["priority"]) || "medium",
    fitScore: typeof row.fit_score === "number" ? row.fit_score : null,
    worthScore: typeof row.worth_score === "number" ? row.worth_score : null,
    goNoGo:
      row.go_no_go === "go" ||
      row.go_no_go === "no_go" ||
      row.go_no_go === "review"
        ? row.go_no_go
        : null,
    assignedTo:
      typeof row.assigned_to === "string" ? row.assigned_to : null,
    estimatedValue:
      typeof row.estimated_value === "number" ? row.estimated_value : null,
    pageLimit: typeof row.page_limit === "number" ? row.page_limit : undefined,
    lastActivity:
      typeof row.last_activity === "string" ? row.last_activity : "",
    lastActivityNote:
      typeof row.last_activity_note === "string" ? row.last_activity_note : "",
    contractRole:
      row.contract_role === "subconsultant" ? "subconsultant" : "prime",
    description:
      typeof row.description === "string" ? row.description : undefined,
    justwinTab:
      row.justwin_tab === "hot" ||
      row.justwin_tab === "warm" ||
      row.justwin_tab === "review"
        ? row.justwin_tab
        : undefined,
    justwinDetailUrl:
      typeof row.justwin_detail_url === "string"
        ? row.justwin_detail_url
        : undefined,
    syncedAt: typeof row.synced_at === "string" ? row.synced_at : undefined,
    pdfPath,
    goNoGoAnalysis: parseAnalysis(row.go_no_go_analysis),
  };
}
