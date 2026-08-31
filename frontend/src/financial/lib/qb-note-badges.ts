/**
 * Maps QuickBooks note cards to the mockup impact badges.
 * Pure labels only — no styling. Tunable in one place.
 */

import type { Severity } from "../types/quickbooks";

export type NoteBadge =
  | "High impact"
  | "Opportunity"
  | "Risk"
  | "Action"
  | "Watch";

export type NoteRowKind = "collect" | "margin" | "fix";

export type NoteSource = "quickbooks" | "teamwork" | "agency";

/** Rule-based signals from derive_signals (QuickBooks) or build_signals (Teamwork). */
export function badgeForSignal(
  id: string,
  severity: Severity,
  source: NoteSource = "quickbooks",
): NoteBadge {
  // Teamwork ids are partly dynamic (capacity:sustained:<person_id>), so there
  // is no id table to switch on — severity is the only stable input. `warn`
  // there is a live delivery risk, not something to merely watch.
  if (source === "teamwork") {
    if (severity === "critical") return "High impact";
    return severity === "warn" ? "Risk" : "Watch";
  }

  if (source === "agency") {
    if (id.startsWith("carryover:") || id.startsWith("aging:")) {
      return severity === "critical" ? "High impact" : "Risk";
    }
    if (id.startsWith("priority:delivery:")) return "High impact";
    if (id.startsWith("priority:")) return "Risk";
    if (id === "new:week") return "Watch";
    if (id === "invoices:unlinked" || id === "orphans:billed") {
      return severity === "critical" ? "High impact" : "Risk";
    }
    if (id === "queue:baseline" || id === "ar:open") return "Watch";
    if (severity === "critical") return "High impact";
    if (severity === "warn") return "Risk";
    return "Watch";
  }

  switch (id) {
    case "ap-over-cash":
    case "ar-late":
      return severity === "info" ? "Watch" : "High impact";
    case "vendor-concentration":
    case "segment-gap":
      return "Risk";
    case "cost-untagged":
      return "Action";
    case "slow-payers":
    case "collection-rate":
    case "sync":
      return "Watch";
    default:
      if (severity === "critical") return "High impact";
      if (severity === "warn") return "Watch";
      return "Watch";
  }
}

/** Factual chase / margin / hygiene rows. */
export function badgeForRow(kind: NoteRowKind, slowPayer?: boolean): NoteBadge {
  if (kind === "margin") return "Opportunity";
  if (kind === "fix") return "Action";
  // collect
  return slowPayer ? "Watch" : "High impact";
}
