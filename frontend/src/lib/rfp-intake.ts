import { formatDate, formatDateTime, formatRelativeTime } from "@/lib/format";
import type { RfpRecord } from "@/types/rfp";

const JUSTWIN_TAB_LABEL: Record<string, string> = {
  hot: "Hot",
  warm: "Warm",
  review: "Review",
};

/** Short JustWin posted date — e.g. "Aug 28" (year omitted when current). */
export function formatJustWinDate(dateStr: string): string {
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return dateStr;
  const now = new Date();
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

export interface RfpIntakeDescription {
  method: string;
  syncedLabel: string;
  tooltip: string;
  /** JustWin lead posted date — the date column in JustWin when this lead appeared. */
  justwinDateLabel?: string;
}

/** How an RFP entered ZO and when it was last synced / added. */
export function describeRfpIntake(rfp: RfpRecord): RfpIntakeDescription {
  const syncedAt = rfp.syncedAt || rfp.lastActivity;
  const syncedTs = syncedAt ? new Date(syncedAt).getTime() : Number.NaN;
  const validSync = Number.isFinite(syncedTs);

  let method: string;
  if (rfp.source === "manual") {
    method = "Manual upload";
  } else {
    const tab = rfp.justwinTab
      ? (JUSTWIN_TAB_LABEL[rfp.justwinTab] ?? rfp.justwinTab)
      : null;
    method = tab ? `JustWin · ${tab}` : "JustWin sync";
  }

  const syncedLabel = validSync
    ? `Synced ${formatRelativeTime(syncedAt!)}`
    : "Sync time unknown";

  const justwinDateLabel =
    rfp.source === "justwin" && rfp.receivedDate
      ? `JustWin date ${formatJustWinDate(rfp.receivedDate)}`
      : undefined;

  const tooltipParts = [method];
  if (justwinDateLabel && rfp.receivedDate) {
    tooltipParts.push(`Lead posted on JustWin ${formatDate(rfp.receivedDate)}`);
  }
  if (validSync) {
    tooltipParts.push(`Added to ZO ${formatDateTime(syncedAt!)}`);
  }

  return {
    method,
    syncedLabel,
    tooltip: tooltipParts.join(" · "),
    justwinDateLabel,
  };
}
