import { describe, expect, it } from "vitest";

import { buildNoteCards, type InsightsData } from "./use-qb-insights";
import type { Signal } from "../types/quickbooks";

function agencyData(over: Partial<InsightsData> = {}): InsightsData {
  return {
    status: "ok",
    brief: "Two items carried over from last week.",
    notes: {},
    signals: [],
    cadence: "weekly",
    period_label: "Monday 25 August to Friday 29 August",
    current_week_label: "This week: Monday 1 September to Friday 5 September (in progress)",
    as_of: "2026-08-25",
    generated_at: "2026-08-31T06:00:00Z",
    provider: "gemini",
    model: "gemini-2.0-flash",
    stale: false,
    ...over,
  };
}

describe("agency weekly insights", () => {
  const carryover: Signal = {
    id: "carryover:week",
    severity: "warn",
    headline: "2 items carried over",
    figure: "$1,200",
    detail: "Late: Alpha · open 3 wks",
    go_to: "jobs",
  };

  it("builds cards from server signals with agency badge rules", () => {
    const [card] = buildNoteCards(agencyData({ signals: [carryover] }), [], "agency");
    expect(card.id).toBe("carryover:week");
    expect(card.badge).toBe("Risk");
    expect(card.goTo).toBe("jobs");
  });

  it("marks aging carryover as high impact at 4+ weeks", () => {
    const aging: Signal = {
      id: "aging:queue",
      severity: "critical",
      headline: "1 items open 3+ weeks",
      figure: "4",
      detail: "Longest: Late: Alpha",
      go_to: "jobs",
    };
    const [card] = buildNoteCards(agencyData({ signals: [aging] }), [], "agency");
    expect(card.badge).toBe("High impact");
  });

  it("attaches model notes to signal ids", () => {
    const [card] = buildNoteCards(
      agencyData({
        signals: [carryover],
        notes: { "carryover:week": "Call the client today." },
      }),
      [],
      "agency",
    );
    expect(card.aiEnhanced).toBe(true);
    expect(card.detail).toContain("Call the client today.");
  });
});
