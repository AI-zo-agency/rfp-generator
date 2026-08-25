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

/** Rule-based signals from derive_signals. */
export function badgeForSignal(id: string, severity: Severity): NoteBadge {
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
